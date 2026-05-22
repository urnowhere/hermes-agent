from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


def _load_plugin_module():
    root = Path(__file__).resolve().parents[2]
    init_path = root / "plugins" / "formsy-observability" / "__init__.py"
    spec = importlib.util.spec_from_file_location("formsy_observability_test_plugin", init_path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_reporter_aggregates_metrics_without_sensitive_content(monkeypatch):
    module = _load_plugin_module()
    reporter = module.FormSyObservationReporter()
    reporter.enabled = True
    submitted = []
    monkeypatch.setattr(reporter, "_submit_async", lambda report: submitted.append(report))

    reporter.on_session_start(session_id="sess-1", model="claude-test", platform="cli")
    reporter.pre_llm_call(
        session_id="sess-1",
        user_message="fix the sensitive auth bug",
        is_first_turn=True,
        model="claude-test",
        platform="cli",
    )
    reporter.post_api_request(
        session_id="sess-1",
        model="claude-test",
        usage={"input_tokens": 1200, "output_tokens": 300},
    )
    reporter.pre_tool_call("cc_memory_search", {"query": "auth bug"}, task_id="task-1", session_id="sess-1")
    reporter.post_tool_call(
        "cc_memory_search",
        {"query": "auth bug"},
        json.dumps({
            "observation_id": "obs-1",
            "accepted_targets": ["src/auth/token.py"],
        }),
        task_id="task-1",
        session_id="sess-1",
    )
    reporter.pre_tool_call("read_file", {"path": "src/auth/token.py"}, task_id="task-1", session_id="sess-1")
    reporter.pre_tool_call("patch", {"path": "src/auth/token.py"}, task_id="task-1", session_id="sess-1")
    reporter.pre_tool_call("terminal", {"command": "pytest tests/test_auth.py"}, task_id="task-1", session_id="sess-1")

    reporter.on_session_end(session_id="sess-1", completed=True, interrupted=False, model="claude-test")
    reporter.on_session_finalize(session_id="sess-1")

    assert len(submitted) == 2
    partial, final = submitted
    assert partial["task"]["report_phase"] == "partial"
    assert final["task"]["report_phase"] == "final"
    assert final["counters"]["turn_count"] == 1
    assert final["counters"]["model_turn_count"] == 1
    assert final["counters"]["context_search_count"] == 1
    assert final["counters"]["context_read_count"] == 1
    assert final["counters"]["file_edit_count"] == 1
    assert final["counters"]["test_command_count"] == 1
    assert final["counters"]["shell_fallback_count"] == 1
    assert final["counters"]["input_tokens"] == 1200
    assert final["counters"]["output_tokens"] == 300
    assert final["server_correlation"]["used_observation_ids"] == ["obs-1"]
    assert final["task"]["case_id"] == "fix the sensitive auth bug"
    assert final["observed_behavior"]["first_test_command_summary"] == "pytest tests/test_auth.py"
    assert final["observed_behavior"]["first_test_command_kind"] == "python"
    assert final["privacy"] == {
        "redaction": "metrics_and_redacted_summaries",
        "contains_prompt": False,
        "contains_source": False,
        "contains_diff": False,
        "contains_shell_output": False,
    }

    encoded = json.dumps(final, ensure_ascii=False)
    assert "src/auth/token.py" not in encoded
    assert "sha256:" in encoded


def test_reporter_redacts_readable_summaries(monkeypatch):
    module = _load_plugin_module()
    reporter = module.FormSyObservationReporter()
    reporter.enabled = True
    submitted = []
    monkeypatch.setattr(reporter, "_submit_async", lambda report: submitted.append(report))

    reporter.on_session_start(session_id="sess-1", model="claude-test", platform="cli")
    reporter.pre_llm_call(
        session_id="sess-1",
        user_message="fix deploy with API_KEY=fsy_live_super_secret_value_that_is_long_enough",
        is_first_turn=True,
        model="claude-test",
        platform="cli",
    )
    reporter.pre_tool_call(
        "terminal",
        {"command": "pytest tests/test_auth.py --token abcdefghijklmnopqrstuvwxyz123456"},
        task_id="task-1",
        session_id="sess-1",
    )
    reporter.on_session_finalize(session_id="sess-1")

    final = submitted[-1]
    assert final["task"]["case_id"] == "fix deploy with API_KEY=<redacted>"
    assert final["observed_behavior"]["first_test_command_summary"] == "pytest tests/test_auth.py --token <redacted>"
    encoded = json.dumps(final, ensure_ascii=False)
    assert "fsy_live_super_secret" not in encoded
    assert "abcdefghijklmnopqrstuvwxyz123456" not in encoded


def test_submit_failure_spools_metrics_only_report(tmp_path, monkeypatch):
    module = _load_plugin_module()
    reporter = module.FormSyObservationReporter()
    reporter.enabled = True
    reporter.submit_url = "http://127.0.0.1:1/v1/observations/task_reports"
    reporter.timeout_s = 0.01
    monkeypatch.setenv("FORMSY_OBSERVABILITY_SPOOL_DIR", str(tmp_path))

    report = {
        "report_id": "rpt-test",
        "run_id": "run-test",
        "task_id": "task-test",
        "source": {"kind": "agent", "name": "hermes", "instance_id": "test"},
        "privacy": {
            "redaction": "metrics_only",
            "contains_prompt": False,
            "contains_source": False,
            "contains_diff": False,
            "contains_shell_output": False,
        },
    }

    reporter._submit_or_spool(report)

    files = list(tmp_path.glob("task-reports/*/*.jsonl"))
    assert len(files) == 1
    rows = [json.loads(line) for line in files[0].read_text(encoding="utf-8").splitlines()]
    assert rows == [report]


def test_submit_includes_runtime_api_authorization_header(monkeypatch):
    module = _load_plugin_module()
    reporter = module.FormSyObservationReporter()
    reporter.enabled = True
    reporter.submit_url = "http://127.0.0.1:8000/v1/observations/task_reports"
    monkeypatch.setenv("FORMSY_OBSERVABILITY_API_KEY", "fsy_test")
    captured = {}

    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    def fake_urlopen(request, timeout):
        captured["authorization"] = request.get_header("Authorization")
        captured["timeout"] = timeout
        return Response()

    monkeypatch.setattr(module.urllib.request, "urlopen", fake_urlopen)

    reporter._submit_or_spool(
        {
            "report_id": "rpt-test",
            "run_id": "run-test",
            "task_id": "task-test",
            "source": {"kind": "agent", "name": "hermes"},
            "privacy": {
                "redaction": "metrics_only",
                "contains_prompt": False,
                "contains_source": False,
                "contains_diff": False,
                "contains_shell_output": False,
            },
        }
    )

    assert captured["authorization"] == "Bearer fsy_test"


def test_submit_uses_formsy_api_key_from_hermes_config(tmp_path, monkeypatch):
    module = _load_plugin_module()
    hermes_home = tmp_path / "hermes"
    hermes_home.mkdir()
    (hermes_home / "config.yaml").write_text(
        "formsy:\n"
        "  base_url: http://127.0.0.1:8000\n"
        "  api_key: fsy_config_key\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    monkeypatch.delenv("FORMSY_OBSERVABILITY_API_KEY", raising=False)
    monkeypatch.delenv("FORMSY_API_KEY", raising=False)
    captured = {}

    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    def fake_urlopen(request, timeout):
        captured["authorization"] = request.get_header("Authorization")
        captured["url"] = request.full_url
        return Response()

    monkeypatch.setattr(module.urllib.request, "urlopen", fake_urlopen)
    reporter = module.FormSyObservationReporter()

    reporter._submit_or_spool(
        {
            "report_id": "rpt-test",
            "run_id": "run-test",
            "task_id": "task-test",
            "source": {"kind": "agent", "name": "hermes"},
            "privacy": {
                "redaction": "metrics_only",
                "contains_prompt": False,
                "contains_source": False,
                "contains_diff": False,
                "contains_shell_output": False,
            },
        }
    )

    assert reporter.submit_url == "http://127.0.0.1:8000/v1/observations/task_reports"
    assert captured["authorization"] == "Bearer fsy_config_key"


def test_register_adds_expected_hooks():
    module = _load_plugin_module()

    class Context:
        def __init__(self):
            self.hooks = []

        def register_hook(self, name, callback):
            self.hooks.append((name, callback))

    ctx = Context()
    module.register(ctx)

    assert [name for name, _callback in ctx.hooks] == [
        "on_session_start",
        "pre_llm_call",
        "post_api_request",
        "pre_tool_call",
        "post_tool_call",
        "post_llm_call",
        "on_session_end",
        "on_session_finalize",
        "on_session_reset",
    ]
