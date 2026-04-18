"""Tests for gateway admin web-console API routes."""

from __future__ import annotations

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from gateway.web_console.api.gateway_admin import GATEWAY_SERVICE_APP_KEY
from gateway.web_console.routes import register_web_console_routes


class FakeGatewayService:
    def __init__(self) -> None:
        self.approve_calls: list[tuple[str, str]] = []
        self.revoke_calls: list[tuple[str, str]] = []

    def get_overview(self) -> dict:
        return {
            "gateway": {"running": True, "pid": 4242, "state": "running", "exit_reason": None, "updated_at": "2026-03-30T21:48:00Z"},
            "summary": {
                "platform_count": 2,
                "enabled_platforms": 1,
                "configured_platforms": 1,
                "connected_platforms": 1,
                "pending_pairings": 1,
                "approved_pairings": 2,
            },
        }

    def get_platforms(self) -> list[dict]:
        return [
            {
                "key": "discord",
                "label": "Discord",
                "enabled": True,
                "configured": True,
                "runtime_state": "connected",
                "error_code": None,
                "error_message": None,
                "updated_at": "2026-03-30T21:49:00Z",
                "home_channel": {"platform": "discord", "chat_id": "chan-1", "name": "Home"},
                "auth": {"mode": "pairing", "allow_all": False, "allowlist_count": 0, "pairing_behavior": "pair"},
                "pairing": {"pending_count": 1, "approved_count": 2},
                "extra": {},
            }
        ]

    def get_pairing_state(self) -> dict:
        return {
            "pending": [
                {"platform": "discord", "code": "ABC12345", "user_id": "u-1", "user_name": "Ada", "age_minutes": 3}
            ],
            "approved": [
                {"platform": "discord", "user_id": "u-2", "user_name": "Grace", "approved_at": 123.0}
            ],
            "summary": {
                "pending_count": 1,
                "approved_count": 1,
                "platforms_with_pending": ["discord"],
                "platforms_with_approved": ["discord"],
            },
        }

    def approve_pairing(self, *, platform: str, code: str) -> dict | None:
        self.approve_calls.append((platform, code))
        if platform == "discord" and code == "abc12345":
            return {
                "platform": "discord",
                "code": "ABC12345",
                "user": {"user_id": "u-1", "user_name": "Ada"},
            }
        return None

    def revoke_pairing(self, *, platform: str, user_id: str) -> bool:
        self.revoke_calls.append((platform, user_id))
        return platform == "discord" and user_id == "u-2"


class FakeRunner:
    def __init__(self) -> None:
        self.restart_calls: list[tuple[bool, bool]] = []

    def request_restart(self, *, detached: bool = False, via_service: bool = False) -> bool:
        self.restart_calls.append((detached, via_service))
        return True


class RestartCapableAdapter:
    class _Handler:
        def __init__(self, runner: FakeRunner) -> None:
            self.__self__ = runner

    def __init__(self, runner: FakeRunner) -> None:
        self._message_handler = self._Handler(runner)


