"""Tests for the web console skills API and skill service."""

from __future__ import annotations

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from hermes_state import SessionDB
from gateway.web_console.api.skills import SKILLS_SERVICE_APP_KEY
from gateway.web_console.routes import register_web_console_routes
from gateway.web_console.services.skill_service import SkillService


class FakeSkillService:
    def __init__(self) -> None:
        self.skills = {
            "planner": {
                "name": "planner",
                "description": "Plan complex work.",
                "category": "workflow",
                "path": "workflow/planner/SKILL.md",
                "content": "# Planner",
                "source": "official",
                "source_type": "hub",
                "trust_level": "trusted",
                "identifier": "official/workflow/planner",
                "install_path": "/tmp/planner",
                "scan_verdict": "allow",
                "installed_at": "2026-03-30T00:00:00+00:00",
                "updated_at": "2026-03-30T00:00:00+00:00",
                "installed_metadata": {"author": "Hermes"},
                "readiness_status": "available",
                "setup_needed": False,
            },
            "blocked": {
                "name": "blocked",
                "description": "Needs setup first.",
                "category": "workflow",
                "path": "workflow/blocked/SKILL.md",
                "content": "# Blocked",
                "source": "local",
                "source_type": "local",
                "trust_level": "local",
                "identifier": None,
                "install_path": None,
                "scan_verdict": None,
                "installed_at": None,
                "updated_at": None,
                "installed_metadata": {},
                "readiness_status": "setup_needed",
                "setup_needed": True,
            },
        }
        self.session_skills = {
            "sess-1": {
                "planner": {
                    "name": "planner",
                    "description": "Plan complex work.",
                    "path": "workflow/planner/SKILL.md",
                    "source": "official",
                    "source_type": "hub",
                    "trust_level": "trusted",
                    "readiness_status": "available",
                    "setup_needed": False,
                    "loaded_at": "2026-03-30T00:00:00+00:00",
                }
            }
        }

    def list_skills(self):
        return {
            "skills": [self.skills["planner"], self.skills["blocked"]],
            "categories": ["workflow"],
            "count": 2,
            "hint": "Use the detail endpoint for full content.",
        }

    def get_skill(self, name: str):
        if name == "missing":
            raise FileNotFoundError(name)
        if name == "blocked":
            raise ValueError("Skill 'blocked' is disabled.")
        return self.skills[name]

    def load_skill_for_session(self, session_id: str, name: str):
        if session_id == "missing-session":
            raise LookupError("session_not_found")
        if name == "missing":
            raise FileNotFoundError(name)
        if name == "blocked":
            raise ValueError("Skill 'blocked' is disabled.")
        loaded = self.session_skills.setdefault(session_id, {})
        already_loaded = name in loaded
        skill = loaded.get(name) or {
            "name": name,
            "description": self.skills[name]["description"],
            "path": self.skills[name]["path"],
            "source": self.skills[name]["source"],
            "source_type": self.skills[name]["source_type"],
            "trust_level": self.skills[name]["trust_level"],
            "readiness_status": self.skills[name]["readiness_status"],
            "setup_needed": self.skills[name]["setup_needed"],
            "loaded_at": "2026-03-30T01:00:00+00:00",
        }
        loaded[name] = skill
        return {"session_id": session_id, "skill": skill, "loaded": True, "already_loaded": already_loaded}

    def list_session_skills(self, session_id: str):
        if session_id == "missing-session":
            raise LookupError("session_not_found")
        skills = sorted(self.session_skills.get(session_id, {}).values(), key=lambda item: item["name"])
        return {"session_id": session_id, "skills": skills, "count": len(skills)}

    def unload_skill_for_session(self, session_id: str, name: str):
        if session_id == "missing-session":
            raise LookupError("session_not_found")
        removed = self.session_skills.get(session_id, {}).pop(name, None) is not None
        return {"session_id": session_id, "name": name, "removed": removed}


