"""Tests for the raw Bot API client (Managed Bots endpoints + send-as helper)."""

from __future__ import annotations

from typing import Any, Dict
from unittest.mock import MagicMock

import httpx
import pytest

from gateway.telegram_fleet.api import (
    BotApiError,
    FleetApiClient,
    ManagedBotInfo,
    build_managed_bot_deep_link,
)


def _mock_client(handler):
    """Build an httpx.MockTransport-backed client that calls *handler(method, path, json)*."""

    def _transport_handler(request: httpx.Request) -> httpx.Response:
        body = {} if not request.content else request.read()
        if isinstance(body, (bytes, bytearray)):
            import json as _json

            body = _json.loads(body or b"{}")
        return handler(request.method, request.url.path, body)

    transport = httpx.MockTransport(_transport_handler)
    return httpx.Client(transport=transport)


def test_rejects_invalid_token():
    with pytest.raises(ValueError):
        FleetApiClient("not-a-real-token")


def test_get_managed_bot_token_parses_response():
    def handler(method, path, body):
        assert method == "POST"
        assert path == "/bot12345:ABC/getManagedBotToken"
        assert body == {"user_id": 999}
        return httpx.Response(
            200,
            json={
                "ok": True,
                "result": {
                    "token": "999:childToken",
                    "bot": {"id": 999, "username": "child_bot"},
                },
            },
        )

    client = _mock_client(handler)
    api = FleetApiClient("12345:ABC", client=client)
    info = api.get_managed_bot_token(999)
    assert isinstance(info, ManagedBotInfo)
    assert info.token == "999:childToken"
    assert info.bot_id == 999
    assert info.bot_username == "child_bot"


def test_replace_managed_bot_token_returns_new_token():
    def handler(method, path, body):
        assert path.endswith("/replaceManagedBotToken")
        return httpx.Response(
            200,
            json={
                "ok": True,
                "result": {
                    "token": "999:newToken",
                    "bot": {"id": 999, "username": "child_bot"},
                },
            },
        )

    client = _mock_client(handler)
    api = FleetApiClient("12345:ABC", client=client)
    info = api.replace_managed_bot_token(999)
    assert info.token == "999:newToken"


def test_api_error_includes_method_and_description():
    def handler(method, path, body):
        return httpx.Response(
            403,
            json={"ok": False, "error_code": 403, "description": "Bot Manager Mode disabled"},
        )

    client = _mock_client(handler)
    api = FleetApiClient("12345:ABC", client=client)
    with pytest.raises(BotApiError) as exc:
        api.get_managed_bot_token(1)
    assert exc.value.method == "getManagedBotToken"
    assert exc.value.code == 403
    assert "Manager Mode" in exc.value.description


def test_send_message_as_uses_child_token():
    captured: Dict[str, Any] = {}

    def handler(method, path, body):
        captured["path"] = path
        captured["body"] = body
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 17}})

    client = _mock_client(handler)
    api = FleetApiClient("manager:tok", client=client)
    result = api.send_message_as("999:child", "987654", "hi")
    assert result == {"message_id": 17}
    assert captured["path"] == "/bot999:child/sendMessage"
    assert captured["body"] == {"chat_id": "987654", "text": "hi"}


def test_send_message_as_rejects_bad_child_token():
    api = FleetApiClient("manager:tok", client=_mock_client(lambda *a: httpx.Response(200, json={"ok": True, "result": {}})))
    with pytest.raises(ValueError):
        api.send_message_as("not-a-token", "1", "hi")


def test_can_manage_bots_reads_get_me():
    def handler(method, path, body):
        return httpx.Response(
            200,
            json={"ok": True, "result": {"id": 1, "username": "M", "can_manage_bots": True}},
        )

    api = FleetApiClient("12345:ABC", client=_mock_client(handler))
    assert api.can_manage_bots() is True


# ── Deep link ─────────────────────────────────────────────────────────


def test_build_deep_link_basic():
    url = build_managed_bot_deep_link("HermesMgr", "worker_a7f3")
    assert url == "https://t.me/newbot/HermesMgr/worker_a7f3"


