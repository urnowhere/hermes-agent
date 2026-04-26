"""
DingTalk platform adapter using Stream Mode.

Uses dingtalk-stream SDK (>=0.20) for real-time message reception without webhooks.
Responses are sent via DingTalk's session webhook (markdown format).
Supports: text, images, audio, video, rich text, files, and group @mentions.

Requires:
    pip install "dingtalk-stream>=0.20" httpx
    DINGTALK_CLIENT_ID and DINGTALK_CLIENT_SECRET env vars

Configuration in config.yaml:
    platforms:
      dingtalk:
        enabled: true
        # Optional group-chat gating (mirrors Slack/Telegram/Discord):
        require_mention: true            # or DINGTALK_REQUIRE_MENTION env var
        # free_response_chats:           # conversations that skip require_mention
        #   - cidABC==
        # mention_patterns:              # regex wake-words (e.g. Chinese bot names)
        #   - "^小马"
        # allowed_users:                 # staff_id or sender_id list; "*" = any
        #   - "manager1234"
        extra:
          client_id: "your-app-key"      # or DINGTALK_CLIENT_ID env var
          client_secret: "your-secret"   # or DINGTALK_CLIENT_SECRET env var
"""

import asyncio
import json
import logging
import mimetypes
import os
import re
import struct
import tempfile
import traceback
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

try:
    import dingtalk_stream
    from dingtalk_stream import ChatbotMessage
    from dingtalk_stream.frames import CallbackMessage, AckMessage

    DINGTALK_STREAM_AVAILABLE = True
except ImportError:
    DINGTALK_STREAM_AVAILABLE = False
    dingtalk_stream = None  # type: ignore[assignment]
    ChatbotMessage = None  # type: ignore[assignment]
    CallbackMessage = None  # type: ignore[assignment]
    AckMessage = type(
        "AckMessage",
        (),
        {
            "STATUS_OK": 200,
            "STATUS_SYSTEM_EXCEPTION": 500,
        },
    )  # type: ignore[assignment]

try:
    import httpx

    HTTPX_AVAILABLE = True
except ImportError:
    HTTPX_AVAILABLE = False
    httpx = None  # type: ignore[assignment]

# Card SDK for AI Cards (following QwenPaw pattern)
try:
    from alibabacloud_dingtalk.card_1_0 import (
        client as dingtalk_card_client,
        models as dingtalk_card_models,
    )
    from alibabacloud_dingtalk.robot_1_0 import (
        client as dingtalk_robot_client,
        models as dingtalk_robot_models,
    )
    from alibabacloud_tea_openapi import models as open_api_models
    from alibabacloud_tea_util import models as tea_util_models

    CARD_SDK_AVAILABLE = True
except ImportError:
    CARD_SDK_AVAILABLE = False
    dingtalk_card_client = None
    dingtalk_card_models = None
    dingtalk_robot_client = None
    dingtalk_robot_models = None
    open_api_models = None
    tea_util_models = None

from gateway.config import Platform, PlatformConfig
from gateway.platforms.helpers import MessageDeduplicator
from gateway.platforms.base import (
    BasePlatformAdapter,
    MessageEvent,
    MessageType,
    SendResult,
)

logger = logging.getLogger(__name__)

MAX_MESSAGE_LENGTH = 20000
RECONNECT_BACKOFF = [2, 5, 10, 30, 60]
_SESSION_WEBHOOKS_MAX = 500
_DINGTALK_WEBHOOK_RE = re.compile(r'^https://(?:api|oapi)\.dingtalk\.com/')

# DingTalk message type → runtime content type
DINGTALK_TYPE_MAPPING = {
    "picture": "image",
    "voice": "audio",
}


def check_dingtalk_requirements() -> bool:
    """Check if DingTalk dependencies are available and configured."""
    if not DINGTALK_STREAM_AVAILABLE or not HTTPX_AVAILABLE:
        return False
    if not os.getenv("DINGTALK_CLIENT_ID") or not os.getenv("DINGTALK_CLIENT_SECRET"):
        return False
    return True


