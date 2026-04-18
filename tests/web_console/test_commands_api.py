"""Tests for the web console commands API."""

from __future__ import annotations

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from gateway.web_console.routes import register_web_console_routes


class TestCommandsApi:
    @staticmethod
    async def _make_client() -> TestClient:
        app = web.Application()
        register_web_console_routes(app)
        client = TestClient(TestServer(app))
        await client.start_server()
        return client

    @pytest.mark.asyncio
    async def test_commands_registry_route_exposes_cli_registry(self):
        client = await self._make_client()
        try:
            resp = await client.get("/api/gui/commands")
            assert resp.status == 200
            payload = await resp.json()

            assert payload["ok"] is True
            assert isinstance(payload["commands"], list)
            assert payload["commands"]

            by_name = {entry["name"]: entry for entry in payload["commands"]}
            assert "new" in by_name
            assert "model" in by_name
            assert "commands" in by_name

            new_entry = by_name["new"]
            assert "reset" in new_entry["aliases"]
            assert "reset" in new_entry["names"]
            assert new_entry["cli_only"] is False

            commands_entry = by_name["commands"]
            assert commands_entry["gateway_only"] is True
            assert commands_entry["args_hint"] == "[page]"
        finally:
            await client.close()