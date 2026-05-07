"""Tests for the Formsy context engine plugin."""

import json
from inspect import iscoroutinefunction, signature

from plugins.context_engine.formsy.config import EngineConfigManager
from plugins.context_engine.formsy.engine import FormsyContextEngine
from plugins.formsy.models import (
    CompileBundle,
    CompiledMessage,
    Metrics,
    SceneType,
)


def test_formsy_engine_methods_match_context_engine_sync_contract():
    engine = FormsyContextEngine()

    assert engine.name == "formsy"

    assert not iscoroutinefunction(engine.update_from_response)
    assert not iscoroutinefunction(engine.should_compress)
    assert not iscoroutinefunction(engine.compress)

    assert list(signature(engine.update_from_response).parameters) == ["usage"]
    assert list(signature(engine.should_compress).parameters) == ["prompt_tokens"]
    assert list(signature(engine.compress).parameters) == [
        "messages",
        "current_tokens",
        "focus_topic",
    ]


def test_formsy_engine_tracks_usage_and_threshold():
    engine = FormsyContextEngine()
    engine.update_model(model="demo", context_length=1000)

    engine.update_from_response({
        "prompt_tokens": 760,
        "completion_tokens": 40,
        "total_tokens": 800,
    })

    assert engine.last_prompt_tokens == 760
    assert engine.last_completion_tokens == 40
    assert engine.last_total_tokens == 800
    assert engine.threshold_tokens == 750
    assert engine.should_compress() is True
    assert engine.should_compress(749) is False


def test_formsy_engine_compress_runs_client_and_returns_messages(monkeypatch):
    engine = FormsyContextEngine()
    calls = []

    class FakeClient:
        async def compile(self, **kwargs):
            calls.append(kwargs)
            return CompileBundle(
                scene=SceneType.GENERAL,
                compiled_messages=[
                    CompiledMessage(role="system", content="compiled context"),
                    CompiledMessage(role="user", content="latest request"),
                ],
                metrics=Metrics(elapsed_ms=12),
            )

    engine._engine_client = FakeClient()
    engine._config = type("Config", (), {"workspace_id": "ws_test"})()
    engine._session_id = "session-123"

    messages = [{"role": "user", "content": "fix the parser"}]

    result = engine.compress(messages, current_tokens=900, focus_topic="parser")

    assert result == [
        {"role": "system", "content": "compiled context"},
        {"role": "user", "content": "latest request"},
    ]
    assert engine.compression_count == 1
    assert calls[0]["workspace_id"] == "ws_test"
    assert calls[0]["session_id"] == "session-123"
    assert calls[0]["hints"]["focus_topic"] == "parser"
    assert calls[0]["task"]["instruction"] == "fix the parser"


def test_formsy_engine_exposes_memory_search_tool():
    engine = FormsyContextEngine()

    schemas = engine.get_tool_schemas()

    assert [schema["name"] for schema in schemas] == ["memory_search"]
    params = schemas[0]["parameters"]
    assert params["required"] == ["query"]
    assert "query" in params["properties"]
    assert "repo_id" in params["properties"]
    assert "revision" in params["properties"]
    assert "budget" in params["properties"]
    assert "limit" in params["properties"]
    assert "proactively" in schemas[0]["description"]
    assert "django__django-14053" in schemas[0]["description"]


def test_formsy_engine_memory_search_tool_queries_runtime():
    engine = FormsyContextEngine()
    calls = []

    class FakeClient:
        async def memory_search(self, **kwargs):
            calls.append(kwargs)
            return {
                "extra_context": "Relevant parser notes",
                "matches": [{"path": "parser.py", "score": 0.91}],
                "_latency_ms": 17,
            }

    engine._engine_client = FakeClient()
    engine._config = type("Config", (), {
        "repo_id": "django__django-14053",
        "revision": "latest",
        "query_budget": 4000,
    })()
    engine._session_id = "session-123"

    result = engine.handle_tool_call(
        "memory_search",
        {"query": "parser state handling", "repo_id": "django__django-14053", "budget": 3000},
    )

    data = json.loads(result)
    assert data["ok"] is True
    assert data["query"] == "parser state handling"
    assert data["extra_context"] == "Relevant parser notes"
    assert "results" not in data
    assert "matches" not in data
    assert calls == [{
        "repo_id": "django__django-14053",
        "session_id": "session-123",
        "query": "parser state handling",
        "revision": "latest",
        "budget": 3000,
    }]


def test_formsy_config_loads_global_formsy_config_when_session_kwargs_do_not_include_it(tmp_path, monkeypatch):
    """AIAgent.on_session_start passes runtime kwargs, not the full config."""
    monkeypatch.setattr(
        "hermes_cli.config.load_config",
        lambda: {
            "formsy": {
                "base_url": "http://localhost:8000",
                "memory_search_endpoint": "/api/v1/query",
                "repo_id": "django__django-14053",
                "revision": "latest",
                "query_budget": 4000,
                "workspace_id": "ws_local",
                "timeout_s": 45,
            }
        },
    )

    config = EngineConfigManager(tmp_path).load_config({
        "platform": "cli",
        "model": "demo",
    })

    assert config.base_url == "http://localhost:8000"
    assert config.memory_search_endpoint == "/api/v1/query"
    assert config.repo_id == "django__django-14053"
    assert config.revision == "latest"
    assert config.query_budget == 4000
    assert config.workspace_id == "ws_local"
    assert config.timeout_s == 45
