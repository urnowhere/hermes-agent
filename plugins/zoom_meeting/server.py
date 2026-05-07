"""A small aiohttp webhook receiver for Zoom meeting events."""

from __future__ import annotations

import asyncio
import hmac
import json
import logging
from typing import Optional

try:
    from aiohttp import web

    AIOHTTP_AVAILABLE = True
except ImportError:  # pragma: no cover - import guard
    web = None  # type: ignore[assignment]
    AIOHTTP_AVAILABLE = False

from plugins.zoom_meeting.store import ZoomMeetingStore, compute_webhook_validation_token

logger = logging.getLogger(__name__)


def _safe_equal(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))


class ZoomWebhookServer:
    def __init__(
        self,
        store: ZoomMeetingStore,
        *,
        secret_token: str = "",
        host: str = "0.0.0.0",
        port: int = 8754,
        path: str = "/zoom/webhook",
    ):
        self.store = store
        self.secret_token = secret_token
        self.host = host
        self.port = port
        self.path = path
        self._runner: Optional["web.AppRunner"] = None

    def _verify_signature(self, timestamp: str, signature: str, body: bytes) -> bool:
        if not self.secret_token:
            return True
        if not timestamp or not signature:
            return False
        expected = compute_webhook_validation_token(self.secret_token, f"v0:{timestamp}:{body.decode('utf-8')}")
        actual = signature.split("=", 1)[-1]
        return _safe_equal(expected, actual)

    async def _handle_health(self, request: "web.Request") -> "web.Response":
        return web.json_response({"status": "ok"})

    async def _handle_webhook(self, request: "web.Request") -> "web.Response":
        raw_body = await request.read()
        try:
            payload = json.loads(raw_body.decode("utf-8") or "{}")
        except Exception:
            return web.json_response({"ok": False, "error": "invalid JSON"}, status=400)

        timestamp = request.headers.get("x-zm-request-timestamp", "")
        signature = request.headers.get("x-zm-signature", "")
        if self.secret_token and not self._verify_signature(timestamp, signature, raw_body):
            return web.json_response({"ok": False, "error": "invalid signature"}, status=401)

        event_name = str(payload.get("event") or payload.get("event_type") or payload.get("type") or "")
        if event_name == "endpoint.url_validation":
            plain_token = (
                payload.get("payload", {}).get("plainToken")
                or payload.get("payload", {}).get("plain_token")
                or payload.get("plainToken")
                or ""
            )
            if not plain_token:
                return web.json_response({"ok": False, "error": "missing plainToken"}, status=400)
            return web.json_response(
                {
                    "plainToken": plain_token,
                    "encryptedToken": compute_webhook_validation_token(self.secret_token, plain_token),
                }
            )

        normalized = self.store.ingest_event(payload)
        return web.json_response(
            {
                "ok": True,
                "meeting_id": normalized.get("meeting_id"),
                "event": normalized.get("event"),
                "transcript_entries": len(normalized.get("transcript_entries") or []),
            }
        )

    async def start(self) -> None:
        if not AIOHTTP_AVAILABLE:
            raise RuntimeError("aiohttp not installed. Run: pip install aiohttp")
        app = web.Application()
        app.router.add_get("/health", self._handle_health)
        app.router.add_post(self.path, self._handle_webhook)
        self._runner = web.AppRunner(app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, self.host, self.port)
        await site.start()
        logger.info("[zoom_meeting] webhook server listening on %s:%d%s", self.host, self.port, self.path)

    async def stop(self) -> None:
        if self._runner:
            await self._runner.cleanup()
            self._runner = None

    def serve_forever(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(self.start())
        try:
            loop.run_forever()
        finally:  # pragma: no cover - shutdown path
            loop.run_until_complete(self.stop())
            loop.close()
