"""LineAdapter — Hermes platform adapter for LINE Messaging API.

This adapter delegates control flow to BasePlatformAdapter.handle_message()
so LINE traffic gets the same queue/interrupt/bypass/photo-burst behavior
as Telegram/Discord/Signal/Matrix.

Three hooks are overridden for LINE-specific behavior:

  * ``send()`` — smart routing: cached reply_token (free Reply API) when
    valid; Push API fallback (metered) when expired or a Quick Reply
    postback button is pending delivery.
  * ``_keep_typing()`` — shows LINE's loading indicator (DM only) and at
    the ``slow_response_threshold`` mark (default 45s) sends a Quick Reply
    postback button so the user can fetch the response later via a fresh
    reply_token instead of paying for a Push API delivery.
  * ``_handle_postback`` (LINE-specific) — when the user taps the button,
    retrieves the cached response (PENDING/READY/DELIVERED/ERROR) and
    delivers via the postback's fresh reply_token.
"""
from __future__ import annotations

import asyncio
import base64
import enum
import hashlib
import hmac
import json
import logging
import os
import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import httpx

# aiohttp lives in the [messaging] optional extra; guard the top-level import
# so the module is importable when only core deps are installed. The factory
# in gateway/run.py uses check_line_requirements() to gate adapter creation.
try:
    import aiohttp  # noqa: F401
    from aiohttp import web
    AIOHTTP_AVAILABLE = True
except ImportError:
    aiohttp = None  # type: ignore[assignment]
    web = None  # type: ignore[assignment]
    AIOHTTP_AVAILABLE = False

from gateway.config import Platform, PlatformConfig
from gateway.platforms.helpers import MessageDeduplicator, strip_markdown
from gateway.platforms.base import (
    BasePlatformAdapter,
    MessageEvent,
    MessageType,
    SendResult,
    coerce_plaintext_gateway_command,
)
from gateway.session import SessionSource

logger = logging.getLogger(__name__)


def check_line_requirements() -> bool:
    """Check if LINE adapter dependencies (httpx, aiohttp) are available."""
    return AIOHTTP_AVAILABLE  # httpx is a core dep — only aiohttp can be missing


LINE_REPLY_URL = "https://api.line.me/v2/bot/message/reply"
LINE_LOADING_URL = "https://api.line.me/v2/bot/chat/loading/start"
LINE_PUSH_URL = "https://api.line.me/v2/bot/message/push"

# Defaults for the slow-LLM Quick Reply notice strings. Per-instance overrides
# come from env vars at __init__ time so tests can monkeypatch and operators
# can customize without restarting the module.
DEFAULT_PENDING_REPLY_TEXT = "🤔 Still thinking, please wait. Tap below to fetch the answer when ready."
DEFAULT_EXPIRED_REPLY_TEXT = "Response expired — please ask again."
DEFAULT_ALREADY_DELIVERED_TEXT = "Already replied ✅"
DEFAULT_SHOW_RESPONSE_BUTTON_LABEL = "📋 Show response"
DEFAULT_INTERRUPTED_TEXT = "⚡ This run was interrupted by a newer message — see the latest reply above."

# Known prefixes for system messages that base/run.py emit during an active
# session (interrupt-ack, queue-ack, steer-ack). When `_pending_buttons[chat]`
# is armed waiting for the agent's final response, these system messages must
# bypass the cache — otherwise the orphan button would resolve to the wrong
# content and the user would never see the system message as a bubble.
_SYSTEM_MESSAGE_PREFIXES = ("⚡ Interrupting", "⏳ Queued", "⏩ Steered")

# Match Markdown links — same shape as helpers._RE_LINK but we rewrite
# (preserving the URL) rather than discard.
_RE_MARKDOWN_LINK = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")


def _strip_markdown_for_line(text: str) -> str:
    """Strip Markdown for LINE plain-text bubbles, preserving link URLs.

    The shared ``strip_markdown`` discards URLs entirely (``[label](url)``
    → ``label``) — fine for SMS-style platforms with no link auto-detection,
    useless for LINE which auto-linkifies bare URLs in text. We rewrite
    ``[label](url)`` to ``label (url)`` first so the URL survives as
    tappable plain text, then delegate to the shared helper for the rest
    (bold, italic, code, headings).
    """
    text = _RE_MARKDOWN_LINK.sub(r"\1 (\2)", text)
    return strip_markdown(text)


# ---------------------------------------------------------------------------
# Webhook signature + payload parsing
# ---------------------------------------------------------------------------
# Spec: https://developers.line.biz/en/reference/messaging-api/#signature-validation


def _scrub_token(text: str) -> str:
    """Best-effort scrubbing of Bearer tokens before they hit logs / SendResult."""
    return re.sub(r"Bearer\s+\S+", "Bearer <redacted>", text)


def verify_signature(body: bytes, signature: str, channel_secret: str) -> bool:
    """Constant-time compare LINE's X-Line-Signature header against an HMAC-SHA256
    of the raw body using the channel secret.

    Returns False (rejecting the webhook) when ``channel_secret`` is empty —
    operators running in outbound-only mode get 401 on every inbound webhook,
    cleanly disabling incoming traffic without breaking sends.
    """
    if not signature or not channel_secret:
        return False
    expected = base64.b64encode(
        hmac.new(channel_secret.encode("utf-8"), body, hashlib.sha256).digest()
    ).decode()
    return hmac.compare_digest(expected, signature)


def parse_events(body: bytes) -> List[Dict[str, Any]]:
    """Parse a LINE webhook body into the raw events list. Returns [] on no events."""
    try:
        payload = json.loads(body)
    except (json.JSONDecodeError, TypeError):
        return []
    if not isinstance(payload, dict):
        return []
    return payload.get("events", []) or []