class DingTalkAdapter(BasePlatformAdapter):
    """DingTalk chatbot adapter using Stream Mode.

    The dingtalk-stream SDK maintains a long-lived WebSocket connection.
    Incoming messages arrive via a ChatbotHandler callback. Replies are
    sent via the incoming message's session_webhook URL using httpx.

    Features:
    - Text messages (plain + rich text)
    - Images, audio, video, files (via download codes)
    - Group chat @mention detection
    - Session webhook caching with expiry tracking
    - Markdown formatted replies
    """

    MAX_MESSAGE_LENGTH = MAX_MESSAGE_LENGTH

    @property
    def SUPPORTS_MESSAGE_EDITING(self) -> bool:  # noqa: N802
        """Edits only meaningful when AI Cards are configured.

        The gateway gates streaming cursor + edit behaviour on this flag,
        so we must reflect the actual adapter capability at runtime.
        """
        return bool(self._card_template_id and self._card_sdk)

    @property
    def REQUIRES_EDIT_FINALIZE(self) -> bool:  # noqa: N802
        """AI Card lifecycle requires an explicit ``finalize=True`` edit
        to close the streaming indicator, even when the final content is
        identical to the last streamed update.  Enabled only when cards
        are configured — webhook-only DingTalk doesn't need it.
        """
        return bool(self._card_template_id and self._card_sdk)

    def __init__(self, config: PlatformConfig):
        super().__init__(config, Platform.DINGTALK)

        extra = config.extra or {}
        self._client_id: str = extra.get("client_id") or os.getenv(
            "DINGTALK_CLIENT_ID", ""
        )
        self._client_secret: str = extra.get("client_secret") or os.getenv(
            "DINGTALK_CLIENT_SECRET", ""
        )

        # Group-chat gating (mirrors Slack/Telegram/Discord/WhatsApp conventions).
        # Mention state is the structured ``is_in_at_list`` attribute from the
        # dingtalk-stream SDK (set from the callback's ``isInAtList`` flag),
        # not text parsing.
        self._mention_patterns: List[re.Pattern] = self._compile_mention_patterns()
        self._allowed_users: Set[str] = self._load_allowed_users()

        self._stream_client: Any = None
        self._stream_task: Optional[asyncio.Task] = None
        self._http_client: Optional["httpx.AsyncClient"] = None
        self._card_sdk: Optional[Any] = None
        self._robot_sdk: Optional[Any] = None
        self._robot_code: str = extra.get("robot_code") or self._client_id

        # Message deduplication
        self._dedup = MessageDeduplicator(max_size=1000)
        # Map chat_id -> (session_webhook, expired_time_ms) for reply routing
        self._session_webhooks: Dict[str, tuple[str, int]] = {}
        # Map chat_id -> last inbound ChatbotMessage. Keyed by chat_id instead
        # of a single class attribute to avoid cross-message clobbering when
        # multiple conversations run concurrently.
        self._message_contexts: Dict[str, Any] = {}
        self._card_template_id: Optional[str] = extra.get("card_template_id")

        # Chats for which we've already fired the Done reaction — prevents
        # double-firing across segment boundaries or parallel flows
        # (tool-progress + stream-consumer both finalizing their cards).
        # Reset each inbound message.
        self._done_emoji_fired: Set[str] = set()
        # Cards in streaming state per chat: chat_id -> { out_track_id -> last_content }.
        # Every `send()` creates+finalizes a card (closed state).  A subsequent
        # `edit_message(finalize=False)` re-opens the card (DingTalk's API
        # allows streaming_update on a finalized card — it flips back to
        # streaming).  We track those reopened cards so the next `send()` can
        # auto-close them as siblings — otherwise tool-progress cards get
        # stuck in streaming state forever.
        self._streaming_cards: Dict[str, Dict[str, str]] = {}
        # Track fire-and-forget emoji/reaction coroutines so Python's GC
        # doesn't drop them mid-flight, and we can cancel them on disconnect.
        self._bg_tasks: Set[asyncio.Task] = set()

    # -- Connection lifecycle -----------------------------------------------

    async def connect(self) -> bool:
        """Connect to DingTalk via Stream Mode."""
        if not DINGTALK_STREAM_AVAILABLE:
            logger.warning(
                "[%s] dingtalk-stream not installed. Run: pip install 'dingtalk-stream>=0.20'",
                self.name,
            )
            return False
        if not HTTPX_AVAILABLE:
            logger.warning(
                "[%s] httpx not installed. Run: pip install httpx", self.name
            )
            return False
        if not self._client_id or not self._client_secret:
            logger.warning(
                "[%s] DINGTALK_CLIENT_ID and DINGTALK_CLIENT_SECRET required", self.name
            )
            return False

        try:
            self._http_client = httpx.AsyncClient(timeout=30.0)

            credential = dingtalk_stream.Credential(
                self._client_id, self._client_secret
            )
            self._stream_client = dingtalk_stream.DingTalkStreamClient(credential)

            # Initialize card SDK if available and configured
            if CARD_SDK_AVAILABLE and self._card_template_id:
                sdk_config = open_api_models.Config()
                sdk_config.protocol = "https"
                sdk_config.region_id = "central"
                self._card_sdk = dingtalk_card_client.Client(sdk_config)
                self._robot_sdk = dingtalk_robot_client.Client(sdk_config)
                logger.info(
                    "[%s] Card SDK initialized with template: %s",
                    self.name,
                    self._card_template_id,
                )
            elif CARD_SDK_AVAILABLE:
                # Initialize robot SDK even without card template (for media download)
                sdk_config = open_api_models.Config()
                sdk_config.protocol = "https"
                sdk_config.region_id = "central"
                self._robot_sdk = dingtalk_robot_client.Client(sdk_config)
                logger.info("[%s] Robot SDK initialized (media download)", self.name)

            # Capture the current event loop for cross-thread dispatch
            loop = asyncio.get_running_loop()
            handler = _IncomingHandler(self, loop)
            self._stream_client.register_callback_handler(
                dingtalk_stream.ChatbotMessage.TOPIC, handler
            )

            self._stream_task = asyncio.create_task(self._run_stream())
            self._mark_connected()
            logger.info("[%s] Connected via Stream Mode", self.name)
            return True
        except Exception as e:
            logger.error("[%s] Failed to connect: %s", self.name, e)
            return False

    async def _run_stream(self) -> None:
        """Run the async stream client with auto-reconnection."""
        backoff_idx = 0
        while self._running:
            try:
                logger.debug("[%s] Starting stream client...", self.name)
                await self._stream_client.start()
            except asyncio.CancelledError:
                return
            except Exception as e:
                if not self._running:
                    return
                logger.warning("[%s] Stream client error: %s", self.name, e)

            if not self._running:
                return

            delay = RECONNECT_BACKOFF[min(backoff_idx, len(RECONNECT_BACKOFF) - 1)]
            logger.info("[%s] Reconnecting in %ds...", self.name, delay)
            await asyncio.sleep(delay)
            backoff_idx += 1

    async def disconnect(self) -> None:
        """Disconnect from DingTalk."""
        self._running = False
        self._mark_disconnected()

        # Close the active websocket first so the stream task sees the
        # disconnection and exits cleanly, rather than getting stuck
        # awaiting frames that will never arrive.
        websocket = getattr(self._stream_client, "websocket", None) if self._stream_client else None
        if websocket is not None:
            try:
                await websocket.close()
            except Exception as e:
                logger.debug("[%s] websocket close during disconnect failed: %s", self.name, e)

        if self._stream_task:
            # Try graceful close first if SDK supports it. The SDK's close()
            # is sync and may block on network I/O, so offload to a thread.
            if hasattr(self._stream_client, "close"):
                try:
                    await asyncio.to_thread(self._stream_client.close)
                except Exception:
                    pass

            self._stream_task.cancel()
            try:
                await asyncio.wait_for(self._stream_task, timeout=5.0)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                logger.debug("[%s] stream task did not exit cleanly during disconnect", self.name)
            self._stream_task = None

        # Cancel any in-flight background tasks (emoji reactions, etc.)
        if self._bg_tasks:
            for task in list(self._bg_tasks):
                task.cancel()
            await asyncio.gather(*self._bg_tasks, return_exceptions=True)
            self._bg_tasks.clear()

        if self._http_client:
            await self._http_client.aclose()
            self._http_client = None

        self._stream_client = None
        self._session_webhooks.clear()
        self._message_contexts.clear()
        self._streaming_cards.clear()
        self._done_emoji_fired.clear()
        self._dedup.clear()
        logger.info("[%s] Disconnected", self.name)

    # -- Group gating --------------------------------------------------------

    def _dingtalk_require_mention(self) -> bool:
        """Return whether group chats should require an explicit bot trigger."""
        configured = self.config.extra.get("require_mention")
        if configured is not None:
            if isinstance(configured, str):
                return configured.lower() in ("true", "1", "yes", "on")
            return bool(configured)
        return os.getenv("DINGTALK_REQUIRE_MENTION", "false").lower() in ("true", "1", "yes", "on")

    def _dingtalk_free_response_chats(self) -> Set[str]:
        raw = self.config.extra.get("free_response_chats")
        if raw is None:
            raw = os.getenv("DINGTALK_FREE_RESPONSE_CHATS", "")
        if isinstance(raw, list):
            return {str(part).strip() for part in raw if str(part).strip()}
        return {part.strip() for part in str(raw).split(",") if part.strip()}

    def _compile_mention_patterns(self) -> List[re.Pattern]:
        """Compile optional regex wake-word patterns for group triggers."""
        patterns = self.config.extra.get("mention_patterns") if self.config.extra else None
        if patterns is None:
            raw = os.getenv("DINGTALK_MENTION_PATTERNS", "").strip()
            if raw:
                try:
                    loaded = json.loads(raw)
                except Exception:
                    loaded = [part.strip() for part in raw.splitlines() if part.strip()]
                    if not loaded:
                        loaded = [part.strip() for part in raw.split(",") if part.strip()]
                patterns = loaded

        if patterns is None:
            return []
        if isinstance(patterns, str):
            patterns = [patterns]
        if not isinstance(patterns, list):
            logger.warning(
                "[%s] dingtalk mention_patterns must be a list or string; got %s",
                self.name,
                type(patterns).__name__,
            )
            return []

        compiled: List[re.Pattern] = []
        for pattern in patterns:
            if not isinstance(pattern, str) or not pattern.strip():
                continue
            try:
                compiled.append(re.compile(pattern, re.IGNORECASE))
            except re.error as exc:
                logger.warning("[%s] Invalid DingTalk mention pattern %r: %s", self.name, pattern, exc)
        if compiled:
            logger.info("[%s] Loaded %d DingTalk mention pattern(s)", self.name, len(compiled))
        return compiled

    def _load_allowed_users(self) -> Set[str]:
        """Load allowed-users list from config.extra or env var.

        IDs are matched case-insensitively against the sender's ``staff_id`` and
        ``sender_id``. A wildcard ``*`` disables the check.
        """
        raw = self.config.extra.get("allowed_users") if self.config.extra else None
        if raw is None:
            raw = os.getenv("DINGTALK_ALLOWED_USERS", "")
        if isinstance(raw, list):
            items = [str(part).strip() for part in raw if str(part).strip()]
        else:
            items = [part.strip() for part in str(raw).split(",") if part.strip()]
        return {item.lower() for item in items}

    def _is_user_allowed(self, sender_id: str, sender_staff_id: str) -> bool:
        if not self._allowed_users or "*" in self._allowed_users:
            return True
        candidates = {(sender_id or "").lower(), (sender_staff_id or "").lower()}
        candidates.discard("")
        return bool(candidates & self._allowed_users)

    def _message_mentions_bot(self, message: "ChatbotMessage") -> bool:
        """True if the bot was @-mentioned in a group message.

        dingtalk-stream sets ``is_in_at_list`` on the incoming ChatbotMessage
        when the bot is addressed via @-mention.
        """
        return bool(getattr(message, "is_in_at_list", False))

    def _message_matches_mention_patterns(self, text: str) -> bool:
        if not text or not self._mention_patterns:
            return False
        return any(pattern.search(text) for pattern in self._mention_patterns)

    def _should_process_message(self, message: "ChatbotMessage", text: str, is_group: bool, chat_id: str) -> bool:
        """Apply DingTalk group trigger rules.

        DMs remain unrestricted (subject to ``allowed_users`` which is enforced
        earlier). Group messages are accepted when:
        - the chat is explicitly allowlisted in ``free_response_chats``
        - ``require_mention`` is disabled
        - the bot is @mentioned (``is_in_at_list``)
        - the text matches a configured regex wake-word pattern
        """
        if not is_group:
            return True
        if chat_id and chat_id in self._dingtalk_free_response_chats():
            return True
        if not self._dingtalk_require_mention():
            return True
        if self._message_mentions_bot(message):
            return True
        return self._message_matches_mention_patterns(text)

    def _spawn_bg(self, coro) -> None:
        """Start a fire-and-forget coroutine and track it for cleanup."""
        task = asyncio.create_task(coro)
        self._bg_tasks.add(task)
        task.add_done_callback(self._bg_tasks.discard)

    # -- AI Card lifecycle helpers ------------------------------------------

    async def _close_streaming_siblings(self, chat_id: str) -> None:
        """Finalize any previously-open streaming cards for this chat.

        Called at the start of every ``send()`` so lingering tool-progress
        cards that were reopened by ``edit_message(finalize=False)`` get
        cleanly closed before the next card is created.  Without this,
        tool-progress cards stay stuck in streaming state after the agent
        moves on (there is no explicit "turn end" signal from the gateway).
        """
        cards = self._streaming_cards.pop(chat_id, None)
        if not cards:
            return
        token = await self._get_access_token()
        if not token:
            return
        for out_track_id, last_content in list(cards.items()):
            try:
                await self._stream_card_content(
                    out_track_id, token, last_content, finalize=True,
                )
                logger.debug(
                    "[%s] AI Card sibling closed: %s",
                    self.name, out_track_id,
                )
            except Exception as e:
                logger.debug(
                    "[%s] Sibling close failed for %s: %s",
                    self.name, out_track_id, e,
                )

    def _fire_done_reaction(self, chat_id: str) -> None:
        """Swap 🤔Thinking → 🥳Done on the original user message.

        Idempotent per chat_id — safe to call from segment-break flushes
        and final-done flushes without double-firing.
        """
        if chat_id in self._done_emoji_fired:
            return
        self._done_emoji_fired.add(chat_id)
        msg = self._message_contexts.get(chat_id)
        if not msg:
            return
        msg_id = getattr(msg, "message_id", "") or ""
        conversation_id = getattr(msg, "conversation_id", "") or ""
        if not (msg_id and conversation_id):
            return

        async def _swap() -> None:
            await self._send_emotion(
                msg_id, conversation_id, "🤔Thinking", recall=True,
            )
            await self._send_emotion(
                msg_id, conversation_id, "🥳Done", recall=False,
            )

        self._spawn_bg(_swap())

    # -- Inbound message processing -----------------------------------------

    async def _on_message(
        self,
        message: "ChatbotMessage",
    ) -> None:
        """Process an incoming DingTalk chatbot message."""
        msg_id = getattr(message, "message_id", None) or uuid.uuid4().hex
        if self._dedup.is_duplicate(msg_id):
            logger.debug("[%s] Duplicate message %s, skipping", self.name, msg_id)
            return

        # Chat context
        conversation_id = getattr(message, "conversation_id", "") or ""
        conversation_type = getattr(message, "conversation_type", "1")
        is_group = str(conversation_type) == "2"
        sender_id = getattr(message, "sender_id", "") or ""
        sender_nick = getattr(message, "sender_nick", "") or sender_id
        sender_staff_id = getattr(message, "sender_staff_id", "") or ""

        chat_id = conversation_id or sender_id
        chat_type = "group" if is_group else "dm"

        # Allowed-users gate (applies to both DM and group)
        if not self._is_user_allowed(sender_id, sender_staff_id):
            logger.debug(
                "[%s] Dropping message from non-allowlisted user staff_id=%s sender_id=%s",
                self.name, sender_staff_id, sender_id,
            )
            return

        # Group mention/pattern gate.  DMs pass through unconditionally.
        # We need the message text for regex wake-word matching; extract it
        # early but don't consume the rest of the pipeline until after the
        # gate decides whether to process.
        _early_text = self._extract_text(message) or ""
        if not self._should_process_message(message, _early_text, is_group, chat_id):
            logger.debug(
                "[%s] Dropping group message that failed mention gate message_id=%s chat_id=%s",
                self.name, msg_id, chat_id,
            )
            return

        # Stash the incoming message keyed by chat_id so concurrent
        # conversations don't clobber each other's context.  Also reset
        # the per-chat "Done emoji fired" marker so a new inbound message
        # gets its own Thinking→Done cycle.
        if chat_id:
            self._message_contexts[chat_id] = message
            self._done_emoji_fired.discard(chat_id)

        # Store session webhook
        session_webhook = getattr(message, "session_webhook", None) or ""
        session_webhook_expired_time = (
            getattr(message, "session_webhook_expired_time", 0) or 0
        )
        if session_webhook and chat_id and _DINGTALK_WEBHOOK_RE.match(session_webhook):
            if len(self._session_webhooks) >= _SESSION_WEBHOOKS_MAX:
                try:
                    self._session_webhooks.pop(next(iter(self._session_webhooks)))
                except StopIteration:
                    pass
            self._session_webhooks[chat_id] = (
                session_webhook,
                session_webhook_expired_time,
            )

        # Make the current session webhook available to tool calls running in
        # this message's async context. Child tasks created from here inherit
        # these contextvars, which lets send_message() reply with media back to
        # the active DingTalk chat without requiring a global adapter cache.
        try:
            from gateway.session_context import set_session_runtime_vars

            set_session_runtime_vars(
                dingtalk_webhook=session_webhook,
                dingtalk_webhook_expires_at=str(session_webhook_expired_time or ""),
            )
        except Exception:
            logger.debug("[%s] Failed to set DingTalk runtime session vars", self.name, exc_info=True)

        # Resolve media download codes to URLs so vision tools can use them
        await self._resolve_media_codes(message)

        # Extract text content
        text = self._extract_text(message)

        # Determine message type and build media list
        msg_type, media_urls, media_types = self._extract_media(message)

        logger.info(
            "[%s] Parsed inbound: sdk_msgtype=%s text_len=%d media_count=%d media_types=%s",
            self.name,
            getattr(message, "message_type", "") or "",
            len(text or ""),
            len(media_urls),
            media_types,
        )

        if not text and not media_urls:
            logger.debug("[%s] Empty message, skipping", self.name)
            return

        source = self.build_source(
            chat_id=chat_id,
            chat_name=getattr(message, "conversation_title", None),
            chat_type=chat_type,
            user_id=sender_id,
            user_name=sender_nick,
            user_id_alt=sender_staff_id if sender_staff_id else None,
        )

        # Parse timestamp
        create_at = getattr(message, "create_at", None)
        try:
            timestamp = (
                datetime.fromtimestamp(int(create_at) / 1000, tz=timezone.utc)
                if create_at
                else datetime.now(tz=timezone.utc)
            )
        except (ValueError, OSError, TypeError):
            timestamp = datetime.now(tz=timezone.utc)

        event = MessageEvent(
            text=text,
            message_type=msg_type,
            source=source,
            message_id=msg_id,
            raw_message=message,
            media_urls=media_urls,
            media_types=media_types,
            timestamp=timestamp,
        )

        logger.debug(
            "[%s] Message from %s in %s: %s",
            self.name,
            sender_nick,
            chat_id[:20] if chat_id else "?",
            text[:80] if text else "(media)",
        )
        await self.handle_message(event)

    @staticmethod
    def _extract_text(message: "ChatbotMessage") -> str:
        """Extract plain text from a DingTalk chatbot message.

        Handles both legacy and current dingtalk-stream SDK payload shapes:
          * legacy: ``message.text`` was a dict ``{"content": "..."}``
          * >= 0.20: ``message.text`` is a ``TextContent`` dataclass whose
            ``__str__`` returns ``"TextContent(content=...)"`` — never fall
            back to ``str(text)`` without extracting ``.content`` first.
          * rich text moved from ``message.rich_text`` (list) to
            ``message.rich_text_content.rich_text_list`` (list of dicts).
        """
        text = getattr(message, "text", None) or ""

        # Handle TextContent object (SDK style)
        if hasattr(text, "content"):
            content = (text.content or "").strip()
        elif isinstance(text, dict):
            content = text.get("content", "").strip()
        else:
            content = str(text).strip()

        if not content:
            rich_text = getattr(message, "rich_text_content", None) or getattr(
                message, "rich_text", None
            )
            if rich_text:
                rich_list = getattr(rich_text, "rich_text_list", None) or rich_text
                if isinstance(rich_list, list):
                    parts = []
                    for item in rich_list:
                        if isinstance(item, dict):
                            t = item.get("text") or item.get("content") or ""
                            if t:
                                parts.append(t)
                        elif hasattr(item, "text") and item.text:
                            parts.append(item.text)
                    content = " ".join(parts).strip()

        # Do NOT strip "@bot" from the text.  The mention is a routing
        # signal (delivered structurally via callback `isInAtList`), and
        # regex-stripping @handles would collateral-damage e-mails
        # (alice@example.com), SSH URLs (git@github.com), and literal
        # references the user wrote ("what does @openai think").  Let the
        # LLM see the raw text — it handles "@bot hello" cleanly.
        return content

    def _extract_media(self, message: "ChatbotMessage"):
        """Extract media info from message. Returns (MessageType, [urls], [mime_types])."""
        msg_type = MessageType.TEXT
        media_urls = []
        media_types = []

        # Check for image/picture
        image_content = getattr(message, "image_content", None)
        if image_content:
            download_code = getattr(image_content, "download_code", None)
            if download_code:
                media_urls.append(download_code)
                media_types.append("image")
                msg_type = MessageType.PHOTO

        # Check for rich text with mixed content
        rich_text = getattr(message, "rich_text_content", None) or getattr(
            message, "rich_text", None
        )
        if rich_text:
            rich_list = getattr(rich_text, "rich_text_list", None) or rich_text
            if isinstance(rich_list, list):
                for item in rich_list:
                    if isinstance(item, dict):
                        dl_code = (
                            item.get("downloadCode") or item.get("download_code") or ""
                        )
                        item_type = item.get("type", "")
                        if dl_code:
                            mapped = DINGTALK_TYPE_MAPPING.get(item_type, "file")
                            media_urls.append(dl_code)
                            if mapped == "image":
                                media_types.append("image")
                                if msg_type == MessageType.TEXT:
                                    msg_type = MessageType.PHOTO
                            elif mapped == "audio":
                                media_types.append("audio")
                                if msg_type == MessageType.TEXT:
                                    msg_type = MessageType.AUDIO
                            elif mapped == "video":
                                media_types.append("video")
                                if msg_type == MessageType.TEXT:
                                    msg_type = MessageType.VIDEO
                            else:
                                media_types.append("application/octet-stream")
                                if msg_type == MessageType.TEXT:
                                    msg_type = MessageType.DOCUMENT

        # File / audio / video / voice message types from stream SDK (download codes)
        for attr_name, mtype, type_str in [
            ("file_content", MessageType.DOCUMENT, "file"),
            ("audio_content", MessageType.AUDIO, "audio"),
            ("video_content", MessageType.VIDEO, "video"),
            ("voice_content", MessageType.AUDIO, "voice"),
        ]:
            content_obj = getattr(message, attr_name, None)
            if content_obj:
                local_path = getattr(content_obj, "local_path", None) or (
                    content_obj.get("local_path") if isinstance(content_obj, dict) else None
                )
                dl_code = getattr(content_obj, "download_code", None) or (
                    content_obj.get("downloadCode") if isinstance(content_obj, dict) else None
                )
                media_ref = local_path or dl_code
                if media_ref:
                    media_urls.append(media_ref)
                    media_types.append(self._infer_media_type(content_obj, default_kind=type_str))
                    if msg_type == MessageType.TEXT:
                        msg_type = mtype

        msg_type_str = getattr(message, "message_type", "") or ""
        if msg_type_str == "picture" and not media_urls:
            msg_type = MessageType.PHOTO
        elif msg_type_str == "richText":
            msg_type = (
                MessageType.PHOTO
                if any("image" in t for t in media_types)
                else MessageType.TEXT
            )

        return msg_type, media_urls, media_types

    # -- Outbound messaging -------------------------------------------------

    async def send(
        self,
        chat_id: str,
        content: str,
        reply_to: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> SendResult:
        """Send a markdown reply via DingTalk session webhook."""
        metadata = metadata or {}
        logger.debug(
            "[%s] send() chat_id=%s card_enabled=%s",
            self.name,
            chat_id,
            bool(self._card_template_id and self._card_sdk),
        )

        # Check metadata first (for direct webhook sends)
        session_webhook = metadata.get("session_webhook")
        if not session_webhook:
            webhook_info = self._get_valid_webhook(chat_id)
            if not webhook_info:
                logger.warning(
                    "[%s] No valid session_webhook for chat_id=%s",
                    self.name, chat_id,
                )
                return SendResult(
                    success=False,
                    error="No valid session_webhook available. Reply must follow an incoming message.",
                )
            session_webhook, _ = webhook_info

        if not self._http_client:
            return SendResult(success=False, error="HTTP client not initialized")

        # Look up the inbound message for this chat (for AI Card routing)
        current_message = self._message_contexts.get(chat_id)

        # ``reply_to`` is the signal that this send is the FINAL response
        # to an inbound user message — only `base.py:_send_with_retry` sets
        # it.  Tool-progress, commentary, and stream-consumer first-sends
        # all leave it None.  We use it for two orthogonal decisions:
        #   1. finalize on create?  Yes if final reply, No if intermediate
        #      (intermediate cards stay in streaming state so edit_message
        #      updates don't flicker closed→streaming→closed repeatedly).
        #   2. fire Done reaction?  Only when this is the final reply.
        is_final_reply = reply_to is not None

        # Try AI Card first (using alibabacloud_dingtalk.card_1_0 SDK).
        if self._card_template_id and current_message and self._card_sdk:
            # Close any previously-open streaming cards for this chat
            # before creating a new one (handles tool-progress → final-
            # response handoff; also cleans up lingering commentary cards).
            await self._close_streaming_siblings(chat_id)

            result = await self._create_and_stream_card(
                chat_id, current_message, content,
                finalize=is_final_reply,
            )
            if result and result.success:
                if is_final_reply:
                    # Final reply: card closed, swap Thinking → Done.
                    self._fire_done_reaction(chat_id)
                else:
                    # Intermediate (tool progress / commentary / streaming
                    # first chunk): keep the card open and track it so the
                    # next send() auto-closes it as a sibling, or
                    # edit_message(finalize=True) closes it explicitly.
                    self._streaming_cards.setdefault(chat_id, {})[
                        result.message_id
                    ] = content
                return result

            logger.warning("[%s] AI Card send failed, falling back to webhook", self.name)

        logger.debug("[%s] Sending via webhook", self.name)
        # Normalize markdown for DingTalk
        normalized = self._normalize_markdown(content[: self.MAX_MESSAGE_LENGTH])

        payload = {
            "msgtype": "markdown",
            "markdown": {"title": "Hermes", "text": normalized},
        }

        try:
            resp = await self._http_client.post(
                session_webhook, json=payload, timeout=15.0
            )
            if resp.status_code < 300:
                # Webhook path: fire Done only for final replies, same as
                # the card path.
                if is_final_reply:
                    self._fire_done_reaction(chat_id)
                return SendResult(success=True, message_id=uuid.uuid4().hex[:12])
            body = resp.text
            logger.warning(
                "[%s] Send failed HTTP %d: %s", self.name, resp.status_code, body[:200]
            )
            return SendResult(
                success=False, error=f"HTTP {resp.status_code}: {body[:200]}"
            )
        except httpx.TimeoutException:
            return SendResult(
                success=False, error="Timeout sending message to DingTalk"
            )
        except Exception as e:
            logger.error("[%s] Send error: %s", self.name, e)
            return SendResult(success=False, error=str(e))

    async def send_typing(self, chat_id: str, metadata=None) -> None:
        """DingTalk does not support typing indicators."""
        pass

    @staticmethod
    def _get_video_duration_ms(video_path: str) -> Optional[int]:
        """Parse video duration from MP4 moov/mvhd atom.

        Returns duration in milliseconds, or None if it cannot be determined.
        Does not require external libraries — reads the MP4 container directly.
        """
        try:
            with open(video_path, "rb") as f:
                data = f.read()
            # Find moov atom
            moov_pos = data.find(b"moov")
            if moov_pos == -1:
                return None
            moov_data = data[moov_pos:]
            # Find mvhd inside moov
            mvhd_pos = moov_data.find(b"mvhd")
            if mvhd_pos == -1:
                return None
            mvhd_payload = moov_data[mvhd_pos + 4:]  # skip size(4) + 'mvhd'(4)
            version = mvhd_payload[0]
            if version == 0:
                # 32-bit: timescale at offset 12, duration at offset 16
                timescale = struct.unpack(">I", mvhd_payload[12:16])[0]
                duration = struct.unpack(">I", mvhd_payload[16:20])[0]
            else:
                # 64-bit: timescale at offset 20, duration at offset 24
                # (creation_time and modification_time are 8 bytes each in v1)
                timescale = struct.unpack(">I", mvhd_payload[20:24])[0]
                duration = struct.unpack(">Q", mvhd_payload[24:32])[0]
            if timescale == 0:
                return None
            return int(duration / timescale * 1000)
        except Exception:
            return None

    async def _upload_media(
        self, file_path: str, file_type: str = "file"
    ) -> Optional[str]:
        """Upload a file to DingTalk and return the media_id.

        Uses the stream client's built-in upload_to_dingtalk method,
        which handles access token acquisition automatically.
        """
        if not self._stream_client:
            return None
        try:
            with open(file_path, "rb") as f:
                file_bytes = f.read()
            ext = os.path.splitext(file_path)[1].lower()
            mime_map = {
                ".jpg": "image/jpeg",
                ".jpeg": "image/jpeg",
                ".png": "image/png",
                ".gif": "image/gif",
                ".mp4": "video/mp4",
                ".mov": "video/quicktime",
                ".avi": "video/x-msvideo",
                ".mp3": "audio/mpeg",
                ".m4a": "audio/mp4",
                ".wav": "audio/wav",
                ".pdf": "application/pdf",
                ".doc": "application/msword",
                ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                ".xls": "application/vnd.ms-excel",
                ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                ".zip": "application/zip",
            }
            mime_type = mime_map.get(ext, "application/octet-stream")
            media_id = await asyncio.to_thread(
                self._stream_client.upload_to_dingtalk,
                file_bytes,
                filetype=file_type,
                filename=os.path.basename(file_path),
                mimetype=mime_type,
            )
            return media_id
        except Exception as e:
            logger.error("[%s] Media upload failed: %s", self.name, e)
            return None

    async def _send_file_message(
        self,
        session_webhook: str,
        media_id: str,
        file_name: str,
        msg_type: str = "file",
        video_duration_ms: Optional[int] = None,
    ) -> SendResult:
        """Send a file/media message via session webhook.

        Args:
            video_duration_ms: Required for video messages. Duration in milliseconds.
        """
        payload = {
            "msgtype": msg_type,
            msg_type: {"media_id": media_id},
        }
        if msg_type == "file":
            payload["file"] = {"media_id": media_id, "file_name": file_name}
        elif msg_type == "video":
            video_data: Dict[str, Any] = {"media_id": media_id}
            if video_duration_ms is not None:
                video_data["duration"] = video_duration_ms // 1000  # DingTalk expects seconds
            payload["video"] = video_data

        try:
            resp = await self._http_client.post(
                session_webhook, json=payload, timeout=30.0
            )
            if resp.status_code < 300:
                return SendResult(success=True, message_id=uuid.uuid4().hex[:12])
            body = resp.text
            logger.warning(
                "[%s] File send failed HTTP %d: %s",
                self.name, resp.status_code, body[:200],
            )
            return SendResult(success=False, error=f"HTTP {resp.status_code}: {body[:200]}")
        except httpx.TimeoutException:
            return SendResult(success=False, error="Timeout sending file to DingTalk")
        except Exception as e:
            logger.error("[%s] File send error: %s", self.name, e)
            return SendResult(success=False, error=str(e))

    async def send_document(
        self,
        chat_id: str,
        file_path: str,
        caption: Optional[str] = None,
        file_name: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        **kwargs,
    ) -> SendResult:
        """Send a document/file attachment via DingTalk session webhook."""
        metadata = metadata or {}
        session_webhook = metadata.get("session_webhook")
        if not session_webhook:
            webhook_info = self._get_valid_webhook(chat_id)
            if not webhook_info:
                return SendResult(
                    success=False,
                    error="No valid session_webhook available. File send must follow an incoming message.",
                )
            session_webhook, _ = webhook_info

        display_name = file_name or os.path.basename(file_path)
        media_id = await self._upload_media(file_path, file_type="file")
        if not media_id:
            return SendResult(success=False, error="File upload to DingTalk failed")

        result = await self._send_file_message(
            session_webhook, media_id, display_name, msg_type="file"
        )

        # If caption is provided, send it as a follow-up text message
        caption_failed = None
        if caption and result.success:
            text_payload = {
                "msgtype": "text",
                "text": {"content": caption},
            }
            try:
                await self._http_client.post(
                    session_webhook, json=text_payload, timeout=15.0
                )
            except Exception as e:
                caption_failed = str(e)
                logger.warning("[%s] Caption send failed: %s", self.name, e)
        if caption_failed and result.success:
            result.error = f"[caption_failed] {caption_failed}"
        return result

    async def send_image_file(
        self,
        chat_id: str,
        image_path: str,
        caption: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        **kwargs,
    ) -> SendResult:
        """Send an image file via DingTalk session webhook."""
        metadata = metadata or {}
        session_webhook = metadata.get("session_webhook")
        if not session_webhook:
            webhook_info = self._get_valid_webhook(chat_id)
            if not webhook_info:
                return SendResult(
                    success=False,
                    error="No valid session_webhook available. Image send must follow an incoming message.",
                )
            session_webhook, _ = webhook_info

        media_id = await self._upload_media(image_path, file_type="image")
        if not media_id:
            return SendResult(success=False, error="Image upload to DingTalk failed")

        payload = {
            "msgtype": "image",
            "image": {"media_id": media_id},
        }
        result: SendResult
        try:
            resp = await self._http_client.post(
                session_webhook, json=payload, timeout=30.0
            )
            if resp.status_code < 300:
                result = SendResult(success=True, message_id=uuid.uuid4().hex[:12])
            else:
                body = resp.text
                logger.warning(
                    "[%s] Image send failed HTTP %d: %s",
                    self.name, resp.status_code, body[:200],
                )
                return SendResult(success=False, error=f"HTTP {resp.status_code}: {body[:200]}")
        except httpx.TimeoutException:
            return SendResult(success=False, error="Timeout sending image to DingTalk")
        except Exception as e:
            logger.error("[%s] Image send error: %s", self.name, e)
            return SendResult(success=False, error=str(e))

        # If caption is provided, send it as a follow-up text message
        caption_failed = None
        if caption and result.success:
            text_payload = {
                "msgtype": "text",
                "text": {"content": caption},
            }
            try:
                await self._http_client.post(
                    session_webhook, json=text_payload, timeout=15.0
                )
            except Exception as e:
                caption_failed = str(e)
                logger.warning("[%s] Caption send failed: %s", self.name, e)
        if caption_failed and result.success:
            result.error = f"[caption_failed] {caption_failed}"
        return result

    async def send_video(
        self,
        chat_id: str,
        video_path: str,
        caption: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        **kwargs,
    ) -> SendResult:
        """Send a video file via DingTalk session webhook."""
        metadata = metadata or {}
        session_webhook = metadata.get("session_webhook")
        if not session_webhook:
            webhook_info = self._get_valid_webhook(chat_id)
            if not webhook_info:
                return SendResult(
                    success=False,
                    error="No valid session_webhook available. Video send must follow an incoming message.",
                )
            session_webhook, _ = webhook_info

        media_id = await self._upload_media(video_path, file_type="video")
        if not media_id:
            return SendResult(success=False, error="Video upload to DingTalk failed")

        # Get video duration (before upload so we can fail fast if missing)
        duration_ms = self._get_video_duration_ms(video_path)
        if duration_ms is None:
            logger.warning("[%s] Could not determine video duration for %s", self.name, video_path)

        result = await self._send_file_message(
            session_webhook,
            media_id,
            os.path.basename(video_path),
            msg_type="video",
            video_duration_ms=duration_ms,
        )

        # If caption is provided, send it as a follow-up text message
        caption_failed = None
        if caption and result.success:
            text_payload = {
                "msgtype": "text",
                "text": {"content": caption},
            }
            try:
                await self._http_client.post(
                    session_webhook, json=text_payload, timeout=15.0
                )
            except Exception as e:
                caption_failed = str(e)
                logger.warning("[%s] Caption send failed: %s", self.name, e)
        if caption_failed and result.success:
            result.error = f"[caption_failed] {caption_failed}"
        return result

    async def send_voice(
        self,
        chat_id: str,
        audio_path: str,
        caption: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        **kwargs,
    ) -> SendResult:
        """Send a voice/audio file via DingTalk session webhook.

        Note: DingTalk voice messages use the same upload mechanism as files.
        The file is sent as an 'audio' type message.
        """
        metadata = metadata or {}
        session_webhook = metadata.get("session_webhook")
        if not session_webhook:
            webhook_info = self._get_valid_webhook(chat_id)
            if not webhook_info:
                return SendResult(
                    success=False,
                    error="No valid session_webhook available. Voice send must follow an incoming message.",
                )
            session_webhook, _ = webhook_info

        media_id = await self._upload_media(audio_path, file_type="voice")
        if not media_id:
            return SendResult(success=False, error="Audio upload to DingTalk failed")

        payload = {
            "msgtype": "voice",
            "voice": {"media_id": media_id},
        }
        try:
            resp = await self._http_client.post(
                session_webhook, json=payload, timeout=30.0
            )
            if resp.status_code < 300:
                result = SendResult(success=True, message_id=uuid.uuid4().hex[:12])
            else:
                body = resp.text
                logger.warning(
                    "[%s] Voice send failed HTTP %d: %s",
                    self.name, resp.status_code, body[:200],
                )
                return SendResult(success=False, error=f"HTTP {resp.status_code}: {body[:200]}")
        except httpx.TimeoutException:
            return SendResult(success=False, error="Timeout sending voice to DingTalk")
        except Exception as e:
            logger.error("[%s] Voice send error: %s", self.name, e)
            return SendResult(success=False, error=str(e))

        # If caption is provided, send it as a follow-up text message
        caption_failed = None
        if caption and result.success:
            text_payload = {
                "msgtype": "text",
                "text": {"content": caption},
            }
            try:
                await self._http_client.post(
                    session_webhook, json=text_payload, timeout=15.0
                )
            except Exception as e:
                caption_failed = str(e)
                logger.warning("[%s] Caption send failed: %s", self.name, e)
        if caption_failed and result.success:
            result.error = f"[caption_failed] {caption_failed}"
        return result

    async def get_chat_info(self, chat_id: str) -> Dict[str, Any]:
        """Return basic info about a DingTalk conversation."""
        return {
            "name": chat_id,
            "type": "group" if "group" in chat_id.lower() else "dm",
        }

    def _get_valid_webhook(self, chat_id: str) -> Optional[tuple[str, int]]:
        """Get a valid (non-expired) session webhook for the given chat_id."""
        info = self._session_webhooks.get(chat_id)
        if not info:
            return None
        webhook, expired_time_ms = info
        # Check expiry with 5-minute safety margin
        if expired_time_ms and expired_time_ms > 0:
            now_ms = int(datetime.now(tz=timezone.utc).timestamp() * 1000)
            safety_margin_ms = 5 * 60 * 1000
            if now_ms + safety_margin_ms >= expired_time_ms:
                # Expired, remove from cache
                self._session_webhooks.pop(chat_id, None)
                return None
        return info

    async def _create_and_stream_card(
        self,
        chat_id: str,
        message: Any,
        content: str,
        *,
        finalize: bool = True,
    ) -> Optional[SendResult]:
        """Create an AI Card, deliver it to the conversation, and stream initial content.

        Always called with ``finalize=True`` from ``send()`` (closed state).
        If the caller later issues ``edit_message(finalize=False)``, the
        DingTalk streaming_update API reopens the card into streaming
        state, and we track that in ``_streaming_cards`` for sibling
        cleanup on the next send.
        """
        try:
            token = await self._get_access_token()
            if not token:
                return None

            out_track_id = f"hermes_{uuid.uuid4().hex[:12]}"

            conversation_id = getattr(message, "conversation_id", "") or ""
            conversation_type = getattr(message, "conversation_type", "1")
            is_group = str(conversation_type) == "2"
            sender_staff_id = getattr(message, "sender_staff_id", "") or ""

            runtime = tea_util_models.RuntimeOptions()

            # Step 1: Create card with STREAM callback type
            create_request = dingtalk_card_models.CreateCardRequest(
                card_template_id=self._card_template_id,
                out_track_id=out_track_id,
                card_data=dingtalk_card_models.CreateCardRequestCardData(
                    card_param_map={"content": ""},
                ),
                callback_type="STREAM",
                im_group_open_space_model=(
                    dingtalk_card_models.CreateCardRequestImGroupOpenSpaceModel(
                        support_forward=True,
                    )
                ),
                im_robot_open_space_model=(
                    dingtalk_card_models.CreateCardRequestImRobotOpenSpaceModel(
                        support_forward=True,
                    )
                ),
            )

            create_headers = dingtalk_card_models.CreateCardHeaders(
                x_acs_dingtalk_access_token=token,
            )

            await self._card_sdk.create_card_with_options_async(
                create_request, create_headers, runtime
            )

            # Step 2: Deliver card to the conversation
            if is_group:
                open_space_id = f"dtv1.card//IM_GROUP.{conversation_id}"
                deliver_request = dingtalk_card_models.DeliverCardRequest(
                    out_track_id=out_track_id,
                    user_id_type=1,
                    open_space_id=open_space_id,
                    im_group_open_deliver_model=(
                        dingtalk_card_models.DeliverCardRequestImGroupOpenDeliverModel(
                            robot_code=self._robot_code,
                        )
                    ),
                )
            else:
                if not sender_staff_id:
                    logger.warning(
                        "[%s] AI Card skipped: missing sender_staff_id for DM",
                        self.name,
                    )
                    return None
                open_space_id = f"dtv1.card//IM_ROBOT.{sender_staff_id}"
                deliver_request = dingtalk_card_models.DeliverCardRequest(
                    out_track_id=out_track_id,
                    user_id_type=1,
                    open_space_id=open_space_id,
                    im_robot_open_deliver_model=(
                        dingtalk_card_models.DeliverCardRequestImRobotOpenDeliverModel(
                            space_type="IM_ROBOT",
                        )
                    ),
                )

            deliver_headers = dingtalk_card_models.DeliverCardHeaders(
                x_acs_dingtalk_access_token=token,
            )

            await self._card_sdk.deliver_card_with_options_async(
                deliver_request, deliver_headers, runtime
            )

            # Step 3: Stream initial content.  finalize=True closes the
            # card immediately (one-shot); finalize=False keeps it open
            # for streaming edit_message updates by out_track_id.
            await self._stream_card_content(
                out_track_id, token, content, finalize=finalize,
            )

            logger.info(
                "[%s] AI Card %s: %s",
                self.name,
                "created+finalized" if finalize else "created (streaming)",
                out_track_id,
            )
            return SendResult(success=True, message_id=out_track_id)

        except Exception as e:
            logger.warning(
                "[%s] AI Card create failed: %s\n%s",
                self.name, e, traceback.format_exc(),
            )
            return None

    async def edit_message(
        self,
        chat_id: str,
        message_id: str,
        content: str,
        *,
        finalize: bool = False,
    ) -> SendResult:
        """Edit an AI Card by streaming updated content.

        ``message_id`` is the out_track_id returned by the initial ``send()``
        call that created this card.  Callers (stream_consumer, tool
        progress) track their own ids independently so two parallel flows
        on the same chat_id don't interfere.
        """
        if not message_id:
            return SendResult(success=False, error="message_id required")
        token = await self._get_access_token()
        if not token:
            return SendResult(success=False, error="No access token")

        try:
            await self._stream_card_content(
                message_id, token, content, finalize=finalize,
            )
            if finalize:
                # Remove from streaming-cards tracking and fire Done.  This
                # is the canonical "response ended" signal from stream
                # consumer's final edit.
                self._streaming_cards.get(chat_id, {}).pop(message_id, None)
                if not self._streaming_cards.get(chat_id):
                    self._streaming_cards.pop(chat_id, None)
                logger.debug(
                    "[%s] AI Card finalized (edit): %s",
                    self.name, message_id,
                )
                self._fire_done_reaction(chat_id)
            else:
                # Non-final edit reopens the card into streaming state —
                # track it so the next send() can auto-close it as a
                # sibling.
                self._streaming_cards.setdefault(chat_id, {})[message_id] = content
            return SendResult(success=True, message_id=message_id)
        except Exception as e:
            logger.warning("[%s] Card edit failed: %s", self.name, e)
            return SendResult(success=False, error=str(e))

    async def _stream_card_content(
        self,
        out_track_id: str,
        token: str,
        content: str,
        finalize: bool = False,
    ) -> None:
        """Stream content to an existing AI Card."""
        if not self._card_sdk:
            logger.warning("[%s] Card SDK not initialized, skipping stream update", self.name)
            return
        stream_request = dingtalk_card_models.StreamingUpdateRequest(
            out_track_id=out_track_id,
            guid=str(uuid.uuid4()),
            key="content",
            content=content[: self.MAX_MESSAGE_LENGTH],
            is_full=True,
            is_finalize=finalize,
            is_error=False,
        )

        stream_headers = dingtalk_card_models.StreamingUpdateHeaders(
            x_acs_dingtalk_access_token=token,
        )

        runtime = tea_util_models.RuntimeOptions()
        await self._card_sdk.streaming_update_with_options_async(
            stream_request, stream_headers, runtime
        )

    async def _get_access_token(self) -> Optional[str]:
        """Get access token using SDK's cached token."""
        if not self._stream_client:
            return None
        try:
            # SDK's get_access_token is sync and uses requests
            token = await asyncio.to_thread(self._stream_client.get_access_token)
            return token
        except Exception as e:
            logger.error("[%s] Failed to get access token: %s", self.name, e)
            return None

    async def _send_emotion(
        self,
        open_msg_id: str,
        open_conversation_id: str,
        emoji_name: str,
        *,
        recall: bool = False,
    ) -> None:
        """Add or recall an emoji reaction on a message."""
        if not self._robot_sdk or not open_msg_id or not open_conversation_id:
            return
        action = "recall" if recall else "reply"
        try:
            token = await self._get_access_token()
            if not token:
                return

            emotion_kwargs = {
                "robot_code": self._robot_code,
                "open_msg_id": open_msg_id,
                "open_conversation_id": open_conversation_id,
                "emotion_type": 2,
                "emotion_name": emoji_name,
            }
            runtime = tea_util_models.RuntimeOptions()

            if recall:
                emotion_kwargs["text_emotion"] = (
                    dingtalk_robot_models.RobotRecallEmotionRequestTextEmotion(
                        emotion_id="2659900",
                        emotion_name=emoji_name,
                        text=emoji_name,
                        background_id="im_bg_1",
                    )
                )
                request = dingtalk_robot_models.RobotRecallEmotionRequest(
                    **emotion_kwargs,
                )
                sdk_headers = dingtalk_robot_models.RobotRecallEmotionHeaders(
                    x_acs_dingtalk_access_token=token,
                )
                await self._robot_sdk.robot_recall_emotion_with_options_async(
                    request, sdk_headers, runtime
                )
            else:
                emotion_kwargs["text_emotion"] = (
                    dingtalk_robot_models.RobotReplyEmotionRequestTextEmotion(
                        emotion_id="2659900",
                        emotion_name=emoji_name,
                        text=emoji_name,
                        background_id="im_bg_1",
                    )
                )
                request = dingtalk_robot_models.RobotReplyEmotionRequest(
                    **emotion_kwargs,
                )
                sdk_headers = dingtalk_robot_models.RobotReplyEmotionHeaders(
                    x_acs_dingtalk_access_token=token,
                )
                await self._robot_sdk.robot_reply_emotion_with_options_async(
                    request, sdk_headers, runtime
                )
            logger.info(
                "[%s] _send_emotion: %s %s on msg=%s",
                self.name, action, emoji_name, open_msg_id[:24],
            )
        except Exception:
            logger.debug(
                "[%s] _send_emotion %s failed", self.name, action, exc_info=True
            )

    async def _resolve_media_codes(self, message: "ChatbotMessage") -> None:
        """Resolve download codes in message to actual URLs."""
        token = await self._get_access_token()
        if not token:
            return

        robot_code = getattr(message, "robot_code", None) or self._client_id
        codes_to_resolve = []

        # Collect codes and references to update
        # 1. Single image content
        img_content = getattr(message, "image_content", None)
        if img_content and getattr(img_content, "download_code", None):
            codes_to_resolve.append((img_content, "download_code"))

        # 2. Rich text list
        rich_text = getattr(message, "rich_text_content", None)
        if rich_text:
            rich_list = getattr(rich_text, "rich_text_list", []) or []
            for item in rich_list:
                if isinstance(item, dict):
                    for key in ("downloadCode", "pictureDownloadCode", "download_code"):
                        if item.get(key):
                            codes_to_resolve.append((item, key))

        # 3. File / audio / video / voice content
        for attr_name in ("file_content", "audio_content", "video_content", "voice_content"):
            content_obj = getattr(message, attr_name, None)
            if content_obj:
                for key in ("downloadCode", "download_code"):
                    code = getattr(content_obj, key, None) if hasattr(content_obj, key) else content_obj.get(key) if isinstance(content_obj, dict) else None
                    if code:
                        codes_to_resolve.append((content_obj, key))
                        break

        if not codes_to_resolve:
            return

        # Resolve all codes in parallel
        tasks = []
        for obj, key in codes_to_resolve:
            code = getattr(obj, key, None) if hasattr(obj, key) else obj.get(key)
            if code:
                tasks.append(
                    self._fetch_download_url(code, robot_code, token, obj, key)
                )

        await asyncio.gather(*tasks, return_exceptions=True)

        # Convert file-like download URLs into local cached files so downstream
        # document handling receives a real filesystem path instead of a remote URL.
        for attr_name, media_kind in (
            ("file_content", "file"),
            ("audio_content", "audio"),
            ("video_content", "video"),
            ("voice_content", "voice"),
        ):
            content_obj = getattr(message, attr_name, None)
            if not content_obj:
                continue
            current_value = getattr(content_obj, "download_code", None) or (
                content_obj.get("downloadCode") if isinstance(content_obj, dict) else None
            )
            if not current_value or not str(current_value).startswith("http"):
                continue

            original_name = (
                getattr(content_obj, "filename", None)
                or getattr(content_obj, "file_name", None)
                or (content_obj.get("filename") if isinstance(content_obj, dict) else None)
                or (content_obj.get("fileName") if isinstance(content_obj, dict) else None)
            )
            local_path = await self._download_media_file(
                current_value,
                token,
                file_name=original_name or "",
                media_kind=media_kind,
            )
            if local_path:
                if hasattr(content_obj, "download_code"):
                    content_obj.download_code = local_path
                elif isinstance(content_obj, dict):
                    content_obj["downloadCode"] = local_path

    async def _fetch_download_url(
        self, code: str, robot_code: str, token: str, obj, key: str
    ) -> Optional[str]:
        """Fetch download URL for a single code via direct DingTalk Open API.

        Uses the DingTalk robot message file download endpoint directly with
        httpx, avoiding the alibabacloud_dingtalk SDK dependency.

        Returns the download URL on success, None on failure.
        """
        if not HTTPX_AVAILABLE or not self._http_client:
            logger.warning("[%s] httpx not available, cannot resolve media code", self.name)
            return None
        try:
            # DingTalk Open API: get file download URL
            # Endpoint: https://api.dingtalk.com/v1.0/robot/messageFiles/download
            # Note: the "getFileDownloadUrl" path is wrong (404); the correct one is "messageFiles/download"
            endpoint = "https://api.dingtalk.com/v1.0/robot/messageFiles/download"
            headers = {
                "Content-Type": "application/json",
                "x-acs-dingtalk-access-token": token,
            }
            payload = {
                "downloadCode": code,
                "robotCode": robot_code,
            }
            response = await self._http_client.post(endpoint, json=payload, headers=headers, timeout=15.0)
            response.raise_for_status()
            result = response.json()
            url = result.get("data", {}).get("downloadUrl") or result.get("downloadUrl")
            if url:
                # Store URL back into the object so callers can read it
                if hasattr(obj, key):
                    setattr(obj, key, url)
                elif isinstance(obj, dict):
                    obj[key] = url
                return url
            logger.warning(
                "[%s] Failed to get download URL: empty response for code %s, body=%s",
                self.name, code, str(result)[:200],
            )
        except Exception as e:
            logger.error("[%s] Error resolving media code %s: %s", self.name, code, e)
        return None

    async def _download_media_file(
        self,
        download_url: str,
        token: str,
        file_extension: str = "",
        file_name: str = "",
        media_kind: str = "file",
    ) -> Optional[str]:
        """Download a DingTalk media file to a local cached path.

        Args:
            download_url: The CDN URL returned by the DingTalk file download API.
            token: The current DingTalk access token (used as Bearer auth).
            file_extension: Optional file extension hint (e.g. '.pdf', '.mp4').
            file_name: Original human-readable filename when available.
            media_kind: file/audio/video/voice.

        Returns:
            Local file path on success, None on failure.
        """
        if not HTTPX_AVAILABLE or not self._http_client:
            logger.warning("[%s] httpx not available, cannot download media file", self.name)
            return None

        try:
            headers = {
                "Authorization": f"Bearer {token}",
            }
            response = await self._http_client.get(download_url, headers=headers, timeout=30.0)
            response.raise_for_status()
            raw_bytes = await response.aread()

            ext = (
                file_extension
                or self._guess_extension_from_filename(file_name)
                or self._guess_extension_from_url(download_url)
            )
            safe_name = Path(file_name).name if file_name else ""
            if not safe_name:
                default_base = "audio" if media_kind in ("audio", "voice") else "download"
                safe_name = f"{default_base}{ext or '.bin'}"
            elif not Path(safe_name).suffix and ext:
                safe_name = f"{safe_name}{ext}"

            suffix = f"_{safe_name}" if safe_name else (Path(safe_name).suffix or ext or ".bin")
            with tempfile.NamedTemporaryFile(
                prefix="dingtalk_",
                suffix=suffix,
                delete=False,
            ) as tmp:
                tmp.write(raw_bytes)
                local_path = tmp.name

            logger.info("[%s] Downloaded media to: %s", self.name, local_path)
            return local_path
        except Exception as e:
            logger.error("[%s] Failed to download media from %s: %s", self.name, download_url, e)
            return None

    @staticmethod
    def _guess_extension_from_filename(filename: str) -> str:
        """Guess file extension from original filename."""
        if not filename:
            return ""
        return Path(filename).suffix.lower()

    @staticmethod
    def _guess_extension_from_url(url: str) -> str:
        """Guess file extension from URL path."""
        path = url.split("?")[0]
        _, ext = os.path.splitext(path)
        # Normalize common extensions
        ext_map = {
            ".jpg": ".jpg",
            ".jpeg": ".jpg",
            ".png": ".png",
            ".gif": ".gif",
            ".mp4": ".mp4",
            ".mp3": ".mp3",
            ".wav": ".wav",
            ".pdf": ".pdf",
            ".doc": ".doc",
            ".docx": ".docx",
            ".xls": ".xls",
            ".xlsx": ".xlsx",
            ".ppt": ".ppt",
            ".pptx": ".pptx",
            ".txt": ".txt",
        }
        return ext_map.get(ext.lower(), ".bin")

    @staticmethod
    def _infer_media_type(content_obj, default_kind: str = "file") -> str:
        """Infer a concrete media type from filename/path instead of generic labels."""
        name = (
            getattr(content_obj, "filename", None)
            or getattr(content_obj, "file_name", None)
            or getattr(content_obj, "download_code", None)
        )
        if isinstance(content_obj, dict):
            name = (
                name
                or content_obj.get("filename")
                or content_obj.get("fileName")
                or content_obj.get("downloadCode")
            )
        guessed, _ = mimetypes.guess_type(str(name or ""))
        if guessed:
            return guessed
        if default_kind in ("audio", "voice"):
            return "audio/ogg"
        if default_kind == "video":
            return "video/mp4"
        return "application/octet-stream"

    @staticmethod
    def _normalize_markdown(text: str) -> str:
        """Normalize markdown for DingTalk's parser.

        DingTalk's markdown renderer has quirks:
        - Numbered lists need blank line before them
        - Indented code blocks may render incorrectly
        """
        lines = text.split("\n")
        out = []
        for i, line in enumerate(lines):
            # Ensure blank line before numbered list items
            is_numbered = re.match(r"^\d+\.\s", line.strip())
            if is_numbered and i > 0:
                prev = lines[i - 1]
                if prev.strip() and not re.match(r"^\d+\.\s", prev.strip()):
                    out.append("")
            # Dedent fenced code blocks
            if line.strip().startswith("```") and line != line.lstrip():
                indent = len(line) - len(line.lstrip())
                line = line[indent:]
            out.append(line)
        return "\n".join(out)


# ---------------------------------------------------------------------------
# Internal stream handler
# ---------------------------------------------------------------------------


class _IncomingHandler(
    dingtalk_stream.ChatbotHandler if DINGTALK_STREAM_AVAILABLE else object
):
    """dingtalk-stream ChatbotHandler that forwards messages to the adapter.

    SDK >= 0.20 changed process() from sync to async, and the message
    parameter from ChatbotMessage to CallbackMessage. We parse the
    CallbackMessage.data dict into a ChatbotMessage before forwarding.
    """

    def __init__(self, adapter: DingTalkAdapter, loop: Optional[asyncio.AbstractEventLoop] = None):
        if DINGTALK_STREAM_AVAILABLE:
            super().__init__()
        self._adapter = adapter
        self._loop = loop

    async def process(self, message: "CallbackMessage"):
        """Called by dingtalk-stream (>=0.20) when a message arrives.

        dingtalk-stream >= 0.24 passes a CallbackMessage whose ``.data`` contains
        the chatbot payload. Convert it to ChatbotMessage via
        ``ChatbotMessage.from_dict()``.

        Message processing is dispatched as a background task so that this
        method returns the ACK immediately — blocking here would prevent the
        SDK from sending heartbeats, eventually causing a disconnect.
        """
        try:
            # CallbackMessage.data is a dict containing the raw DingTalk payload
            data = message.data
            if isinstance(data, str):
                data = json.loads(data)

            if isinstance(data, dict):
                raw_msgtype = data.get("msgtype") or data.get("msgType") or ""
                raw_content = data.get("content")
                logger.info(
                    "[%s] Raw callback: msgtype=%s msgId=%s conv=%s top_keys=%s content_keys=%s",
                    self._adapter.name,
                    raw_msgtype,
                    data.get("msgId") or data.get("messageId") or "",
                    data.get("conversationId") or "",
                    sorted(list(data.keys()))[:20],
                    sorted(list(raw_content.keys()))[:20] if isinstance(raw_content, dict) else type(raw_content).__name__,
                )

            # Parse dict into ChatbotMessage using SDK's from_dict
            chatbot_msg = ChatbotMessage.from_dict(data)

            # Preserve original filenames from raw content for file-like messages.
            # The SDK maps `filename` but DingTalk callbacks often send `fileName`.
            if isinstance(data, dict):
                raw_content = data.get("content") if isinstance(data.get("content"), dict) else {}
                raw_filename = raw_content.get("fileName") or raw_content.get("filename")
                raw_msgtype = data.get("msgtype") or data.get("msgType") or ""
                content_attr = {
                    "file": "file_content",
                    "audio": "audio_content",
                    "video": "video_content",
                    "voice": "voice_content",
                }.get(str(raw_msgtype))
                if content_attr and raw_filename:
                    content_obj = getattr(chatbot_msg, content_attr, None)
                    if content_obj is not None and not getattr(content_obj, "filename", None):
                        try:
                            content_obj.filename = raw_filename
                        except Exception:
                            pass

            # Ensure session_webhook is populated even if the SDK's
            # from_dict() did not map it (field name mismatch across
            # SDK versions).
            if not getattr(chatbot_msg, "session_webhook", None):
                webhook = (
                    data.get("sessionWebhook")
                    or data.get("session_webhook")
                    or ""
                ) if isinstance(data, dict) else ""
                if webhook:
                    chatbot_msg.session_webhook = webhook

            # Ensure is_in_at_list is populated from the structured callback
            # flag even if from_dict() did not map it.  DingTalk sends
            # ``isInAtList`` in the raw payload; the adapter's mention check
            # reads the ChatbotMessage attribute ``is_in_at_list``.
            if not getattr(chatbot_msg, "is_in_at_list", False):
                raw_flag = (
                    data.get("isInAtList") if isinstance(data, dict) else False
                )
                if raw_flag:
                    chatbot_msg.is_in_at_list = True

            msg_id = getattr(chatbot_msg, "message_id", None) or ""
            conversation_id = getattr(chatbot_msg, "conversation_id", None) or ""

            # Thinking reaction — fire-and-forget, tracked
            if msg_id and conversation_id:
                self._adapter._spawn_bg(
                    self._adapter._send_emotion(
                        msg_id, conversation_id, "🤔Thinking", recall=False,
                    )
                )

            # Fire-and-forget: return ACK immediately, process in background.
            # Blocking here would prevent the SDK from sending heartbeats,
            # eventually causing a disconnect.  _on_message is wrapped so
            # exceptions inside the task surface in logs instead of
            # disappearing into the event loop.
            asyncio.create_task(self._safe_on_message(chatbot_msg))
        except Exception:
            logger.exception(
                "[%s] Error preparing incoming message", self._adapter.name
            )
            return AckMessage.STATUS_SYSTEM_EXCEPTION, "error"

        return AckMessage.STATUS_OK, "OK"

    async def _safe_on_message(self, chatbot_msg: "ChatbotMessage") -> None:
        """Wrapper that catches exceptions from _on_message."""
        try:
            await self._adapter._on_message(chatbot_msg)
        except Exception:
            logger.exception(
                "[%s] Error processing incoming message", self._adapter.name
            )