def test_build_deep_link_quotes_name():
    url = build_managed_bot_deep_link("HermesMgr", "worker_a7f3", name="Research & Synthesis")
    assert "Research%20%26%20Synthesis" in url


def test_build_deep_link_strips_at_signs():
    url = build_managed_bot_deep_link("@HermesMgr", "@worker")
    assert url == "https://t.me/newbot/HermesMgr/worker"


# ── getUpdates / managed_bot drain ────────────────────────────────────


def test_get_updates_returns_raw_list():
    def handler(method, path, body):
        assert path.endswith("/getUpdates")
        return httpx.Response(
            200,
            json={
                "ok": True,
                "result": [
                    {"update_id": 1, "managed_bot": {"bot": {"id": 99, "username": "child_bot"}}},
                    {"update_id": 2, "message": {"text": "hi"}},
                ],
            },
        )

    api = FleetApiClient("12345:ABC", client=_mock_client(handler))
    updates = api.get_updates()
    assert len(updates) == 2
    assert updates[0]["managed_bot"]["bot"]["id"] == 99


def test_drain_managed_bot_events_resolves_tokens():
    """Drain should call getManagedBotToken for each managed_bot update."""
    def handler(method, path, body):
        if path.endswith("/getUpdates"):
            return httpx.Response(
                200,
                json={
                    "ok": True,
                    "result": [
                        {
                            "update_id": 5,
                            "managed_bot": {
                                "bot": {"id": 99, "username": "child_bot"},
                            },
                        },
                    ],
                },
            )
        if path.endswith("/getManagedBotToken"):
            assert body == {"user_id": 99}
            return httpx.Response(
                200,
                json={
                    "ok": True,
                    "result": {
                        "token": "99:childTok",
                        "bot": {"id": 99, "username": "child_bot"},
                    },
                },
            )
        return httpx.Response(404, json={"ok": False, "description": "nope"})

    api = FleetApiClient("12345:ABC", client=_mock_client(handler))
    infos, next_offset = api.drain_managed_bot_events()
    assert len(infos) == 1
    assert infos[0].token == "99:childTok"
    assert infos[0].bot_username == "child_bot"
    assert next_offset == 6


def test_drain_skips_non_managed_bot_updates():
    def handler(method, path, body):
        if path.endswith("/getUpdates"):
            return httpx.Response(
                200,
                json={
                    "ok": True,
                    "result": [
                        {"update_id": 1, "message": {"text": "hello"}},
                        {"update_id": 2, "edited_message": {"text": "edit"}},
                    ],
                },
            )
        return httpx.Response(404, json={"ok": False, "description": "nope"})

    api = FleetApiClient("12345:ABC", client=_mock_client(handler))
    infos, next_offset = api.drain_managed_bot_events()
    assert infos == []
    assert next_offset == 3


def test_drain_continues_when_token_resolution_fails():
    """A single bad managed_bot shouldn't break the rest of the batch."""
    def handler(method, path, body):
        if path.endswith("/getUpdates"):
            return httpx.Response(
                200,
                json={
                    "ok": True,
                    "result": [
                        {"update_id": 1, "managed_bot": {"bot": {"id": 99, "username": "ok_bot"}}},
                        {"update_id": 2, "managed_bot": {"bot": {"id": 100, "username": "bad_bot"}}},
                    ],
                },
            )
        if path.endswith("/getManagedBotToken"):
            uid = body["user_id"]
            if uid == 100:
                return httpx.Response(
                    400,
                    json={"ok": False, "error_code": 400, "description": "no such bot"},
                )
            return httpx.Response(
                200,
                json={
                    "ok": True,
                    "result": {
                        "token": "99:tok",
                        "bot": {"id": 99, "username": "ok_bot"},
                    },
                },
            )
        return httpx.Response(404, json={"ok": False, "description": "nope"})

    api = FleetApiClient("12345:ABC", client=_mock_client(handler))
    infos, next_offset = api.drain_managed_bot_events()
    assert len(infos) == 1
    assert infos[0].bot_username == "ok_bot"
    assert next_offset == 3
