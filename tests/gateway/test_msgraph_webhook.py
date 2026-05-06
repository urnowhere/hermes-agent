"""Tests for the Microsoft Graph webhook adapter."""

import asyncio
import json

import pytest

from gateway.config import GatewayConfig, Platform, PlatformConfig
from gateway.platforms.msgraph_webhook import MSGraphWebhookAdapter


def _make_adapter(tmp_path, **extra_overrides) -> MSGraphWebhookAdapter:
    extra = {
        "host": "0.0.0.0",
        "port": 0,
        "store_path": str(tmp_path / "teams-pipeline-store.json"),
        "client_state": "expected-client-state",
        "accepted_resources": ["communications/onlineMeetings"],
    }
    extra.update(extra_overrides)
    return MSGraphWebhookAdapter(PlatformConfig(enabled=True, extra=extra))


class _FakeRequest:
    def __init__(self, *, query=None, json_payload=None):
        self.query = query or {}
        self._json_payload = json_payload

    async def json(self):
        if isinstance(self._json_payload, Exception):
            raise self._json_payload
        return self._json_payload


class TestMSGraphValidationHandshake:
    def test_gateway_config_accepts_msgraph_webhook_platform(self):
        config = GatewayConfig.from_dict(
            {
                "platforms": {
                    "msgraph_webhook": {
                        "enabled": True,
                        "extra": {"client_state": "expected"},
                    }
                }
            }
        )

        assert Platform.MSGRAPH_WEBHOOK in config.platforms
        assert Platform.MSGRAPH_WEBHOOK in config.get_connected_platforms()

    @pytest.mark.anyio
    async def test_validation_token_echo(self, tmp_path):
        adapter = _make_adapter(tmp_path)
        resp = await adapter._handle_notification(
            _FakeRequest(query={"validationToken": "abc123"})
        )
        assert resp.status == 200
        assert resp.text == "abc123"
        assert resp.content_type == "text/plain"


class TestMSGraphNotifications:
    @pytest.mark.anyio
    async def test_valid_notification_accepted_and_scheduled(self, tmp_path):
        adapter = _make_adapter(tmp_path)
        adapter.store.upsert_subscription(
            "sub-1",
            {
                "client_state": "expected-client-state",
                "resource": "communications/onlineMeetings",
            },
        )
        scheduled: list[tuple[dict, object]] = []

        async def _capture(notification, event):
            scheduled.append((notification, event))

        adapter.set_notification_scheduler(_capture)
        payload = {
            "value": [
                {
                    "id": "notif-1",
                    "subscriptionId": "sub-1",
                    "changeType": "updated",
                    "resource": "communications/onlineMeetings/meeting-1",
                    "clientState": "expected-client-state",
                    "resourceData": {"id": "meeting-1"},
                }
            ]
        }

        resp = await adapter._handle_notification(_FakeRequest(json_payload=payload))
        assert resp.status == 202
        data = json.loads(resp.text)
        assert data["accepted"] == 1
        assert data["duplicates"] == 0
        assert data["rejected"] == 0
        assert data["scheduled"] == 1

        await asyncio.sleep(0.05)

        assert len(scheduled) == 1
        notification, event = scheduled[0]
        assert notification["id"] == "notif-1"
        assert event.source.platform == Platform.MSGRAPH_WEBHOOK
        assert event.source.chat_type == "webhook"
        assert event.message_id == "id:notif-1"
        assert adapter.store.has_notification_receipt("id:notif-1") is True
        assert adapter.store.get_event_timestamp("id:notif-1") is not None

    @pytest.mark.anyio
    async def test_bad_client_state_rejected(self, tmp_path):
        adapter = _make_adapter(tmp_path)
        adapter.store.upsert_subscription("sub-1", {"client_state": "expected-client-state"})
        scheduled: list[tuple[dict, object]] = []

        async def _capture(notification, event):
            scheduled.append((notification, event))

        adapter.set_notification_scheduler(_capture)
        payload = {
            "value": [
                {
                    "id": "notif-2",
                    "subscriptionId": "sub-1",
                    "changeType": "updated",
                    "resource": "communications/onlineMeetings/meeting-2",
                    "clientState": "wrong-state",
                }
            ]
        }

        resp = await adapter._handle_notification(_FakeRequest(json_payload=payload))
        assert resp.status == 403
        data = json.loads(resp.text)
        assert data["accepted"] == 0
        assert data["duplicates"] == 0
        assert data["rejected"] == 1

        await asyncio.sleep(0.05)

        assert scheduled == []
        assert adapter.store.has_notification_receipt("id:notif-2") is False

    @pytest.mark.anyio
    async def test_duplicate_notification_deduped(self, tmp_path):
        adapter = _make_adapter(tmp_path)
        adapter.store.upsert_subscription("sub-1", {"client_state": "expected-client-state"})
        scheduled: list[tuple[dict, object]] = []

        async def _capture(notification, event):
            scheduled.append((notification, event))

        adapter.set_notification_scheduler(_capture)
        payload = {
            "value": [
                {
                    "id": "notif-dup",
                    "subscriptionId": "sub-1",
                    "changeType": "updated",
                    "resource": "communications/onlineMeetings/meeting-3",
                    "clientState": "expected-client-state",
                }
            ]
        }

        first = await adapter._handle_notification(_FakeRequest(json_payload=payload))
        assert first.status == 202
        second = await adapter._handle_notification(_FakeRequest(json_payload=payload))
        assert second.status == 202
        second_data = json.loads(second.text)
        assert second_data["accepted"] == 0
        assert second_data["duplicates"] == 1
        assert second_data["scheduled"] == 0

        await asyncio.sleep(0.05)

        assert len(scheduled) == 1
