"""Tests for the web console sessions API."""

from __future__ import annotations

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from hermes_state import SessionDB
from gateway.web_console.api.sessions import SESSIONS_SERVICE_APP_KEY
from gateway.web_console.routes import register_web_console_routes
from gateway.web_console.services.session_service import SessionService


class FakeSessionService:
    def __init__(self):
        self.sessions = {
            "sess-1": {
                "session_id": "sess-1",
                "title": "Session One",
                "last_active": 123.0,
                "source": "cli",
                "workspace": None,
                "model": "hermes-agent",
                "token_summary": {"input": 1, "output": 2, "total": 3, "cache_read": 0, "cache_write": 0, "reasoning": 0},
                "parent_session_id": None,
                "has_tools": True,
                "has_attachments": False,
                "preview": "hello",
                "message_count": 2,
                "started_at": 100.0,
                "ended_at": None,
                "end_reason": None,
                "system_prompt": "system",
                "metadata": {
                    "user_id": "user-1",
                    "model_config": {"temperature": 0.2},
                    "billing_provider": None,
                    "billing_base_url": None,
                    "billing_mode": None,
                    "estimated_cost_usd": None,
                    "actual_cost_usd": None,
                    "cost_status": None,
                    "cost_source": None,
                    "pricing_version": None,
                },
                "recap": {"message_count": 2, "preview": "hello", "last_role": "assistant"},
            }
        }
        self.transcripts = {
            "sess-1": {
                "session_id": "sess-1",
                "items": [
                    {"id": 1, "type": "user_message", "role": "user", "content": "hello", "timestamp": 100.0},
                    {"id": 2, "type": "assistant_message", "role": "assistant", "content": "hi", "timestamp": 101.0},
                ],
            }
        }

    def list_sessions(self, *, source=None, limit=20, offset=0):
        items = list(self.sessions.values())
        if source:
            items = [item for item in items if item["source"] == source]
        return items[offset:offset + limit]

    def get_session_detail(self, session_id):
        return self.sessions.get(session_id)

    def get_transcript(self, session_id):
        return self.transcripts.get(session_id)

    def set_title(self, session_id, title):
        session = self.sessions.get(session_id)
        if session is None:
            return None
        if title == "bad title":
            raise ValueError("Title is invalid")
        session["title"] = title
        return {"session_id": session_id, "title": title}

    def resume_session(self, session_id):
        session = self.sessions.get(session_id)
        if session is None:
            return None
        return {
            "session_id": session_id,
            "status": "resumed",
            "resume_supported": True,
            "title": session["title"],
            "conversation_history": [
                {"role": "user", "content": "hello"},
                {"role": "assistant", "content": "hi"},
            ],
            "session": session,
        }

    def delete_session(self, session_id):
        return self.sessions.pop(session_id, None) is not None


