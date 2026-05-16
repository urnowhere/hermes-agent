"""Tests for the Formsy context engine plugin."""

import json
import subprocess
from inspect import iscoroutinefunction, signature
from typing import cast

import pytest

from plugins.context_engine.formsy.config import EngineConfigManager
from plugins.context_engine.formsy.client import EngineClient
from plugins.context_engine.formsy.engine import FormsyContextEngine
from plugins.formsy import RuntimeClient


@pytest.fixture(autouse=True)
def _disable_git_identity_for_existing_tests(monkeypatch):
    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="")

    monkeypatch.setattr("plugins.context_engine.formsy.engine.subprocess.run", fake_run)


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


def test_formsy_engine_compress_returns_messages_without_runtime_compile(monkeypatch):
    engine = FormsyContextEngine()

    class UnexpectedCompileClient:
        async def runtime_compile(self, **kwargs):
            raise AssertionError("runtime compile should not be called")

    engine._engine_client = UnexpectedCompileClient()
    engine._config = type("Config", (), {"workspace_id": "ws_test"})()
    engine._session_id = "session-123"

    messages = [{"role": "user", "content": "fix the parser"}]

    result = engine.compress(messages, current_tokens=900, focus_topic="parser")

    assert result is messages
    assert engine.compression_count == 0


def test_formsy_engine_exposes_memory_search_tool():
    engine = FormsyContextEngine()

    schemas = engine.get_tool_schemas()

    assert [schema["name"] for schema in schemas] == ["context_search", "context_read"]
    params = schemas[0]["parameters"]
    assert params["required"] == ["query"]
    assert "query" in params["properties"]
    assert "repo_id" not in params["properties"]
    assert "revision" not in params["properties"]
    assert "budget" in params["properties"]
    assert "limit" in params["properties"]
    metadata = params["properties"]["metadata"]
    assert metadata["type"] == "object"
    assert "retrieval_mode" in metadata["properties"]
    assert "grounding_phase" in metadata["properties"]
    assert "response_format" in metadata["properties"]
    assert "trace_id" in metadata["properties"]
    assert "case_id" in metadata["properties"]
    assert "grounded_symbols" in metadata["properties"]
    assert "grounded_files" in metadata["properties"]
    assert "retrieval_feedback" in metadata["properties"]
    assert "fallback" in metadata["properties"]["grounding_phase"]["enum"]
    assert "proactively" in schemas[0]["description"]
    assert "current git remote URL and commit" in schemas[0]["description"]
    read_params = schemas[1]["parameters"]
    assert read_params["required"] == ["path"]
    assert "repo_id" not in read_params["properties"]
    assert "revision" not in read_params["properties"]
    assert "start_line" in read_params["properties"]
    assert "end_line" in read_params["properties"]


def test_formsy_engine_infers_repo_id_from_common_git_urls():
    assert (
        FormsyContextEngine._repo_id_from_git_url("https://github.com/urnowhere/hermes-agent.git")
        == "urnowhere__hermes-agent"
    )
    assert (
        FormsyContextEngine._repo_id_from_git_url("git@github.com:django/django.git")
        == "django__django"
    )
    assert (
        FormsyContextEngine._repo_id_from_git_url("ssh://git@github.com/pallets/flask.git")
        == "pallets__flask"
    )


def test_formsy_engine_memory_search_derives_repo_and_revision_from_git(monkeypatch):
    engine = FormsyContextEngine()
    calls = []

    class FakeClient:
        async def memory_search(self, **kwargs):
            calls.append(kwargs)
            return {"matches": []}

    def fake_run(cmd, **kwargs):
        if cmd == ["git", "remote", "get-url", "origin"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="https://github.com/urnowhere/hermes-agent.git\n")
        if cmd == ["git", "rev-parse", "HEAD"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="abc123def456\n")
        raise AssertionError(f"unexpected git command: {cmd}")

    monkeypatch.setattr("plugins.context_engine.formsy.engine.subprocess.run", fake_run)
    engine._engine_client = FakeClient()
    engine._config = type("Config", (), {
        "repo_id": "configured__repo",
        "revision": "configured-revision",
        "query_budget": 4000,
    })()
    engine._session_id = "session-123"

    result = engine.handle_tool_call(
        "context_search",
        {
            "query": "parser state handling",
            "repo_id": "llm__provided",
            "revision": "llm-revision",
        },
    )

    assert json.loads(result)["ok"] is True
    assert calls == [{
        "repo_id": "urnowhere__hermes-agent",
        "session_id": "session-123",
        "query": "parser state handling",
        "revision": "abc123def456",
        "budget": 4000,
        "metadata": {
            "retrieval_mode": "symbolic",
            "grounding_phase": "seed",
            "response_format": "bundle",
            "case_id": "urnowhere__hermes-agent",
            "trace_id": "session-123",
        },
    }]


