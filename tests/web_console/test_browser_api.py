"""Tests for the web console browser API."""

from __future__ import annotations

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from gateway.web_console.api.browser import BROWSER_SERVICE_APP_KEY
from gateway.web_console.routes import register_web_console_routes


class FakeBrowserService:
    def __init__(self) -> None:
        self.connect_calls: list[str | None] = []
        self.disconnect_calls = 0

    def get_status(self) -> dict:
        return {
            "mode": "local",
            "connected": False,
            "cdp_url": "",
            "reachable": None,
            "requirements_ok": True,
            "active_sessions": {},
        }

    def connect(self, cdp_url: str | None = None) -> dict:
        self.connect_calls.append(cdp_url)
        if cdp_url == "bad":
            raise ValueError("bad cdp url")
        return {
            "mode": "live_cdp",
            "connected": True,
            "cdp_url": cdp_url or "http://localhost:9222",
            "reachable": True,
            "requirements_ok": True,
            "active_sessions": {},
            "message": "Browser connected to a live Chrome CDP endpoint.",
        }

    def disconnect(self) -> dict:
        self.disconnect_calls += 1
        return {
            "mode": "local",
            "connected": False,
            "cdp_url": "",
            "reachable": None,
            "requirements_ok": True,
            "active_sessions": {},
            "message": "Browser reverted to the default backend.",
        }


class TestBrowserApi:
    @staticmethod
    async def _make_client(service: FakeBrowserService) -> TestClient:
        app = web.Application()
        app[BROWSER_SERVICE_APP_KEY] = service
        register_web_console_routes(app)
        client = TestClient(TestServer(app))
        await client.start_server()
        return client

    @pytest.mark.asyncio
    async def test_browser_status_connect_and_disconnect_routes(self):
        service = FakeBrowserService()
        client = await self._make_client(service)
        try:
            status_resp = await client.get("/api/gui/browser/status")
            assert status_resp.status == 200
            status_payload = await status_resp.json()
            assert status_payload["ok"] is True
            assert status_payload["browser"]["mode"] == "local"

            connect_resp = await client.post("/api/gui/browser/connect", json={"cdp_url": "http://localhost:9222"})
            assert connect_resp.status == 200
            connect_payload = await connect_resp.json()
            assert connect_payload["ok"] is True
            assert connect_payload["browser"]["connected"] is True
            assert service.connect_calls == ["http://localhost:9222"]

            disconnect_resp = await client.post("/api/gui/browser/disconnect")
            assert disconnect_resp.status == 200
            disconnect_payload = await disconnect_resp.json()
            assert disconnect_payload["ok"] is True
            assert disconnect_payload["browser"]["connected"] is False
            assert service.disconnect_calls == 1
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_browser_connect_validation(self):
        service = FakeBrowserService()
        client = await self._make_client(service)
        try:
            invalid_json_resp = await client.post(
                "/api/gui/browser/connect",
                data="not json",
                headers={"Content-Type": "application/json"},
            )
            assert invalid_json_resp.status == 400
            assert (await invalid_json_resp.json())["error"]["code"] == "invalid_json"

            invalid_cdp_resp = await client.post("/api/gui/browser/connect", json={"cdp_url": "bad"})
            assert invalid_cdp_resp.status == 400
            assert (await invalid_cdp_resp.json())["error"]["code"] == "invalid_cdp_url"
        finally:
            await client.close()
