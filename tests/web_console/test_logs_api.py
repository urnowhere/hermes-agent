"""Tests for the web console logs API."""

from __future__ import annotations

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from gateway.web_console.api.logs import LOG_SERVICE_APP_KEY
from gateway.web_console.routes import register_web_console_routes


class FakeLogService:
    def get_logs(self, *, file_name=None, limit=200):
        if file_name == "missing.log":
            raise FileNotFoundError(file_name)
        return {
            "directory": "/tmp/hermes/logs",
            "file": file_name or "gateway.log",
            "available_files": ["gateway.log", "gateway.error.log"],
            "line_count": min(2, limit),
            "lines": ["line 1", "line 2"][:limit],
        }


class TestLogsApi:
    @staticmethod
    async def _make_client(service: FakeLogService) -> TestClient:
        app = web.Application()
        app[LOG_SERVICE_APP_KEY] = service
        register_web_console_routes(app)
        client = TestClient(TestServer(app))
        await client.start_server()
        return client

    @pytest.mark.asyncio
    async def test_get_logs_route(self):
        client = await self._make_client(FakeLogService())
        try:
            resp = await client.get("/api/gui/logs?file=gateway.error.log&limit=1")
            assert resp.status == 200
            payload = await resp.json()
            assert payload["ok"] is True
            assert payload["logs"]["file"] == "gateway.error.log"
            assert payload["logs"]["line_count"] == 1
            assert payload["logs"]["lines"] == ["line 1"]
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_get_logs_route_validation_and_not_found(self):
        client = await self._make_client(FakeLogService())
        try:
            invalid_limit_resp = await client.get("/api/gui/logs?limit=abc")
            assert invalid_limit_resp.status == 400
            assert (await invalid_limit_resp.json())["error"]["code"] == "invalid_limit"

            missing_resp = await client.get("/api/gui/logs?file=missing.log")
            assert missing_resp.status == 404
            missing_payload = await missing_resp.json()
            assert missing_payload["error"]["code"] == "log_not_found"
            assert missing_payload["error"]["file"] == "missing.log"
        finally:
            await client.close()