def test_formsy_engine_memory_search_compiles_before_query(monkeypatch):
    engine = FormsyContextEngine()
    calls = []

    class FakeClient:
        async def compile_repo(self, **kwargs):
            calls.append(("compile", kwargs))
            return {
                "repo_id": kwargs["repo_id"],
                "revision": kwargs["revision"],
                "parsed_file_count": len(kwargs["files"]),
            }

        async def memory_search(self, **kwargs):
            calls.append(("memory_search", kwargs))
            return {"matches": []}

    def fake_run(cmd, **kwargs):
        if cmd == ["git", "remote", "get-url", "origin"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="https://github.com/urnowhere/hermes-agent.git\n")
        if cmd == ["git", "rev-parse", "HEAD"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="abc123def456\n")
        raise AssertionError(f"unexpected git command: {cmd}")

    monkeypatch.setattr("plugins.context_engine.formsy.engine.subprocess.run", fake_run)
    monkeypatch.setattr(
        FormsyContextEngine,
        "_collect_memory_source_files",
        staticmethod(lambda root: [{
            "path": "pkg/mod.py",
            "content": "x = 1\n",
            "language": "python",
            "is_test": False,
        }]),
    )
    engine._engine_client = FakeClient()
    engine._config = type("Config", (), {
        "repo_id": "",
        "revision": "latest",
        "query_budget": 4000,
        "workspace_id": "ws_test",
    })()
    engine._session_id = "session-123"

    result = engine.handle_tool_call(
        "context_search",
        {"query": "parser state handling"},
    )

    assert json.loads(result)["ok"] is True
    assert [name for name, _ in calls] == ["compile", "memory_search"]
    assert calls[0][1] == {
        "repo_id": "urnowhere__hermes-agent",
        "files": [{
            "path": "pkg/mod.py",
            "content": "x = 1\n",
            "language": "python",
            "is_test": False,
        }],
        "revision": "abc123def456",
        "metadata": {
            "instance_id": "urnowhere__hermes-agent",
            "query": "parser state handling",
            "source_file_count": 1,
        },
        "session_id": "session-123",
    }
    assert calls[1][1]["revision"] == "abc123def456"


def test_formsy_engine_memory_search_tool_queries_runtime():
    engine = FormsyContextEngine()
    calls = []

    class FakeClient:
        async def memory_search(self, **kwargs):
            calls.append(kwargs)
            return {
                "extra_context": "Relevant parser notes",
                "symbolic_prompt": (
                    "Formal Semantics:\n"
                    "Constraints:\n"
                    "Retrieval Strategy:\n"
                    "Retrieved Facts:"
                ),
                "matches": [{"path": "parser.py", "score": 0.91}],
                "suggested_queries": ["tests for parser state handling"],
                "coverage": "partial",
                "missing_context": ["No test constraints were selected for this query."],
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
        "context_search",
        {
            "query": "parser state handling",
            "repo_id": "django__django-14053",
            "budget": 3000,
            "retrieval_mode": "symbolic",
            "grounding_phase": "seed",
            "response_format": "bundle",
            "trace_id": "trace-1",
            "case_id": "case-1",
            "grounded_symbols": ["Parser.parse"],
            "grounded_files": ["parser.py"],
            "retrieval_feedback": "symbolic retrieval looked weak",
        },
    )

    data = json.loads(result)
    assert data["ok"] is True
    assert data["query"] == "parser state handling"
    assert data["extra_context"] == "Relevant parser notes"
    assert data["matches"] == [{"path": "parser.py", "score": 0.91}]
    assert data["suggested_queries"] == ["tests for parser state handling"]
    assert data["coverage"] == "partial"
    assert data["missing_context"] == ["No test constraints were selected for this query."]
    assert data["symbolic_prompt"] == (
        "Formal Semantics:\n"
        "Constraints:\n"
        "Retrieval Strategy:\n"
        "Retrieved Facts:"
    )
    assert data["direct_match_files"] == ["parser.py"]
    assert data["bundle_primary_files"] == []
    assert data["bundle_must_edit"] == []
    assert data["retrieval_state"] == "inspect_candidates"
    assert data["preferred_next_step"] == "context_read"
    assert "results" not in data
    assert calls == [{
        "repo_id": "django__django-14053",
        "session_id": "session-123",
        "query": "parser state handling",
        "revision": "latest",
        "budget": 3000,
        "metadata": {
            "retrieval_mode": "symbolic",
            "grounding_phase": "seed",
            "response_format": "bundle",
            "trace_id": "trace-1",
            "case_id": "case-1",
            "grounded_symbols": ["Parser.parse"],
            "grounded_files": ["parser.py"],
            "retrieval_feedback": "symbolic retrieval looked weak",
        },
    }]


def test_formsy_engine_memory_search_prefers_nested_metadata():
    engine = FormsyContextEngine()
    calls = []

    class FakeClient:
        async def memory_search(self, **kwargs):
            calls.append(kwargs)
            return {"matches": []}

    engine._engine_client = FakeClient()
    engine._config = type("Config", (), {
        "repo_id": "django__django-14053",
        "revision": "latest",
        "query_budget": 4000,
    })()
    engine._session_id = "session-123"

    result = engine.handle_tool_call(
        "context_search",
        {
            "query": "parser state handling",
            "repo_id": "django__django-14053",
            "metadata": {
                "retrieval_mode": "symbolic",
                "grounding_phase": "grounded",
                "response_format": "bundle",
                "trace_id": "trace-nested",
            },
            "retrieval_mode": "legacy",
            "grounding_phase": "seed",
            "response_format": "legacy",
            "trace_id": "trace-top-level",
        },
    )

    data = json.loads(result)
    assert data["ok"] is True
    assert calls == [{
        "repo_id": "django__django-14053",
        "session_id": "session-123",
        "query": "parser state handling",
        "revision": "latest",
        "budget": 4000,
        "metadata": {
            "retrieval_mode": "symbolic",
            "grounding_phase": "grounded",
            "response_format": "bundle",
            "trace_id": "trace-nested",
            "case_id": "django__django-14053",
        },
    }]