class TestGatewayAdminApiRoutes:
    @staticmethod
    async def _make_client(service: FakeGatewayService) -> TestClient:
        app = web.Application()
        app[GATEWAY_SERVICE_APP_KEY] = service
        register_web_console_routes(app)
        client = TestClient(TestServer(app))
        await client.start_server()
        return client

    @pytest.mark.asyncio
    async def test_overview_platforms_and_pairing_routes(self):
        service = FakeGatewayService()
        client = await self._make_client(service)
        try:
            overview_resp = await client.get("/api/gui/gateway/overview")
            assert overview_resp.status == 200
            overview_payload = await overview_resp.json()
            assert overview_payload["ok"] is True
            assert overview_payload["overview"]["gateway"]["pid"] == 4242
            assert overview_payload["overview"]["summary"]["pending_pairings"] == 1

            platforms_resp = await client.get("/api/gui/gateway/platforms")
            assert platforms_resp.status == 200
            platforms_payload = await platforms_resp.json()
            assert platforms_payload["ok"] is True
            assert platforms_payload["platforms"][0]["key"] == "discord"
            assert platforms_payload["platforms"][0]["auth"]["mode"] == "pairing"

            pairing_resp = await client.get("/api/gui/gateway/pairing")
            assert pairing_resp.status == 200
            pairing_payload = await pairing_resp.json()
            assert pairing_payload["ok"] is True
            assert pairing_payload["pairing"]["pending"][0]["code"] == "ABC12345"
            assert pairing_payload["pairing"]["summary"]["approved_count"] == 1
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_pairing_approve_and_revoke_routes(self):
        service = FakeGatewayService()
        client = await self._make_client(service)
        try:
            approve_resp = await client.post(
                "/api/gui/gateway/pairing/approve",
                json={"platform": "discord", "code": "abc12345"},
            )
            assert approve_resp.status == 200
            approve_payload = await approve_resp.json()
            assert approve_payload["ok"] is True
            assert approve_payload["pairing"]["code"] == "ABC12345"
            assert approve_payload["pairing"]["user"]["user_id"] == "u-1"
            assert service.approve_calls == [("discord", "abc12345")]

            revoke_resp = await client.post(
                "/api/gui/gateway/pairing/revoke",
                json={"platform": "discord", "user_id": "u-2"},
            )
            assert revoke_resp.status == 200
            revoke_payload = await revoke_resp.json()
            assert revoke_payload["ok"] is True
            assert revoke_payload["pairing"]["revoked"] is True
            assert revoke_payload["pairing"]["user_id"] == "u-2"
            assert service.revoke_calls == [("discord", "u-2")]
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_pairing_routes_return_structured_validation_and_not_found_errors(self):
        service = FakeGatewayService()
        client = await self._make_client(service)
        try:
            invalid_json_resp = await client.post(
                "/api/gui/gateway/pairing/approve",
                data="not json",
                headers={"Content-Type": "application/json"},
            )
            assert invalid_json_resp.status == 400
            invalid_json_payload = await invalid_json_resp.json()
            assert invalid_json_payload["ok"] is False
            assert invalid_json_payload["error"]["code"] == "invalid_json"

            invalid_platform_resp = await client.post(
                "/api/gui/gateway/pairing/approve",
                json={"platform": "", "code": "abc12345"},
            )
            assert invalid_platform_resp.status == 400
            invalid_platform_payload = await invalid_platform_resp.json()
            assert invalid_platform_payload["error"]["code"] == "invalid_platform"

            approve_missing_resp = await client.post(
                "/api/gui/gateway/pairing/approve",
                json={"platform": "discord", "code": "missing"},
            )
            assert approve_missing_resp.status == 404
            approve_missing_payload = await approve_missing_resp.json()
            assert approve_missing_payload["error"]["code"] == "pairing_not_found"
            assert approve_missing_payload["error"]["code"] != ""

            revoke_missing_resp = await client.post(
                "/api/gui/gateway/pairing/revoke",
                json={"platform": "discord", "user_id": "missing"},
            )
            assert revoke_missing_resp.status == 404
            revoke_missing_payload = await revoke_missing_resp.json()
            assert revoke_missing_payload["error"]["code"] == "paired_user_not_found"
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_restart_route_requests_gateway_restart_when_runner_is_available(self):
        service = FakeGatewayService()
        runner = FakeRunner()
        app = web.Application()
        app[GATEWAY_SERVICE_APP_KEY] = service
        register_web_console_routes(app, adapter=RestartCapableAdapter(runner))
        client = TestClient(TestServer(app))
        await client.start_server()
        try:
            resp = await client.post("/api/gui/gateway/restart", json={})
            assert resp.status == 200
            payload = await resp.json()
            assert payload["ok"] is True
            assert payload["accepted"] is True
            assert runner.restart_calls == [(True, False)]
        finally:
            await client.close()