# ---------------------------------------------------------------------------
# Source allowlist (three-allowlist model: users, groups, rooms)
# ---------------------------------------------------------------------------


def is_allowed(event: Dict[str, Any], cfg: Dict[str, List[str]]) -> bool:
    """Return True if the event's source is in the appropriate allowlist.

    cfg expected shape:
        {"users": ["U..."], "groups": ["C..."], "rooms": ["R..."]}

    The debug-only ``allow_all_users`` escape hatch is resolved by the
    adapter (``self._allow_all_sources``) and short-circuited at the
    call site — kept out of this helper so it stays pure.
    """
    source = event.get("source") or {}
    if not isinstance(source, dict):
        return False
    src_type = source.get("type")
    if src_type == "user":
        return source.get("userId") in cfg.get("users", [])
    if src_type == "group":
        return source.get("groupId") in cfg.get("groups", [])
    if src_type == "room":
        return source.get("roomId") in cfg.get("rooms", [])
    return False


# ---------------------------------------------------------------------------
# Cache state machine — used for slow-LLM Quick Reply postback retrieval
# ---------------------------------------------------------------------------
# When the LLM is slow (> slow_response_threshold), the adapter sends a
# Quick Reply postback button using the original reply_token (free), then
# stashes the response here for the user to fetch by tapping the button.
# The postback callback gets a fresh reply_token and uses it to deliver
# the cached payload.
#
# State machine:
#     PENDING   → button sent, LLM still running
#     READY     → LLM done, response cached, waiting for postback tap
#     DELIVERED → response sent via postback's fresh reply_token
#     ERROR     → LLM raised; cached error text waiting to be shown
#
# Two TTLs:
#     - ttl_seconds (default 1h) for READY/DELIVERED/ERROR (by updated_at)
#     - pending_ttl_seconds (default 24h) for PENDING (ceiling, by created_at)


class State(enum.Enum):
    PENDING = "pending"
    READY = "ready"
    DELIVERED = "delivered"
    ERROR = "error"


@dataclass
class CacheEntry:
    state: State
    payload: Any = None
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)


class RequestCache:
    """In-memory ``Dict[request_id, CacheEntry]`` with TTL pruning.

    Not thread-safe — relies on cooperative single-event-loop scheduling.
    """

    def __init__(
        self,
        ttl_seconds: int = 3600,
        pending_ttl_seconds: int = 86400,
    ) -> None:
        self._entries: Dict[str, CacheEntry] = {}
        self._ttl = ttl_seconds
        self._pending_ttl = pending_ttl_seconds

    def register_pending(self) -> str:
        rid = str(uuid.uuid4())
        self._entries[rid] = CacheEntry(state=State.PENDING)
        return rid

    def get(self, request_id: str) -> Optional[CacheEntry]:
        return self._entries.get(request_id)

    def set_ready(self, request_id: str, payload: Any) -> None:
        entry = self._entries.get(request_id)
        if entry is None or entry.state is not State.PENDING:
            return
        entry.state = State.READY
        entry.payload = payload
        entry.updated_at = time.time()

    def set_error(self, request_id: str, message: str) -> None:
        entry = self._entries.get(request_id)
        if entry is None or entry.state is not State.PENDING:
            return
        entry.state = State.ERROR
        entry.payload = message
        entry.updated_at = time.time()

    def mark_delivered(self, request_id: str) -> None:
        entry = self._entries.get(request_id)
        if entry is None or entry.state not in (State.READY, State.ERROR):
            return
        entry.state = State.DELIVERED
        entry.updated_at = time.time()

    def prune(self) -> int:
        now = time.time()
        removed = 0
        for rid in list(self._entries.keys()):
            entry = self._entries[rid]
            if entry.state is State.PENDING:
                if now - entry.created_at > self._pending_ttl:
                    del self._entries[rid]
                    removed += 1
            else:
                if now - entry.updated_at > self._ttl:
                    del self._entries[rid]
                    removed += 1
        return removed


# ---------------------------------------------------------------------------
# LINE Reply / Push API client
# ---------------------------------------------------------------------------


class LineReplyClient:
    """Thin wrapper around LINE Messaging API HTTP endpoints."""

    def __init__(self, channel_access_token: str, timeout: float = 10.0) -> None:
        self._token = channel_access_token
        self._headers = {
            "Authorization": f"Bearer {channel_access_token}",
            "Content-Type": "application/json",
        }
        self._timeout = timeout

    async def reply(self, reply_token: str, messages: List[Dict[str, Any]]) -> None:
        """Reply API — single-use, ~60s window. Raises on non-2xx."""
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            r = await client.post(
                LINE_REPLY_URL,
                headers=self._headers,
                json={"replyToken": reply_token, "messages": messages},
            )
            r.raise_for_status()

    async def push(self, chat_id: str, messages: List[Dict[str, Any]]) -> None:
        """Push API — metered, no token. Raises on non-2xx."""
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            r = await client.post(
                LINE_PUSH_URL,
                headers=self._headers,
                json={"to": chat_id, "messages": messages},
            )
            r.raise_for_status()

    async def show_loading(self, chat_id: str, seconds: int = 60) -> None:
        """Loading indicator — DM only. Auto-stops when the bot replies.
        seconds is the fallback fade timer (LINE max 60). Best-effort: errors
        are swallowed since the indicator is purely visual feedback."""
        if not chat_id or not chat_id.startswith("U"):
            return  # LINE only supports loading indicator for 1-on-1 user chats
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                await client.post(
                    LINE_LOADING_URL,
                    headers=self._headers,
                    json={"chatId": chat_id, "loadingSeconds": seconds},
                )
        except Exception as exc:
            logger.debug("LINE loading indicator failed (chat=%s): %s", chat_id, exc)