def test_formsy_engine_memory_search_merges_memory_provider_hints():
    engine = FormsyContextEngine()
    calls = []

    class FakeClient:
        async def memory_search(self, **kwargs):
            calls.append(kwargs)
            return {"matches": []}

    class HintProvider:
        def get_context_hints(self):
            return {
                "memory_artifact_ids": ["artifact-1", "artifact-2"],
                "memory_query_hints": ["search auth tests", "search auth tests"],
                "memory_test_hints": ["python -m pytest tests/auth"],
                "memory_status": "warm",
                "memory_freshness": "fresh",
            }

    class FakeMemoryManager:
        providers = [HintProvider()]

    engine._engine_client = FakeClient()
    engine._memory_manager = FakeMemoryManager()
    engine._config = type("Config", (), {
        "repo_id": "django__django-14053",
        "revision": "latest",
        "query_budget": 4000,
    })()
    engine._session_id = "session-123"

    result = engine.handle_tool_call(
        "context_search",
        {
            "query": "parser state handling",
            "repo_id": "django__django-14053",
            "metadata": {
                "memory_artifact_ids": ["artifact-2", "artifact-3"],
                "memory_query_hints": ["existing hint"],
            },
        },
    )

    assert json.loads(result)["ok"] is True
    assert calls == [{
        "repo_id": "django__django-14053",
        "session_id": "session-123",
        "query": "parser state handling",
        "revision": "latest",
        "budget": 4000,
        "metadata": {
            "retrieval_mode": "symbolic",
            "grounding_phase": "seed",
            "response_format": "bundle",
            "case_id": "django__django-14053",
            "trace_id": "session-123",
            "memory_artifact_ids": ["artifact-2", "artifact-3", "artifact-1"],
            "memory_query_hints": ["existing hint", "search auth tests"],
            "memory_test_hints": ["python -m pytest tests/auth"],
            "memory_status": "warm",
            "memory_freshness": "fresh",
        },
    }]


def test_formsy_engine_memory_search_marks_poor_symbolic_results_for_retry():
    engine = FormsyContextEngine()
    calls = []

    class FakeClient:
        async def memory_search(self, **kwargs):
            calls.append(kwargs)
            return {
                "extra_context": "No useful matches",
                "matches": [],
                "suggested_queries": ["auth validator regex anchors"],
                "coverage": "poor",
                "missing_context": ["No structured file or symbol matches were selected."],
                "diagnostics": {"reason": "symbolic search was too broad"},
                "test_plan": {"test_runner": "python tests/runtests.py"},
            }

    engine._engine_client = FakeClient()
    engine._config = type("Config", (), {
        "repo_id": "django__django-14053",
        "revision": "latest",
        "query_budget": 4000,
    })()
    engine._session_id = "session-123"

    result = engine.handle_tool_call(
        "context_search",
        {
            "query": "parser state handling",
            "repo_id": "django__django-14053",
            "metadata": {
                "retrieval_mode": "symbolic",
                "grounding_phase": "seed",
                "response_format": "bundle",
            },
        },
    )

    data = json.loads(result)
    assert data["ok"] is True
    assert data["retrieval_state"] == "retry"
    assert data["preferred_next_step"] == "context_search"
    assert data["next_retrieval"] == {
        "query": "auth validator regex anchors",
        "retrieval_mode": "symbolic",
        "grounding_phase": "seed",
        "response_format": "bundle",
    }
    assert data["diagnostics"] == {"reason": "symbolic search was too broad"}
    assert data["test_plan"] == {"test_runner": "python tests/runtests.py"}
    assert calls == [{
        "repo_id": "django__django-14053",
        "session_id": "session-123",
        "query": "parser state handling",
        "revision": "latest",
        "budget": 4000,
        "metadata": {
            "retrieval_mode": "symbolic",
            "grounding_phase": "seed",
            "response_format": "bundle",
            "case_id": "django__django-14053",
            "trace_id": "session-123",
        },
    }]

    retry_result = json.loads(
        engine.handle_tool_call(
            "context_search",
            {
                "query": "auth validator regex anchors",
                "repo_id": "django__django-14053",
                "metadata": {
                    "retrieval_mode": "symbolic",
                    "grounding_phase": "seed",
                    "response_format": "bundle",
                },
            },
        )
    )
    assert retry_result["retrieval_state"] == "retry"
    assert retry_result["next_retrieval"] == {
        "retrieval_mode": "legacy",
        "grounding_phase": "fallback",
        "response_format": "bundle",
        "retrieval_feedback": "Symbolic seed searches returned no matches or poor coverage.",
    }
    assert engine.get_retrieval_status()["symbolic_prompt_present"] is False
    assert engine.get_retrieval_status()["symbolic_prompt_sections"] == []


def test_formsy_engine_compile_missing_goes_directly_to_degraded_recovery():
    engine = FormsyContextEngine()

    class FakeClient:
        async def memory_search(self, **kwargs):
            return {
                "extra_context": "",
                "matches": [],
                "coverage": "poor",
                "suggested_queries": [
                    "compile the repository before querying",
                    "ASCIIUsernameValidator UnicodeUsernameValidator username validator regex",
                ],
                "missing_context": [
                    "\"Compiled repository not found for repo_id='django__django-14053' revision='latest'\"",
                ],
                "test_plan": {},
            }

    engine._engine_client = FakeClient()
    engine._config = type("Config", (), {
        "repo_id": "django__django-14053",
        "revision": "latest",
        "query_budget": 4000,
    })()
    engine._session_id = "session-123"

    result = json.loads(
        engine.handle_tool_call(
            "context_search",
            {
                "query": "username validator regex",
                "repo_id": "django__django-14053",
                "metadata": {
                    "retrieval_mode": "symbolic",
                    "grounding_phase": "seed",
                    "response_format": "bundle",
                },
            },
        )
    )

    assert result["retrieval_state"] == "degraded_recovery"
    assert result["preferred_next_step"] == "bounded_shell_inspection"
    assert result["next_retrieval"]["recovery_mode"] == "degraded_recovery"


