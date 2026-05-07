"""Thin Telegram Bot API client for Managed Bots endpoints.

PTB 22.7 (the version pinned by hermes-agent at the time of writing) does
NOT yet expose the Bot API 9.5/9.6 ``getManagedBotToken`` /
``replaceManagedBotToken`` methods or the ``managed_bot`` update payload.
We call them via raw HTTP.  This client is deliberately small — it only
covers what the fleet coordinator and tools need.

All calls return parsed JSON.  Errors raise :class:`BotApiError`.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, Optional

import httpx

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://api.telegram.org"
DEFAULT_TIMEOUT = 15.0


class BotApiError(Exception):
    """Wraps a Telegram Bot API failure."""

    def __init__(self, method: str, code: Optional[int], description: str):
        super().__init__(f"{method}: {description} (code={code})")
        self.method = method
        self.code = code
        self.description = description


@dataclass
class ManagedBotInfo:
    """Result of a successful ``getManagedBotToken`` call."""

    token: str
    bot_id: int
    bot_username: str


class FleetApiClient:
    """Bot API client scoped to a single manager-bot token."""

    def __init__(
        self,
        manager_token: str,
        *,
        base_url: str = DEFAULT_BASE_URL,
        timeout: float = DEFAULT_TIMEOUT,
        client: Optional[httpx.Client] = None,
    ):
        if not manager_token or ":" not in manager_token:
            raise ValueError("manager_token must be a valid bot token (e.g. '12345:ABC...')")
        self._token = manager_token
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._client = client  # injectable for tests

    def _http(self) -> httpx.Client:
        return self._client or httpx.Client(timeout=self._timeout)

    def _post(self, method: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        url = f"{self._base_url}/bot{self._token}/{method}"
        client = self._http()
        try:
            resp = client.post(url, json=payload)
        finally:
            if self._client is None:
                client.close()
        try:
            data = resp.json()
        except ValueError as e:
            raise BotApiError(method, resp.status_code, f"non-JSON response: {e}") from e
        if not isinstance(data, dict):
            raise BotApiError(method, resp.status_code, f"unexpected response type {type(data).__name__}")
        if not data.get("ok"):
            raise BotApiError(
                method,
                data.get("error_code") or resp.status_code,
                str(data.get("description", "unknown error")),
            )
        return data

    # ── Manager-mode introspection ────────────────────────────────────

    def get_me(self) -> Dict[str, Any]:
        """Return the manager bot's identity (``getMe``)."""
        return self._post("getMe", {}).get("result", {}) or {}

    def can_manage_bots(self) -> bool:
        """Return True if the bot has Manager Mode enabled in @BotFather."""
        me = self.get_me()
        return bool(me.get("can_manage_bots"))

    # ── Managed Bots (Bot API 9.5/9.6) ────────────────────────────────

    def get_managed_bot_token(self, user_id: int) -> ManagedBotInfo:
        """Return token+identity for the child bot owned by *user_id*.

        Mirrors the API method ``getManagedBotToken``.  Telegram returns the
        full bot token plus the child bot's ``User`` object.
        """
        data = self._post("getManagedBotToken", {"user_id": int(user_id)})
        result = data.get("result") or {}
        token = result.get("token")
        bot = result.get("bot") or {}
        if not token or not isinstance(bot, dict):
            raise BotApiError(
                "getManagedBotToken",
                None,
                f"malformed result payload: {result!r}",
            )
        return ManagedBotInfo(
            token=str(token),
            bot_id=int(bot.get("id") or 0),
            bot_username=str(bot.get("username") or ""),
        )

    def replace_managed_bot_token(self, user_id: int) -> ManagedBotInfo:
        """Rotate the token for a managed bot.  Mirrors ``replaceManagedBotToken``."""
        data = self._post("replaceManagedBotToken", {"user_id": int(user_id)})
        result = data.get("result") or {}
        token = result.get("token")
        bot = result.get("bot") or {}
        if not token or not isinstance(bot, dict):
            raise BotApiError(
                "replaceManagedBotToken",
                None,
                f"malformed result payload: {result!r}",
            )
        return ManagedBotInfo(
            token=str(token),
            bot_id=int(bot.get("id") or 0),
            bot_username=str(bot.get("username") or ""),
        )

    # ── Update stream (manager bot polling) ────────────────────────────

    def get_updates(
        self,
        *,
        offset: Optional[int] = None,
        timeout: int = 0,
        allowed_updates: Optional[list] = None,
    ) -> list:
        """Long-poll ``getUpdates`` and return the raw ``Update`` list.

        Used by the manager bot to surface ``managed_bot`` events when a
        user taps a spawn deep link.  ``timeout`` 0 means non-blocking.
        """
        payload: Dict[str, Any] = {}
        if offset is not None:
            payload["offset"] = int(offset)
        if timeout:
            payload["timeout"] = int(timeout)
        if allowed_updates is not None:
            payload["allowed_updates"] = list(allowed_updates)
        data = self._post("getUpdates", payload)
        result = data.get("result")
        if not isinstance(result, list):
            raise BotApiError(
                "getUpdates",
                None,
                f"unexpected result type {type(result).__name__}",
            )
        return result

    def drain_managed_bot_events(
        self, *, offset: Optional[int] = None, timeout: int = 0
    ):
        """Drain pending ``managed_bot`` updates → resolved ``ManagedBotInfo``.

        Returns ``(infos, next_offset)`` — the list of newly-confirmed
        managed bots and the update_id to pass back as ``offset`` on the
        next call (so we don't re-process the same updates).
        """
        updates = self.get_updates(
            offset=offset, timeout=timeout, allowed_updates=["managed_bot"]
        )
        infos: list = []
        next_offset = offset
        for upd in updates:
            if not isinstance(upd, dict):
                continue
            uid = upd.get("update_id")
            if isinstance(uid, int):
                next_offset = uid + 1
            mb = upd.get("managed_bot")
            if not isinstance(mb, dict):
                continue
            bot = mb.get("bot") or {}
            bot_id = int(bot.get("id") or 0)
            if not bot_id:
                continue
            try:
                infos.append(self.get_managed_bot_token(bot_id))
            except BotApiError as e:
                logger.warning(
                    "could not resolve token for managed bot id=%s: %s",
                    bot_id, e,
                )
        return infos, next_offset

    # ── Child-bot helpers (sending as a fleet member) ────────────────

    def send_message_as(
        self,
        child_token: str,
        chat_id: str,
        text: str,
        *,
        reply_to: Optional[int] = None,
        parse_mode: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Send *text* as the child bot identified by *child_token*."""
        if not child_token or ":" not in child_token:
            raise ValueError("child_token must be a valid bot token")
        payload: Dict[str, Any] = {"chat_id": chat_id, "text": text}
        if reply_to is not None:
            payload["reply_to_message_id"] = int(reply_to)
        if parse_mode:
            payload["parse_mode"] = parse_mode
        url = f"{self._base_url}/bot{child_token}/sendMessage"
        client = self._http()
        try:
            resp = client.post(url, json=payload)
        finally:
            if self._client is None:
                client.close()
        try:
            data = resp.json()
        except ValueError as e:
            raise BotApiError("sendMessage", resp.status_code, f"non-JSON response: {e}") from e
        if not isinstance(data, dict) or not data.get("ok"):
            raise BotApiError(
                "sendMessage",
                (data or {}).get("error_code") or resp.status_code,
                str((data or {}).get("description", "unknown error")),
            )
        return data.get("result") or {}


def build_managed_bot_deep_link(
    manager_username: str,
    suggested_username: str,
    *,
    name: Optional[str] = None,
) -> str:
    """Build the ``t.me/newbot/...`` deep link a user taps to confirm spawn.

    Telegram opens a pre-filled bot-creation dialog when the user opens this
    link.  See https://core.telegram.org/bots/features#managed-bots .
    """
    manager = manager_username.lstrip("@")
    suggested = suggested_username.lstrip("@")
    base = f"https://t.me/newbot/{manager}/{suggested}"
    if name:
        # Telegram accepts the display name as a query parameter; URL-quote it.
        from urllib.parse import quote

        return f"{base}?name={quote(name, safe='')}"
    return base
