"""Tests for the web console chat service, chat API routes, and run tracking."""

from __future__ import annotations

import asyncio

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

import gateway.web_console.api.chat as chat_api
from gateway.web_console.api.chat import CHAT_SERVICE_APP_KEY
from gateway.web_console.routes import register_web_console_routes
from gateway.web_console.services.chat_service import ChatService
from gateway.web_console.state import MAX_TRACKED_RUNS, create_web_console_state


class TestChatService:
    @pytest.mark.asyncio
    async def test_default_runtime_runner_passes_gui_callback_into_agent_path(self, monkeypatch):
        import gateway.run as gateway_run
        import run_agent

        state = create_web_console_state()
        queue = await state.event_bus.subscribe("session-default")

        class FakeAgent:
            def __init__(self, *args, **kwargs):
                self.gui_event_callback = kwargs.get("gui_event_callback")
                self.session_id = kwargs.get("session_id")
                assert self.gui_event_callback is not None

            def run_conversation(self, user_message, conversation_history=None, task_id=None):
                self.gui_event_callback(
                    "tool.started",
                    {
                        "tool_name": "search_files",
                        "preview": "search_files(pattern=*.py)",
                        "tool_args": {"pattern": "*.py"},
                    },
                )
                self.gui_event_callback(
                    "tool.completed",
                    {
                        "tool_name": "search_files",
                        "duration": 0.01,
                        "result_preview": "found 2 files",
                    },
                )
                return {
                    "final_response": f"Handled: {user_message}",
                    "completed": True,
                    "messages": [
                        {"role": "assistant", "content": f"Handled: {user_message}"},
                    ],
                }

        monkeypatch.setattr(gateway_run, "_resolve_gateway_model", lambda: "hermes-agent")
        monkeypatch.setattr(gateway_run, "_resolve_runtime_agent_kwargs", lambda: {})
        monkeypatch.setattr(run_agent, "AIAgent", FakeAgent)

        service = ChatService(state=state)
        result = await service.run_chat(prompt="Hello from default runner", session_id="session-default")

        assert result["final_response"] == "Handled: Hello from default runner"
        events = [await queue.get() for _ in range(6)]
        assert [event.type for event in events] == [
            "run.started",
            "message.user",
            "tool.started",
            "tool.completed",
            "message.assistant.completed",
            "run.completed",
        ]

    @pytest.mark.asyncio
    async def test_default_runtime_runner_wires_human_service_callbacks(self, monkeypatch):
        import importlib
        import gateway.run as gateway_run
        import run_agent
        from gateway.web_console.services.approval_service import ApprovalService

        terminal_tool = importlib.import_module("tools.terminal_tool")

        captured: dict[str, object] = {}

        class FakeAgent:
            def __init__(self, *args, **kwargs):
                captured["clarify_callback"] = kwargs.get("clarify_callback")
                captured["gui_event_callback"] = kwargs.get("gui_event_callback")

            def run_conversation(self, user_message, conversation_history=None, task_id=None):
                return {
                    "final_response": f"Handled: {user_message}",
                    "completed": True,
                    "messages": [{"role": "assistant", "content": f"Handled: {user_message}"}],
                }

        approvals = ApprovalService()
        monkeypatch.setattr(gateway_run, "_resolve_gateway_model", lambda: "hermes-agent")
        monkeypatch.setattr(gateway_run, "_resolve_runtime_agent_kwargs", lambda: {})
        monkeypatch.setattr(run_agent, "AIAgent", FakeAgent)

        previous_approval_callback = terminal_tool._approval_callback
        service = ChatService(state=create_web_console_state(), human_service=approvals)
        result = await service.run_chat(prompt="Needs humans", session_id="session-human")

        assert result["final_response"] == "Handled: Needs humans"
        assert captured["clarify_callback"] is not None
        assert captured["gui_event_callback"] is not None
        assert terminal_tool._approval_callback is previous_approval_callback

    @pytest.mark.asyncio
    async def test_run_chat_publishes_expected_event_sequence(self):
        state = create_web_console_state()
        queue = await state.event_bus.subscribe("session-1")

        def runtime_runner(**kwargs):
            gui_event_callback = kwargs["gui_event_callback"]
            gui_event_callback(
                "tool.started",
                {
                    "tool_name": "search_files",
                    "tool_args": {"pattern": "*.py"},
                    "preview": "search_files(pattern=*.py)",
                },
            )
            gui_event_callback(
                "tool.completed",
                {
                    "tool_name": "search_files",
                    "duration": 0.01,
                    "result_preview": "found 3 files",
                },
            )
            return {
                "final_response": "Done.",
                "completed": True,
                "messages": [
                    {"role": "user", "content": kwargs["prompt"]},
                    {"role": "assistant", "content": "Done."},
                ],
            }

        service = ChatService(state=state, runtime_runner=runtime_runner)

        result = await service.run_chat(prompt="Find Python files", session_id="session-1")

        assert result["final_response"] == "Done."

        events = [await queue.get() for _ in range(6)]
        assert [event.type for event in events] == [
            "run.started",
            "message.user",
            "tool.started",
            "tool.completed",
            "message.assistant.completed",
            "run.completed",
        ]
        assert events[0].payload == {"prompt": "Find Python files"}
        assert events[1].payload == {"content": "Find Python files"}
        assert events[2].payload["tool_name"] == "search_files"
        assert events[3].payload["tool_name"] == "search_files"
        assert events[4].payload == {"content": "Done."}
        assert events[5].payload["final_response"] == "Done."

    @pytest.mark.asyncio
    async def test_run_chat_publishes_failed_event_for_failed_result(self):
        state = create_web_console_state()
        queue = await state.event_bus.subscribe("session-failed-result")

        def runtime_runner(**kwargs):
            return {
                "failed": True,
                "error": "toolchain aborted",
            }

        service = ChatService(state=state, runtime_runner=runtime_runner)
        result = await service.run_chat(prompt="Run something", session_id="session-failed-result")

        assert result["failed"] is True
        events = [await queue.get() for _ in range(3)]
        assert [event.type for event in events] == [
            "run.started",
            "message.user",
            "run.failed",
        ]
        assert events[-1].payload["error"] == "toolchain aborted"

    @pytest.mark.asyncio
    async def test_run_chat_publishes_failed_event_on_exception(self):
        state = create_web_console_state()
        queue = await state.event_bus.subscribe("session-2")

        def runtime_runner(**kwargs):
            gui_event_callback = kwargs["gui_event_callback"]
            gui_event_callback(
                "tool.started",
                {
                    "tool_name": "terminal",
                    "preview": "terminal(command=false)",
                },
            )
            raise RuntimeError("boom")

        service = ChatService(state=state, runtime_runner=runtime_runner)

        with pytest.raises(RuntimeError, match="boom"):
            await service.run_chat(prompt="Run something", session_id="session-2")

        events = [await queue.get() for _ in range(4)]
        assert [event.type for event in events] == [
            "run.started",
            "message.user",
            "tool.started",
            "run.failed",
        ]
        assert events[-1].payload["error"] == "boom"
        assert events[-1].payload["error_type"] == "RuntimeError"