def test_formsy_engine_enforces_seed_read_grounded_sequence():
    engine = FormsyContextEngine()
    search_calls = []
    read_calls = []

    class FakeClient:
        async def memory_search(self, **kwargs):
            search_calls.append(kwargs)
            if kwargs["metadata"].get("grounding_phase") == "grounded":
                return {
                    "extra_context": "Grounded validator notes",
                    "matches": [{"path": "django/contrib/auth/validators.py"}],
                    "coverage": "good",
                }
            return {
                "extra_context": "Seed validator notes",
                "matches": [{"path": "django/contrib/auth/validators.py"}],
                "coverage": "partial",
                "suggested_queries": ["confirm UsernameValidator regex anchor fix in validators.py"],
                "requirement_analysis": {"need": "anchor regex"},
                "template_family": "username_validator",
                "retrieval_targets": ["django/contrib/auth/validators.py"],
                "test_plan": {"test_runner": "python tests/runtests.py"},
            }

        async def memory_read(self, **kwargs):
            read_calls.append(kwargs)
            return {
                "path": "django/contrib/auth/validators.py",
                "start_line": 1,
                "end_line": 25,
                "content": "class ASCIIUsernameValidator: ...",
            }

    engine._engine_client = FakeClient()
    engine._config = type("Config", (), {
        "repo_id": "django__django-14053",
        "revision": "latest",
        "query_budget": 4000,
    })()
    engine._session_id = "session-123"

    seed_result = json.loads(
        engine.handle_tool_call(
            "context_search",
            {
                "query": "username validator regex",
                "repo_id": "django__django-14053",
                "metadata": {
                    "retrieval_mode": "symbolic",
                    "grounding_phase": "seed",
                    "response_format": "bundle",
                },
            },
        )
    )
    assert seed_result["retrieval_state"] == "inspect_candidates"
    assert seed_result["preferred_next_step"] == "context_read"
    assert engine.get_tool_block_message("terminal", {"command": "ls"}) is not None

    read_result = engine.handle_tool_call(
        "context_read",
        {
            "path": "django/contrib/auth/validators.py",
            "repo_id": "django__django-14053",
            "start_line": 1,
            "end_line": 25,
        },
    )
    assert "ok: true" in read_result
    status_after_read = engine.get_retrieval_status()
    assert status_after_read["retrieval_state"] == "context_read"
    assert engine.get_tool_block_message("terminal", {"command": "ls"}) is not None
    assert engine.get_tool_block_message("terminal", {"command": "python tests/runtests.py staticfiles_tests.test_storage -v 2"}) is None
    assert engine.get_tool_block_message("terminal", {"command": "python -c \"print('inspect target')\""}) is not None
    assert engine.get_tool_block_message(
        "context_search",
        {
            "query": "retry seed",
            "metadata": {
                "retrieval_mode": "symbolic",
                "grounding_phase": "seed",
                "response_format": "bundle",
            },
        },
    ) is not None
    assert engine.get_tool_block_message(
        "context_search",
        {
            "query": "confirm grounded source details",
            "metadata": {
                "retrieval_mode": "symbolic",
                "grounding_phase": "grounded",
                "response_format": "bundle",
            },
        },
    ) is None
    assert engine.get_tool_block_message("patch", {"path": "django/contrib/auth/validators.py"}) is not None
    assert status_after_read["last_decision"]["next_retrieval"] == {
        "query": "confirm grounded source details",
        "retrieval_mode": "symbolic",
        "grounding_phase": "grounded",
        "response_format": "bundle",
        "grounded_symbols": [],
        "grounded_files": ["django/contrib/auth/validators.py"],
        "requirement_analysis": {"need": "anchor regex"},
        "template_family": "username_validator",
        "retrieval_targets": ["django/contrib/auth/validators.py"],
        "test_plan": {"test_runner": "python tests/runtests.py"},
    }
    assert status_after_read["requirement_analysis"] == {"need": "anchor regex"}
    assert status_after_read["template_family"] == "username_validator"
    assert status_after_read["retrieval_targets"] == ["django/contrib/auth/validators.py"]
    assert status_after_read["test_plan"] == {"test_runner": "python tests/runtests.py"}

    grounded_result = json.loads(
        engine.handle_tool_call(
            "context_search",
            {
                "query": "confirm UsernameValidator regex anchor fix in validators.py",
                "repo_id": "django__django-14053",
                "metadata": {
                    "retrieval_mode": "symbolic",
                    "grounding_phase": "grounded",
                    "response_format": "bundle",
                    "grounded_symbols": ["ASCIIUsernameValidator", "UnicodeUsernameValidator"],
                    "grounded_files": ["django/contrib/auth/validators.py"],
                    "requirement_analysis": {"need": "anchor regex"},
                    "template_family": "username_validator",
                    "retrieval_targets": ["django/contrib/auth/validators.py"],
                    "test_plan": {"test_runner": "python tests/runtests.py"},
                },
            },
        )
    )
    assert grounded_result["retrieval_state"] == "grounded"
    assert grounded_result["preferred_next_step"] == "edit"
    assert grounded_result["accepted_targets"] == ["django/contrib/auth/validators.py"]
    assert grounded_result["exploration_closed"] is True
    assert grounded_result["retrieval_decision"]["contradiction_found"] is False
    assert grounded_result["retrieval_decision"]["target_conflict"] is False
    assert engine.get_tool_block_message("terminal", {"command": "ls"}) is None
    assert engine.get_tool_block_message("context_search", {"query": "more"}) is not None
    assert engine.get_tool_block_message("terminal", {"command": "grep -R UsernameValidator django"}) is not None
    assert engine.get_tool_block_message("patch", {"path": "django/contrib/auth/validators.py"}) is None
    assert engine.get_tool_block_message("search_files", {"query": "validators"}) is not None
    assert engine.get_tool_block_message("read_file", {"path": "django/contrib/auth/other.py"}) is not None
    assert engine.get_tool_block_message("context_read", {"path": "django/contrib/auth/other.py"}) is not None
    assert engine.get_tool_block_message("context_read", {"path": "tests/staticfiles_tests/test_storage.py"}) is None
    assert json.loads(
        engine.handle_tool_call(
            "context_read",
            {
                "path": "django/contrib/auth/other.py",
                "repo_id": "django__django-14053",
                "start_line": 1,
                "end_line": 5,
            },
        )
    )["ok"] is False
    assert search_calls[0]["metadata"]["trace_id"] == "session-123"
    assert search_calls[0]["metadata"]["case_id"] == "django__django-14053"
    assert search_calls[-1]["metadata"]["requirement_analysis"] == {"need": "anchor regex"}
    assert search_calls[-1]["metadata"]["template_family"] == "username_validator"
    assert search_calls[-1]["metadata"]["retrieval_targets"] == ["django/contrib/auth/validators.py"]
    assert search_calls[-1]["metadata"]["test_plan"] == {"test_runner": "python tests/runtests.py"}
    assert len(read_calls) == 1
    status = engine.get_retrieval_status()
    assert status["retrieval_budget"] == 4000
    assert status["seed_calls"] == 1
    assert status["grounded_calls"] == 1
    assert status["retry_calls"] == 0
    assert status["legacy_calls"] == 0
    assert status["accepted_targets"] == ["django/contrib/auth/validators.py"]
    assert status["blocked_tool_reason"]


