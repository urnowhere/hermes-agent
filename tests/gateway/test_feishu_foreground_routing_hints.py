"""Runtime tests for Feishu foreground routing hints."""

import sys
import threading
import types
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import gateway.route_decision as route_decision
import gateway.route_envelope as route_envelope
import gateway.run as gateway_run

_REPO_ROOT = __import__("pathlib").Path(__file__).resolve().parents[2]
for _module in (route_decision, route_envelope):
    _module_path = __import__("pathlib").Path(_module.__file__).resolve()
    assert _module_path.is_relative_to(_REPO_ROOT), (
        f"{_module.__name__} resolved outside clean worktree: {_module_path}"
    )
from gateway.background_wakeups import clear_background_wake_manifest_cache
from gateway.config import Platform
from gateway.platforms.base import MessageEvent
from gateway.session import SessionSource


class _CapturingAgent:
    last_init = None
    last_run = None

    def __init__(self, *args, **kwargs):
        type(self).last_init = dict(kwargs)
        self.tools = []

    def run_conversation(self, user_message, conversation_history=None, task_id=None, persist_user_message=None):
        type(self).last_run = {
            "user_message": user_message,
            "conversation_history": conversation_history,
            "task_id": task_id,
            "persist_user_message": persist_user_message,
        }
        return {
            "final_response": "ok",
            "messages": [],
            "api_calls": 1,
            "completed": True,
        }


def _install_fake_agent(monkeypatch):
    fake_run_agent = types.ModuleType("run_agent")
    fake_run_agent.AIAgent = _CapturingAgent
    monkeypatch.setitem(sys.modules, "run_agent", fake_run_agent)


def _make_runner():
    runner = object.__new__(gateway_run.GatewayRunner)
    runner.adapters = {}
    runner._ephemeral_system_prompt = ""
    runner._prefill_messages = []
    runner._reasoning_config = None
    runner._service_tier = None
    runner._provider_routing = {}
    runner._fallback_model = None
    runner._running_agents = {}
    runner._pending_model_notes = {}
    runner._session_db = None
    runner._agent_cache = {}
    runner._agent_cache_lock = threading.Lock()
    runner._session_model_overrides = {}
    runner.hooks = SimpleNamespace(loaded_hooks=False)
    runner.config = SimpleNamespace(streaming=None)
    runner.session_store = SimpleNamespace(
        get_or_create_session=lambda source: SimpleNamespace(session_id="session-1"),
        load_transcript=lambda session_id: [],
    )
    runner._get_or_create_gateway_honcho = lambda session_key: (None, None)
    runner._enrich_message_with_vision = AsyncMock(return_value="ENRICHED")
    return runner


def _make_source(platform: Platform = Platform.FEISHU) -> SessionSource:
    return SessionSource(
        platform=platform,
        chat_id="chat-1",
        chat_type="dm",
        user_id="user-1",
    )


@pytest.fixture(autouse=True)
def isolated_hermes_home(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setattr(gateway_run, "_hermes_home", tmp_path)
    monkeypatch.setattr(gateway_run, "_env_path", tmp_path / ".env")
    clear_background_wake_manifest_cache()
    yield
    clear_background_wake_manifest_cache()


def _patch_runtime(monkeypatch):
    monkeypatch.setattr(gateway_run, "load_dotenv", lambda *args, **kwargs: None)
    monkeypatch.setattr(gateway_run, "_load_gateway_config", lambda: {})
    monkeypatch.setattr(gateway_run, "_resolve_gateway_model", lambda config=None: "gpt-5.4")
    monkeypatch.setattr(
        gateway_run,
        "_resolve_runtime_agent_kwargs",
        lambda: {
            "provider": "openrouter",
            "api_mode": "chat_completions",
            "base_url": "https://openrouter.ai/api/v1",
            "api_key": "***",
        },
    )

    import hermes_cli.tools_config as tools_config

    monkeypatch.setattr(
        tools_config,
        "_get_platform_tools",
        lambda user_config, platform_key: {
            "clarify",
            "file",
            "memory",
            "session_search",
            "skills",
            "terminal",
            "todo",
        },
    )


@pytest.mark.asyncio
async def test_run_agent_injects_feishu_director_and_capability_gap_hints(monkeypatch):
    _install_fake_agent(monkeypatch)
    _patch_runtime(monkeypatch)
    runner = _make_runner()

    _CapturingAgent.last_init = None
    _CapturingAgent.last_run = None
    result = await runner._run_agent(
        message="请帮我搜集行业资料和公开来源",
        context_prompt="Context prompt",
        history=[],
        source=_make_source(),
        session_id="session-1",
        session_key="agent:main:feishu:dm:chat-1",
    )

    assert result["final_response"] == "ok"
    assert _CapturingAgent.last_init is not None
    assert _CapturingAgent.last_run is not None
    assert "/bg" in _CapturingAgent.last_init["ephemeral_system_prompt"]
    assert "/research" in _CapturingAgent.last_init["ephemeral_system_prompt"]
    assert "/doc" in _CapturingAgent.last_init["ephemeral_system_prompt"]
    assert "/ppt" in _CapturingAgent.last_init["ephemeral_system_prompt"]
    assert "/repo" in _CapturingAgent.last_init["ephemeral_system_prompt"]
    assert "receipt" in _CapturingAgent.last_init["ephemeral_system_prompt"].lower()
    assert _CapturingAgent.last_run["user_message"].endswith("请帮我搜集行业资料和公开来源")
    assert "live worker lanes" in _CapturingAgent.last_run["user_message"].lower()
    assert "lane / wrapper / worker language" in _CapturingAgent.last_run["user_message"]
    assert "bran/claire/frank" in _CapturingAgent.last_run["user_message"].lower()
    assert "/research" in _CapturingAgent.last_run["user_message"]
    assert "RouteDecision shadow" in _CapturingAgent.last_run["user_message"]


@pytest.mark.asyncio
async def test_run_agent_keeps_plain_feishu_message_clean_when_no_capability_gap(monkeypatch):
    _install_fake_agent(monkeypatch)
    _patch_runtime(monkeypatch)
    runner = _make_runner()

    _CapturingAgent.last_init = None
    _CapturingAgent.last_run = None
    result = await runner._run_agent(
        message="把这段话润一下",
        context_prompt="Context prompt",
        history=[],
        source=_make_source(),
        session_id="session-1",
        session_key="agent:main:feishu:dm:chat-1",
    )

    assert result["final_response"] == "ok"
    assert "/bg" in _CapturingAgent.last_init["ephemeral_system_prompt"]
    assert "/research" in _CapturingAgent.last_init["ephemeral_system_prompt"]
    assert _CapturingAgent.last_run["user_message"] == "把这段话润一下"


@pytest.mark.asyncio
async def test_run_agent_does_not_inject_feishu_routing_hints_on_other_platforms(monkeypatch):
    _install_fake_agent(monkeypatch)
    _patch_runtime(monkeypatch)
    runner = _make_runner()

    _CapturingAgent.last_init = None
    _CapturingAgent.last_run = None
    result = await runner._run_agent(
        message="请帮我搜集行业资料和公开来源",
        context_prompt="Context prompt",
        history=[],
        source=_make_source(platform=Platform.TELEGRAM),
        session_id="session-1",
        session_key="agent:main:telegram:dm:chat-1",
    )

    assert result["final_response"] == "ok"
    assert _CapturingAgent.last_init["ephemeral_system_prompt"] == "Context prompt"
    assert _CapturingAgent.last_run["user_message"] == "请帮我搜集行业资料和公开来源"
