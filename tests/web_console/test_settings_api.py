"""Tests for the web console settings and auth-status APIs."""

from __future__ import annotations

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from gateway.web_console.api.settings import SETTINGS_SERVICE_APP_KEY
from gateway.web_console.routes import register_web_console_routes


class FakeSettingsService:
    def __init__(self) -> None:
        self.settings = {
            "model": "anthropic/claude-opus-4.1",
            "auxiliary": {"vision": {"api_key": "***", "base_url": "https://example.test/v1"}},
            "browser": {"record_sessions": False},
        }
        self.auth = {
            "active_provider": "nous",
            "resolved_provider": "nous",
            "logged_in": True,
            "active_status": {"logged_in": True, "has_refresh_token": True},
            "providers": [
                {
                    "provider": "nous",
                    "name": "Nous Portal",
                    "auth_type": "oauth_device_code",
                    "active": True,
                    "status": {"logged_in": True, "has_refresh_token": True},
                }
            ],
        }
        self.update_calls: list[dict] = []

    def get_settings(self) -> dict:
        return self.settings

    def update_settings(self, patch: dict) -> dict:
        self.update_calls.append(patch)
        if patch.get("bad"):
            raise ValueError("patch rejected")
        self.settings = {
            **self.settings,
            **patch,
        }
        return self.settings

    def get_auth_status(self) -> dict:
        return self.auth


class TestSettingsApi:
    @staticmethod
    async def _make_client(service: FakeSettingsService) -> TestClient:
        app = web.Application()
        app[SETTINGS_SERVICE_APP_KEY] = service
        register_web_console_routes(app)
        client = TestClient(TestServer(app))
        await client.start_server()
        return client

    @pytest.mark.asyncio
    async def test_get_settings_and_auth_status_routes(self):
        client = await self._make_client(FakeSettingsService())
        try:
            settings_resp = await client.get("/api/gui/settings")
            assert settings_resp.status == 200
            settings_payload = await settings_resp.json()
            assert settings_payload["ok"] is True
            assert settings_payload["settings"]["model"] == "anthropic/claude-opus-4.1"
            assert settings_payload["settings"]["auxiliary"]["vision"]["api_key"] == "***"

            auth_resp = await client.get("/api/gui/auth-status")
            assert auth_resp.status == 200
            auth_payload = await auth_resp.json()
            assert auth_payload["ok"] is True
            assert auth_payload["auth"]["active_provider"] == "nous"
            assert auth_payload["auth"]["providers"][0]["status"]["logged_in"] is True
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_patch_settings_route_and_validation(self):
        service = FakeSettingsService()
        client = await self._make_client(service)
        try:
            patch_resp = await client.patch("/api/gui/settings", json={"browser": {"record_sessions": True}})
            assert patch_resp.status == 200
            patch_payload = await patch_resp.json()
            assert patch_payload["ok"] is True
            assert service.update_calls == [{"browser": {"record_sessions": True}}]
            assert patch_payload["settings"]["browser"]["record_sessions"] is True

            invalid_json_resp = await client.patch(
                "/api/gui/settings",
                data="not json",
                headers={"Content-Type": "application/json"},
            )
            assert invalid_json_resp.status == 400
            assert (await invalid_json_resp.json())["error"]["code"] == "invalid_json"

            invalid_patch_resp = await client.patch("/api/gui/settings", json={"bad": True})
            assert invalid_patch_resp.status == 400
            assert (await invalid_patch_resp.json())["error"]["code"] == "invalid_patch"
        finally:
            await client.close()