def test_formsy_engine_accepts_grounded_target_without_new_matches():
    engine = FormsyContextEngine()

    class FakeClient:
        async def memory_search(self, **kwargs):
            if kwargs["metadata"].get("grounding_phase") == "grounded":
                return {
                    "extra_context": "Grounded validator notes",
                    "symbolic_prompt": (
                        "Formal Semantics:\nusername validation must reject newline anchors\n"
                        "Constraints:\npreserve ASCII and Unicode validator behavior\n"
                        "Retrieval Strategy:\ninspect auth validator classes\n"
                        "Retrieved Facts:\nvalidators.py contains regex validators"
                    ),
                    "matches": [],
                    "coverage": "good",
                    "grounded_files": ["django/contrib/auth/validators.py"],
                    "bundle": {"must_edit": ["django/__init__.py"]},
                }
            return {
                "extra_context": "Seed validator notes",
                "symbolic_prompt": (
                    "Formal Semantics:\nusername validation must reject newline anchors\n"
                    "Constraints:\npreserve ASCII and Unicode validator behavior\n"
                    "Retrieval Strategy:\ninspect auth validator classes\n"
                    "Retrieved Facts:\nvalidators.py contains regex validators"
                ),
                "matches": [{"path": "django/contrib/auth/validators.py"}],
                "coverage": "partial",
            }

        async def memory_read(self, **kwargs):
            return {
                "path": "django/contrib/auth/validators.py",
                "start_line": 1,
                "end_line": 25,
                "content": "class ASCIIUsernameValidator: ...",
            }

    engine._engine_client = FakeClient()
    engine._config = type("Config", (), {
        "repo_id": "django__django-14053",
        "revision": "latest",
        "query_budget": 4000,
    })()
    engine._session_id = "session-123"

    engine.handle_tool_call(
        "context_search",
        {
            "query": "username validator regex",
            "repo_id": "django__django-14053",
            "metadata": {
                "retrieval_mode": "symbolic",
                "grounding_phase": "seed",
                "response_format": "bundle",
            },
        },
    )
    engine.handle_tool_call(
        "context_read",
        {
            "path": "django/contrib/auth/validators.py",
            "repo_id": "django__django-14053",
            "start_line": 1,
            "end_line": 25,
        },
    )

    grounded_result = json.loads(
        engine.handle_tool_call(
            "context_search",
            {
                "query": "confirm UsernameValidator regex anchor fix in validators.py",
                "repo_id": "django__django-14053",
                "metadata": {
                    "retrieval_mode": "symbolic",
                    "grounding_phase": "grounded",
                    "response_format": "bundle",
                    "grounded_files": ["django/contrib/auth/validators.py"],
                },
            },
        )
    )

    assert grounded_result["retrieval_state"] == "grounded"
    assert grounded_result["preferred_next_step"] == "edit"
    assert grounded_result["accepted_targets"] == ["django/contrib/auth/validators.py"]
    assert grounded_result["bundle_must_edit"] == ["django/__init__.py"]
    assert engine.get_tool_block_message("context_search", {"query": "more"}) is not None