class TestSkillsApi:
    @staticmethod
    async def _make_client(service: FakeSkillService) -> TestClient:
        app = web.Application()
        app[SKILLS_SERVICE_APP_KEY] = service
        register_web_console_routes(app)
        client = TestClient(TestServer(app))
        await client.start_server()
        return client

    @pytest.mark.asyncio
    async def test_list_and_get_skill_routes(self):
        client = await self._make_client(FakeSkillService())
        try:
            list_resp = await client.get("/api/gui/skills")
            assert list_resp.status == 200
            list_payload = await list_resp.json()
            assert list_payload["ok"] is True
            assert list_payload["count"] == 2
            assert list_payload["categories"] == ["workflow"]
            assert list_payload["skills"][0]["name"] == "planner"
            assert list_payload["skills"][0]["source_type"] == "hub"
            assert list_payload["skills"][0]["installed_metadata"] == {"author": "Hermes"}

            detail_resp = await client.get("/api/gui/skills/planner")
            assert detail_resp.status == 200
            detail_payload = await detail_resp.json()
            assert detail_payload["ok"] is True
            assert detail_payload["skill"]["name"] == "planner"
            assert detail_payload["skill"]["content"] == "# Planner"
            assert detail_payload["skill"]["trust_level"] == "trusted"
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_session_skill_load_list_and_unload_routes(self):
        client = await self._make_client(FakeSkillService())
        try:
            load_resp = await client.post("/api/gui/skills/planner/load", json={"session_id": "sess-1"})
            assert load_resp.status == 200
            load_payload = await load_resp.json()
            assert load_payload["ok"] is True
            assert load_payload["session_id"] == "sess-1"
            assert load_payload["skill"]["name"] == "planner"
            assert load_payload["already_loaded"] is True

            session_resp = await client.get("/api/gui/skills/session/sess-1")
            assert session_resp.status == 200
            session_payload = await session_resp.json()
            assert session_payload["ok"] is True
            assert session_payload["count"] == 1
            assert session_payload["skills"][0]["name"] == "planner"

            unload_resp = await client.delete("/api/gui/skills/session/sess-1/planner")
            assert unload_resp.status == 200
            unload_payload = await unload_resp.json()
            assert unload_payload == {"ok": True, "session_id": "sess-1", "name": "planner", "removed": True}

            session_after_resp = await client.get("/api/gui/skills/session/sess-1")
            session_after_payload = await session_after_resp.json()
            assert session_after_payload["count"] == 0
            assert session_after_payload["skills"] == []
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_skills_api_returns_structured_errors(self):
        client = await self._make_client(FakeSkillService())
        try:
            invalid_json_resp = await client.post(
                "/api/gui/skills/planner/load",
                data="not json",
                headers={"Content-Type": "application/json"},
            )
            assert invalid_json_resp.status == 400
            invalid_json_payload = await invalid_json_resp.json()
            assert invalid_json_payload["error"]["code"] == "invalid_json"

            invalid_session_resp = await client.post("/api/gui/skills/planner/load", json={})
            assert invalid_session_resp.status == 400
            invalid_session_payload = await invalid_session_resp.json()
            assert invalid_session_payload["error"]["code"] == "invalid_session_id"

            missing_skill_resp = await client.get("/api/gui/skills/missing")
            assert missing_skill_resp.status == 404
            missing_skill_payload = await missing_skill_resp.json()
            assert missing_skill_payload["error"]["code"] == "skill_not_found"

            blocked_skill_resp = await client.get("/api/gui/skills/blocked")
            assert blocked_skill_resp.status == 400
            blocked_skill_payload = await blocked_skill_resp.json()
            assert blocked_skill_payload["error"]["code"] == "skill_unavailable"

            missing_session_resp = await client.get("/api/gui/skills/session/missing-session")
            assert missing_session_resp.status == 404
            missing_session_payload = await missing_session_resp.json()
            assert missing_session_payload["error"]["code"] == "session_not_found"
        finally:
            await client.close()


class TestSkillService:
    def test_real_service_tracks_loaded_skills_and_metadata(self, tmp_path, monkeypatch):
        db = SessionDB(tmp_path / "state.db")
        db.create_session(
            session_id="sess-real",
            source="cli",
            model="hermes-agent",
            model_config={"temperature": 0.1},
            system_prompt="system prompt",
            user_id="user-1",
            parent_session_id=None,
        )

        service = SkillService(db=db)

        monkeypatch.setattr(
            SkillService,
            "list_skills",
            lambda self: {
                "skills": [
                    {
                        "name": "planner",
                        "description": "Plan work.",
                        "category": "workflow",
                        "source": "builtin",
                        "source_type": "builtin",
                        "trust_level": "builtin",
                    }
                ],
                "categories": ["workflow"],
                "count": 1,
                "hint": None,
            },
        )
        monkeypatch.setattr(
            SkillService,
            "get_skill",
            lambda self, name: {
                "name": name,
                "description": "Plan work.",
                "path": "workflow/planner/SKILL.md",
                "source": "builtin",
                "source_type": "builtin",
                "trust_level": "builtin",
                "readiness_status": "available",
                "setup_needed": False,
            },
        )

        listing = service.list_skills()
        assert listing["count"] == 1
        assert listing["skills"][0]["name"] == "planner"

        loaded = service.load_skill_for_session("sess-real", "planner")
        assert loaded["loaded"] is True
        assert loaded["already_loaded"] is False
        assert loaded["skill"]["source_type"] == "builtin"

        loaded_again = service.load_skill_for_session("sess-real", "planner")
        assert loaded_again["already_loaded"] is True

        session_skills = service.list_session_skills("sess-real")
        assert session_skills["count"] == 1
        assert session_skills["skills"][0]["name"] == "planner"

        removed = service.unload_skill_for_session("sess-real", "planner")
        assert removed == {"session_id": "sess-real", "name": "planner", "removed": True}
        assert service.list_session_skills("sess-real")["skills"] == []

        service.load_skill_for_session("sess-real", "planner")
        monkeypatch.setattr(
            SkillService,
            "get_skill",
            lambda self, name: {
                "name": "planner",
                "description": "Plan work.",
                "path": "workflow/planner/SKILL.md",
                "source": "builtin",
                "source_type": "builtin",
                "trust_level": "builtin",
                "readiness_status": "available",
                "setup_needed": False,
            },
        )
        removed_via_alias = service.unload_skill_for_session("sess-real", "Planner")
        assert removed_via_alias == {"session_id": "sess-real", "name": "planner", "removed": True}
        assert service.list_session_skills("sess-real")["skills"] == []

        with pytest.raises(LookupError):
            service.list_session_skills("missing")

        db.close()
