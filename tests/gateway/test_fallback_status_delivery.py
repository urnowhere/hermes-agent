"""Regression tests for gateway fallback status delivery."""

import asyncio
import importlib
import sys
import types
from types import SimpleNamespace

import pytest

from gateway.config import Platform, PlatformConfig
from gateway.platforms.base import BasePlatformAdapter, SendResult
from gateway.session import SessionSource

PRIMARY_MODEL = "anthropic/claude-sonnet-4.6"


class StatusCaptureAdapter(BasePlatformAdapter):
    def __init__(self, platform=Platform.TELEGRAM):
        super().__init__(PlatformConfig(enabled=True, token="***"), platform)
        self.sent = []

    async def connect(self) -> bool:
        return True

    async def disconnect(self) -> None:
        return None

    async def send(self, chat_id, content, reply_to=None, metadata=None) -> SendResult:
        self.sent.append(
            {
                "chat_id": chat_id,
                "content": content,
                "reply_to": reply_to,
                "metadata": metadata,
            }
        )
        return SendResult(success=True, message_id="status-1")

    async def get_chat_info(self, chat_id: str):
        return {"id": chat_id}


class FakeAgentNoFallback:
    def __init__(self, **kwargs):
        self.status_callback = kwargs.get("status_callback")
        self.tool_progress_callback = kwargs.get("tool_progress_callback")
        self.model = PRIMARY_MODEL
        self.provider = "anthropic"
        self.tools = []

    def run_conversation(self, message, conversation_history=None, task_id=None):
        return {
            "final_response": "done",
            "messages": [],
            "api_calls": 1,
        }


class FakeAgentWithFallback:
    def __init__(self, **kwargs):
        self.status_callback = kwargs.get("status_callback")
        self.tool_progress_callback = kwargs.get("tool_progress_callback")
        self.model = "gpt-5.4"
        self.provider = "openai-codex"
        self.tools = []

    def run_conversation(self, message, conversation_history=None, task_id=None):
        return {
            "final_response": "done",
            "messages": [],
            "api_calls": 1,
        }


def _make_runner(adapter):
    gateway_run = importlib.import_module("gateway.run")
    GatewayRunner = gateway_run.GatewayRunner

    runner = object.__new__(GatewayRunner)
    runner.adapters = {adapter.platform: adapter}
    runner._voice_mode = {}
    runner._prefill_messages = []
    runner._ephemeral_system_prompt = ""
    runner._reasoning_config = None
    runner._provider_routing = {}
    runner._fallback_model = None
    runner._session_db = None
    runner._running_agents = {}
    runner._effective_model = None
    runner._effective_provider = None
    runner.hooks = SimpleNamespace(loaded_hooks=False)
    return runner


def _source():
    return SessionSource(
        platform=Platform.TELEGRAM,
        chat_id="12345",
        chat_type="dm",
        thread_id=None,
        user_id="u1",
    )


@pytest.mark.asyncio
async def test_gateway_suppresses_fallback_status_when_agent_finishes_on_primary(monkeypatch, tmp_path):
    fake_dotenv = types.ModuleType("dotenv")
    fake_dotenv.load_dotenv = lambda *args, **kwargs: None
    monkeypatch.setitem(sys.modules, "dotenv", fake_dotenv)

    fake_run_agent = types.ModuleType("run_agent")
    fake_run_agent.AIAgent = FakeAgentNoFallback
    monkeypatch.setitem(sys.modules, "run_agent", fake_run_agent)

    adapter = StatusCaptureAdapter()
    runner = _make_runner(adapter)
    gateway_run = importlib.import_module("gateway.run")
    monkeypatch.setattr(gateway_run, "_hermes_home", tmp_path)
    monkeypatch.setattr(gateway_run, "_resolve_runtime_agent_kwargs", lambda: {"api_key": "***"})
    (tmp_path / "config.yaml").write_text(
        "model:\n  default: anthropic/claude-sonnet-4.6\n  provider: anthropic\n",
        encoding="utf-8",
    )

    result = await runner._run_agent(
        message="hello",
        context_prompt="",
        history=[],
        source=_source(),
        session_id="sess-1",
        session_key="agent:main:telegram:dm:12345",
    )

    assert result["final_response"] == "done"
    assert adapter.sent == []
    assert runner._effective_model is None
    assert runner._effective_provider is None


@pytest.mark.asyncio
async def test_gateway_delivers_fallback_status_when_agent_finishes_on_fallback(monkeypatch, tmp_path):
    fake_dotenv = types.ModuleType("dotenv")
    fake_dotenv.load_dotenv = lambda *args, **kwargs: None
    monkeypatch.setitem(sys.modules, "dotenv", fake_dotenv)

    fake_run_agent = types.ModuleType("run_agent")
    fake_run_agent.AIAgent = FakeAgentWithFallback
    monkeypatch.setitem(sys.modules, "run_agent", fake_run_agent)

    adapter = StatusCaptureAdapter()
    runner = _make_runner(adapter)
    gateway_run = importlib.import_module("gateway.run")
    monkeypatch.setattr(gateway_run, "_hermes_home", tmp_path)
    monkeypatch.setattr(gateway_run, "_resolve_runtime_agent_kwargs", lambda: {"api_key": "***"})
    (tmp_path / "config.yaml").write_text(
        "model:\n  default: anthropic/claude-sonnet-4.6\n  provider: anthropic\n",
        encoding="utf-8",
    )

    result = await runner._run_agent(
        message="hello",
        context_prompt="",
        history=[],
        source=_source(),
        session_id="sess-2",
        session_key="agent:main:telegram:dm:12345",
    )

    assert result["final_response"] == "done"
    assert adapter.sent == []
    assert runner._effective_model == "gpt-5.4"
    assert runner._effective_provider == "openai-codex"