def test_formsy_engine_honors_server_grounded_closeout_fields():
    engine = FormsyContextEngine()
    calls = []

    class FakeClient:
        async def memory_search(self, **kwargs):
            calls.append(kwargs)
            if kwargs["metadata"].get("grounding_phase") == "grounded":
                return {
                    "extra_context": "Grounded validator notes",
                    "matches": [],
                    "coverage": "good",
                    "test_plan": {
                        "commands": [
                            "python tests/runtests.py staticfiles_tests.test_storage -v 2",
                        ],
                    },
                    "bundle": {"must_edit": ["django/__init__.py"]},
                    "retrieval_state": "grounded",
                    "preferred_next_step": "edit",
                    "accepted_targets": ["django/contrib/auth/validators.py"],
                    "exploration_closed": True,
                    "blocked_tool_reason": "grounded target accepted; broad search disabled",
                }
            return {
                "extra_context": "Seed validator notes",
                "matches": [{"path": "django/contrib/auth/validators.py"}],
                "coverage": "partial",
            }

    engine._engine_client = FakeClient()
    engine._config = type("Config", (), {
        "repo_id": "django__django-14053",
        "revision": "latest",
        "query_budget": 4000,
    })()
    engine._session_id = "session-123"

    engine.handle_tool_call(
        "context_search",
        {
            "query": "username validator regex",
            "repo_id": "django__django-14053",
            "metadata": {
                "retrieval_mode": "symbolic",
                "grounding_phase": "seed",
                "response_format": "bundle",
            },
        },
    )

    grounded_result = json.loads(
        engine.handle_tool_call(
            "context_search",
            {
                "query": "confirm UsernameValidator regex anchor fix in validators.py",
                "repo_id": "django__django-14053",
                "metadata": {
                    "retrieval_mode": "symbolic",
                    "grounding_phase": "grounded",
                    "response_format": "bundle",
                },
            },
        )
    )

    assert grounded_result["retrieval_state"] == "grounded"
    assert grounded_result["preferred_next_step"] == "edit"
    assert grounded_result["accepted_targets"] == ["django/contrib/auth/validators.py"]
    assert grounded_result["exploration_closed"] is True
    assert grounded_result["blocked_tool_reason"] == "grounded target accepted; broad search disabled"
    assert engine.get_tool_block_message("context_search", {"query": "more"}) is not None
    assert engine.get_tool_block_message(
        "terminal",
        {"command": "python tests/runtests.py staticfiles_tests.test_storage -v 2"},
    ) is None
    assert engine.get_tool_block_message("terminal", {"command": "grep -rn post_process django"}) is not None
    assert calls[-1]["metadata"]["retrieval_mode"] == "symbolic"


def test_formsy_engine_records_missing_symbolic_prompt_as_weak_signal():
    engine = FormsyContextEngine()

    class FakeClient:
        async def memory_search(self, **kwargs):
            return {
                "extra_context": "Relevant notes",
                "matches": [{"path": "parser.py"}],
                "coverage": "partial",
            }

    engine._engine_client = FakeClient()
    engine._config = type("Config", (), {
        "repo_id": "django__django-14053",
        "revision": "latest",
        "query_budget": 4000,
    })()
    engine._session_id = "session-123"

    data = json.loads(
        engine.handle_tool_call(
            "context_search",
            {
                "query": "parser state handling",
                "repo_id": "django__django-14053",
                "metadata": {
                    "retrieval_mode": "symbolic",
                    "grounding_phase": "seed",
                    "response_format": "bundle",
                },
            },
        )
    )

    status = engine.get_retrieval_status()
    assert "symbolic_prompt" not in data
    assert status["symbolic_prompt_present"] is False
    assert status["symbolic_prompt_missing"] is True


