"""Microsoft Graph webhook adapter for Teams meeting pipeline events."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, Optional

try:
    from aiohttp import web

    AIOHTTP_AVAILABLE = True
except ImportError:
    AIOHTTP_AVAILABLE = False
    web = None  # type: ignore[assignment]

from gateway.config import Platform, PlatformConfig
from gateway.platforms.base import BasePlatformAdapter, MessageEvent, MessageType, SendResult
from tools.teams_pipeline_store import TeamsPipelineStore, resolve_teams_pipeline_store_path

logger = logging.getLogger(__name__)

DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 8646
DEFAULT_WEBHOOK_PATH = "/msgraph/webhook"
NotificationScheduler = Callable[[Dict[str, Any], MessageEvent], Awaitable[None] | None]


def check_msgraph_webhook_requirements() -> bool:
    """Check if Microsoft Graph webhook adapter dependencies are available."""
    return AIOHTTP_AVAILABLE


class MSGraphWebhookAdapter(BasePlatformAdapter):
    """Receive Microsoft Graph change notifications for the Teams pipeline."""

    def __init__(self, config: PlatformConfig):
        super().__init__(config, Platform.MSGRAPH_WEBHOOK)
        extra = config.extra or {}
        self._host: str = extra.get("host", DEFAULT_HOST)
        self._port: int = int(extra.get("port", DEFAULT_PORT))
        self._webhook_path: str = self._normalize_path(
            extra.get("webhook_path", DEFAULT_WEBHOOK_PATH)
        )
        self._health_path: str = self._normalize_path(extra.get("health_path", "/health"))
        self._accepted_resources: list[str] = [
            str(value).strip()
            for value in (extra.get("accepted_resources") or [])
            if str(value).strip()
        ]
        store_path = resolve_teams_pipeline_store_path(platform_extra=extra)
        self.store = TeamsPipelineStore(store_path)
        self._client_state: Optional[str] = self._string_or_none(extra.get("client_state"))
        self._runner = None
        self._notification_scheduler: Optional[NotificationScheduler] = None

    @staticmethod
    def _string_or_none(value: Any) -> Optional[str]:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    @staticmethod
    def _normalize_path(path: str) -> str:
        raw = str(path or "").strip() or "/"
        return raw if raw.startswith("/") else f"/{raw}"

    def set_notification_scheduler(self, scheduler: Optional[NotificationScheduler]) -> None:
        self._notification_scheduler = scheduler

    async def connect(self) -> bool:
        app = web.Application()
        app.router.add_get(self._health_path, self._handle_health)
        app.router.add_get(self._webhook_path, self._handle_notification)
        app.router.add_post(self._webhook_path, self._handle_notification)

        import socket as _socket

        try:
            with _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM) as sock:
                sock.settimeout(1)
                sock.connect(("127.0.0.1", self._port))
            logger.error(
                "[msgraph_webhook] Port %d already in use. Set a different port in config.yaml: platforms.msgraph_webhook.port",
                self._port,
            )
            return False
        except (ConnectionRefusedError, OSError):
            pass

        self._runner = web.AppRunner(app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, self._host, self._port)
        await site.start()
        self._mark_connected()
        logger.info(
            "[msgraph_webhook] Listening on %s:%d%s",
            self._host,
            self._port,
            self._webhook_path,
        )
        return True

    async def disconnect(self) -> None:
        if self._runner:
            await self._runner.cleanup()
            self._runner = None
        self._mark_disconnected()

    async def send(
        self,
        chat_id: str,
        content: str,
        reply_to: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> SendResult:
        logger.info("[msgraph_webhook] Response for %s: %s", chat_id, content[:200])
        return SendResult(success=True)

    async def get_chat_info(self, chat_id: str) -> Dict[str, Any]:
        return {"name": chat_id, "type": "webhook"}

    async def _handle_health(self, request: "web.Request") -> "web.Response":
        return web.json_response(
            {
                "status": "ok",
                "platform": self.platform.value,
                "webhook_path": self._webhook_path,
                "store": self.store.stats(),
            }
        )

    async def _handle_notification(self, request: "web.Request") -> "web.Response":
        validation_token = request.query.get("validationToken", "")
        if validation_token:
            return web.Response(
                text=validation_token,
                content_type="text/plain",
            )

        try:
            body = await request.json()
        except Exception:
            return web.json_response({"error": "Invalid JSON body"}, status=400)

        notifications = body.get("value")
        if not isinstance(notifications, list):
            return web.json_response({"error": "Missing notification batch"}, status=400)

        accepted = 0
        duplicates = 0
        rejected = 0
        scheduled = 0

        for raw_notification in notifications:
            if not isinstance(raw_notification, dict):
                rejected += 1
                continue
            notification = dict(raw_notification)
            if not self._resource_accepted(str(notification.get("resource") or "")):
                rejected += 1
                continue
            if not self._verify_client_state(notification):
                rejected += 1
                continue

            receipt_key = TeamsPipelineStore.build_notification_receipt_key(notification)
            if not self.store.record_notification_receipt(receipt_key, notification):
                duplicates += 1
                continue

            accepted += 1
            scheduled += 1
            self.store.record_event_timestamp(
                receipt_key,
                str(notification.get("subscriptionExpirationDateTime") or self._utc_now()),
            )
            event = self._build_message_event(notification, receipt_key)
            self._schedule_notification(notification, event)

        status = 202 if accepted or duplicates else 403
        return web.json_response(
            {
                "status": "accepted" if accepted or duplicates else "rejected",
                "accepted": accepted,
                "duplicates": duplicates,
                "rejected": rejected,
                "scheduled": scheduled,
            },
            status=status,
        )

    def _resource_accepted(self, resource: str) -> bool:
        if not self._accepted_resources:
            return True
        for pattern in self._accepted_resources:
            if pattern.endswith("*") and resource.startswith(pattern[:-1]):
                return True
            if resource == pattern or resource.startswith(f"{pattern}/"):
                return True
        return False

    def _verify_client_state(self, notification: Dict[str, Any]) -> bool:
        provided = self._string_or_none(notification.get("clientState"))
        subscription_id = self._string_or_none(notification.get("subscriptionId"))
        expected = None
        if subscription_id:
            subscription = self.store.get_subscription(subscription_id) or {}
            expected = self._string_or_none(subscription.get("client_state"))
        if expected is None:
            expected = self._client_state
        if expected is None:
            return True
        return provided == expected

    def _build_message_event(
        self,
        notification: Dict[str, Any],
        receipt_key: str,
    ) -> MessageEvent:
        source = self.build_source(
            chat_id=f"msgraph:{notification.get('subscriptionId', 'unknown')}",
            chat_name="msgraph/webhook",
            chat_type="webhook",
            user_id="msgraph",
            user_name="Microsoft Graph",
        )
        text = self._render_prompt(notification)
        return MessageEvent(
            text=text,
            message_type=MessageType.TEXT,
            source=source,
            raw_message=notification,
            message_id=receipt_key,
            internal=True,
        )

    def _render_prompt(self, notification: Dict[str, Any]) -> str:
        template = self.config.extra.get("prompt", "")
        if template:
            payload = {
                "notification": notification,
                "resource": notification.get("resource", ""),
                "change_type": notification.get("changeType", ""),
                "subscription_id": notification.get("subscriptionId", ""),
            }
            return self._render_template(template, payload)
        rendered = json.dumps(notification, indent=2, sort_keys=True)[:4000]
        return f"Microsoft Graph change notification:\n\n```json\n{rendered}\n```"

    def _render_template(self, template: str, payload: Dict[str, Any]) -> str:
        import re

        def _resolve(match: "re.Match[str]") -> str:
            key = match.group(1)
            value: Any = payload
            for part in key.split("."):
                if isinstance(value, dict):
                    value = value.get(part, f"{{{key}}}")
                else:
                    return f"{{{key}}}"
            if isinstance(value, (dict, list)):
                return json.dumps(value, sort_keys=True)[:2000]
            return str(value)

        return re.sub(r"\{([a-zA-Z0-9_.]+)\}", _resolve, template)

    def _schedule_notification(
        self,
        notification: Dict[str, Any],
        event: MessageEvent,
    ) -> None:
        scheduler = self._notification_scheduler
        if scheduler is not None:
            result = scheduler(notification, event)
            if asyncio.iscoroutine(result):
                task = asyncio.create_task(result)
                self._background_tasks.add(task)
                task.add_done_callback(self._background_tasks.discard)
            return

        task = asyncio.create_task(self.handle_message(event))
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

    @staticmethod
    def _utc_now() -> str:
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
