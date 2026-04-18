"""Tests for the web console memory and session-search APIs."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from gateway.web_console.api.memory import MEMORY_SERVICE_APP_KEY
from gateway.web_console.routes import register_web_console_routes
from gateway.web_console.services import memory_service as memory_service_module
from gateway.web_console.services.memory_service import MemoryService


class FakeMemoryService:
    def __init__(self) -> None:
        self.payloads = {
            "memory": {
                "target": "memory",
                "enabled": True,
                "entries": ["Project prefers pytest."],
                "entry_count": 1,
                "usage": {"text": "1% — 22/2200 chars", "percent": 1, "current_chars": 22, "char_limit": 2200},
                "path": "/tmp/MEMORY.md",
            },
            "user": {
                "target": "user",
                "enabled": True,
                "entries": ["User likes concise answers."],
                "entry_count": 1,
                "usage": {"text": "2% — 30/1375 chars", "percent": 2, "current_chars": 30, "char_limit": 1375},
                "path": "/tmp/USER.md",
            },
        }
        self.search_payload = {
            "success": True,
            "query": "deploy OR docker",
            "results": [{"session_id": "sess-1", "summary": "We fixed the deploy issue.", "source": "cli", "when": "today", "model": "hermes"}],
            "count": 1,
            "sessions_searched": 1,
        }

    def get_memory(self, *, target="memory"):
        if target not in self.payloads:
            raise ValueError("bad target")
        return self.payloads[target]

    def mutate_memory(self, *, action, target="memory", content=None, old_text=None):
        if target == "disabled":
            raise PermissionError("Local memory is disabled in config.")
        if target not in self.payloads:
            raise ValueError("bad target")
        if content == "fail" or old_text == "missing":
            return {
                **self.payloads[target],
                "success": False,
                "error": "No entry matched 'missing'.",
                "matches": ["candidate one"],
            }
        payload = dict(self.payloads[target])
        payload["success"] = True
        payload["message"] = f"{action} ok"
        if action == "add" and content:
            payload["entries"] = payload["entries"] + [content]
            payload["entry_count"] = len(payload["entries"])
        return payload

    def search_sessions(self, *, query, role_filter=None, limit=3, current_session_id=None):
        if query == "explode":
            raise RuntimeError("boom")
        if query == "offline":
            return {"success": False, "error": "Session database not available."}
        payload = dict(self.search_payload)
        payload["query"] = query
        payload["role_filter"] = role_filter
        payload["limit"] = limit
        payload["current_session_id"] = current_session_id
        return payload


class TestMemoryApi:
    @staticmethod
    async def _make_client(service: FakeMemoryService) -> TestClient:
        app = web.Application()
        app[MEMORY_SERVICE_APP_KEY] = service
        register_web_console_routes(app)
        client = TestClient(TestServer(app))
        await client.start_server()
        return client

    @pytest.mark.asyncio
    async def test_memory_routes_return_structured_payloads(self):
        client = await self._make_client(FakeMemoryService())
        try:
            memory_resp = await client.get("/api/gui/memory")
            assert memory_resp.status == 200
            memory_payload = await memory_resp.json()
            assert memory_payload["ok"] is True
            assert memory_payload["memory"]["target"] == "memory"
            assert memory_payload["memory"]["entries"] == ["Project prefers pytest."]

            profile_resp = await client.get("/api/gui/user-profile")
            assert profile_resp.status == 200
            profile_payload = await profile_resp.json()
            assert profile_payload["ok"] is True
            assert profile_payload["user_profile"]["target"] == "user"
            assert profile_payload["user_profile"]["entries"] == ["User likes concise answers."]

            add_resp = await client.post("/api/gui/memory", json={"target": "user", "content": "Prefers dark mode."})
            assert add_resp.status == 200
            add_payload = await add_resp.json()
            assert add_payload["ok"] is True
            assert add_payload["memory"]["target"] == "user"
            assert add_payload["memory"]["message"] == "add ok"

            replace_resp = await client.patch(
                "/api/gui/memory",
                json={"target": "memory", "old_text": "pytest", "content": "Project prefers pytest -q."},
            )
            assert replace_resp.status == 200
            replace_payload = await replace_resp.json()
            assert replace_payload["memory"]["message"] == "replace ok"

            delete_resp = await client.delete("/api/gui/memory", json={"target": "memory", "old_text": "pytest"})
            assert delete_resp.status == 200
            delete_payload = await delete_resp.json()
            assert delete_payload["memory"]["message"] == "remove ok"
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_session_search_route_and_structured_errors(self):
        client = await self._make_client(FakeMemoryService())
        try:
            search_resp = await client.get(
                "/api/gui/session-search?query=deploy%20OR%20docker&role_filter=user,assistant&limit=2&current_session_id=sess-live"
            )
            assert search_resp.status == 200
            search_payload = await search_resp.json()
            assert search_payload["ok"] is True
            assert search_payload["search"]["query"] == "deploy OR docker"
            assert search_payload["search"]["count"] == 1
            assert search_payload["search"]["role_filter"] == "user,assistant"
            assert search_payload["search"]["limit"] == 2
            assert search_payload["search"]["current_session_id"] == "sess-live"

            missing_query_resp = await client.get("/api/gui/session-search")
            assert missing_query_resp.status == 400
            assert (await missing_query_resp.json())["error"]["code"] == "missing_query"

            invalid_limit_resp = await client.get("/api/gui/session-search?query=deploy&limit=0")
            assert invalid_limit_resp.status == 400
            assert (await invalid_limit_resp.json())["error"]["code"] == "invalid_search"

            unavailable_resp = await client.get("/api/gui/session-search?query=offline")
            assert unavailable_resp.status == 503
            assert (await unavailable_resp.json())["error"]["code"] == "search_failed"

            failed_resp = await client.get("/api/gui/session-search?query=explode")
            assert failed_resp.status == 500
            assert (await failed_resp.json())["error"]["code"] == "search_failed"
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_memory_routes_validate_payloads(self):
        client = await self._make_client(FakeMemoryService())
        try:
            invalid_json_resp = await client.post(
                "/api/gui/memory",
                data="not json",
                headers={"Content-Type": "application/json"},
            )
            assert invalid_json_resp.status == 400
            assert (await invalid_json_resp.json())["error"]["code"] == "invalid_json"

            failed_update_resp = await client.patch(
                "/api/gui/memory",
                json={"target": "memory", "old_text": "missing", "content": "fail"},
            )
            assert failed_update_resp.status == 400
            failed_update_payload = await failed_update_resp.json()
            assert failed_update_payload["error"]["code"] == "memory_update_failed"
            assert failed_update_payload["error"]["matches"] == ["candidate one"]
        finally:
            await client.close()


class TestMemoryService:
    def test_memory_service_formats_store_and_search_payloads(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        class FakeStore:
            def __init__(self):
                self.memory_entries = []
                self.user_entries = []
                self.memory_char_limit = 2200
                self.user_char_limit = 1375

            @staticmethod
            def _path_for(target):
                return tmp_path / ("USER.md" if target == "user" else "MEMORY.md")

            def _char_count(self, target):
                entries = self.user_entries if target == "user" else self.memory_entries
                return len("\n§\n".join(entries)) if entries else 0

            def _char_limit(self, target):
                return self.user_char_limit if target == "user" else self.memory_char_limit

            def add(self, target, content):
                entries = self.user_entries if target == "user" else self.memory_entries
                entries.append(content)
                return {"success": True, "message": "Entry added."}

            def replace(self, target, old_text, content):
                entries = self.user_entries if target == "user" else self.memory_entries
                for index, entry in enumerate(entries):
                    if old_text in entry:
                        entries[index] = content
                        return {"success": True, "message": "Entry replaced."}
                return {"success": False, "error": f"No entry matched '{old_text}'."}

            def remove(self, target, old_text):
                entries = self.user_entries if target == "user" else self.memory_entries
                for index, entry in enumerate(entries):
                    if old_text in entry:
                        entries.pop(index)
                        return {"success": True, "message": "Entry removed."}
                return {"success": False, "error": f"No entry matched '{old_text}'."}

        monkeypatch.setattr(
            memory_service_module,
            "load_config",
            lambda: {
                "memory": {
                    "memory_enabled": True,
                    "user_profile_enabled": True,
                    "memory_char_limit": 2200,
                    "user_char_limit": 1375,
                }
            },
        )
        monkeypatch.setattr(
            memory_service_module,
            "session_search",
            lambda **kwargs: json.dumps(
                {
                    "success": True,
                    "query": kwargs["query"],
                    "results": [{"session_id": "sess-real", "summary": "Found prior discussion."}],
                    "count": 1,
                    "sessions_searched": 1,
                }
            ),
        )

        service = MemoryService(store=FakeStore(), db=object())

        initial = service.get_memory(target="memory")
        assert initial["entries"] == []
        assert initial["enabled"] is True
        assert initial["usage"]["char_limit"] == 2200

        added = service.mutate_memory(action="add", target="memory", content="Remember the deploy flag.")
        assert added["success"] is True
        assert added["entries"] == ["Remember the deploy flag."]
        assert added["path"].endswith("MEMORY.md")

        profile = service.mutate_memory(action="add", target="user", content="User prefers terse updates.")
        assert profile["success"] is True
        assert profile["entries"] == ["User prefers terse updates."]
        assert profile["usage"]["char_limit"] == 1375

        replaced = service.mutate_memory(action="replace", target="memory", old_text="deploy", content="Remember the deploy flag loudly.")
        assert replaced["entries"] == ["Remember the deploy flag loudly."]

        search_payload = service.search_sessions(query="deploy", limit=2)
        assert search_payload["success"] is True
        assert search_payload["results"][0]["session_id"] == "sess-real"
