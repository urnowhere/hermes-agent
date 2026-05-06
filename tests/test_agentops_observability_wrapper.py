"""Regression tests for AgentOps/native observability wrapper."""

import sys
from types import SimpleNamespace

from run_agent import _agentops_observe_run_conversation, _agentops_run_operation


class _FakeSessionDB:
    def __init__(self):
        self.started = []
        self.finished = []

    def record_run_start(self, **kwargs):
        self.started.append(kwargs)
        return "run-1"

    def record_run_finish(self, run_id, **kwargs):
        self.finished.append((run_id, kwargs))


class _FakeAgent:
    platform = "discord"
    provider = "openai-codex"
    model = "gpt-5.5"
    session_id = "session-1"
    session_api_calls = 2
    session_input_tokens = 100
    session_output_tokens = 25
    session_cache_read_tokens = 3
    session_cache_write_tokens = 4
    session_reasoning_tokens = 5
    session_estimated_cost_usd = 0.01

    def __init__(self):
        self._session_db = _FakeSessionDB()


def test_observability_wrapper_finishes_success_without_signature_error(monkeypatch):
    monkeypatch.setenv("HERMES_AGENTOPS_ENABLED", "0")

    @_agentops_observe_run_conversation
    def run_conversation(self):
        return {"completed": True, "interrupted": False}

    agent = _FakeAgent()
    assert run_conversation(agent) == {"completed": True, "interrupted": False}

    assert len(agent._session_db.finished) == 1
    run_id, finish_kwargs = agent._session_db.finished[0]
    assert run_id == "run-1"
    assert finish_kwargs["status"] == "success"
    assert finish_kwargs["api_call_count"] == 2
    assert finish_kwargs["input_tokens"] == 100
    assert finish_kwargs["output_tokens"] == 25


def test_observability_wrapper_finishes_error_then_reraises(monkeypatch):
    monkeypatch.setenv("HERMES_AGENTOPS_ENABLED", "0")

    @_agentops_observe_run_conversation
    def run_conversation(self):
        raise RuntimeError("boom")

    agent = _FakeAgent()
    try:
        run_conversation(agent)
    except RuntimeError as exc:
        assert str(exc) == "boom"
    else:
        raise AssertionError("expected RuntimeError")

    assert len(agent._session_db.finished) == 1
    run_id, finish_kwargs = agent._session_db.finished[0]
    assert run_id == "run-1"
    assert finish_kwargs["status"] == "error"
    assert finish_kwargs["error_type"] == "RuntimeError"
    assert finish_kwargs["error_message"] == "boom"


class _FakeAgentOps:
    def __init__(self, fail=False, fail_after_call=False):
        self.fail = fail
        self.fail_after_call = fail_after_call
        self.operations = []

    def operation(self, **kwargs):
        self.operations.append(kwargs)
        if self.fail:
            raise RuntimeError("agentops broken")

        def decorator(func):
            def wrapped(*args, **inner_kwargs):
                result = func(*args, **inner_kwargs)
                if self.fail_after_call:
                    raise RuntimeError("agentops broke after operation")
                return result
            return wrapped
        return decorator


def test_agentops_operation_helper_uses_manual_operation_without_capture(monkeypatch):
    fake_agentops = _FakeAgentOps()
    monkeypatch.setitem(sys.modules, "agentops", fake_agentops)
    monkeypatch.setenv("AGENTOPS_API_KEY", "test-key")
    monkeypatch.setenv("HERMES_AGENTOPS_ENABLED", "1")
    monkeypatch.setattr("run_agent._AGENTOPS_CLIENT_READY", True)

    result = _agentops_run_operation(
        "hermes.llm",
        {"provider": "openai-codex", "model": "gpt-5.5", "prompt": "secret"},
        lambda: "ok",
    )

    assert result == "ok"
    assert fake_agentops.operations == [
        {
            "name": "hermes.llm",
            "tags": {"provider": "openai-codex", "model": "gpt-5.5"},
            "capture_request": False,
            "capture_response": False,
        }
    ]


def test_agentops_operation_helper_fails_open(monkeypatch):
    fake_agentops = _FakeAgentOps(fail=True)
    monkeypatch.setitem(sys.modules, "agentops", fake_agentops)
    monkeypatch.setenv("AGENTOPS_API_KEY", "test-key")
    monkeypatch.setenv("HERMES_AGENTOPS_ENABLED", "1")
    monkeypatch.setattr("run_agent._AGENTOPS_CLIENT_READY", True)

    result = _agentops_run_operation(
        "hermes.llm",
        {"provider": "openai-codex", "messages": [{"role": "user", "content": "secret"}]},
        lambda: "ok",
    )

    assert result == "ok"


def test_agentops_operation_helper_does_not_repeat_completed_operation(monkeypatch):
    fake_agentops = _FakeAgentOps(fail_after_call=True)
    monkeypatch.setitem(sys.modules, "agentops", fake_agentops)
    monkeypatch.setenv("AGENTOPS_API_KEY", "test-key")
    monkeypatch.setenv("HERMES_AGENTOPS_ENABLED", "1")
    monkeypatch.setattr("run_agent._AGENTOPS_CLIENT_READY", True)
    calls = []

    result = _agentops_run_operation(
        "hermes.llm",
        {"provider": "openai-codex", "model": "gpt-5.5"},
        lambda: calls.append("called") or "ok",
    )

    assert result == "ok"
    assert calls == ["called"]


def test_agentops_operation_helper_preserves_operation_errors(monkeypatch):
    fake_agentops = _FakeAgentOps()
    monkeypatch.setitem(sys.modules, "agentops", fake_agentops)
    monkeypatch.setenv("AGENTOPS_API_KEY", "test-key")
    monkeypatch.setenv("HERMES_AGENTOPS_ENABLED", "1")
    monkeypatch.setattr("run_agent._AGENTOPS_CLIENT_READY", True)
    calls = []

    def failing_operation():
        calls.append("called")
        raise RuntimeError("provider failed")

    try:
        _agentops_run_operation("hermes.llm", {"provider": "openai-codex"}, failing_operation)
    except RuntimeError as exc:
        assert str(exc) == "provider failed"
    else:
        raise AssertionError("expected provider failure")

    assert calls == ["called"]
