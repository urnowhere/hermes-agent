"""Tests for tools/microsoft_graph_subscription_tools.py."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

import model_tools
from tools import microsoft_graph_subscription_tools as graph_tools


@pytest.mark.anyio
class TestMicrosoftGraphSubscriptionTools:
    async def test_create_subscription_dry_run_returns_payload(self):
        result = await graph_tools._create_graph_subscription(
            {
                "changeType": "created",
                "notificationUrl": "https://example.com/webhook",
                "resource": "communications/onlineMeetings",
                "expirationDateTime": "2026-05-04T00:00:00Z",
                "clientState": "secret-state",
                "dry_run": True,
            }
        )
        payload = json.loads(result)
        assert payload["success"] is True
        assert payload["dry_run"] is True
        assert payload["payload"]["clientState"] == "secret-state"

    async def test_create_subscription_validates_required_fields(self):
        result = await graph_tools._create_graph_subscription({"dry_run": True})
        payload = json.loads(result)
        assert "error" in payload
        assert "changeType" in payload["error"]

    async def test_list_subscriptions_returns_structured_json(self, monkeypatch):
        fake_client = AsyncMock()
        fake_client.collect_paginated.return_value = [{"id": "sub-1"}]
        monkeypatch.setattr(graph_tools, "_build_graph_client", lambda: fake_client)

        result = await graph_tools._list_graph_subscriptions({})
        payload = json.loads(result)
        assert payload["success"] is True
        assert payload["count"] == 1
        assert payload["subscriptions"][0]["id"] == "sub-1"

    async def test_renew_subscription_calls_patch_json(self, monkeypatch):
        fake_client = AsyncMock()
        fake_client.patch_json.return_value = {
            "id": "sub-1",
            "expirationDateTime": "2026-05-05T00:00:00Z",
        }
        monkeypatch.setattr(graph_tools, "_build_graph_client", lambda: fake_client)

        result = await graph_tools._renew_graph_subscription(
            {
                "subscription_id": "sub-1",
                "expirationDateTime": "2026-05-05T00:00:00Z",
            }
        )
        payload = json.loads(result)
        assert payload["success"] is True
        assert payload["subscription"]["id"] == "sub-1"
        fake_client.patch_json.assert_awaited_once()

    async def test_delete_subscription_calls_delete(self, monkeypatch):
        fake_client = AsyncMock()
        fake_client.delete.return_value = {"deleted": True}
        monkeypatch.setattr(graph_tools, "_build_graph_client", lambda: fake_client)

        result = await graph_tools._delete_graph_subscription(
            {"subscription_id": "sub-1"}
        )
        payload = json.loads(result)
        assert payload["success"] is True
        assert payload["subscription_id"] == "sub-1"
        fake_client.delete.assert_awaited_once()

    async def test_inspect_token_health_can_force_refresh(self, monkeypatch):
        provider = MagicMock()
        provider.inspect_token_health.side_effect = [
            {"cached": False},
            {"cached": True, "expires_in_seconds": 300},
        ]
        provider.get_access_token = AsyncMock(return_value="abcdef1234567890")
        monkeypatch.setattr(
            graph_tools,
            "MicrosoftGraphTokenProvider",
            type(
                "ProviderFactory",
                (),
                {"from_env": staticmethod(lambda: provider)},
            ),
        )

        result = await graph_tools._inspect_graph_token_health(
            {"force_refresh": True}
        )
        payload = json.loads(result)
        assert payload["cached"] is True
        assert payload["last_refresh_succeeded"] is True
        assert payload["access_token_length"] == len("abcdef1234567890")

    async def test_maintain_subscriptions_dry_run_lists_candidates(self, monkeypatch, tmp_path):
        fake_client = AsyncMock()
        fake_client.collect_paginated.return_value = [
            {
                "id": "sub-1",
                "resource": "communications/onlineMeetings/getAllTranscripts",
                "changeType": "updated",
                "notificationUrl": "https://example.com/webhooks/msgraph",
                "expirationDateTime": "2099-05-05T00:00:00Z",
            },
            {
                "id": "sub-2",
                "resource": "communications/onlineMeetings/getAllRecordings",
                "changeType": "updated",
                "notificationUrl": "https://example.com/webhooks/msgraph",
                "expirationDateTime": "2026-05-04T12:00:00Z",
            },
        ]
        monkeypatch.setattr(graph_tools, "_build_graph_client", lambda: fake_client)
        monkeypatch.setattr(graph_tools, "_utc_now", lambda: graph_tools._parse_datetime("2026-05-04T10:00:00Z"))
        store = graph_tools.TeamsPipelineStore(tmp_path / "teams_pipeline_store.json")
        store.upsert_subscription(
            "sub-2",
            {
                "subscription_id": "sub-2",
                "resource": "communications/onlineMeetings/getAllRecordings",
                "change_type": "updated",
                "notification_url": "https://example.com/webhooks/msgraph",
                "expiration_datetime": "2026-05-04T12:00:00Z",
            },
        )

        result = await graph_tools._maintain_graph_subscriptions(
            {
                "renew_within_hours": 3,
                "extend_hours": 24,
                "dry_run": True,
                "store_path": str(tmp_path / "teams_pipeline_store.json"),
            }
        )
        payload = json.loads(result)
        assert payload["success"] is True
        assert payload["dry_run"] is True
        assert payload["candidate_count"] == 1
        assert payload["candidates"][0]["subscription_id"] == "sub-2"
        assert payload["skipped"][0]["reason"] == "not_managed_by_teams_pipeline"
        fake_client.patch_json.assert_not_awaited()

    async def test_maintain_subscriptions_renews_and_updates_store(self, monkeypatch, tmp_path):
        fake_client = AsyncMock()
        fake_client.collect_paginated.return_value = [
            {
                "id": "sub-2",
                "resource": "communications/onlineMeetings/getAllRecordings",
                "changeType": "updated",
                "notificationUrl": "https://example.com/webhooks/msgraph",
                "expirationDateTime": "2026-05-04T12:00:00Z",
            }
        ]
        fake_client.patch_json.return_value = {"id": "sub-2", "renewed": True}
        monkeypatch.setattr(graph_tools, "_build_graph_client", lambda: fake_client)
        monkeypatch.setattr(graph_tools, "_utc_now", lambda: graph_tools._parse_datetime("2026-05-04T10:00:00Z"))
        store = graph_tools.TeamsPipelineStore(tmp_path / "teams_pipeline_store.json")
        store.upsert_subscription(
            "sub-2",
            {
                "subscription_id": "sub-2",
                "resource": "communications/onlineMeetings/getAllRecordings",
                "change_type": "updated",
                "notification_url": "https://example.com/webhooks/msgraph",
                "expiration_datetime": "2026-05-04T12:00:00Z",
            },
        )

        result = await graph_tools._maintain_graph_subscriptions(
            {
                "renew_within_hours": 3,
                "extend_hours": 24,
                "store_path": str(tmp_path / "teams_pipeline_store.json"),
            }
        )
        payload = json.loads(result)
        assert payload["success"] is True
        assert payload["renewed_count"] == 1
        fake_client.patch_json.assert_awaited_once()

        store = graph_tools.TeamsPipelineStore(tmp_path / "teams_pipeline_store.json")
        record = store.get_subscription("sub-2")
        assert record is not None
        assert record["status"] == "active"
        assert record["latest_renewal_at"]

    async def test_maintain_subscriptions_skips_unmanaged_remote_subscriptions(self, monkeypatch, tmp_path):
        fake_client = AsyncMock()
        fake_client.collect_paginated.return_value = [
            {
                "id": "sub-owned",
                "resource": "communications/onlineMeetings/getAllRecordings",
                "changeType": "updated",
                "notificationUrl": "https://example.com/webhooks/msgraph",
                "expirationDateTime": "2026-05-04T12:00:00Z",
            },
            {
                "id": "sub-foreign",
                "resource": "communications/onlineMeetings/getAllRecordings",
                "changeType": "updated",
                "notificationUrl": "https://example.com/other",
                "expirationDateTime": "2026-05-04T11:00:00Z",
            },
        ]
        fake_client.patch_json.return_value = {"id": "sub-owned", "renewed": True}
        monkeypatch.setattr(graph_tools, "_build_graph_client", lambda: fake_client)
        monkeypatch.setattr(graph_tools, "_utc_now", lambda: graph_tools._parse_datetime("2026-05-04T10:00:00Z"))
        store = graph_tools.TeamsPipelineStore(tmp_path / "teams_pipeline_store.json")
        store.upsert_subscription(
            "sub-owned",
            {
                "subscription_id": "sub-owned",
                "resource": "communications/onlineMeetings/getAllRecordings",
                "change_type": "updated",
                "notification_url": "https://example.com/webhooks/msgraph",
                "expiration_datetime": "2026-05-04T12:00:00Z",
            },
        )

        result = await graph_tools.maintain_graph_subscriptions(
            {
                "renew_within_hours": 3,
                "extend_hours": 24,
                "store_path": str(tmp_path / "teams_pipeline_store.json"),
            },
            client=fake_client,
            store=store,
        )

        assert result["candidate_count"] == 1
        assert result["renewed_count"] == 1
        fake_client.patch_json.assert_awaited_once_with(
            "/subscriptions/sub-owned",
            json_body={"expirationDateTime": "2026-05-05T12:00:00Z"},
        )
        assert any(
            item["subscription_id"] == "sub-foreign" and item["reason"] == "not_managed_by_teams_pipeline"
            for item in result["skipped"]
        )

    async def test_maintain_subscriptions_bootstraps_managed_state_from_client_state(self, monkeypatch, tmp_path):
        fake_client = AsyncMock()
        fake_client.collect_paginated.return_value = [
            {
                "id": "sub-client-state",
                "resource": "communications/onlineMeetings/getAllTranscripts",
                "changeType": "updated",
                "notificationUrl": "https://example.com/webhooks/msgraph",
                "clientState": "hermes-owned",
                "expirationDateTime": "2026-05-04T11:00:00Z",
            }
        ]
        fake_client.patch_json.return_value = {"id": "sub-client-state", "renewed": True}
        monkeypatch.setattr(graph_tools, "_build_graph_client", lambda: fake_client)
        monkeypatch.setattr(graph_tools, "_utc_now", lambda: graph_tools._parse_datetime("2026-05-04T10:00:00Z"))

        result = await graph_tools.maintain_graph_subscriptions(
            {
                "renew_within_hours": 2,
                "extend_hours": 24,
                "client_state": "hermes-owned",
                "store_path": str(tmp_path / "teams_pipeline_store.json"),
            },
            client=fake_client,
            store=graph_tools.TeamsPipelineStore(tmp_path / "teams_pipeline_store.json"),
        )

        assert result["renewed_count"] == 1
        store = graph_tools.TeamsPipelineStore(tmp_path / "teams_pipeline_store.json")
        assert store.get_subscription("sub-client-state") is not None

    async def test_maintain_subscriptions_uses_env_store_path_by_default(self, monkeypatch, tmp_path):
        monkeypatch.setenv("MSGRAPH_WEBHOOK_STORE_PATH", str(tmp_path / "custom-store.json"))
        fake_client = AsyncMock()
        fake_client.collect_paginated.return_value = []

        result = await graph_tools.maintain_graph_subscriptions(
            {},
            client=fake_client,
        )

        assert result["store_path"] == str(tmp_path / "custom-store.json")

    async def test_tools_appear_in_discovery_when_configured(self, monkeypatch):
        monkeypatch.setenv("MSGRAPH_TENANT_ID", "tenant")
        monkeypatch.setenv("MSGRAPH_CLIENT_ID", "client")
        monkeypatch.setenv("MSGRAPH_CLIENT_SECRET", "secret")

        definitions = model_tools.get_tool_definitions(
            enabled_toolsets=["microsoft_graph"],
            quiet_mode=True,
        )
        tool_names = {tool["function"]["name"] for tool in definitions}

        assert "microsoft_graph_list_subscriptions" in tool_names
        assert "microsoft_graph_inspect_token_health" in tool_names
        assert "microsoft_graph_maintain_subscriptions" in tool_names
