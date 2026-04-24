"""Tests for the model_observability plugin.

Covers:
  * Plugin registration: register(ctx) wires two hooks.
  * _on_post_api_request: writes a valid JSONL record per API call.
  * _on_session_start: writes a session boundary marker.
  * _models_match: exact, date-suffix alias, token-reorder alias, auto-router.
  * LOG_PATH: writes to the path pointed to by the module-level constant.
  * Graceful failure: bad kwargs never raise; plugin must not break the agent loop.
  * Concurrent writes: two threads write simultaneously; log stays valid JSONL.
  * plugin.yaml: correct name, version, hooks declared.
"""
from __future__ import annotations

import importlib
import importlib.util
import json
import sys
import threading
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_plugin(tmp_path, monkeypatch):
    """Load the plugin module with LOG_PATH redirected to tmp_path."""
    repo_root = Path(__file__).resolve().parents[2]
    init_path = repo_root / "plugins" / "model_observability" / "__init__.py"

    # Remove stale cached module so monkeypatching LOG_PATH works cleanly.
    for key in list(sys.modules):
        if "model_observability" in key:
            del sys.modules[key]

    spec = importlib.util.spec_from_file_location(
        "model_observability_under_test", init_path
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    # Redirect the module-level LOG_PATH to a temp file.
    log_file = tmp_path / "model_usage.jsonl"
    monkeypatch.setattr(mod, "LOG_PATH", log_file)

    return mod, log_file


class _StubPluginContext:
    """Minimal PluginContext stub that records registered hooks."""

    def __init__(self):
        self.hooks: dict[str, list] = {}

    def register_hook(self, hook_name: str, fn) -> None:
        self.hooks.setdefault(hook_name, []).append(fn)


def _read_jsonl(path: Path) -> list[dict]:
    """Parse a JSONL file into a list of dicts."""
    lines = [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return [json.loads(line) for line in lines]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def plugin(tmp_path, monkeypatch):
    mod, log_file = _load_plugin(tmp_path, monkeypatch)
    return mod, log_file


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

class TestRegistration:

    def test_register_wires_post_api_request_hook(self, plugin):
        mod, _ = plugin
        ctx = _StubPluginContext()
        mod.register(ctx)
        assert "post_api_request" in ctx.hooks
        assert len(ctx.hooks["post_api_request"]) == 1

    def test_register_wires_on_session_start_hook(self, plugin):
        mod, _ = plugin
        ctx = _StubPluginContext()
        mod.register(ctx)
        assert "on_session_start" in ctx.hooks
        assert len(ctx.hooks["on_session_start"]) == 1

    def test_register_exposes_exactly_two_hooks(self, plugin):
        mod, _ = plugin
        ctx = _StubPluginContext()
        mod.register(ctx)
        assert set(ctx.hooks.keys()) == {"post_api_request", "on_session_start"}


# ---------------------------------------------------------------------------
# _on_post_api_request
# ---------------------------------------------------------------------------

class TestPostApiRequest:

    def _call(self, mod, **kwargs):
        """Fire the hook with minimal defaults filled in."""
        defaults = dict(
            task_id="task-abc",
            session_id="sess-123",
            platform="telegram",
            model="openrouter/auto",
            provider="openrouter",
            api_mode="chat_completions",
            api_call_count=1,
            api_duration=1.234,
            finish_reason="stop",
            response_model="google/gemini-2.5-flash",
            usage={"prompt_tokens": 100, "completion_tokens": 50},
            assistant_content_chars=42,
            assistant_tool_call_count=0,
        )
        defaults.update(kwargs)
        mod._on_post_api_request(**defaults)

    def test_writes_one_jsonl_record(self, plugin):
        mod, log_file = plugin
        self._call(mod)
        records = _read_jsonl(log_file)
        assert len(records) == 1

    def test_record_has_required_fields(self, plugin):
        mod, log_file = plugin
        self._call(mod)
        rec = _read_jsonl(log_file)[0]
        for field in ("ts", "session_id", "task_id", "agent_type",
                      "model_request", "model_response", "provider",
                      "api_mode", "api_call", "duration_s", "finish_reason",
                      "tokens_in", "tokens_out", "assistant_chars",
                      "tool_calls", "platform", "match"):
            assert field in rec, f"Missing field: {field}"

    def test_agent_type_is_subagent_when_task_id_set(self, plugin):
        mod, log_file = plugin
        self._call(mod, task_id="task-xyz")
        rec = _read_jsonl(log_file)[0]
        assert rec["agent_type"] == "subagent"

    def test_agent_type_is_parent_when_task_id_none(self, plugin):
        mod, log_file = plugin
        self._call(mod, task_id=None)
        rec = _read_jsonl(log_file)[0]
        assert rec["agent_type"] == "parent"

    def test_model_request_and_response_captured(self, plugin):
        mod, log_file = plugin
        self._call(mod, model="x-ai/grok-4.20", response_model="x-ai/grok-4.20-20260101")
        rec = _read_jsonl(log_file)[0]
        assert rec["model_request"] == "x-ai/grok-4.20"
        assert rec["model_response"] == "x-ai/grok-4.20-20260101"

    def test_tokens_extracted_from_prompt_completion_keys(self, plugin):
        mod, log_file = plugin
        self._call(mod, usage={"prompt_tokens": 200, "completion_tokens": 80})
        rec = _read_jsonl(log_file)[0]
        assert rec["tokens_in"] == 200
        assert rec["tokens_out"] == 80

    def test_tokens_extracted_from_input_output_keys(self, plugin):
        """Anthropic usage dict uses input_tokens/output_tokens."""
        mod, log_file = plugin
        self._call(mod, usage={"input_tokens": 300, "output_tokens": 120})
        rec = _read_jsonl(log_file)[0]
        assert rec["tokens_in"] == 300
        assert rec["tokens_out"] == 120

    def test_missing_usage_yields_zero_tokens(self, plugin):
        mod, log_file = plugin
        self._call(mod, usage=None)
        rec = _read_jsonl(log_file)[0]
        assert rec["tokens_in"] == 0
        assert rec["tokens_out"] == 0

    def test_duration_rounded_to_three_decimals(self, plugin):
        mod, log_file = plugin
        self._call(mod, api_duration=2.123456789)
        rec = _read_jsonl(log_file)[0]
        assert rec["duration_s"] == 2.123

    def test_multiple_calls_append_multiple_records(self, plugin):
        mod, log_file = plugin
        self._call(mod, session_id="s1")
        self._call(mod, session_id="s2")
        self._call(mod, session_id="s3")
        records = _read_jsonl(log_file)
        assert len(records) == 3

    def test_graceful_on_completely_empty_kwargs(self, plugin):
        """Must never raise — plugin must not break the agent loop."""
        mod, _ = plugin
        mod._on_post_api_request()  # Must not raise.

    def test_graceful_on_corrupt_usage_dict(self, plugin):
        mod, _ = plugin
        mod._on_post_api_request(usage={"prompt_tokens": "not-a-number"})  # Must not raise.


# ---------------------------------------------------------------------------
# _on_session_start
# ---------------------------------------------------------------------------

class TestSessionStart:

    def test_writes_session_boundary_marker(self, plugin):
        mod, log_file = plugin
        mod._on_session_start(session_id="sess-abc", platform="cli", model="gpt-5-nano", provider="openai")
        records = _read_jsonl(log_file)
        assert len(records) == 1
        rec = records[0]
        assert rec["event"] == "session_start"
        assert rec["session_id"] == "sess-abc"

    def test_session_marker_has_required_fields(self, plugin):
        mod, log_file = plugin
        mod._on_session_start(session_id="x", platform="telegram", model="m", provider="p")
        rec = _read_jsonl(log_file)[0]
        for field in ("ts", "event", "session_id", "platform", "model", "provider"):
            assert field in rec, f"Missing field: {field}"

    def test_session_marker_does_not_have_agent_type(self, plugin):
        """Session markers are boundary records, not API call records."""
        mod, log_file = plugin
        mod._on_session_start(session_id="x", platform="cli", model="m", provider="p")
        rec = _read_jsonl(log_file)[0]
        assert "agent_type" not in rec

    def test_graceful_on_empty_kwargs(self, plugin):
        mod, _ = plugin
        mod._on_session_start()  # Must not raise.


# ---------------------------------------------------------------------------
# _models_match
# ---------------------------------------------------------------------------

class TestModelsMatch:

    def test_exact_match(self, plugin):
        mod, _ = plugin
        assert mod._models_match("google/gemini-2.5-flash", "google/gemini-2.5-flash") is True

    def test_case_insensitive_match(self, plugin):
        mod, _ = plugin
        assert mod._models_match("OpenAI/GPT-5", "openai/gpt-5") is True

    def test_date_suffix_alias_match(self, plugin):
        """Response model has a date suffix appended — should be a match."""
        mod, _ = plugin
        assert mod._models_match(
            "anthropic/claude-sonnet-4.6",
            "anthropic/claude-sonnet-4.6-20260217",
        ) is True

    def test_token_reorder_alias_match(self, plugin):
        """Anthropic reorders tokens: claude-sonnet-4.6 -> claude-4.6-sonnet-20260217."""
        mod, _ = plugin
        assert mod._models_match(
            "anthropic/claude-sonnet-4.6",
            "anthropic/claude-4.6-sonnet-20260217",
        ) is True

    def test_auto_router_is_never_a_match(self, plugin):
        """openrouter/auto -> anything is always a real resolution, never a match."""
        mod, _ = plugin
        assert mod._models_match("openrouter/auto", "google/gemini-2.5-flash") is False

    def test_bare_auto_is_never_a_match(self, plugin):
        mod, _ = plugin
        assert mod._models_match("auto", "x-ai/grok-4.20") is False

    def test_genuinely_different_models_do_not_match(self, plugin):
        mod, _ = plugin
        assert mod._models_match("x-ai/grok-4.20", "google/gemini-2.5-flash") is False

    def test_empty_strings_do_not_match(self, plugin):
        mod, _ = plugin
        assert mod._models_match("", "") is False

    def test_none_values_do_not_match(self, plugin):
        mod, _ = plugin
        assert mod._models_match(None, None) is False


# ---------------------------------------------------------------------------
# Concurrent write safety
# ---------------------------------------------------------------------------

class TestConcurrentWrites:

    def test_two_threads_produce_valid_jsonl(self, plugin):
        """Concurrent writes must not produce interleaved/corrupt JSON lines."""
        mod, log_file = plugin

        errors = []

        def write_records():
            try:
                for i in range(20):
                    mod._on_post_api_request(
                        task_id=f"task-{threading.current_thread().name}-{i}",
                        session_id="sess-concurrent",
                        platform="cli",
                        model="openrouter/auto",
                        provider="openrouter",
                        api_mode="chat_completions",
                        api_call_count=i,
                        api_duration=0.1,
                        finish_reason="stop",
                        response_model="google/gemini-2.5-flash",
                        usage={"prompt_tokens": 10, "completion_tokens": 5},
                        assistant_content_chars=5,
                        assistant_tool_call_count=0,
                    )
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=write_records, name=f"t{i}") for i in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"Thread errors: {errors}"
        records = _read_jsonl(log_file)
        assert len(records) == 40, f"Expected 40 records, got {len(records)}"


# ---------------------------------------------------------------------------
# plugin.yaml discovery
# ---------------------------------------------------------------------------

class TestPluginYaml:

    def test_plugin_yaml_declares_correct_hooks(self):
        """plugin.yaml must declare both hooks."""
        import yaml
        repo_root = Path(__file__).resolve().parents[2]
        yaml_path = repo_root / "plugins" / "model_observability" / "plugin.yaml"
        data = yaml.safe_load(yaml_path.read_text())
        assert data["name"] == "model_observability"
        assert "post_api_request" in data["hooks"]
        assert "on_session_start" in data["hooks"]

    def test_plugin_yaml_has_required_keys(self):
        import yaml
        repo_root = Path(__file__).resolve().parents[2]
        yaml_path = repo_root / "plugins" / "model_observability" / "plugin.yaml"
        data = yaml.safe_load(yaml_path.read_text())
        for key in ("name", "version", "description", "hooks"):
            assert key in data, f"plugin.yaml missing key: {key}"