def build_postback_button_message(
    text: str, button_label: str, request_id: str
) -> Dict[str, Any]:
    """Build a LINE Template Buttons message with a single postback action.

    Uses Template Buttons rather than Quick Reply because Quick Reply chips
    are TRANSIENT — LINE clients dismiss them when the user sends another
    message or when any subsequent bot message arrives. Template Button
    bubbles are PERSISTENT and stay tappable from chat history indefinitely,
    which is essential for the slow-LLM postback-fetch UX (the user must be
    able to tap the button at any time before TTL expiry, even if other
    messages have come and gone in between).

    Limits: Template Buttons text capped at 160 chars (LINE constraint).

    See ``tests/gateway/test_line_reply.py`` for the message-shape contract
    and ``tests/gateway/test_line_send_routing.py`` for the system-message
    cache-bypass contract.
    """
    truncated_text = text if len(text) <= 160 else text[:157] + "..."
    # altText capped at 400 chars by LINE — exceeding rejects the whole
    # message with a 400 from the API.
    alt_text = text if len(text) <= 400 else text[:397] + "..."
    return {
        "type": "template",
        "altText": alt_text,
        "template": {
            "type": "buttons",
            "text": truncated_text,
            "actions": [
                {
                    "type": "postback",
                    "label": button_label,
                    "data": json.dumps(
                        {"action": "show_response", "request_id": request_id}
                    ),
                    "displayText": button_label,
                }
            ],
        },
    }




# ---------------------------------------------------------------------------
# LineAdapter
# ---------------------------------------------------------------------------


def _csv_env(name: str) -> List[str]:
    """Parse a comma-separated env var into a stripped, non-empty list."""
    raw = os.environ.get(name, "").strip()
    return [item.strip() for item in raw.split(",") if item.strip()]


def _csv_or_extra(extra: Dict[str, Any], extra_key: str, env_key: str) -> List[str]:
    """Resolve a CSV-or-list setting from PlatformConfig.extra first, then
    a comma-separated env var. Returns a stripped, non-empty list."""
    raw = extra.get(extra_key)
    if isinstance(raw, list):
        return [str(x).strip() for x in raw if str(x).strip()]
    if isinstance(raw, str) and raw.strip():
        return [item.strip() for item in raw.split(",") if item.strip()]
    return _csv_env(env_key)


def _bool_env(name: str) -> bool:
    return os.environ.get(name, "").lower() in ("true", "1", "yes")


def _coerce_bool(value: Any, default: bool) -> bool:
    """Coerce a YAML/extra value to bool with truthy-string semantics.
    Avoids ``bool("false") == True`` which is the naive trap."""
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ("true", "1", "yes")
    return bool(value)


