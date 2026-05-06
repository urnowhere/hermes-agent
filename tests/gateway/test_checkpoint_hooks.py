"""Tests for gateway checkpoint hooks on file-mutating tools."""

import sys
import threading
import types
from types import SimpleNamespace

import pytest

import gateway.run as gateway_run
from gateway.config import Platform
from gateway.session import SessionSource


class _RecordingCheckpointManager:
    def __init__(self):
        self.paths = []
        self.snapshots = []

    def get_working_dir_for_path(self, path):
        self.paths.append(path)
        return "/tmp/workspace"

    def ensure_checkpoint(self, work_dir, reason):
        self.snapshots.append((work_dir, reason))
        return True


class _CheckpointTriggerAgent:
    last_init = None
    checkpoint_path = "/tmp/workspace/app.py"

    def __init__(self, *args, **kwargs):
        type(self).last_init = dict(kwargs)
        self.tools = []
        self.tool_start_callback = None
        self.context_compressor = SimpleNamespace(last_prompt_tokens=0)
        self.session_prompt_tokens = 0
        self.session_completion_tokens = 0
        self.model = kwargs.get("model")
        self.session_id = kwargs.get("session_id")

    def run_conversation(self, user_message, conversation_history=None, task_id=None, persist_user_message=None):
        if self.tool_start_callback:
            self.tool_start_callback(
                "call_1",
                "write_file",
                {"path": type(self).checkpoint_path},
            )
        return {
            "final_response": "ok",
            "messages": [],
            "api_calls": 1,
            "completed": True,
        }

    def interrupt(self, _pending_text=None):
        return None


def _install_fake_agent(monkeypatch):
    fake_run_agent = types.ModuleType("run_agent")
    fake_run_agent.AIAgent = _CheckpointTriggerAgent
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
    runner._smart_model_routing = {}
    runner._running_agents = {}
    runner._pending_model_notes = {}
    runner._session_db = None
    runner._agent_cache = {}
    runner._agent_cache_lock = threading.Lock()
    runner._session_model_overrides = {}
    runner._draining = False
    runner.hooks = SimpleNamespace(loaded_hooks=False)
    runner.config = SimpleNamespace(streaming=SimpleNamespace(enabled=False, transport="off"))
    runner.session_store = SimpleNamespace(
        _entries={},
        get_or_create_session=lambda source: SimpleNamespace(session_id="session-1"),
        load_transcript=lambda session_id: [],
        _save=lambda: None,
    )
    runner._get_or_create_gateway_honcho = lambda session_key: (None, None)
    runner._enrich_message_with_vision = lambda *args, **kwargs: None
    runner._update_runtime_status = lambda *args, **kwargs: None
    runner._resolve_session_agent_runtime = lambda **kwargs: (
        "gpt-5.4",
        {
            "provider": "openrouter",
            "api_mode": "chat_completions",
            "base_url": "https://openrouter.ai/api/v1",
            "api_key": "test-key",
        },
    )
    runner._resolve_turn_agent_config = lambda message, model, runtime: {
        "model": model,
        "runtime": dict(runtime),
        "request_overrides": None,
    }
    runner._agent_config_signature = lambda *args: ("sig",)
    runner._load_reasoning_config = lambda: None
    runner._load_service_tier = lambda: None
    return runner


def _make_source() -> SessionSource:
    return SessionSource(
        platform=Platform.TELEGRAM,
        chat_id="12345",
        chat_type="dm",
        user_id="user-1",
    )


def test_checkpoint_tool_start_snapshots_write_file(monkeypatch, tmp_path):
    runner = _make_runner()
    mgr = _RecordingCheckpointManager()

    (tmp_path / "config.yaml").write_text(
        "checkpoints:\n  enabled: true\n  max_snapshots: 12\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(gateway_run, "_hermes_home", tmp_path)
    monkeypatch.setattr(gateway_run.GatewayRunner, "_build_checkpoint_manager", staticmethod(lambda cp_cfg: mgr))

    runner._checkpoint_tool_start("call_1", "write_file", {"path": "/tmp/workspace/app.py"})

    assert mgr.paths == ["/tmp/workspace/app.py"]
    assert mgr.snapshots == [("/tmp/workspace", "before write_file")]


@pytest.mark.asyncio
async def test_run_agent_wires_checkpoint_tool_start_callback(monkeypatch, tmp_path):
    _install_fake_agent(monkeypatch)
    runner = _make_runner()
    mgr = _RecordingCheckpointManager()

    (tmp_path / "config.yaml").write_text(
        "checkpoints:\n  enabled: true\n  max_snapshots: 9\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(gateway_run, "_hermes_home", tmp_path)
    monkeypatch.setattr(gateway_run, "_env_path", tmp_path / ".env")
    monkeypatch.setattr(gateway_run, "load_dotenv", lambda *args, **kwargs: None)
    monkeypatch.setattr(gateway_run, "_load_gateway_config", lambda: {"display": {"tool_progress": "off"}})
    monkeypatch.setattr(gateway_run.GatewayRunner, "_build_checkpoint_manager", staticmethod(lambda cp_cfg: mgr))

    import hermes_cli.tools_config as tools_config

    monkeypatch.setattr(tools_config, "_get_platform_tools", lambda user_config, platform_key: {"core"})

    result = await runner._run_agent(
        message="write the file",
        context_prompt="",
        history=[],
        source=_make_source(),
        session_id="session-1",
        session_key="agent:main:telegram:dm:12345",
    )

    assert result["final_response"] == "ok"
    assert _CheckpointTriggerAgent.last_init is not None
    assert mgr.paths == ["/tmp/workspace/app.py"]
    assert mgr.snapshots == [("/tmp/workspace", "before write_file")]