class TestChatApiRoutes:
    @staticmethod
    async def _make_client(service: ChatService) -> TestClient:
        app = web.Application()
        app[CHAT_SERVICE_APP_KEY] = service
        register_web_console_routes(app)
        client = TestClient(TestServer(app))
        await client.start_server()
        return client

    @pytest.mark.asyncio
    async def test_send_and_get_run_return_tracked_metadata(self):
        state = create_web_console_state()

        async def runtime_runner(**kwargs):
            await asyncio.sleep(0.01)
            return {
                "final_response": f"Echo: {kwargs['prompt']}",
                "completed": True,
                "messages": [{"role": "assistant", "content": f"Echo: {kwargs['prompt']}"}],
            }

        service = ChatService(state=state, runtime_runner=runtime_runner)
        client = await self._make_client(service)

        try:
            send_resp = await client.post(
                "/api/gui/chat/send",
                json={"session_id": "session-api", "prompt": "Hello API"},
            )
            assert send_resp.status == 200
            send_payload = await send_resp.json()
            assert send_payload["ok"] is True
            assert send_payload["session_id"] == "session-api"
            assert send_payload["status"] == "started"
            run_id = send_payload["run_id"]

            run_resp = await client.get(f"/api/gui/chat/run/{run_id}")
            assert run_resp.status == 200
            run_payload = await run_resp.json()
            assert run_payload["ok"] is True
            assert run_payload["run"]["run_id"] == run_id
            assert run_payload["run"]["session_id"] == "session-api"
            assert run_payload["run"]["status"] in {"started", "completed"}

            await asyncio.sleep(0.03)

            completed_resp = await client.get(f"/api/gui/chat/run/{run_id}")
            completed_payload = await completed_resp.json()
            assert completed_payload["run"]["status"] == "completed"
            assert completed_payload["run"]["final_response"] == "Echo: Hello API"
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_retry_creates_new_run_from_existing_metadata(self):
        state = create_web_console_state()
        seen_prompts: list[str] = []

        async def runtime_runner(**kwargs):
            seen_prompts.append(kwargs["prompt"])
            return {
                "final_response": f"Handled: {kwargs['prompt']}",
                "completed": True,
                "messages": [{"role": "assistant", "content": f"Handled: {kwargs['prompt']}"}],
            }

        service = ChatService(state=state, runtime_runner=runtime_runner)
        client = await self._make_client(service)

        try:
            send_resp = await client.post(
                "/api/gui/chat/send",
                json={"session_id": "session-retry", "prompt": "Retry me"},
            )
            first_payload = await send_resp.json()
            first_run_id = first_payload["run_id"]

            await asyncio.sleep(0.01)

            retry_resp = await client.post("/api/gui/chat/retry", json={"run_id": first_run_id})
            assert retry_resp.status == 200
            retry_payload = await retry_resp.json()
            assert retry_payload["ok"] is True
            assert retry_payload["session_id"] == "session-retry"
            assert retry_payload["retried_from_run_id"] == first_run_id
            assert retry_payload["run_id"] != first_run_id
            assert retry_payload["status"] == "started"

            await asyncio.sleep(0.01)

            retried_run_resp = await client.get(f"/api/gui/chat/run/{retry_payload['run_id']}")
            retried_run_payload = await retried_run_resp.json()
            assert retried_run_payload["run"]["source_run_id"] == first_run_id
            assert retried_run_payload["run"]["prompt"] == "Retry me"
            assert seen_prompts == ["Retry me", "Retry me"]
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_invalid_session_id_and_undo_fields_return_validation_errors(self):
        state = create_web_console_state()
        service = ChatService(
            state=state,
            runtime_runner=lambda **kwargs: {
                "final_response": "ok",
                "completed": True,
                "messages": [{"role": "assistant", "content": "ok"}],
            },
        )
        client = await self._make_client(service)

        try:
            send_resp = await client.post("/api/gui/chat/send", json={"prompt": "hello", "session_id": 123})
            assert send_resp.status == 400
            send_payload = await send_resp.json()
            assert send_payload == {
                "ok": False,
                "error": {
                    "code": "invalid_session_id",
                    "message": "The 'session_id' field must be a non-empty string when provided.",
                },
            }

            undo_resp = await client.post("/api/gui/chat/undo", json={"session_id": 123, "run_id": []})
            assert undo_resp.status == 400
            undo_payload = await undo_resp.json()
            assert undo_payload == {
                "ok": False,
                "error": {
                    "code": "invalid_session_id",
                    "message": "The 'session_id' field must be a string when provided.",
                },
            }
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_invalid_json_requests_return_structured_errors(self):
        state = create_web_console_state()
        service = ChatService(
            state=state,
            runtime_runner=lambda **kwargs: {
                "final_response": "ok",
                "completed": True,
                "messages": [{"role": "assistant", "content": "ok"}],
            },
        )
        client = await self._make_client(service)

        try:
            for path in (
                "/api/gui/chat/send",
                "/api/gui/chat/stop",
                "/api/gui/chat/retry",
                "/api/gui/chat/undo",
            ):
                resp = await client.post(path, data="not json", headers={"Content-Type": "application/json"})
                assert resp.status == 400
                payload = await resp.json()
                assert payload == {
                    "ok": False,
                    "error": {
                        "code": "invalid_json",
                        "message": "Request body must be a valid JSON object.",
                    },
                }
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_run_tracking_is_bounded(self):
        state = create_web_console_state()
        for idx in range(MAX_TRACKED_RUNS):
            state.record_run(
                f"run-{idx}",
                {"run_id": f"run-{idx}", "prompt": f"prompt-{idx}", "status": "completed"},
            )

        state.record_run(
            "active-run",
            {"run_id": "active-run", "prompt": "active", "status": "started"},
        )

        assert len(state.runs) == MAX_TRACKED_RUNS
        assert state.get_run("run-0") is None
        assert state.get_run("active-run")["prompt"] == "active"

    @pytest.mark.asyncio
    async def test_run_tracking_remains_bounded_even_when_all_runs_are_active(self):
        state = create_web_console_state()
        for idx in range(MAX_TRACKED_RUNS + 3):
            state.record_run(
                f"active-{idx}",
                {"run_id": f"active-{idx}", "prompt": f"prompt-{idx}", "status": "started"},
            )

        assert len(state.runs) == MAX_TRACKED_RUNS
        assert state.get_run("active-0") is None
        assert state.get_run(f"active-{MAX_TRACKED_RUNS + 2}")["prompt"] == f"prompt-{MAX_TRACKED_RUNS + 2}"

    @pytest.mark.asyncio
    async def test_app_without_injected_chat_service_creates_isolated_local_state(self, monkeypatch):
        class FakeChatService(ChatService):
            async def run_chat(self, *, prompt, session_id, conversation_history=None, ephemeral_system_prompt=None, run_id=None, runtime_context=None):
                return {
                    "final_response": f"Echo: {prompt}",
                    "completed": True,
                    "messages": [{"role": "assistant", "content": f"Echo: {prompt}"}],
                }

        monkeypatch.setattr(chat_api, "ChatService", FakeChatService)

        app_one = web.Application()
        register_web_console_routes(app_one)
        app_two = web.Application()
        register_web_console_routes(app_two)

        client_one = TestClient(TestServer(app_one))
        client_two = TestClient(TestServer(app_two))
        await client_one.start_server()
        await client_two.start_server()

        try:
            send_resp = await client_one.post(
                "/api/gui/chat/send",
                json={"session_id": "isolated-session", "prompt": "hello from app one"},
            )
            assert send_resp.status == 200
            send_payload = await send_resp.json()
            run_id = send_payload["run_id"]

            await asyncio.sleep(0.02)

            run_resp = await client_one.get(f"/api/gui/chat/run/{run_id}")
            assert run_resp.status == 200

            missing_resp = await client_two.get(f"/api/gui/chat/run/{run_id}")
            assert missing_resp.status == 404
        finally:
            await client_one.close()
            await client_two.close()

    @pytest.mark.asyncio
    async def test_stop_undo_and_not_found_run_responses_are_structured(self):
        state = create_web_console_state()
        service = ChatService(
            state=state,
            runtime_runner=lambda **kwargs: {
                "final_response": "ok",
                "completed": True,
                "messages": [{"role": "assistant", "content": "ok"}],
            },
        )
        client = await self._make_client(service)

        try:
            send_resp = await client.post(
                "/api/gui/chat/send",
                json={"session_id": "session-stop", "prompt": "Stop test"},
            )
            send_payload = await send_resp.json()
            run_id = send_payload["run_id"]

            stop_resp = await client.post("/api/gui/chat/stop", json={"run_id": run_id})
            assert stop_resp.status == 200
            stop_payload = await stop_resp.json()
            assert stop_payload["ok"] is True
            assert stop_payload["supported"] is False
            assert stop_payload["action"] == "stop"
            assert stop_payload["run_id"] == run_id
            assert stop_payload["session_id"] == "session-stop"
            assert stop_payload["status"] in {"started", "completed"}
            assert stop_payload["stop_requested"] is False

            undo_resp = await client.post(
                "/api/gui/chat/undo",
                json={"session_id": "session-stop", "run_id": run_id},
            )
            assert undo_resp.status == 200
            undo_payload = await undo_resp.json()
            assert undo_payload == {
                "ok": True,
                "supported": False,
                "action": "undo",
                "session_id": "session-stop",
                "run_id": run_id,
                "status": "unavailable",
            }

            missing_resp = await client.get("/api/gui/chat/run/does-not-exist")
            assert missing_resp.status == 404
            missing_payload = await missing_resp.json()
            assert missing_payload == {
                "ok": False,
                "error": {
                    "code": "run_not_found",
                    "message": "No tracked run was found for the provided run_id.",
                    "run_id": "does-not-exist",
                },
            }
        finally:
            await client.close()