def test_formsy_engine_prefers_grounded_targets_over_bundle_must_edit():
    engine = FormsyContextEngine()

    class FakeClient:
        async def memory_search(self, **kwargs):
            if kwargs["metadata"].get("grounding_phase") == "grounded":
                return {
                    "extra_context": "Grounded validator notes",
                    "symbolic_prompt": (
                        "Formal Semantics:\nusername validation must reject newline anchors\n"
                        "Constraints:\npreserve ASCII and Unicode validator behavior\n"
                        "Retrieval Strategy:\ninspect auth validator classes\n"
                        "Retrieved Facts:\nvalidators.py contains regex validators"
                    ),
                    "matches": [{"path": "django/contrib/auth/validators.py"}],
                    "coverage": "good",
                    "bundle": {"must_edit": ["django/__init__.py"]},
                }
            return {
                "extra_context": "Seed validator notes",
                "symbolic_prompt": (
                    "Formal Semantics:\nusername validation must reject newline anchors\n"
                    "Constraints:\npreserve ASCII and Unicode validator behavior\n"
                    "Retrieval Strategy:\ninspect auth validator classes\n"
                    "Retrieved Facts:\nvalidators.py contains regex validators"
                ),
                "matches": [{"path": "django/contrib/auth/validators.py"}],
                "coverage": "partial",
                "bundle": {
                    "primary_files": ["django/contrib/auth/validators.py"],
                    "must_edit": ["django/__init__.py"],
                },
            }

        async def memory_read(self, **kwargs):
            return {
                "path": "django/contrib/auth/validators.py",
                "start_line": 1,
                "end_line": 25,
                "content": "class ASCIIUsernameValidator: ...",
            }

    engine._engine_client = FakeClient()
    engine._config = type("Config", (), {
        "repo_id": "django__django-14053",
        "revision": "latest",
        "query_budget": 4000,
    })()
    engine._session_id = "session-123"

    seed_result = json.loads(engine.handle_tool_call(
        "context_search",
        {
            "query": "username validator regex",
            "repo_id": "django__django-14053",
            "metadata": {
                "retrieval_mode": "symbolic",
                "grounding_phase": "seed",
                "response_format": "bundle",
            },
        },
    ))
    assert seed_result["retrieval_decision"]["direct_match_files"] == ["django/contrib/auth/validators.py"]
    assert seed_result["retrieval_decision"]["bundle_primary_files"] == ["django/contrib/auth/validators.py"]
    assert seed_result["retrieval_decision"]["bundle_must_edit"] == ["django/__init__.py"]
    assert seed_result["retrieval_decision"]["preferred_edit_targets"] == ["django/contrib/auth/validators.py"]
    assert seed_result["direct_match_files"] == ["django/contrib/auth/validators.py"]
    assert seed_result["bundle_primary_files"] == ["django/contrib/auth/validators.py"]
    assert seed_result["bundle_must_edit"] == ["django/__init__.py"]
    engine.handle_tool_call(
        "context_read",
        {
            "path": "django/contrib/auth/validators.py",
            "repo_id": "django__django-14053",
            "start_line": 1,
            "end_line": 25,
        },
    )
    grounded_result = json.loads(
        engine.handle_tool_call(
            "context_search",
            {
                "query": "confirm grounded source details",
                "repo_id": "django__django-14053",
                "metadata": {
                    "retrieval_mode": "symbolic",
                    "grounding_phase": "grounded",
                    "response_format": "bundle",
                    "grounded_files": ["django/contrib/auth/validators.py"],
                },
            },
        )
    )

    decision = grounded_result["retrieval_decision"]
    assert grounded_result["retrieval_state"] == "grounded"
    assert grounded_result["preferred_next_step"] == "edit"
    assert grounded_result["accepted_targets"] == ["django/contrib/auth/validators.py"]
    assert decision["constraints_present"] is True
    assert decision["constraints_quality"] == "present"
    assert decision["bundle_must_edit"] == ["django/__init__.py"]
    assert decision["preferred_edit_targets"] == ["django/contrib/auth/validators.py"]
    assert decision["target_conflict"] is False
    assert decision["contradiction_found"] is False
    assert decision["target_changed_after_grounding"] is False
    assert engine.get_retrieval_status()["preferred_edit_targets"] == ["django/contrib/auth/validators.py"]
    assert engine.get_retrieval_status()["exploration_closed"] is True


def test_formsy_engine_enters_degraded_recovery_after_weak_legacy_fallback():
    engine = FormsyContextEngine()
    calls = []

    class FakeClient:
        async def memory_search(self, **kwargs):
            calls.append(kwargs)
            if kwargs["metadata"].get("retrieval_mode") == "legacy":
                return {
                    "extra_context": "Still weak",
                    "matches": [],
                    "coverage": "poor",
                    "missing_context": ["Legacy fallback did not recover grounded evidence."],
                }
            return {
                "extra_context": "No useful matches",
                "matches": [],
                "coverage": "poor",
                "suggested_queries": ["auth validator regex anchors"],
            }

    engine._engine_client = FakeClient()
    engine._config = type("Config", (), {
        "repo_id": "django__django-14053",
        "revision": "latest",
        "query_budget": 4000,
    })()
    engine._session_id = "session-123"

    engine.handle_tool_call(
        "context_search",
        {
            "query": "username validator regex",
            "repo_id": "django__django-14053",
            "metadata": {
                "retrieval_mode": "symbolic",
                "grounding_phase": "seed",
                "response_format": "bundle",
            },
        },
    )
    engine.handle_tool_call(
        "context_search",
        {
            "query": "auth validator regex anchors",
            "repo_id": "django__django-14053",
            "metadata": {
                "retrieval_mode": "symbolic",
                "grounding_phase": "seed",
                "response_format": "bundle",
            },
        },
    )

    legacy_result = json.loads(
        engine.handle_tool_call(
            "context_search",
            {
                "query": "auth validator regex anchors",
                "repo_id": "django__django-14053",
                "metadata": {
                    "retrieval_mode": "legacy",
                    "grounding_phase": "fallback",
                    "response_format": "bundle",
                },
            },
        )
    )

    assert legacy_result["retrieval_state"] == "degraded_recovery"
    assert legacy_result["next_retrieval"]["recovery_mode"] == "degraded_recovery"
    assert "terminal" in legacy_result["next_retrieval"]["allowed_tools"]
    assert engine.get_retrieval_status()["retrieval_status"] == "failed"
    assert engine.get_tool_block_message("terminal", {"command": "ls"}) is None
    assert engine.get_tool_block_message("patch", {"path": "django/contrib/auth/validators.py"}) is not None
    assert calls[-1]["metadata"]["retrieval_mode"] == "legacy"