class TestSessionsApi:
    @staticmethod
    async def _make_client(service: FakeSessionService) -> TestClient:
        app = web.Application()
        app[SESSIONS_SERVICE_APP_KEY] = service
        register_web_console_routes(app)
        client = TestClient(TestServer(app))
        await client.start_server()
        return client

    @pytest.mark.asyncio
    async def test_list_and_get_session_routes(self):
        client = await self._make_client(FakeSessionService())
        try:
            list_resp = await client.get("/api/gui/sessions")
            assert list_resp.status == 200
            list_payload = await list_resp.json()
            assert list_payload["ok"] is True
            session_summary = list_payload["sessions"][0]
            assert session_summary["session_id"] == "sess-1"
            assert session_summary["title"] == "Session One"
            assert session_summary["last_active"] == 123.0
            assert session_summary["source"] == "cli"
            assert session_summary["workspace"] is None
            assert session_summary["model"] == "hermes-agent"
            assert session_summary["token_summary"]["total"] == 3
            assert session_summary["parent_session_id"] is None
            assert session_summary["has_tools"] is True
            assert session_summary["has_attachments"] is False

            detail_resp = await client.get("/api/gui/sessions/sess-1")
            assert detail_resp.status == 200
            detail_payload = await detail_resp.json()
            assert detail_payload["ok"] is True
            assert detail_payload["session"]["title"] == "Session One"
            assert detail_payload["session"]["recap"]["last_role"] == "assistant"
            assert detail_payload["session"]["metadata"]["user_id"] == "user-1"
            assert detail_payload["session"]["metadata"]["model_config"]["temperature"] == 0.2
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_get_transcript_route(self):
        client = await self._make_client(FakeSessionService())
        try:
            resp = await client.get("/api/gui/sessions/sess-1/transcript")
            assert resp.status == 200
            payload = await resp.json()
            assert payload["ok"] is True
            assert payload["session_id"] == "sess-1"
            assert len(payload["items"]) == 2
            assert payload["items"][0]["type"] == "user_message"
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_title_resume_and_delete_routes(self):
        client = await self._make_client(FakeSessionService())
        try:
            title_resp = await client.post("/api/gui/sessions/sess-1/title", json={"title": "Renamed"})
            assert title_resp.status == 200
            title_payload = await title_resp.json()
            assert title_payload == {"ok": True, "session_id": "sess-1", "title": "Renamed"}

            resume_resp = await client.post("/api/gui/sessions/sess-1/resume")
            assert resume_resp.status == 200
            resume_payload = await resume_resp.json()
            assert resume_payload["ok"] is True
            assert resume_payload["status"] == "resumed"
            assert resume_payload["resume_supported"] is True
            assert resume_payload["conversation_history"][0]["role"] == "user"
            assert resume_payload["session"]["session_id"] == "sess-1"

            delete_resp = await client.delete("/api/gui/sessions/sess-1")
            assert delete_resp.status == 200
            delete_payload = await delete_resp.json()
            assert delete_payload == {"ok": True, "session_id": "sess-1", "deleted": True}
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_invalid_pagination_returns_structured_error(self):
        client = await self._make_client(FakeSessionService())
        try:
            resp = await client.get("/api/gui/sessions?limit=abc")
            assert resp.status == 400
            payload = await resp.json()
            assert payload == {
                "ok": False,
                "error": {
                    "code": "invalid_pagination",
                    "message": "The 'limit' field must be an integer.",
                },
            }
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_real_session_service_uses_sessiondb_storage(self, tmp_path):
        db = SessionDB(tmp_path / "state.db")
        db.create_session(
            session_id="sess-real",
            source="cli",
            model="hermes-agent",
            model_config={"temperature": 0.4},
            system_prompt="system prompt",
            user_id="user-real",
            parent_session_id=None,
        )
        db.append_message("sess-real", "user", content="hello")
        db.append_message("sess-real", "assistant", content="hi there")
        db.set_session_title("sess-real", "Real Session")
        db.update_token_counts("sess-real", input_tokens=5, output_tokens=7, model="hermes-agent")

        service = SessionService(db=db)
        sessions = service.list_sessions()
        assert sessions[0]["session_id"] == "sess-real"
        assert sessions[0]["token_summary"]["total"] == 12

        detail = service.get_session_detail("sess-real")
        assert detail["title"] == "Real Session"
        assert detail["metadata"]["user_id"] == "user-real"
        assert detail["metadata"]["model_config"]["temperature"] == 0.4

        transcript = service.get_transcript("sess-real")
        assert transcript["items"][0]["role"] == "user"
        assert transcript["items"][1]["role"] == "assistant"

        resumed = service.resume_session("sess-real")
        assert resumed["status"] == "resumed"
        assert resumed["conversation_history"][0]["role"] == "user"

        db.close()

    @pytest.mark.asyncio
    async def test_missing_and_invalid_requests_are_structured(self):
        client = await self._make_client(FakeSessionService())
        try:
            missing_resp = await client.get("/api/gui/sessions/missing")
            assert missing_resp.status == 404
            missing_payload = await missing_resp.json()
            assert missing_payload["error"]["code"] == "session_not_found"

            invalid_title_resp = await client.post(
                "/api/gui/sessions/sess-1/title",
                data="not json",
                headers={"Content-Type": "application/json"},
            )
            assert invalid_title_resp.status == 400
            invalid_title_payload = await invalid_title_resp.json()
            assert invalid_title_payload["error"]["code"] == "invalid_json"

            bad_title_resp = await client.post("/api/gui/sessions/sess-1/title", json={"title": "bad title"})
            assert bad_title_resp.status == 400
            bad_title_payload = await bad_title_resp.json()
            assert bad_title_payload["error"]["code"] == "invalid_title"
        finally:
            await client.close()