class LineAdapter(BasePlatformAdapter):
    """LINE Messaging API adapter, fully integrated with BasePlatformAdapter.

    Inherits queue/interrupt/bypass/photo-burst behavior from base via
    ``handle_message()``. Overrides ``send()``, ``_keep_typing()``, and
    adds ``_handle_postback`` to handle LINE's reply-token semantics and
    the slow-LLM Quick Reply fallback.
    """

    MAX_MESSAGE_LENGTH = 5000  # LINE per-message hard limit
    # LINE Messaging API has no edit/update endpoint — every send is final.
    # Setting False disables token streaming (run.py:10495) so the user
    # doesn't receive a partial-then-final duplicate. Matches the
    # bluebubbles / weixin / qqbot convention for non-editable platforms.
    SUPPORTS_MESSAGE_EDITING = False
    # LINE's documented webhook body cap is ~1 MB; reject larger payloads
    # before reading them into memory (defends against hostile traffic
    # to the public webhook URL).
    _MAX_WEBHOOK_BYTES = 1 * 1024 * 1024

    def __init__(self, config: PlatformConfig):
        super().__init__(config, Platform.LINE)
        # Settings honor PlatformConfig.extra (YAML) first, then LINE_* env
        # var, then default — matches bluebubbles/signal/mattermost peer
        # convention. Channel secret is optional: outbound-only mode rejects
        # every inbound webhook (401) when empty, cleanly disabling incoming
        # traffic without breaking sends.
        extra = config.extra or {}
        self._channel_access_token: str = config.token or ""
        self._channel_secret: str = (
            extra.get("channel_secret") or os.getenv("LINE_CHANNEL_SECRET", "")
        )
        self._allowed_users: List[str] = _csv_or_extra(extra, "allowed_users", "LINE_ALLOWED_USERS")
        self._allowed_groups: List[str] = _csv_or_extra(extra, "allowed_groups", "LINE_ALLOWED_GROUPS")
        self._allowed_rooms: List[str] = _csv_or_extra(extra, "allowed_rooms", "LINE_ALLOWED_ROOMS")
        self._free_response_groups: List[str] = _csv_or_extra(
            extra, "free_response_groups", "LINE_FREE_RESPONSE_GROUPS"
        )
        self._free_response_rooms: List[str] = _csv_or_extra(
            extra, "free_response_rooms", "LINE_FREE_RESPONSE_ROOMS"
        )
        # LINE's reply token is documented as valid for ~60 seconds. 45s
        # default leaves a 15s safety margin for the Quick Reply button
        # to land before the token expires.
        self._slow_response_threshold: float = float(
            extra.get("slow_response_threshold")
            or os.getenv("LINE_SLOW_RESPONSE_THRESHOLD", "45")
        )
        self._cache_ttl: int = int(
            extra.get("cache_ttl") or os.getenv("LINE_CACHE_TTL", "3600")
        )
        self._require_mention: bool = _coerce_bool(
            extra.get("require_mention"), _bool_env("LINE_REQUIRE_MENTION")
        )
        # Resolved at connect() time via GET /v2/bot/info; extra/env override
        # for tests/offline use.
        self._bot_display_name: str = str(
            extra.get("bot_display_name") or os.getenv("LINE_BOT_DISPLAY_NAME", "") or ""
        ).strip()
        self._pending_reply_text: str = (
            extra.get("pending_text")
            or os.getenv("LINE_PENDING_TEXT")
            or DEFAULT_PENDING_REPLY_TEXT
        )
        self._expired_reply_text: str = (
            extra.get("expired_text")
            or os.getenv("LINE_EXPIRED_TEXT")
            or DEFAULT_EXPIRED_REPLY_TEXT
        )
        self._already_delivered_text: str = (
            extra.get("delivered_text")
            or os.getenv("LINE_DELIVERED_TEXT")
            or DEFAULT_ALREADY_DELIVERED_TEXT
        )
        self._show_response_button_label: str = (
            extra.get("button_label")
            or os.getenv("LINE_BUTTON_LABEL")
            or DEFAULT_SHOW_RESPONSE_BUTTON_LABEL
        )
        self._interrupted_text: str = (
            extra.get("interrupted_text")
            or os.getenv("LINE_INTERRUPTED_TEXT")
            or DEFAULT_INTERRUPTED_TEXT
        )
        # Debug-only escape hatch — bypass all three allowlists.
        # Mirrors DISCORD_ALLOW_ALL_USERS; honors extra-then-env-then-default.
        self._allow_all_sources: bool = _coerce_bool(
            extra.get("allow_all_users"),
            os.environ.get("LINE_ALLOW_ALL_USERS", "").lower() in ("true", "1", "yes"),
        )

        self._reply = LineReplyClient(channel_access_token=self._channel_access_token)
        self._cache = RequestCache(ttl_seconds=self._cache_ttl)
        self._dedup = MessageDeduplicator()
        # chat_id → (reply_token, expires_at). Reply tokens are single-use
        # and ~60s lived. We cache the most recent one per chat so send()
        # can use it for free Reply API delivery; expired/consumed tokens
        # fall back to Push API. Pruned opportunistically in _handle_http.
        self._reply_tokens: Dict[str, Tuple[str, float]] = {}
        # chat_id → request_id. Set by _keep_typing when it sends a slow-LLM
        # Quick Reply postback button. send() consults this to avoid Push API:
        # if a button is pending, the response is cached for postback retrieval
        # instead of pushed.
        self._pending_buttons: Dict[str, str] = {}
        # Annotated as Any because aiohttp is an optional [messaging] extra —
        # the module imports cleanly when it's absent (web=None, AIOHTTP_AVAILABLE=False)
        # and the factory in gateway/run.py guards instantiation. Matches the
        # peer pattern for adapters with conditional aiohttp imports.
        self._runner: Optional[Any] = None

    # -----------------------------------------------------------------------
    # connect / disconnect — webhook server lifecycle + platform lock
    # -----------------------------------------------------------------------

    async def connect(self) -> bool:
        """Acquire platform lock, fetch bot info, start webhook listener."""
        if not self._acquire_platform_lock(
            "line-channel-token",
            self._channel_access_token,
            "LINE channel access token",
        ):
            return False
        # Lock acquired — release on any failure between here and end of connect.
        try:
            # Resolve bot info BEFORE accepting webhooks so the mention gate
            # never has a cold-start window with bot_display_name unresolved.
            await self._fetch_bot_info()
            # Operator footgun: free_response_* IDs not in the allowlist are
            # silently dropped before the free-response check fires.
            unreachable_groups = set(self._free_response_groups) - set(self._allowed_groups)
            unreachable_rooms = set(self._free_response_rooms) - set(self._allowed_rooms)
            if unreachable_groups:
                logger.warning(
                    "[%s] free_response_groups contains IDs not in LINE_ALLOWED_GROUPS — "
                    "messages will be silently dropped at the allowlist: %s",
                    self.name, sorted(unreachable_groups),
                )
            if unreachable_rooms:
                logger.warning(
                    "[%s] free_response_rooms contains IDs not in LINE_ALLOWED_ROOMS — "
                    "messages will be silently dropped at the allowlist: %s",
                    self.name, sorted(unreachable_rooms),
                )
            app = web.Application()
            self.register_routes(app)
            extra = self.config.extra or {}
            port = int(
                extra.get("webhook_port")
                or os.getenv("LINE_WEBHOOK_PORT", "8646")
            )
            runner = web.AppRunner(app)
            await runner.setup()
            try:
                site = web.TCPSite(runner, "0.0.0.0", port)
                await site.start()
            except Exception:
                await runner.cleanup()
                raise
            self._runner = runner
            logger.info("[%s] webhook listening on :%d/line/webhook", self.name, port)
        except Exception:
            self._release_platform_lock()
            raise
        self._mark_connected()
        return True

    async def disconnect(self) -> None:
        await self.cancel_background_tasks()
        if self._runner is not None:
            try:
                await self._runner.cleanup()
            finally:
                self._runner = None
        self._release_platform_lock()
        self._mark_disconnected()

    def register_routes(self, app: web.Application) -> None:
        """Register webhook routes on the provided aiohttp.web.Application."""
        app.router.add_post("/line/webhook", self._handle_http)
        app.router.add_get("/line/webhook/health", self._handle_health)

    async def _fetch_bot_info(self) -> None:
        """Resolve bot display name from /v2/bot/info. Best-effort — failure
        leaves bot_display_name empty and the mention gate falls back to
        fail-closed behavior (drops all group/room messages with a warning).
        Skipped when require_mention=False or operator already set the name."""
        if not self._require_mention or self._bot_display_name:
            return
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                r = await client.get(
                    "https://api.line.me/v2/bot/info",
                    headers={"Authorization": f"Bearer {self._channel_access_token}"},
                )
                r.raise_for_status()
                name = (r.json() or {}).get("displayName", "").strip()
                if not name:
                    logger.warning(
                        "[%s] /v2/bot/info returned empty displayName — set "
                        "LINE_BOT_DISPLAY_NAME manually for group mention gating to work.",
                        self.name,
                    )
                    return
                self._bot_display_name = name
                logger.info("[%s] bot display name resolved: %r", self.name, name)
        except Exception:
            logger.warning(
                "[%s] Failed to fetch LINE bot info — set LINE_BOT_DISPLAY_NAME manually "
                "if you want group mention gating to work.",
                self.name,
                exc_info=True,
            )

    # -----------------------------------------------------------------------
    # HTTP handlers
    # -----------------------------------------------------------------------

    async def _handle_http(self, request: web.Request) -> web.Response:
        """LINE webhook entry. Verifies signature, deduplicates, dispatches
        each event to the right handler. Returns 200 immediately to satisfy
        LINE's retry semantics."""
        if request.content_length and request.content_length > self._MAX_WEBHOOK_BYTES:
            return web.json_response({"error": "payload too large"}, status=413)
        body = await request.read()
        signature = request.headers.get("X-Line-Signature", "")
        if not verify_signature(body, signature, self._channel_secret):
            return web.json_response({"error": "invalid signature"}, status=401)

        events = parse_events(body)
        # Opportunistic pruning (cheap; once per webhook). Trims expired
        # cache entries AND expired/consumed reply_tokens so neither dict
        # grows unbounded over the lifetime of a chatty deployment.
        self._cache.prune()
        now = time.time()
        self._reply_tokens = {
            k: v for k, v in self._reply_tokens.items() if v[1] > now
        }

        for event in events:
            event_id = event.get("webhookEventId")
            if event_id and self._dedup.is_duplicate(event_id):
                continue
            try:
                await self._dispatch_event(event)
            except Exception:
                logger.exception("[%s] dispatch failure for event=%r", self.name, event_id)

        return web.Response(status=200)

    async def _handle_health(self, _request: web.Request) -> web.Response:
        return web.json_response({"status": "ok", "platform": "line"})

    # -----------------------------------------------------------------------
    # Event dispatch — message vs postback
    # -----------------------------------------------------------------------

    async def _dispatch_event(self, event: Dict[str, Any]) -> None:
        evt_type = event.get("type")
        if evt_type == "postback":
            await self._handle_postback(event)
            return
        if evt_type != "message":
            logger.debug("[%s] ignoring non-message non-postback event type=%r", self.name, evt_type)
            return

        msg = event.get("message", {})
        if msg.get("type") != "text":
            logger.debug("[%s] ignoring non-text message type=%s", self.name, msg.get("type"))
            return

        cfg = {
            "users": self._allowed_users,
            "groups": self._allowed_groups,
            "rooms": self._allowed_rooms,
        }
        if not self._allow_all_sources and not is_allowed(event, cfg):
            src = event.get("source", {}) or {}
            logger.info(
                "[%s] drop unauthorised src_type=%s user=%s group=%s room=%s",
                self.name,
                src.get("type"),
                src.get("userId"),
                src.get("groupId"),
                src.get("roomId"),
            )
            return

        # Mention gate (group/room only).
        text = msg.get("text", "")
        source = event.get("source", {}) or {}
        src_type = source.get("type")
        free_response = (
            (src_type == "group"
             and source.get("groupId") in self._free_response_groups)
            or (src_type == "room"
                and source.get("roomId") in self._free_response_rooms)
        )
        if src_type in ("group", "room") and self._require_mention and not free_response:
            if not self._bot_display_name:
                # Fail-closed: gate is configured but bot name unresolved.
                logger.warning(
                    "[%s] require_mention=True but bot_display_name is empty — "
                    "dropping group/room message",
                    self.name,
                )
                return
            trigger = f"@{self._bot_display_name}"
            if trigger not in text:
                logger.info(
                    "[%s] group message not addressed to bot — silent drop (trigger=%r)",
                    self.name,
                    trigger,
                )
                return
            # Strip the mention prefix and surrounding whitespace before dispatch.
            text = re.sub(re.escape(trigger), "", text, count=1).strip()
            if not text:
                logger.info("[%s] mention-only message — silent drop", self.name)
                return

        # Register the reply_token for this chat. send() will consume it for
        # free Reply API delivery if still valid; expired tokens fall back to
        # Push API. ~55s expiry leaves a safety margin under LINE's 60s window.
        chat_id = (
            source.get("groupId")
            or source.get("roomId")
            or source.get("userId")
            or ""
        )
        reply_token = event.get("replyToken")
        if chat_id and reply_token:
            self._reply_tokens[chat_id] = (reply_token, time.time() + 55)

        # Build MessageEvent and hand off to base.handle_message — this is
        # where queue / interrupt / bypass / photo-burst behavior lives.
        msg_event = self._build_message_event(event, text)
        coerce_plaintext_gateway_command(msg_event)
        # Base spawns a task; we don't await full agent completion here so
        # the webhook response goes back to LINE within the retry budget.
        await self.handle_message(msg_event)

    def _build_message_event(self, event: Dict[str, Any], text: str) -> MessageEvent:
        source = event.get("source", {}) or {}
        src_type = source.get("type")
        chat_type = {"user": "dm", "group": "group", "room": "room"}.get(src_type, "unknown")
        chat_id = (
            source.get("groupId")
            or source.get("roomId")
            or source.get("userId")
            or "unknown"
        )
        user_id = source.get("userId")
        session_source = SessionSource(
            platform=Platform.LINE,
            chat_id=str(chat_id),
            chat_type=chat_type,
            user_id=str(user_id) if user_id else None,
        )
        return MessageEvent(
            text=text,
            message_type=MessageType.TEXT,
            source=session_source,
            message_id=(event.get("message") or {}).get("id"),
            raw_message={"line_event": event},
        )

    # -----------------------------------------------------------------------
    # Postback handler
    # -----------------------------------------------------------------------

    async def _handle_postback(self, event: Dict[str, Any]) -> None:
        """User tapped the slow-LLM Quick Reply button. Look up the cached
        response and deliver via the postback's fresh reply_token."""
        reply_token = event.get("replyToken")
        if not reply_token:
            logger.info("[%s] postback without replyToken: source=%s", self.name, event.get("source"))
            return
        try:
            payload = json.loads((event.get("postback") or {}).get("data", "{}"))
            if not isinstance(payload, dict):
                payload = {}
        except json.JSONDecodeError:
            payload = {}
        if payload.get("action") != "show_response":
            # Not the slow-LLM Quick Reply button — could be a future
            # postback feature (different action). Log so operators can
            # observe orphan postbacks without surfacing as errors.
            logger.debug(
                "[%s] postback with unrecognised action=%r — ignoring",
                self.name,
                payload.get("action"),
            )
            return

        request_id = payload.get("request_id")
        # Defensive: postback ``data`` is opaque attacker-controlled JSON
        # (signature-verified, but operators-of-the-channel could craft
        # nonsense). Accept only string request_ids — RequestCache.get()
        # expects str.
        entry = self._cache.get(request_id) if isinstance(request_id, str) else None

        if entry is None:
            logger.info(
                "[%s] postback request_id=%s not found (expired or never cached)",
                self.name, request_id,
            )
            await self._reply.reply(
                reply_token, [{"type": "text", "text": self._expired_reply_text}]
            )
            return

        if entry.state is State.PENDING:
            # LLM still running — re-arm the same button on the new reply_token.
            logger.debug(
                "[%s] postback re-arm: request_id=%s still PENDING",
                self.name, request_id,
            )
            msg = build_postback_button_message(
                text=self._pending_reply_text,
                button_label=self._show_response_button_label,
                request_id=request_id,
            )
            await self._reply.reply(reply_token, [msg])
            return

        if entry.state is State.READY:
            await self._reply.reply(
                reply_token, self._build_reply_messages(entry.payload)
            )
            self._cache.mark_delivered(request_id)
            logger.info(
                "[%s] postback delivered cached response: request_id=%s payload=%d chars",
                self.name, request_id, len(entry.payload or ""),
            )
            return

        if entry.state is State.DELIVERED:
            logger.info(
                "[%s] postback duplicate tap: request_id=%s already DELIVERED",
                self.name, request_id,
            )
            await self._reply.reply(
                reply_token, [{"type": "text", "text": self._already_delivered_text}]
            )
            return

        if entry.state is State.ERROR:
            logger.info(
                "[%s] postback delivered cached ERROR: request_id=%s",
                self.name, request_id,
            )
            await self._reply.reply(reply_token, self._build_reply_messages(entry.payload))
            self._cache.mark_delivered(request_id)
            return

        # Defensive: state added in the future but not handled here. Log
        # rather than silently fall through.
        logger.warning(
            "[%s] postback hit unknown cache state=%r request_id=%s",
            self.name, entry.state, request_id,
        )

    # -----------------------------------------------------------------------
    # Override: send() — smart Reply API vs Push API routing
    # -----------------------------------------------------------------------

    async def send(
        self,
        chat_id: str,
        content: str,
        reply_to: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> SendResult:
        """Deliver a response to LINE.

        Routing:
          1. If a slow-LLM Quick Reply button is pending for this chat, cache
             the response under the button's request_id and skip Push (the
             user will tap the button to fetch via fresh reply_token).
          2. Else if a fresh reply_token is cached, use Reply API (free).
          3. Else fall back to Push API (metered).

        Multi-send within a single turn (rare — base usually calls send()
        once per agent response): the first call consumes the
        ``_pending_buttons`` slot and caches the payload; subsequent calls
        for the same chat fall through to step 2/3 and deliver immediately.
        Operators see this in the log via the "second send for chat" line."""
        # 1. Pending Quick Reply button — cache for postback retrieval.
        # If multiple send() calls land for the same chat within one turn
        # (rare; base.handle_message normally calls send() once), only the
        # FIRST one populates the cache slot — subsequent sends fall
        # through to Reply/Push routing below and deliver immediately.
        # That's the intended ordering: streaming intermediate messages go
        # via the still-valid token (or Push) while the final answer waits
        # for the user's postback tap.
        # Skip cache for known system messages (interrupt-ack, queue-ack, etc.)
        # so they reach the user as visible bubbles instead of being swallowed
        # into the orphan postback slot. Brittle but tolerable: the prefixes
        # are centrally defined in run.py and rarely changed; a regression
        # test pins the contract.
        is_system_message = isinstance(content, str) and content.startswith(
            _SYSTEM_MESSAGE_PREFIXES
        )
        if not is_system_message:
            request_id = self._pending_buttons.pop(chat_id, None)
            if request_id is not None:
                self._cache.set_ready(request_id, content)
                logger.info(
                    "[%s] cached response for pending postback (chat=%s request_id=%s)",
                    self.name, chat_id, request_id,
                )
                return SendResult(success=True, message_id=request_id)

        # 2. Reply API if we still have a valid reply_token.
        token_entry = self._reply_tokens.get(chat_id)
        if token_entry is not None:
            token, expires_at = token_entry
            if time.time() < expires_at:
                try:
                    messages = self._build_reply_messages(content)
                    await self._reply.reply(token, messages)
                    # Single-use — drop after success.
                    self._reply_tokens.pop(chat_id, None)
                    return SendResult(success=True)
                except Exception as exc:
                    logger.warning(
                        "[%s] Reply API failed (chat=%s) — falling back to Push: %s",
                        self.name, chat_id, _scrub_token(str(exc)),
                    )
                    self._reply_tokens.pop(chat_id, None)

        # 3. Push API fallback (cron deliveries, expired tokens, etc.)
        return await self._push(chat_id, content)

    async def _push(self, chat_id: str, content: str) -> SendResult:
        """Push API delivery — metered. Always extracts media URLs to native
        image bubbles via _build_reply_messages."""
        if not self._channel_access_token:
            return SendResult(success=False, error="LINE: channel access token not set")
        try:
            messages = self._build_reply_messages(content)
            await self._reply.push(chat_id, messages)
            return SendResult(success=True)
        except Exception as exc:
            scrubbed = _scrub_token(str(exc))
            logger.warning("[%s] push failed chat_id=%s: %s", self.name, chat_id, scrubbed)
            return SendResult(success=False, error=scrubbed)

    async def send_image(
        self,
        chat_id: str,
        image_url: str,
        caption: Optional[str] = None,
        reply_to: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> SendResult:
        """Send an image. LINE requires HTTPS image URLs and rejects local
        file paths — non-HTTPS URLs degrade to text-with-link delivery.

        Same routing as ``send()``: prefer free Reply API when a fresh
        reply_token is cached, fall back to Push API otherwise."""
        if not image_url.startswith("https://"):
            text = f"{caption}\n{image_url}" if caption else image_url
            return await self.send(chat_id, text)
        if not self._channel_access_token:
            return SendResult(success=False, error="LINE: channel access token not set")
        messages: List[Dict[str, Any]] = [
            {
                "type": "image",
                "originalContentUrl": image_url,
                "previewImageUrl": image_url,
            }
        ]
        if caption:
            messages.extend(self._chunk_text(caption)[:4])  # 1 image + max 4 text = 5

        # Route through reply_token first (free) when available, else Push.
        token_entry = self._reply_tokens.get(chat_id)
        if token_entry is not None:
            token, expires_at = token_entry
            if time.time() < expires_at:
                try:
                    await self._reply.reply(token, messages)
                    self._reply_tokens.pop(chat_id, None)
                    return SendResult(success=True)
                except Exception as exc:
                    logger.warning(
                        "[%s] image Reply API failed (chat=%s) — falling back to Push: %s",
                        self.name, chat_id, _scrub_token(str(exc)),
                    )
                    self._reply_tokens.pop(chat_id, None)
        try:
            await self._reply.push(chat_id, messages)
            return SendResult(success=True)
        except Exception as exc:
            scrubbed = _scrub_token(str(exc))
            return SendResult(success=False, error=scrubbed)

    async def send_image_file(
        self,
        chat_id: str,
        image_path: str,
        caption: Optional[str] = None,
        reply_to: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> SendResult:
        """LINE has no native local file upload — fall back to a clear text
        notice so the operator knows to host the file at an HTTPS URL."""
        logger.warning(
            "[%s] send_image_file unsupported (LINE requires HTTPS URL): chat=%s path=%s",
            self.name, chat_id, image_path,
        )
        return SendResult(
            success=False,
            error=(
                "LINE Messaging API does not accept local file uploads — "
                "host the image at an HTTPS URL and use send_image() instead. "
                f"Skipped: {image_path}"
            ),
        )

    # -----------------------------------------------------------------------
    # Override: _keep_typing — show loading indicator + slow-LLM Quick Reply
    # -----------------------------------------------------------------------

    async def _keep_typing(
        self,
        chat_id: str,
        interval: float = 2.0,
        metadata: Optional[Dict[str, Any]] = None,
        stop_event: Optional[asyncio.Event] = None,
    ) -> None:
        """LINE-specific typing indicator + slow-LLM Quick Reply orchestration.

        Behavior:
          1. Show LINE loading indicator (DM only — LINE limit). LINE
             auto-stops the indicator when the bot replies, so we max it
             out at 60s (LINE's documented cap).
          2. Wait up to ``slow_response_threshold_seconds`` for the agent
             to finish (signaled by ``stop_event``).
          3. If agent finishes within threshold: return — send() will
             deliver via the still-valid reply_token (free).
          4. If threshold elapses with agent still running: send Quick
             Reply postback button via the original reply_token (free) and
             register the chat in ``_pending_buttons`` so send() caches the
             eventual response for postback retrieval instead of Push.
        """
        registered_request_id: Optional[str] = None
        try:
            # 1. Show loading indicator (best-effort, DM only). LINE's loading
            # indicator is one-shot by API design — it auto-stops when the bot
            # replies, so unlike Telegram/Discord we don't need a periodic
            # refresh loop. The 60s ceiling is LINE's documented maximum.
            await self._reply.show_loading(chat_id, seconds=60)

            if stop_event is None:
                return  # No stop signal → no slow-LLM watcher possible.

            threshold = self._slow_response_threshold

            # 2. Wait for agent done OR threshold elapse.
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=threshold)
                return  # Agent done within threshold — fast path.
            except asyncio.TimeoutError:
                pass
            except asyncio.CancelledError:
                # Agent run was cancelled (interrupt or shutdown) — exit
                # quietly without spamming a "button delivery failed" warning.
                raise

            # 3. Threshold elapsed — try to send postback button.
            token_entry = self._reply_tokens.get(chat_id)
            if token_entry is None:
                # No reply_token cached — agent must have been triggered without
                # an inbound webhook (e.g. cron). Nothing to do; send() will Push.
                return
            token, expires_at = token_entry
            if time.time() >= expires_at:
                return  # Already expired — send() will Push when agent finishes.

            request_id = self._cache.register_pending()
            msg = build_postback_button_message(
                text=self._pending_reply_text,
                button_label=self._show_response_button_label,
                request_id=request_id,
            )
            try:
                await self._reply.reply(token, [msg])
                # Token consumed by the button — mark it gone so send() doesn't
                # try to reuse it. Register the pending button so send() routes
                # the eventual response into the cache instead of Push.
                self._reply_tokens.pop(chat_id, None)
                self._pending_buttons[chat_id] = request_id
                registered_request_id = request_id
                logger.info(
                    "[%s] slow-LLM postback button sent for chat=%s after %.1fs (request_id=%s)",
                    self.name, chat_id, threshold, request_id,
                )
            except asyncio.CancelledError:
                raise

            # 4. Stay alive until base cancels us (agent finished/interrupted).
            # Without this await the task exits immediately and the finally
            # block's orphan-cleanup would race the agent's send() — every
            # successful turn would mis-mark the cache ERROR before the
            # response could land. The wait_for-with-timeout pattern lets
            # ``stop_event`` short-circuit on interrupt without busy-looping.
            try:
                await stop_event.wait()
            except asyncio.CancelledError:
                raise
        except Exception as exc:
            logger.warning(
                "[%s] failed to send slow-LLM postback button for chat=%s: %s",
                self.name, chat_id, _scrub_token(str(exc)),
            )
        finally:
            # Mirror BasePlatformAdapter._keep_typing's cleanup contract.
            # LINE has no continuous send_typing loop, but pause-state set
            # membership must still be cleared on exit so future invocations
            # don't see a stale pause.
            self._typing_paused.discard(chat_id)
            # Orphan-cleanup: if we registered a postback slot but the agent
            # was cancelled (interrupt/shutdown) before send() consumed it,
            # the cache entry is stuck in PENDING. Resolve to ERROR so the
            # persistent button — when tapped — shows "interrupted" instead
            # of looping in "still thinking → re-arm → still thinking".
            if (
                registered_request_id is not None
                and self._pending_buttons.get(chat_id) == registered_request_id
            ):
                entry = self._cache.get(registered_request_id)
                if entry is not None and entry.state is State.PENDING:
                    self._cache.set_error(registered_request_id, self._interrupted_text)
                    logger.info(
                        "[%s] orphan postback marked ERROR (chat=%s request_id=%s) — agent run was cancelled",
                        self.name, chat_id, registered_request_id,
                    )
                self._pending_buttons.pop(chat_id, None)

    # -----------------------------------------------------------------------
    # Helpers — text chunking + media extraction
    # -----------------------------------------------------------------------

    @classmethod
    def _build_reply_messages(cls, text: str) -> List[Dict[str, Any]]:
        """Build a LINE messages array, extracting Markdown/HTML image URLs
        into native image bubbles before chunking the remaining text. LINE
        accepts up to 5 message objects per Reply/Push call. Markdown syntax
        in the remaining text is stripped (LINE renders text bubbles as
        plain — see ``format_message``)."""
        images, cleaned = BasePlatformAdapter.extract_images(text)
        cleaned = _strip_markdown_for_line(cleaned) if cleaned else cleaned
        messages: List[Dict[str, Any]] = []
        max_image_msgs = 4 if cleaned.strip() else 5
        for url, _alt in images[:max_image_msgs]:
            if url.startswith("https://"):
                messages.append({
                    "type": "image",
                    "originalContentUrl": url,
                    "previewImageUrl": url,
                })
        text_budget = 5 - len(messages)
        if cleaned.strip() and text_budget > 0:
            messages.extend(cls._chunk_text(cleaned)[:text_budget])
        if not messages:  # Defensive — never empty.
            messages = cls._chunk_text(_strip_markdown_for_line(text))[:5]
        return messages

    @staticmethod
    def _chunk_text(text: str) -> List[Dict[str, Any]]:
        """Chunk by Python string length into 5000-char segments; truncate
        beyond 5 chunks (25k chars) with a clear suffix on the final chunk."""
        if not text:
            return [{"type": "text", "text": ""}]
        max_len = LineAdapter.MAX_MESSAGE_LENGTH
        all_chunks = [text[i:i + max_len] for i in range(0, len(text), max_len)]
        truncated = len(all_chunks) > 5
        chunks = all_chunks[:5]
        if truncated:
            suffix = "\n… (truncated)"
            last = chunks[-1]
            if len(last) + len(suffix) > max_len:
                last = last[: max_len - len(suffix)]
            chunks[-1] = last + suffix
        return [{"type": "text", "text": chunk} for chunk in chunks]

    def format_message(self, content: str) -> str:
        """LINE renders text as plain — Markdown syntax leaks into the bubble.
        Strips Markdown but preserves link URLs via ``_strip_markdown_for_line``
        so citations stay tappable (LINE auto-linkifies bare URLs)."""
        return _strip_markdown_for_line(content)

    async def get_chat_info(self, chat_id: str) -> Dict[str, Any]:
        """Resolve chat metadata. Uses the LINE chat-id prefix to infer the
        chat type (U → dm, C/R → group). Returns the contract-defined keys
        ``{"name", "type", "chat_id"}`` shared across all platform adapters.

        LINE's "room" source is a multi-user chat without a stable group
        identity (typically a temporary multi-person conversation); we map
        it to ``"group"`` so downstream introspection treats it like any
        other multi-party chat. The original prefix is preserved in
        ``chat_id`` for callers that need to distinguish."""
        prefix = chat_id[:1] if chat_id else ""
        chat_type = {"U": "dm", "C": "group", "R": "group"}.get(prefix, "unknown")
        return {"name": chat_id, "type": chat_type, "chat_id": chat_id}