def test_formsy_engine_degrades_instead_of_full_block_after_failed_grounded_search():
    engine = FormsyContextEngine()

    class FakeClient:
        async def memory_search(self, **kwargs):
            if kwargs["metadata"].get("grounding_phase") == "grounded":
                return {
                    "extra_context": "Grounding failed",
                    "matches": [],
                    "coverage": "poor",
                    "missing_context": ["Grounded search did not return relevant context."],
                }
            return {
                "extra_context": "Seed validator notes",
                "matches": [{"path": "django/contrib/auth/validators.py"}],
                "coverage": "partial",
            }

        async def memory_read(self, **kwargs):
            return {
                "path": "django/contrib/auth/validators.py",
                "start_line": 1,
                "end_line": 25,
                "content": "class ASCIIUsernameValidator: ...",
            }

    engine._engine_client = FakeClient()
    engine._config = type("Config", (), {
        "repo_id": "django__django-14053",
        "revision": "latest",
        "query_budget": 4000,
    })()
    engine._session_id = "session-123"

    engine.handle_tool_call(
        "context_search",
        {
            "query": "username validator regex",
            "repo_id": "django__django-14053",
            "metadata": {
                "retrieval_mode": "symbolic",
                "grounding_phase": "seed",
                "response_format": "bundle",
            },
        },
    )
    engine.handle_tool_call(
        "context_read",
        {
            "path": "django/contrib/auth/validators.py",
            "repo_id": "django__django-14053",
            "start_line": 1,
            "end_line": 25,
        },
    )
    grounded_result = json.loads(
        engine.handle_tool_call(
            "context_search",
            {
                "query": "confirm grounded source details",
                "repo_id": "django__django-14053",
                "metadata": {
                    "retrieval_mode": "symbolic",
                    "grounding_phase": "grounded",
                    "response_format": "bundle",
                    "grounded_files": ["django/contrib/auth/validators.py"],
                },
            },
        )
    )

    assert grounded_result["retrieval_state"] == "degraded_recovery"
    assert grounded_result["preferred_next_step"] == "bounded_shell_inspection"
    assert engine.get_tool_block_message("terminal", {"command": "grep -R UsernameValidator django"}) is None
    assert engine.get_tool_block_message("patch", {"path": "django/contrib/auth/validators.py"}) is not None


def test_formsy_engine_promotes_degraded_recovery_read_of_target_to_grounded():
    engine = FormsyContextEngine()

    class FakeClient:
        async def memory_search(self, **kwargs):
            if kwargs["metadata"].get("retrieval_mode") == "legacy":
                return {
                    "extra_context": "Still weak",
                    "matches": [],
                    "coverage": "poor",
                    "missing_context": ["Legacy fallback did not recover grounded evidence."],
                }
            return {
                "extra_context": "No useful matches",
                "matches": [],
                "coverage": "poor",
                "suggested_queries": ["auth validator regex anchors"],
            }

        async def memory_read(self, **kwargs):
            return {
                "path": "django/contrib/auth/validators.py",
                "start_line": 1,
                "end_line": 25,
                "content": "class ASCIIUsernameValidator: ...",
            }

    engine._engine_client = FakeClient()
    engine._config = type("Config", (), {
        "repo_id": "django__django-14053",
        "revision": "latest",
        "query_budget": 4000,
    })()
    engine._session_id = "session-123"

    engine.handle_tool_call(
        "context_search",
        {
            "query": "username validator regex",
            "repo_id": "django__django-14053",
            "metadata": {
                "retrieval_mode": "symbolic",
                "grounding_phase": "seed",
                "response_format": "bundle",
            },
        },
    )
    engine.handle_tool_call(
        "context_search",
        {
            "query": "auth validator regex anchors",
            "repo_id": "django__django-14053",
            "metadata": {
                "retrieval_mode": "legacy",
                "grounding_phase": "fallback",
                "response_format": "bundle",
            },
        },
    )
    assert engine.get_retrieval_status()["retrieval_state"] == "degraded_recovery"

    engine.handle_tool_call(
        "context_read",
        {
            "path": "django/contrib/auth/validators.py",
            "repo_id": "django__django-14053",
            "start_line": 1,
            "end_line": 25,
        },
    )

    status = engine.get_retrieval_status()
    assert status["retrieval_state"] == "grounded"
    assert status["accepted_targets"] == ["django/contrib/auth/validators.py"]
    assert status["exploration_closed"] is True
    assert engine.get_tool_block_message("patch", {"path": "django/contrib/auth/validators.py"}) is None


def test_formsy_engine_memory_read_tool_queries_runtime():
    engine = FormsyContextEngine()
    calls = []

    class FakeClient:
        async def memory_read(self, **kwargs):
            calls.append(kwargs)
            return {
                "path": "parser.py",
                "start_line": 10,
                "end_line": 12,
                "content": "def parse():\n    return state",
            }

    engine._engine_client = FakeClient()
    engine._config = type("Config", (), {
        "repo_id": "django__django-14053",
        "revision": "latest",
        "query_budget": 4000,
    })()
    engine._session_id = "session-123"

    result = engine.handle_tool_call(
        "context_read",
        {
            "path": "parser.py",
            "repo_id": "django__django-14053",
            "start_line": 10,
            "end_line": 12,
        },
    )

    assert "ok: true" in result
    assert "path: parser.py" in result
    assert "lines: 10-12" in result
    assert "```python\ndef parse():\n    return state\n```" in result
    assert calls == [{
        "repo_id": "django__django-14053",
        "session_id": "session-123",
        "path": "parser.py",
        "revision": "latest",
        "start_line": 10,
        "end_line": 12,
    }]


def test_formsy_engine_client_forwards_memory_read():
    calls = []

    class FakeRuntimeClient:
        async def memory_read(self, **kwargs):
            calls.append(kwargs)
            return {"content": "source"}

    client = EngineClient(cast(RuntimeClient, FakeRuntimeClient()))

    result = FormsyContextEngine()._run_async(
        client.memory_read(
            repo_id="django__django-14053",
            session_id="session-123",
            path="parser.py",
            revision="latest",
            start_line=10,
            end_line=12,
        )
    )

    assert result == {"content": "source"}
    assert calls == [{
        "repo_id": "django__django-14053",
        "session_id": "session-123",
        "path": "parser.py",
        "revision": "latest",
        "start_line": 10,
        "end_line": 12,
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
