"""Tests for human approval and clarification APIs."""

from __future__ import annotations

import threading

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

import time

from gateway.web_console.api.approvals import HUMAN_SERVICE_APP_KEY
from gateway.web_console.routes import register_web_console_routes
from gateway.web_console.services.approval_service import ApprovalService
from tools.approval import disable_session_yolo


def _wait_for_pending(service: ApprovalService, kind: str, timeout: float = 1.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        pending = service.list_pending()
        for item in pending:
            if item["kind"] == kind:
                return item
        time.sleep(0.01)
    return None


class TestApprovalServiceCallbacks:
    def test_approval_callback_round_trip(self):
        service = ApprovalService()
        callback = service.create_approval_callback(session_id="sess-1", run_id="run-1")
        result: dict[str, str] = {}

        def worker():
            result["value"] = callback("rm -rf /tmp/test", "Delete temporary files")

        thread = threading.Thread(target=worker)
        thread.start()
        pending = _wait_for_pending(service, "approval")
        assert pending is not None
        request_id = pending["request_id"]
        service.resolve_approval(request_id, "session")
        thread.join(timeout=2)
        assert result["value"] == "session"

    def test_clarify_callback_round_trip(self):
        service = ApprovalService()
        callback = service.create_clarify_callback(session_id="sess-2", run_id="run-2")
        result: dict[str, str] = {}

        def worker():
            result["value"] = callback("What kind of GUI?", ["web", "desktop"])

        thread = threading.Thread(target=worker)
        thread.start()
        pending = _wait_for_pending(service, "clarify")
        assert pending is not None
        request_id = pending["request_id"]
        service.resolve_clarification(request_id, "web")
        thread.join(timeout=2)
        assert result["value"] == "web"


class TestApprovalApiRoutes:
    @staticmethod
    async def _make_client(service: ApprovalService) -> TestClient:
        app = web.Application()
        app[HUMAN_SERVICE_APP_KEY] = service
        register_web_console_routes(app)
        client = TestClient(TestServer(app))
        await client.start_server()
        return client

    @pytest.mark.asyncio
    async def test_pending_approve_and_clarify_routes(self):
        service = ApprovalService()
        approval_callback = service.create_approval_callback(session_id="sess-1", run_id="run-1")
        clarify_callback = service.create_clarify_callback(session_id="sess-2", run_id="run-2")

        approval_result: dict[str, str] = {}
        clarify_result: dict[str, str] = {}

        approval_thread = threading.Thread(target=lambda: approval_result.setdefault("value", approval_callback("git push", "Push to origin")))
        clarify_thread = threading.Thread(target=lambda: clarify_result.setdefault("value", clarify_callback("Choose a GUI", ["web", "desktop"])))
        approval_thread.start()
        clarify_thread.start()
        assert _wait_for_pending(service, "approval") is not None
        assert _wait_for_pending(service, "clarify") is not None

        client = await self._make_client(service)
        try:
            pending_resp = await client.get("/api/gui/human/pending")
            assert pending_resp.status == 200
            pending_payload = await pending_resp.json()
            assert pending_payload["ok"] is True
            assert len(pending_payload["pending"]) == 2

            approval_request = next(item for item in pending_payload["pending"] if item["kind"] == "approval")
            clarify_request = next(item for item in pending_payload["pending"] if item["kind"] == "clarify")

            approve_resp = await client.post(
                "/api/gui/human/approve",
                json={"request_id": approval_request["request_id"], "decision": "always"},
            )
            assert approve_resp.status == 200
            approve_payload = await approve_resp.json()
            assert approve_payload["ok"] is True
            assert approve_payload["request"]["response"] == "always"

            clarify_resp = await client.post(
                "/api/gui/human/clarify",
                json={"request_id": clarify_request["request_id"], "response": "web"},
            )
            assert clarify_resp.status == 200
            clarify_payload = await clarify_resp.json()
            assert clarify_payload["ok"] is True
            assert clarify_payload["request"]["response"] == "web"
        finally:
            approval_thread.join(timeout=2)
            clarify_thread.join(timeout=2)
            await client.close()

        assert approval_result["value"] == "always"
        assert clarify_result["value"] == "web"

    @pytest.mark.asyncio
    async def test_deny_and_invalid_requests_are_structured(self):
        service = ApprovalService()
        approval_callback = service.create_approval_callback(session_id="sess-3", run_id="run-3")
        result: dict[str, str] = {}
        thread = threading.Thread(target=lambda: result.setdefault("value", approval_callback("sudo rm", "Dangerous command")))
        thread.start()
        assert _wait_for_pending(service, "approval") is not None

        client = await self._make_client(service)
        try:
            pending_payload = await (await client.get("/api/gui/human/pending")).json()
            approval_request = next(item for item in pending_payload["pending"] if item["kind"] == "approval")

            deny_resp = await client.post("/api/gui/human/deny", json={"request_id": approval_request["request_id"]})
            assert deny_resp.status == 200
            deny_payload = await deny_resp.json()
            assert deny_payload["ok"] is True
            assert deny_payload["request"]["response"] == "deny"

            invalid_json_resp = await client.post(
                "/api/gui/human/approve",
                data="not json",
                headers={"Content-Type": "application/json"},
            )
            assert invalid_json_resp.status == 400
            invalid_json_payload = await invalid_json_resp.json()
            assert invalid_json_payload["error"]["code"] == "invalid_json"

            missing_resp = await client.post("/api/gui/human/clarify", json={"request_id": "missing", "response": "x"})
            assert missing_resp.status == 404
            missing_payload = await missing_resp.json()
            assert missing_payload["error"]["code"] == "request_not_found"
        finally:
            thread.join(timeout=2)
            await client.close()

        assert result["value"] == "deny"

    @pytest.mark.asyncio
    async def test_yolo_routes_toggle_session_state(self):
        service = ApprovalService()
        client = await self._make_client(service)
        session_id = "sess-yolo"
        disable_session_yolo(session_id)

        try:
            status_resp = await client.get(f"/api/gui/human/yolo?session_id={session_id}")
            assert status_resp.status == 200
            status_payload = await status_resp.json()
            assert status_payload == {"ok": True, "session_id": session_id, "enabled": False}

            enable_resp = await client.post("/api/gui/human/yolo", json={"session_id": session_id, "enabled": True})
            assert enable_resp.status == 200
            enable_payload = await enable_resp.json()
            assert enable_payload == {"ok": True, "session_id": session_id, "enabled": True}

            disable_resp = await client.post("/api/gui/human/yolo", json={"session_id": session_id, "enabled": False})
            assert disable_resp.status == 200
            disable_payload = await disable_resp.json()
            assert disable_payload == {"ok": True, "session_id": session_id, "enabled": False}
        finally:
            disable_session_yolo(session_id)
            await client.close()
