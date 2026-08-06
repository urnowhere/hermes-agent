"""Tests for the Formsy context engine plugin."""

import json
import subprocess
from inspect import iscoroutinefunction, signature
from typing import cast

import pytest

from plugins.context_engine.formsy.config import EngineConfigManager, EngineConfig
from plugins.context_engine.formsy.client import EngineClient
from plugins.context_engine.formsy.engine import FormsyContextEngine, WriteScopePolicy
from plugins.formsy import RuntimeClient
from plugins.formsy.constraint_keeper.coordinator import ConstraintKeeperCoordinator
from plugins.formsy.identity import derive_formsy_identity


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


def test_formsy_engine_default_config_uses_observe_only_retrieval_gate():
    assert EngineConfig().retrieval_gate == "observe_only"


def test_formsy_engine_observe_only_does_not_block_retrieval_or_scope_after_grounding():
    engine = FormsyContextEngine()
    engine._config = EngineConfig()
    engine._sync_trace_state(state="grounded")
    engine._set_accepted_targets(["lib/ansible/executor/play_iterator.py"])

    assert engine.get_tool_block_message("context_search", {"query": "more context"}) is None
    assert engine.get_tool_block_message("search_files", {"query": "PlayIterator"}) is None
    assert engine.get_tool_block_message(
        "terminal",
        {"command": "grep -R PlayIterator lib/ansible"},
    ) is None
    assert engine.get_tool_block_message(
        "read_file",
        {"path": "lib/ansible/plugins/strategy/linear.py"},
    ) is None
    assert engine.get_tool_block_message(
        "patch",
        {"path": "lib/ansible/plugins/strategy/linear.py"},
    ) is None
    assert engine.get_tool_block_message(
        "terminal",
        {"command": "cat > reproduce.py <<'PY'\nprint('repro')\nPY"},
    ) is None


def test_formsy_engine_observe_only_still_blocks_deterministic_forbidden_operations():
    engine = FormsyContextEngine()
    engine._config = EngineConfig()
    engine._sync_trace_state(state="grounded")
    engine._set_accepted_targets(["lib/ansible/executor/play_iterator.py"])

    rm_git = engine.get_tool_block_message("terminal", {"command": "rm -rf .git"})
    write_git = engine.get_tool_block_message("write_file", {"path": ".git/config", "content": "x"})

    assert rm_git is not None
    assert "forbidden" in rm_git.lower() or "destructive" in rm_git.lower()
    assert write_git is not None
    assert "forbidden" in write_git.lower()


def test_formsy_engine_pre_seed_context_read_returns_synthetic_grounding_payload():
    engine = FormsyContextEngine()
    engine._config = EngineConfig()

    result = engine.handle_tool_call(
        "context_read",
        {
            "path": "lib/ansible/executor/play_iterator.py",
            "start_line": 95,
            "end_line": 100,
        },
    )

    payload = json.loads(result)
    assert payload["ok"] is True
    assert payload["synthetic"] is True
    assert payload["blocking"] is False
    assert payload["grounding_required"] is True
    assert payload["content"] == ""
    assert payload["context_meta"]["source"] == "synthetic_pre_seed"
    assert payload["context_meta"]["read_key"] == "lib/ansible/executor/play_iterator.py:95-100"


def test_formsy_engine_context_read_normalizes_workspace_absolute_path(monkeypatch):
    engine = FormsyContextEngine()
    calls = []

    class FakeClient:
        async def memory_read(self, **kwargs):
            calls.append(kwargs)
            return {
                "path": "lib/ansible/modules/iptables.py",
                "content": "DOCUMENTATION = ''\n",
                "start_line": 1,
                "end_line": 1,
                "total_lines": 1,
                "context_meta": {
                    "source": "compiled_repo",
                    "source_freshness": "compiled",
                    "working_tree_alignment": "unknown",
                    "read_key": "lib/ansible/modules/iptables.py:1-1",
                },
            }

    monkeypatch.setattr(
        engine,
        "_resolve_repository_identity",
        lambda: ("ansible__ansible", "abc123"),
    )
    monkeypatch.setattr(
        engine,
        "_context_read_revision",
        lambda *, repo_id, fallback_revision: fallback_revision,
    )
    engine._engine_client = FakeClient()
    engine._config = EngineConfig()
    engine._session_id = "session-123"
    engine._retrieval_state = "grounded"
    engine._set_accepted_targets(["lib/ansible/modules/iptables.py"])

    result = engine.handle_tool_call(
        "context_read",
        {
            "path": "/Users/wayneliu/dev/ansible/lib/ansible/modules/iptables.py",
            "start_line": 1,
            "end_line": 1,
        },
    )

    assert "context_read is limited" not in result
    assert "ok: true" in result
    assert "path: lib/ansible/modules/iptables.py" in result
    assert calls[0]["path"] == "lib/ansible/modules/iptables.py"


def test_formsy_engine_projects_skill_uptake_status_when_available(monkeypatch):
    engine = FormsyContextEngine()

    class FakeCoordinator:
        def get_skill_uptake_status(self):
            return {
                "skill_name": "formsy-context",
                "skill_visibility": "skill_view_loaded",
                "skill_body_loaded": True,
            }

    monkeypatch.setattr(
        "plugins.context_engine.formsy.engine._get_constraint_keeper_coordinator",
        lambda: FakeCoordinator(),
    )

    status = engine.get_retrieval_status()

    assert status["skill_uptake"] == {
        "skill_name": "formsy-context",
        "skill_visibility": "skill_view_loaded",
        "skill_body_loaded": True,
    }


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


def test_formsy_engine_compress_captures_full_user_task_for_first_context_search():
    engine = FormsyContextEngine()
    full_task = (
        "In ansible, `Request.open` should handle gzip `Content-Encoding` responses correctly "
        "and respect `decompress=False`.\n\n"
        "Expected behavior:\n"
        "- gzip `Content-Encoding` responses are decoded by default.\n"
        "- when `decompress=False`, gzip responses should remain compressed / raw.\n"
        "- non-gzip responses should keep existing behavior."
    )

    messages = [
        {"role": "system", "content": "SYSTEM PROMPT MUST NOT LEAK"},
        {"role": "user", "content": full_task},
    ]

    assert engine.compress(messages, current_tokens=100) is messages
    metadata = engine._build_query_metadata(
        {
            "query": "Request.open gzip",
            "metadata": {"full_task_description": "short stale query"},
        },
        repo_id="ansible__ansible",
        session_id="session-123",
    )

    assert metadata["full_task_description"] == full_task
    assert "Expected behavior" in metadata["full_task_description"]
    assert "decompress=False" in metadata["full_task_description"]
    assert "SYSTEM PROMPT MUST NOT LEAK" not in json.dumps(metadata)


def test_formsy_engine_exposes_memory_search_tool():
    engine = FormsyContextEngine()

    schemas = engine.get_tool_schemas()

    assert [schema["name"] for schema in schemas] == [
        "context_search",
        "formsy_compile_repo",
        "context_read",
    ]
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
    assert "tocs_lookup_identity" in metadata["properties"]
    search_description = schemas[0]["description"]
    assert "semantic_context_action_package" in search_description
    assert "Do not repeat context_search" in search_description
    assert "Use context_search proactively and repeatedly" not in search_description
    read_description = schemas[2]["description"]
    assert "known_read_keys" in read_description
    assert "Do not repeat unchanged source text" in read_description
    read_params = schemas[2]["parameters"]["properties"]
    assert "known_read_keys" in read_params
    tocs_lookup_identity = metadata["properties"]["tocs_lookup_identity"]
    assert tocs_lookup_identity["type"] == "object"
    assert "runtime/plugin-provided" in tocs_lookup_identity["description"]
    assert "fallback" in metadata["properties"]["grounding_phase"]["enum"]
    assert "follow next_action" in schemas[0]["description"]
    assert "already completed before the task starts" not in schemas[0]["description"]
    assert "formsy_compile_repo" in schemas[0]["description"]
    assert "current git remote URL and commit" in schemas[0]["description"]
    compile_params = schemas[1]["parameters"]
    assert compile_params["properties"]["query"]["type"] == "string"
    assert "repo_id" not in compile_params["properties"]
    assert "revision" not in compile_params["properties"]
    read_params = schemas[2]["parameters"]
    assert read_params["required"] == ["path"]
    assert "repo_id" not in read_params["properties"]
    assert "revision" not in read_params["properties"]
    assert "start_line" in read_params["properties"]
    assert "end_line" in read_params["properties"]


def test_formsy_engine_context_search_metadata_includes_full_task_without_system_prompt():
    engine = FormsyContextEngine()
    engine._task_instruction_text = (
        "In ansible, `Request.open` should handle gzip `Content-Encoding` responses.\n"
        "Expected behavior:\n"
        "- gzip `Content-Encoding` responses are decoded by default.\n"
        "- when `decompress=False`, gzip responses should remain compressed / raw.\n"
        "- non-gzip responses should keep existing behavior."
    )

    metadata = engine._build_query_metadata(
        {
            "metadata": {
                "full_task_description": "Request.open gzip only",
                "system_prompt": "SYSTEM PROMPT MUST NOT LEAK",
                "messages": [{"role": "system", "content": "hidden"}],
            }
        },
        repo_id="ansible__ansible",
        session_id="session-123",
    )

    assert metadata["full_task_description"] == engine._task_instruction_text
    assert "decompress=False" in metadata["full_task_description"]
    assert "Expected behavior" in metadata["full_task_description"]
    assert "system_prompt" not in metadata
    assert "messages" not in metadata
    assert "SYSTEM PROMPT MUST NOT LEAK" not in json.dumps(metadata)


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

    data = json.loads(result)
    assert data["ok"] is True
    assert "memory_recall" not in data
    assert "memory_status" not in data
    assert "memory_query_hints" not in data
    assert "memory_test_hints" not in data
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
            "query_timeout_s": 90,
            "fanout_timeout_s": 90,
        },
    }]


def test_formsy_engine_injects_runtime_tocs_lookup_identity(monkeypatch):
    engine = FormsyContextEngine()
    calls = []

    class FakeClient:
        async def memory_search(self, **kwargs):
            calls.append(kwargs)
            return {"matches": []}

    class RuntimeIdentity:
        repo_id = "ansible__ansible"
        revision = "abc123"

        def to_runtime_identity(self):
            return {
                "repo_id": "ansible__ansible",
                "revision": "abc123",
                "tocs_lookup_identity": {
                    "tocs_case_id": "ansible_gzip_response_decompress",
                    "tocs_run_profile": "p0a-real-lane-a",
                    "repo_id": "ansible__ansible",
                    "base_revision": "abc123",
                },
            }

    def fake_run(cmd, **kwargs):
        raise AssertionError(f"unexpected git command: {cmd}")

    monkeypatch.setattr("plugins.context_engine.formsy.engine.subprocess.run", fake_run)
    engine._engine_client = FakeClient()
    engine._config = type("Config", (), {
        "repo_id": "",
        "revision": "latest",
        "query_budget": 4000,
    })()
    engine._session_id = "session-123"
    engine._identity_snapshot = RuntimeIdentity()

    result = engine.handle_tool_call("context_search", {"query": "gzip response"})

    assert json.loads(result)["ok"] is True
    assert calls[0]["metadata"]["tocs_lookup_identity"] == {
        "tocs_case_id": "ansible_gzip_response_decompress",
        "tocs_run_profile": "p0a-real-lane-a",
        "repo_id": "ansible__ansible",
        "base_revision": "abc123",
    }


def test_formsy_engine_injects_config_tocs_lookup_identity_without_runtime_snapshot(
    monkeypatch,
):
    engine = FormsyContextEngine()
    calls = []

    class FakeClient:
        async def memory_search(self, **kwargs):
            calls.append(kwargs)
            return {"matches": []}

    def fake_run(cmd, **kwargs):
        raise AssertionError(f"unexpected git command: {cmd}")

    lookup_identity = {
        "tocs_case_id": "ansible_iptables_chain_management",
        "tocs_run_profile": "tocs-p0-local",
        "repo_id": "ansible__ansible",
        "base_revision": "abc123",
    }
    monkeypatch.setattr("plugins.context_engine.formsy.engine.subprocess.run", fake_run)
    engine._engine_client = FakeClient()
    engine._config = EngineConfig(
        repo_id="ansible__ansible",
        revision="abc123",
        tocs_lookup_identity=lookup_identity,
    )
    engine._session_id = "session-123"
    engine._identity_snapshot = None

    result = engine.handle_tool_call(
        "context_search",
        {"query": "iptables chain management"},
    )

    assert json.loads(result)["ok"] is True
    assert calls[0]["metadata"]["tocs_lookup_identity"] == lookup_identity


def test_formsy_engine_uses_supplied_tocs_identity_when_git_identity_missing(monkeypatch):
    engine = FormsyContextEngine()
    calls = []

    class FakeClient:
        async def memory_search(self, **kwargs):
            calls.append(kwargs)
            return {"matches": []}

    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="not a git repo")

    monkeypatch.setattr("plugins.context_engine.formsy.engine.subprocess.run", fake_run)
    engine._engine_client = FakeClient()
    engine._config = type("Config", (), {
        "repo_id": "",
        "revision": "",
        "query_budget": 4000,
        "timeout_s": 120,
    })()
    engine._session_id = "session-123"

    result = engine.handle_tool_call(
        "context_search",
        {
            "query": "ansible iptables chain_management create delete check mode",
            "metadata": {
                "tocs_lookup_identity": {
                    "repo_id": "ansible__ansible",
                    "revision": "173091e2e36d38c978002990795f66cfc0af30ad",
                }
            },
        },
    )

    data = json.loads(result)
    assert data["ok"] is True
    assert calls[0]["repo_id"] == "ansible__ansible"
    assert calls[0]["revision"] == "173091e2e36d38c978002990795f66cfc0af30ad"


def test_formsy_engine_uses_runtime_tocs_identity_when_compile_fails(monkeypatch):
    engine = FormsyContextEngine()
    calls = []

    class FakeClient:
        last_error = "compile timeout"

        async def compile_repo(self, **kwargs):
            calls.append(("compile", kwargs))
            return None

        async def memory_search(self, **kwargs):
            calls.append(("memory_search", kwargs))
            return {
                "coverage": "partial",
                "matches": [{"path": "lib/ansible/utils/display.py"}],
                "accepted_targets": ["lib/ansible/utils/display.py"],
                "exploration_closed": True,
                "guidance": {
                    "tocs_delivery": {
                        "requested": True,
                        "resolved": True,
                        "artifact_resolution_mode": "latest_gated_case_profile",
                    },
                    "tocs": {
                        "lane_b_mode": "repair_ready_exact",
                        "must_read_files": [
                            {"path": "lib/ansible/modules/iptables.py"},
                        ],
                    },
                },
            }

    class RuntimeIdentity:
        repo_id = "ansible__ansible"
        revision = "abc123"

        def to_runtime_identity(self):
            return {
                "repo_id": "ansible__ansible",
                "revision": "abc123",
                "tocs_lookup_identity": {
                    "tocs_case_id": "ansible_iptables_chain_management",
                    "tocs_run_profile": "tocs-p0-local",
                    "repo_id": "ansible__ansible",
                    "base_revision": "abc123",
                },
            }

    class FakeCoordinator:
        def compile_context_bundle(self, **kwargs):
            return ""

        def observe_tool_result(self, *args, **kwargs):
            return None

    monkeypatch.setattr(
        "plugins.context_engine.formsy.engine._get_constraint_keeper_coordinator",
        lambda: FakeCoordinator(),
    )
    engine._engine_client = FakeClient()
    engine._config = type("Config", (), {
        "repo_id": "",
        "revision": "latest",
        "query_budget": 4000,
        "workspace_id": "local",
    })()
    engine._session_id = "session-123"
    engine._identity_snapshot = RuntimeIdentity()

    result = json.loads(
        engine.handle_tool_call("context_search", {"query": "iptables chain management"})
    )

    assert result["ok"] is True
    assert result["tocs_repair_targets"] == ["lib/ansible/modules/iptables.py"]
    assert result["accepted_targets"] == ["lib/ansible/modules/iptables.py"]
    assert result["tocs_contract_projection"]["source"] == "resolved_tocs"
    assert "### TOCS Priority" in result["extra_context"]
    assert [name for name, _ in calls] == ["compile", "memory_search"]
    assert calls[1][1]["metadata"]["tocs_lookup_identity"]["tocs_case_id"] == (
        "ansible_iptables_chain_management"
    )
    assert calls[1][1]["metadata"]["compile_status"] == "failed"
    assert calls[1][1]["metadata"]["compile_error"] == "compile timeout"


def test_formsy_engine_compile_failure_without_resolved_tocs_requires_compile():
    engine = FormsyContextEngine()
    calls = []

    class FakeClient:
        last_error = "compile timeout"

        async def compile_repo(self, **kwargs):
            calls.append(("compile", kwargs))
            return None

        async def memory_search(self, **kwargs):
            calls.append(("memory_search", kwargs))
            return {
                "coverage": "poor",
                "matches": [],
                "guidance": {
                    "tocs_delivery": {"requested": True, "resolved": False},
                },
            }

    class RuntimeIdentity:
        repo_id = "ansible__ansible"
        revision = "abc123"

        def to_runtime_identity(self):
            return {
                "repo_id": "ansible__ansible",
                "revision": "abc123",
                "tocs_lookup_identity": {
                    "tocs_case_id": "unknown_case",
                    "tocs_run_profile": "tocs-p0-local",
                },
            }

    engine._engine_client = FakeClient()
    engine._config = type("Config", (), {
        "repo_id": "ansible__ansible",
        "revision": "latest",
        "query_budget": 4000,
    })()
    engine._session_id = "session-123"
    engine._identity_snapshot = RuntimeIdentity()

    result = json.loads(
        engine.handle_tool_call("context_search", {"query": "unknown task"})
    )

    assert result["retrieval_state"] == "compile_required"
    assert result["recovery_mode"] == "compile_required"
    assert result["warning"] == "Formsy memory compile unavailable before context_search"
    assert "tocs_repair_targets" not in result
    assert result["allowed_tools"] == ["formsy_compile_repo"]
    assert result["guidance_packet"]["target_candidates"] == []
    assert [name for name, _ in calls] == ["compile", "memory_search"]


def test_formsy_engine_preserves_supplied_tocs_lookup_identity(monkeypatch):
    engine = FormsyContextEngine()
    calls = []

    class FakeClient:
        async def memory_search(self, **kwargs):
            calls.append(kwargs)
            return {"matches": []}

    class RuntimeIdentity:
        repo_id = "ansible__ansible"
        revision = "abc123"

        def to_runtime_identity(self):
            return {
                "repo_id": "ansible__ansible",
                "revision": "abc123",
                "tocs_lookup_identity": {
                    "tocs_case_id": "runtime_case",
                },
            }

    def fake_run(cmd, **kwargs):
        raise AssertionError(f"unexpected git command: {cmd}")

    supplied = {
        "tocs_case_id": "agent_supplied_case",
        "source": "agent_metadata",
    }
    monkeypatch.setattr("plugins.context_engine.formsy.engine.subprocess.run", fake_run)
    engine._engine_client = FakeClient()
    engine._config = type("Config", (), {
        "repo_id": "",
        "revision": "latest",
        "query_budget": 4000,
    })()
    engine._session_id = "session-123"
    engine._identity_snapshot = RuntimeIdentity()

    result = engine.handle_tool_call(
        "context_search",
        {
            "query": "gzip response",
            "metadata": {"tocs_lookup_identity": supplied},
        },
    )

    assert json.loads(result)["ok"] is True
    assert calls[0]["metadata"]["tocs_lookup_identity"] == supplied


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
        staticmethod(lambda root, query="": [{
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
    assert calls[0][1]["repo_id"] == "urnowhere__hermes-agent"
    assert calls[0][1]["files"] == [{
        "path": "pkg/mod.py",
        "content": "x = 1\n",
        "language": "python",
        "is_test": False,
    }]
    assert calls[0][1]["revision"] == "abc123def456"
    assert calls[0][1]["session_id"] == "session-123"
    assert calls[0][1]["mode"] == "merge"
    assert calls[0][1]["metadata"] | {
        "instance_id": "urnowhere__hermes-agent",
        "query": "parser state handling",
        "source_file_count": 1,
        "compile_profile": "interactive_context_search",
        "source_scope": "query_bounded",
        "function_embeddings": "deferred",
        "sync_function_embeddings": False,
    } == calls[0][1]["metadata"]
    assert calls[0][1]["metadata"]["query_signature"]
    assert calls[1][1]["revision"] == "abc123def456"


def test_formsy_engine_seed_search_uses_full_user_task_for_compile_and_retrieval(monkeypatch):
    engine = FormsyContextEngine()
    calls = []
    full_task = (
        "In ansible, uri must preserve an explicitly supplied Authorization header.\n\n"
        "Expected behavior:\n"
        "- Explicit Authorization takes precedence over netrc credentials.\n"
        "- netrc remains available when Authorization is not supplied."
    )

    class FakeClient:
        async def compile_repo(self, **kwargs):
            calls.append(("compile", kwargs))
            return {"repo_id": kwargs["repo_id"], "revision": kwargs["revision"]}

        async def memory_search(self, **kwargs):
            calls.append(("memory_search", kwargs))
            return {"matches": []}

    def fake_run(cmd, **kwargs):
        if cmd == ["git", "remote", "get-url", "origin"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="https://github.com/ansible/ansible.git\n")
        if cmd == ["git", "rev-parse", "HEAD"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="base-revision\n")
        raise AssertionError(f"unexpected git command: {cmd}")

    monkeypatch.setattr("plugins.context_engine.formsy.engine.subprocess.run", fake_run)
    monkeypatch.setattr(
        FormsyContextEngine,
        "_collect_memory_source_files",
        staticmethod(lambda root, query="": [{"path": "lib/ansible/module_utils/urls.py", "content": "x\n"}]),
    )
    engine._engine_client = FakeClient()
    engine._config = type("Config", (), {"repo_id": "", "revision": "latest", "query_budget": 4000})()
    engine._session_id = "session-123"
    engine._task_instruction_text = full_task

    payload = json.loads(engine.handle_tool_call(
        "context_search",
        {
            "query": "uri authorization header Expected behavior Explicit",
            "metadata": {"grounding_phase": "seed", "system_prompt": "must not leak"},
        },
    ))

    assert payload["ok"] is True
    assert payload["query"] == full_task
    assert [name for name, _ in calls] == ["compile", "memory_search"]
    assert calls[0][1]["metadata"]["query"] == full_task
    assert calls[1][1]["query"] == full_task
    assert calls[1][1]["metadata"]["full_task_description"] == full_task
    assert "system_prompt" not in calls[1][1]["metadata"]


def test_formsy_engine_grounded_search_keeps_focused_query(monkeypatch):
    engine = FormsyContextEngine()
    calls = []

    class FakeClient:
        async def memory_search(self, **kwargs):
            calls.append(kwargs)
            return {"matches": []}

    def fake_run(cmd, **kwargs):
        if cmd == ["git", "remote", "get-url", "origin"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="https://github.com/ansible/ansible.git\n")
        if cmd == ["git", "rev-parse", "HEAD"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="base-revision\n")
        raise AssertionError(f"unexpected git command: {cmd}")

    monkeypatch.setattr("plugins.context_engine.formsy.engine.subprocess.run", fake_run)
    engine._engine_client = FakeClient()
    engine._config = type("Config", (), {"repo_id": "", "revision": "latest", "query_budget": 4000})()
    engine._session_id = "session-123"
    engine._memory_compiled_identity = (
        "ansible__ansible",
        "base-revision",
        engine._query_signature("focused Request.open authorization propagation"),
    )
    engine._task_instruction_text = "A full user task with multiple Expected behavior bullets."

    payload = json.loads(engine.handle_tool_call(
        "context_search",
        {
            "query": "focused Request.open authorization propagation",
            "metadata": {"grounding_phase": "grounded"},
        },
    ))

    assert payload["ok"] is True
    assert payload["query"] == "focused Request.open authorization propagation"
    assert calls[0]["query"] == "focused Request.open authorization propagation"


def test_formsy_engine_compile_payload_reserves_query_relevant_tests(tmp_path):
    source_root = tmp_path / "repo"
    source_root.mkdir()
    package = source_root / "django" / "contrib" / "sample"
    package.mkdir(parents=True)
    for index in range(510):
        (package / f"module_{index}.py").write_text(f"VALUE_{index} = {index}\n", encoding="utf-8")

    tests_root = source_root / "tests"
    for index in range(130):
        test_dir = tests_root / f"suite_{index}"
        test_dir.mkdir(parents=True)
        (test_dir / "test_misc.py").write_text("def test_misc():\n    assert True\n", encoding="utf-8")

    target = tests_root / "auth_tests" / "test_validators.py"
    target.parent.mkdir(parents=True)
    target.write_text(
        "from django.contrib.auth.validators import UnicodeUsernameValidator\n"
        "def test_unicode_username_validator():\n"
        "    assert UnicodeUsernameValidator.regex\n",
        encoding="utf-8",
    )

    files = FormsyContextEngine._collect_memory_source_files(
        source_root,
        query="UnicodeUsernameValidator username validator regex",
    )

    paths = [entry["path"] for entry in files]
    assert paths == ["tests/auth_tests/test_validators.py"]
    assert "tests/auth_tests/test_validators.py" in paths
    assert any(entry["is_test"] for entry in files)


def test_formsy_engine_compile_payload_excludes_low_information_matches(tmp_path):
    source_root = tmp_path / "repo"
    source_root.mkdir()
    package = source_root / "lib" / "sample"
    package.mkdir(parents=True)
    for index in range(12):
        (package / f"unrelated_{index:02d}.py").write_text(
            "request_enabled = False\n",
            encoding="utf-8",
        )

    target = package / "transport.py"
    target.write_text(
        "class Request:\n"
        "    def open(self, decompress=True):\n"
        "        return decode_gzip(decompress)\n",
        encoding="utf-8",
    )
    target_test = source_root / "tests" / "test_transport_gzip.py"
    target_test.parent.mkdir(parents=True)
    target_test.write_text(
        "def test_gzip_decompression():\n"
        "    assert Request().open(decompress=False)\n",
        encoding="utf-8",
    )

    files = FormsyContextEngine._collect_memory_source_files(
        source_root,
        query="Request.open gzip decompress False",
        max_files=20,
    )

    assert {entry["path"] for entry in files} == {
        "lib/sample/transport.py",
        "tests/test_transport_gzip.py",
    }


def test_formsy_engine_compile_payload_prefers_structured_and_repeated_query_terms(tmp_path):
    source_root = tmp_path / "repo"
    source_root.mkdir()
    package = source_root / "lib" / "sample"
    package.mkdir(parents=True)
    for index, prose_term in enumerate(
        ("correctly", "respect", "decoded", "default", "remain", "compressed", "raw", "existing")
    ):
        (package / f"unrelated_{index:02d}.py").write_text(
            f"description = '{prose_term}'\n",
            encoding="utf-8",
        )

    target = package / "transport.py"
    target.write_text(
        "class Request:\n"
        "    def open(self, decompress=True):\n"
        "        return decode_gzip(decompress)\n",
        encoding="utf-8",
    )
    target_test = source_root / "tests" / "test_transport_gzip.py"
    target_test.parent.mkdir(parents=True)
    target_test.write_text(
        "def test_gzip_decompression():\n"
        "    assert Request().open(decompress=False)\n",
        encoding="utf-8",
    )
    query = """`Request.open` should handle gzip `Content-Encoding` responses correctly and respect `decompress=False`.
Expected behavior: gzip responses are decoded by default; gzip remains compressed/raw when requested; existing behavior remains."""

    files = FormsyContextEngine._collect_memory_source_files(
        source_root,
        query=query,
        max_files=20,
    )

    assert {entry["path"] for entry in files} == {
        "lib/sample/transport.py",
        "tests/test_transport_gzip.py",
    }


def test_formsy_engine_compile_payload_enforces_total_content_byte_budget(tmp_path):
    source_root = tmp_path / "repo"
    source_root.mkdir()
    (source_root / "a_oversized.py").write_text(
        "needle = '" + ("x" * 180) + "'\n",
        encoding="utf-8",
    )
    (source_root / "b_primary.py").write_text(
        "needle = '" + ("y" * 38) + "'\n",
        encoding="utf-8",
    )
    (source_root / "c_secondary.py").write_text(
        "needle = '" + ("z" * 28) + "'\n",
        encoding="utf-8",
    )

    files = FormsyContextEngine._collect_memory_source_files(
        source_root,
        query="needle",
        max_files=10,
        max_bytes=100,
    )

    paths = [entry["path"] for entry in files]
    content_bytes = sum(len(entry["content"].encode("utf-8")) for entry in files)
    assert paths == ["b_primary.py", "c_secondary.py"]
    assert content_bytes <= 100


def test_formsy_engine_compile_payload_has_tight_default_file_and_byte_budgets(tmp_path):
    source_root = tmp_path / "repo"
    source_root.mkdir()
    for index in range(40):
        (source_root / f"relevant_{index:02d}.py").write_text(
            f"needle_{index} = '" + ("x" * 20_000) + "'\n",
            encoding="utf-8",
        )

    files = FormsyContextEngine._collect_memory_source_files(
        source_root,
        query="needle",
    )

    content_bytes = sum(len(entry["content"].encode("utf-8")) for entry in files)
    assert len(files) <= 32
    assert content_bytes <= 500_000


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
                "guidance": {
                    "summary": "Ranked repository evidence and next actions.",
                    "useful_context": [{"path": "parser.py", "rank": 1}],
                    "suggested_next_actions": ["context_read parser.py"],
                },
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
    assert data["guidance"]["useful_context"] == [{"path": "parser.py", "rank": 1}]
    assert data["guidance"]["suggested_next_actions"] == ["context_read parser.py"]
    assert "symbolic_prompt" not in data
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
            "query_timeout_s": 90,
            "fanout_timeout_s": 90,
        },
    }]


def test_formsy_engine_returns_compact_agent_projection_but_notifies_full_payload(
    monkeypatch,
):
    engine = FormsyContextEngine()
    observed = []

    semantic_context = {
        "status": "partial",
        "task_summary": ["gzip responses decode by default"],
        "semantic_anchors": [
            {"text": "Request.open", "kind": "symbol"},
            {"text": "decompress=False", "kind": "parameter"},
        ],
        "semantic_claims": [
            {
                "subject": "Request.open",
                "relation": "behavior_semantics",
                "statement": "gzip responses decode by default",
                "evidence_refs": [
                    {
                        "source_locator": "lib/ansible/module_utils/urls.py:1649",
                        "internal_blob": "e" * 8_000,
                    }
                ],
            }
        ],
        "recommended_reads": [
            {"path": "lib/ansible/module_utils/urls.py", "symbol": "Request.open"}
        ],
        "coverage_gaps": [{"reason": "structural_only"}],
    }
    result = {
        "extra_context": "x" * 20_000,
        "symbolic_prompt": "s" * 40_000,
        "matches": [
            {
                "path": f"lib/ansible/module_utils/relevant_{index}.py",
                "symbol": "Request.open",
                "kind": "direct_query_match",
                "score": 10 - index,
                "content": "m" * 2_000,
            }
            for index in range(20)
        ],
        "coverage": "partial",
        "missing_context": ["No test constraints were selected."],
        "test_plan": {
            "commands": ["pytest test/units/module_utils/urls/test_gzip.py -v"],
            "files": ["test/units/module_utils/urls/test_gzip.py"],
        },
        "guidance": {
            "summary": "Use the grounded Request.open implementation and gzip tests.",
            "fs_console": {
                "code_plan_id": "cp-gzip",
                "url": "http://localhost:3000/code-plans/cp-gzip",
            },
            "code_plan_review": {
                "code_plan_id": "cp-gzip",
                "url": "http://localhost:3000/code-plans/cp-gzip",
            },
            "accepted_edit_targets": ["lib/ansible/module_utils/urls.py"],
            "recommended_first_reads": [
                {"path": "lib/ansible/module_utils/urls.py", "start_line": 1600}
            ],
            "internal_blob": "g" * 40_000,
        },
        "bundle": {
            "bundle_id": "ctx-large",
            "internal_blob": "b" * 80_000,
            "guidance": {
                "tocs_semantic_context": semantic_context,
                "tocs_contract_context": {
                    "contract_readiness": "partial",
                    "unresolved_obligation_count": 2,
                },
            },
        },
        "retrieval_state": "inspect_candidates",
        "preferred_next_step": "context_read",
        "grounded_files": ["lib/ansible/module_utils/urls.py"],
        "accepted_targets": ["lib/ansible/module_utils/urls.py"],
        "exploration_closed": True,
    }

    class FakeCoordinator:
        def compile_context_bundle(self, **kwargs):
            return (
                "FormSy Constraint Protocol\n"
                "- Run focused baseline validation before changing source."
            )

        def observe_tool_result(self, tool_name, args, payload, *, session_id=""):
            observed.append((tool_name, args, json.loads(payload), session_id))

    monkeypatch.setattr(
        "plugins.context_engine.formsy.engine._get_constraint_keeper_coordinator",
        lambda: FakeCoordinator(),
    )
    engine._config = type("Config", (), {"workspace_id": "ws-test"})()

    agent_json = engine._memory_search_payload_json(
        args={"query": "Request.open gzip"},
        query="Request.open gzip",
        repo_id="ansible__ansible",
        revision="rev",
        session_id="session-123",
        budget=4000,
        metadata={"grounding_phase": "grounded"},
        result=result,
    )

    agent_payload = json.loads(agent_json)
    assert len(agent_json.encode("utf-8")) < 32_000
    assert "bundle" not in agent_payload
    assert "symbolic_prompt" not in agent_payload
    assert "internal_blob" not in agent_payload["guidance"]
    assert agent_payload["guidance"]["fs_console"] == {
        "code_plan_id": "cp-gzip",
        "url": "http://localhost:3000/code-plans/cp-gzip",
    }
    assert agent_payload["guidance"]["code_plan_review"] == {
        "code_plan_id": "cp-gzip",
        "url": "http://localhost:3000/code-plans/cp-gzip",
    }
    assert len(agent_payload["extra_context"]) <= 8_000
    assert len(agent_payload["matches"]) == 12
    assert all("content" not in match for match in agent_payload["matches"])
    assert agent_payload["tocs_semantic_context"]["semantic_anchors"] == [
        {"text": "Request.open", "kind": "symbol"},
        {"text": "decompress=False", "kind": "parameter"},
    ]
    assert agent_payload["accepted_targets"] == ["lib/ansible/module_utils/urls.py"]
    assert "Run focused baseline validation before changing source" in agent_payload[
        "constraint_protocol_text"
    ]
    assert agent_payload["test_plan"]["files"] == [
        "test/units/module_utils/urls/test_gzip.py"
    ]

    assert observed
    full_payload = observed[-1][2]
    assert full_payload["bundle"]["internal_blob"] == "b" * 80_000
    assert full_payload["symbolic_prompt"] == "s" * 40_000


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
            "query_timeout_s": 90,
            "fanout_timeout_s": 90,
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
            "query_timeout_s": 90,
            "fanout_timeout_s": 90,
            "memory_artifact_ids": ["artifact-2", "artifact-3", "artifact-1"],
            "memory_query_hints": ["existing hint", "search auth tests"],
            "memory_test_hints": ["python -m pytest tests/auth"],
            "memory_status": "warm",
            "memory_freshness": "fresh",
        },
    }]


def test_formsy_engine_memory_search_appends_agent_visible_memory_block():
    engine = FormsyContextEngine()

    class FakeClient:
        async def memory_search(self, **kwargs):
            return {
                "extra_context": "## FormSy Guidance\n- ONLY EDIT: lib/ansible/executor/play_iterator.py",
                "matches": [],
            }

    class HintProvider:
        def get_context_hints(self):
            return {
                "memory_status": "hit",
                "memory_freshness": "local",
                "verified_solution_recipes": [{
                    "schema": "formsy.verified_solution_recipe.v1",
                    "primary_edit_files": ["lib/ansible/executor/play_iterator.py"],
                    "validation_commands": ["python3 -m py_compile lib/ansible/executor/play_iterator.py"],
                }],
                "memory_block": (
                    "## Relevant Memory\n"
                    "### Verified Solution Recipe\n"
                    "### Solution Digest\n"
                    "- Prior patch touched: lib/ansible/executor/play_iterator.py"
                ),
            }

    class FakeMemoryManager:
        providers = [HintProvider()]

    engine._engine_client = FakeClient()
    engine._memory_manager = FakeMemoryManager()
    engine._config = type("Config", (), {
        "repo_id": "ansible__ansible",
        "revision": "latest",
        "query_budget": 4000,
    })()
    engine._session_id = "session-123"

    result = json.loads(engine.handle_tool_call(
        "context_search",
        {"query": "PlayIterator state representation"},
    ))

    assert result["memory_status"] == "hit"
    assert result["verified_solution_recipes"] == [{
        "schema": "formsy.verified_solution_recipe.v1",
        "primary_edit_files": ["lib/ansible/executor/play_iterator.py"],
        "validation_commands": ["python3 -m py_compile lib/ansible/executor/play_iterator.py"],
    }]
    assert "## Relevant Memory" in result["extra_context"]
    assert "Prior patch touched: lib/ansible/executor/play_iterator.py" in result["extra_context"]
    assert "## FormSy Guidance" in result["extra_context"]


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
            "query_timeout_s": 90,
            "fanout_timeout_s": 90,
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
                "guidance": {
                    "mode": "degraded_recovery",
                    "target_candidates": ["django/contrib/auth/validators.py"],
                    "likely_edit_files": ["django/contrib/auth/validators.py"],
                    "recommended_first_reads": [
                        {
                            "path": "django/contrib/auth/validators.py",
                            "reason": "Server degraded guidance target.",
                        }
                    ],
                    "probe_budget": {
                        "search_files": 1,
                        "read_file": 2,
                        "terminal_or_execute_code": 2,
                    },
                    "patch_now_threshold": {"grounded_source_reads": 1},
                },
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
    assert result["guidance_packet"]["mode"] == "degraded_recovery"
    assert result["guidance_packet"]["target_candidates"] == [
        "django/contrib/auth/validators.py"
    ]
    assert result["guidance_packet"]["recommended_first_reads"][0]["reason"] == (
        "Server degraded guidance target."
    )
    assert result["guidance_packet"]["probe_budget"] == {
        "search_files": 1,
        "read_file": 2,
        "terminal_or_execute_code": 2,
    }
    assert result["guidance_packet"]["patch_now_threshold"] == {
        "grounded_source_reads": 1
    }


def test_formsy_engine_preserves_server_owned_degraded_edit_scope():
    engine = FormsyContextEngine()

    packet = engine._server_degraded_guidance_packet(
        {
            "guidance": {
                "mode": "degraded_recovery",
                "target_candidates": [
                    "lib/ansible/executor/play_iterator.py",
                    "lib/ansible/plugins/strategy/__init__.py",
                    "lib/ansible/plugins/strategy/linear.py",
                ],
                "primary_edit_target": "lib/ansible/executor/play_iterator.py",
                "accepted_edit_targets": ["lib/ansible/executor/play_iterator.py"],
                "read_only_context_files": [
                    "lib/ansible/plugins/strategy/__init__.py",
                    "lib/ansible/plugins/strategy/linear.py",
                ],
                "non_goals": [
                    "Do not migrate external strategy plugin consumers in the first patch.",
                ],
                "likely_edit_files": ["lib/ansible/executor/play_iterator.py"],
                "recommended_first_reads": [
                    {
                        "path": "lib/ansible/executor/play_iterator.py",
                        "reason": "Server primary edit target.",
                    }
                ],
                "probe_budget": {
                    "search_files": 1,
                    "read_file": 2,
                    "terminal_or_execute_code": 2,
                },
                "patch_now_threshold": {"grounded_source_reads": 1},
            },
        }
    )

    assert packet is not None
    assert packet["primary_edit_target"] == "lib/ansible/executor/play_iterator.py"
    assert packet["accepted_edit_targets"] == ["lib/ansible/executor/play_iterator.py"]
    assert packet["read_only_context_files"] == [
        "lib/ansible/plugins/strategy/__init__.py",
        "lib/ansible/plugins/strategy/linear.py",
    ]
    assert packet["non_goals"] == [
        "Do not migrate external strategy plugin consumers in the first patch.",
    ]


def test_formsy_engine_projects_nested_guidance_into_retrieval_status():
    engine = FormsyContextEngine()

    class FakeClient:
        async def memory_search(self, **kwargs):
            return {
                "extra_context": (
                    "## FormSy Guidance\n"
                    "- ONLY EDIT: lib/ansible/executor/play_iterator.py\n"
                    "- NEXT SUGGESTED TOOL: context_read path=lib/ansible/executor/play_iterator.py"
                ),
                "matches": [],
                "coverage": "partial",
                "guidance": {
                    "retrieval_state": "inspect_candidates",
                    "preferred_next_step": "context_read",
                    "accepted_edit_targets": ["lib/ansible/executor/play_iterator.py"],
                    "behavioral_contracts": [
                        {
                            "id": "public_state_aliases",
                            "description": "Keep legacy aliases compatible.",
                        }
                    ],
                },
            }

    engine._engine_client = FakeClient()
    engine._config = type("Config", (), {
        "repo_id": "ansible__ansible",
        "revision": "latest",
        "query_budget": 4000,
    })()
    engine._session_id = "session-123"

    result = json.loads(
        engine.handle_tool_call(
            "context_search",
            {
                "query": "PlayIterator public state migration",
                "repo_id": "ansible__ansible",
                "metadata": {
                    "retrieval_mode": "symbolic",
                    "grounding_phase": "seed",
                    "response_format": "bundle",
                },
            },
        )
    )

    assert result["retrieval_state"] == "inspect_candidates"
    assert result["preferred_next_step"] == "context_read"
    assert result["accepted_targets"] == ["lib/ansible/executor/play_iterator.py"]
    assert result["exploration_closed"] is True
    assert result["guidance_contracts_present"] is True

    status = engine.get_retrieval_status()
    assert status["retrieval_state"] == "inspect_candidates"
    assert status["retrieval_status"] == "good"
    assert status["seed_calls"] == 1
    assert status["accepted_targets"] == ["lib/ansible/executor/play_iterator.py"]
    assert status["exploration_closed"] is True
    assert status["constraints_present"] is False


def test_formsy_engine_suppresses_duplicate_symbolic_seed_search_while_context_read_pending():
    engine = FormsyContextEngine()
    search_calls = []

    class FakeClient:
        async def memory_search(self, **kwargs):
            search_calls.append(kwargs)
            return {
                "extra_context": "## FormSy Guidance\n- ONLY EDIT: lib/ansible/executor/play_iterator.py",
                "matches": [
                    {
                        "path": "lib/ansible/executor/play_iterator.py",
                        "score": 4.0,
                    }
                ],
                "coverage": "partial",
                "retrieval_state": "inspect_candidates",
                "preferred_next_step": "context_read",
                "accepted_targets": ["lib/ansible/executor/play_iterator.py"],
                "exploration_closed": True,
            }

    engine._engine_client = FakeClient()
    engine._config = type("Config", (), {
        "repo_id": "ansible__ansible",
        "revision": "latest",
        "query_budget": 4000,
    })()
    engine._session_id = "session-123"

    first = json.loads(
        engine.handle_tool_call(
            "context_search",
            {"query": "PlayIterator state representation"},
        )
    )
    duplicate = json.loads(
        engine.handle_tool_call(
            "context_search",
            {"query": "HostState __str__ state string representation"},
        )
    )

    assert first.get("duplicate_seed_suppressed") is not True
    assert duplicate["ok"] is True
    assert duplicate["duplicate_seed_suppressed"] is True
    assert duplicate["preferred_next_step"] == "context_read"
    assert duplicate["next_tool_directive"] == {
        "tool": "context_read",
        "args": {"path": "lib/ansible/executor/play_iterator.py"},
        "reason": "A seed context_search already selected this accepted target.",
        "enforcement": "suggested",
        "max_attempts": 1,
    }
    assert len(search_calls) == 1

    status = engine.get_retrieval_status()
    assert status["seed_calls"] == 1
    assert status["retry_calls"] == 0
    assert status["context_read_required"] is True
    assert status["pending_followup_tool"] == "context_read"


def test_formsy_engine_does_not_promote_nested_guidance_candidates_to_accepted_targets():
    engine = FormsyContextEngine()

    class FakeClient:
        async def memory_search(self, **kwargs):
            return {
                "extra_context": "## FormSy Guidance\n- Candidate target exists.",
                "matches": [],
                "coverage": "partial",
                "guidance": {
                    "retrieval_state": "inspect_candidates",
                    "preferred_next_step": "context_read",
                    "target_candidates": ["lib/ansible/executor/play_iterator.py"],
                    "likely_edit_files": ["lib/ansible/executor/play_iterator.py"],
                    "recommended_first_reads": [
                        {"path": "lib/ansible/executor/play_iterator.py"},
                    ],
                },
            }

    engine._engine_client = FakeClient()
    engine._config = type("Config", (), {
        "repo_id": "ansible__ansible",
        "revision": "latest",
        "query_budget": 4000,
    })()
    engine._session_id = "session-123"

    result = json.loads(
        engine.handle_tool_call(
            "context_search",
            {
                "query": "PlayIterator public state migration",
                "repo_id": "ansible__ansible",
                "metadata": {
                    "retrieval_mode": "symbolic",
                    "grounding_phase": "seed",
                    "response_format": "bundle",
                },
            },
        )
    )

    assert result["retrieval_state"] == "inspect_candidates"
    assert result["accepted_targets"] == []
    assert result["exploration_closed"] is False

    status = engine.get_retrieval_status()
    assert status["preferred_edit_targets"] == ["lib/ansible/executor/play_iterator.py"]
    assert status["accepted_targets"] == []
    assert status["exploration_closed"] is False


def test_formsy_engine_projects_guidance_packet_accepted_targets_into_retrieval_status():
    engine = FormsyContextEngine()

    payload = {
        "retrieval_state": "inspect_candidates",
        "preferred_next_step": "context_read",
        "guidance_packet": {
            "accepted_edit_targets": ["lib/ansible/executor/play_iterator.py"],
            "primary_edit_target": "lib/ansible/executor/play_iterator.py",
            "target_candidates": [
                "lib/ansible/executor/play_iterator.py",
                "lib/ansible/plugins/strategy/linear.py",
            ],
            "recommended_first_reads": [
                {"path": "lib/ansible/executor/play_iterator.py"},
            ],
        },
    }

    engine._apply_nested_guidance_projection(payload)

    assert payload["accepted_targets"] == ["lib/ansible/executor/play_iterator.py"]
    assert payload["exploration_closed"] is True
    assert payload["retrieval_targets"] == [
        "lib/ansible/executor/play_iterator.py",
        "lib/ansible/plugins/strategy/linear.py",
    ]


def test_formsy_engine_projects_bundle_tocs_context_from_server_response():
    engine = FormsyContextEngine()
    semantic_context = {
        "status": "partial",
        "task_summary": ["gzip responses decode by default"],
        "semantic_anchors": [{"text": "decompress", "kind": "parameter"}],
        "recommended_reads": [
            {"path": "lib/ansible/module_utils/urls.py", "symbol": "Request.open"}
        ],
        "semantic_claims": [],
        "coverage_gaps": [],
    }
    contract_context = {
        "contract_readiness": "partial",
        "unresolved_obligation_count": 1,
    }
    payload = {
        "guidance": {"mode": "normal"},
        "bundle": {
            "guidance": {
                "tocs_semantic_context": semantic_context,
                "tocs_contract_context": contract_context,
            }
        },
    }

    engine._apply_nested_guidance_projection(payload)

    assert payload["tocs_semantic_context"] == semantic_context
    assert payload["tocs_contract_context"] == contract_context
    assert payload.get("accepted_targets") is None
    assert payload.get("exploration_closed") is None


def test_context_search_exposes_server_bundle_tocs_context_in_tool_payload():
    engine = FormsyContextEngine()

    class FakeClient:
        async def memory_search(self, **kwargs):
            return {
                "extra_context": (
                    "## FormSy Guidance\n"
                    "- TOCS semantic anchors: decompress"
                ),
                "matches": [],
                "coverage": "partial",
                "guidance": {"mode": "normal"},
                "bundle": {
                    "guidance": {
                        "tocs_semantic_context": {
                            "status": "partial",
                            "semantic_anchors": [
                                {"text": "decompress", "kind": "parameter"}
                            ],
                            "semantic_claims": [],
                            "recommended_reads": [],
                            "coverage_gaps": [],
                        },
                        "tocs_contract_context": {
                            "contract_readiness": "partial",
                            "unresolved_obligation_count": 1,
                        },
                    }
                },
            }

    engine._engine_client = FakeClient()
    engine._config = type(
        "Config",
        (),
        {
            "repo_id": "ansible__ansible",
            "revision": "latest",
            "query_budget": 4000,
            "timeout_s": 120,
            "workspace_id": "ws-test",
        },
    )()
    engine._session_id = "session-123"

    payload = json.loads(
        engine.handle_tool_call("context_search", {"query": "gzip response"})
    )

    assert payload["tocs_semantic_context"]["semantic_anchors"] == [
        {"text": "decompress", "kind": "parameter"}
    ]
    assert payload["tocs_contract_context"]["contract_readiness"] == "partial"
    assert payload["accepted_targets"] == []
    assert payload["exploration_closed"] is False


def test_formsy_engine_persists_guidance_packet_accepted_targets_when_server_top_level_is_empty():
    engine = FormsyContextEngine()

    class FakeClient:
        async def memory_search(self, **kwargs):
            return {
                "extra_context": "## FormSy Guidance\n- ONLY EDIT: lib/ansible/executor/play_iterator.py",
                "matches": [],
                "coverage": "partial",
                "retrieval_state": "inspect_candidates",
                "preferred_next_step": "context_read",
                "accepted_targets": [],
                "exploration_closed": False,
                "guidance_packet": {
                    "accepted_edit_targets": ["lib/ansible/executor/play_iterator.py"],
                    "primary_edit_target": "lib/ansible/executor/play_iterator.py",
                    "target_candidates": ["lib/ansible/executor/play_iterator.py"],
                    "recommended_first_reads": [
                        {"path": "lib/ansible/executor/play_iterator.py"},
                    ],
                },
            }

    engine._engine_client = FakeClient()
    engine._config = type("Config", (), {
        "repo_id": "ansible__ansible",
        "revision": "latest",
        "query_budget": 4000,
    })()
    engine._session_id = "session-123"

    result = json.loads(
        engine.handle_tool_call(
            "context_search",
            {
                "query": "PlayIterator public state migration",
                "repo_id": "ansible__ansible",
                "metadata": {
                    "retrieval_mode": "symbolic",
                    "grounding_phase": "seed",
                    "response_format": "bundle",
                },
            },
        )
    )

    assert result["accepted_targets"] == ["lib/ansible/executor/play_iterator.py"]
    assert result["exploration_closed"] is True

    status = engine.get_retrieval_status()
    assert status["accepted_targets"] == ["lib/ansible/executor/play_iterator.py"]
    assert status["exploration_closed"] is True


def test_formsy_engine_does_not_promote_guidance_packet_candidates_to_accepted_targets():
    engine = FormsyContextEngine()

    payload = {
        "retrieval_state": "inspect_candidates",
        "preferred_next_step": "context_read",
        "guidance_packet": {
            "target_candidates": ["lib/ansible/executor/play_iterator.py"],
            "likely_edit_files": ["lib/ansible/executor/play_iterator.py"],
            "recommended_first_reads": [
                {"path": "lib/ansible/executor/play_iterator.py"},
            ],
        },
    }

    engine._apply_nested_guidance_projection(payload)

    assert payload.get("accepted_targets") is None
    assert payload.get("exploration_closed") is None
    assert payload["retrieval_targets"] == ["lib/ansible/executor/play_iterator.py"]


def test_formsy_engine_projects_p0b_server_guidance_into_guidance_packet():
    engine = FormsyContextEngine()

    class FakeClient:
        async def memory_search(self, **kwargs):
            return {
                "extra_context": "",
                "matches": [
                    {
                        "path": "lib/ansible/executor/play_iterator.py",
                        "score": 4.0,
                    }
                ],
                "coverage": "partial",
                "guidance": {
                    "mode": "degraded_recovery",
                    "run_id": "trace-p0b",
                    "task_identity": {
                        "repo_id": "ansible__ansible",
                        "revision": "rev",
                        "case_id": "ansible__ansible",
                        "task_hash": "abc123def456",
                    },
                    "grounding_confidence": "low",
                    "can_patch_now": False,
                    "pre_seed_workspace_reads": [
                        {"path": "lib/ansible/executor/play_iterator.py"}
                    ],
                    "target_candidates": ["lib/ansible/executor/play_iterator.py"],
                    "likely_edit_files": ["lib/ansible/executor/play_iterator.py"],
                    "recommended_first_reads": [
                        {
                            "path": "lib/ansible/executor/play_iterator.py",
                            "reason": "Validate hinted target.",
                        }
                    ],
                    "next_tool_directive": {
                        "tool": "context_read",
                        "args": {"path": "lib/ansible/executor/play_iterator.py"},
                        "reason": (
                            "Validate hinted target and collect source grounding before broad exploration."
                        ),
                        "enforcement": "suggested",
                        "max_attempts": 1,
                    },
                    "probe_budget": {
                        "search_files": 1,
                        "read_file": 2,
                        "terminal_or_execute_code": 2,
                    },
                    "patch_now_threshold": {"grounded_source_reads": 1},
                },
            }

    engine._engine_client = FakeClient()
    engine._config = type("Config", (), {
        "repo_id": "ansible__ansible",
        "revision": "rev",
        "query_budget": 4000,
    })()
    engine._session_id = "session-123"

    result = json.loads(
        engine.handle_tool_call(
            "context_search",
            {"query": "PlayIterator public state type"},
        )
    )

    assert result["preferred_next_step"] == "context_read"
    assert result["guidance_packet"]["run_id"] == "trace-p0b"
    assert result["guidance_packet"]["task_identity"]["task_hash"] == "abc123def456"
    assert result["guidance_packet"]["grounding_confidence"] == "low"
    assert result["guidance_packet"]["can_patch_now"] is False
    assert result["guidance_packet"]["pre_seed_workspace_reads"] == [
        {"path": "lib/ansible/executor/play_iterator.py"}
    ]
    assert "required_next_tool" not in result["guidance_packet"]
    assert result["guidance_packet"]["next_tool_directive"] == {
        "tool": "context_read",
        "args": {"path": "lib/ansible/executor/play_iterator.py"},
        "reason": (
            "Validate hinted target and collect source grounding before broad exploration."
        ),
        "enforcement": "suggested",
        "max_attempts": 1,
    }
    assert result["next_tool_directive"] == result["guidance_packet"]["next_tool_directive"]
    assert result["guidance_packet"]["next_tool_directive"] == result["next_tool_directive"]
    assert result["next_tool_directive_text"] == (
        "NEXT SUGGESTED TOOL: context_read path=lib/ansible/executor/play_iterator.py"
    )
    assert "next_required_tool_text" not in result


def test_formsy_engine_context_read_notifies_constraint_keeper_to_clear_next_tool(
    monkeypatch,
):
    engine = FormsyContextEngine()

    class FakeClient:
        async def memory_read(self, **kwargs):
            return {
                "path": "lib/ansible/executor/play_iterator.py",
                "start_line": 1,
                "end_line": 3,
                "content": "class PlayIterator:\n    pass\n",
            }

    class FakeCoordinator:
        def __init__(self):
            self.observed = []

        def observe_tool_result(self, tool_name, args, result, *, session_id=""):
            self.observed.append((tool_name, args, result, session_id))

    coordinator = FakeCoordinator()
    monkeypatch.setattr(
        "plugins.context_engine.formsy.engine._get_constraint_keeper_coordinator",
        lambda: coordinator,
    )

    engine._engine_client = FakeClient()
    engine._config = type("Config", (), {
        "repo_id": "ansible__ansible",
        "revision": "rev",
        "query_budget": 4000,
    })()
    engine._session_id = "session-123"
    engine._retrieval_trace.seed_calls = 1

    result = engine.handle_tool_call(
        "context_read",
        {"path": "lib/ansible/executor/play_iterator.py"},
    )

    assert "ok: true" in result
    assert coordinator.observed
    observed_tool, observed_args, observed_result, observed_session = coordinator.observed[0]
    assert observed_tool == "context_read"
    assert observed_args == {"path": "lib/ansible/executor/play_iterator.py"}
    assert "ok: true" in observed_result
    assert observed_session == "session-123"


def test_formsy_engine_context_read_clears_real_constraint_keeper_next_tool(
    monkeypatch,
    tmp_path,
):
    class ConstraintClient:
        async def task_start(self, **kwargs):
            return {"ok": True}

        async def observe(self, payload, session_id=""):
            return {"ok": True}

    class MemoryClient:
        async def memory_read(self, **kwargs):
            return {
                "path": "lib/ansible/executor/play_iterator.py",
                "start_line": 1,
                "end_line": 3,
                "content": "class PlayIterator:\n    pass\n",
            }

    identity = derive_formsy_identity(
        session_id="session-123",
        task_id="task-123",
        run_id="run-123",
        repo_id="ansible__ansible",
        revision="rev",
        workspace_id="workspace-123",
    )
    coordinator = ConstraintKeeperCoordinator(
        client=ConstraintClient(),
        spool_root=tmp_path,
        identity=identity,
    )
    monkeypatch.setattr(
        "plugins.context_engine.formsy.engine._get_constraint_keeper_coordinator",
        lambda: coordinator,
    )

    guidance_result = {
        "ok": True,
        "guidance_packet": {
            "mode": "degraded_recovery",
            "required_next_tool": {
                "tool": "context_read",
                "args": {"path": "lib/ansible/executor/play_iterator.py"},
                "reason": "Validate hinted target.",
            },
        },
    }
    coordinator.observe_tool_result(
        "context_search",
        {"query": "PlayIterator states"},
        guidance_result,
        session_id="session-123",
    )
    assert coordinator.pre_tool_call_block_message(
        "terminal",
        {"command": "grep -R PlayIterator lib/ansible"},
        session_id="session-123",
    ) is None
    suggested = coordinator.transform_tool_result(
        "context_search",
        {},
        "original",
        session_id="session-123",
    )
    assert suggested is not None
    assert "NEXT SUGGESTED TOOL" in suggested

    engine = FormsyContextEngine()
    engine._engine_client = MemoryClient()
    engine._config = type("Config", (), {
        "repo_id": "ansible__ansible",
        "revision": "rev",
        "query_budget": 4000,
    })()
    engine._session_id = "session-123"
    engine._retrieval_trace.seed_calls = 1

    result = engine.handle_tool_call(
        "context_read",
        {"path": "lib/ansible/executor/play_iterator.py"},
    )

    assert "ok: true" in result
    assert coordinator.pre_tool_call_block_message(
        "terminal",
        {"command": "pytest test/units/executor/test_play_iterator.py"},
        session_id="session-123",
    ) is None


def test_formsy_engine_compile_failure_returns_compact_compile_repair_guidance(
    monkeypatch,
):
    engine = FormsyContextEngine()

    class FakeClient:
        last_error = "compile timeout"

        async def compile_repo(self, **kwargs):
            return None

        async def memory_prefetch(self, **kwargs):
            return {"memory_status": "miss"}

    class FakeCoordinator:
        def __init__(self):
            self.observed = []

        def observe_tool_result(self, tool_name, args, result, *, session_id=""):
            self.observed.append((tool_name, args, json.loads(result), session_id))

    coordinator = FakeCoordinator()
    monkeypatch.setattr(
        "plugins.context_engine.formsy.engine._get_constraint_keeper_coordinator",
        lambda: coordinator,
    )

    engine._engine_client = FakeClient()
    engine._config = type("Config", (), {
        "repo_id": "ansible__ansible",
        "revision": "rev",
        "query_budget": 4000,
    })()
    engine._session_id = "session-123"

    result = json.loads(
        engine.handle_tool_call(
            "context_search",
            {
                "query": (
                    "Standardize PlayIterator state representation "
                    "ITERATING_TASKS FAILED_SETUP HostState __str__"
                ),
            },
        )
    )

    assert result["ok"] is True
    assert result["warning"] == "Formsy memory compile unavailable before context_search"
    assert result["error_code"] == "MEMORY_COMPILE_TIMEOUT"
    assert result["compile_status"] == "compile_timeout"
    assert result["compile_error"] == "compile timeout"
    assert result["compile_repair_tool"] == "formsy_compile_repo"
    assert result["tocs_identity_status"] == "missing"
    assert result["diagnostic_reason"] == (
        "missing_tocs_lookup_identity_or_compile_unavailable"
    )
    assert result["retrieval_state"] == "compile_required"
    assert result["recovery_mode"] == "compile_required"
    assert result["preferred_next_step"] == "formsy_compile_repo"
    assert result["allowed_tools"] == ["formsy_compile_repo"]
    assert result["guidance_packet"]["mode"] == "compile_required"
    assert result["guidance_packet"]["target_candidates"] == []
    assert result["guidance_packet"]["probe_budget"]["search_files"] == 0
    assert result["guidance_packet"]["probe_budget"]["read_file"] == 0
    assert result["guidance_packet"]["probe_budget"]["terminal_or_execute_code"] == 0
    assert coordinator.observed
    observed_tool, _observed_args, observed_result, observed_session_id = coordinator.observed[0]
    assert observed_tool == "context_search"
    assert observed_session_id == "session-123"
    assert observed_result["guidance_packet"]["probe_budget"]["terminal_or_execute_code"] == 0


def test_formsy_engine_compile_failure_preserves_status_error_when_compile_has_no_error():
    engine = FormsyContextEngine()

    class FakeClient:
        last_error = ""

        async def compile_status(self, **kwargs):
            self.last_error = "RuntimeAPIError: compiled repo not found"
            return None

        async def compile_repo(self, **kwargs):
            self.last_error = ""
            return None

        async def memory_prefetch(self, **kwargs):
            return {"memory_status": "miss"}

    engine._engine_client = FakeClient()
    engine._config = type("Config", (), {
        "repo_id": "ansible__ansible",
        "revision": "rev",
        "query_budget": 4000,
    })()
    engine._session_id = "session-123"

    result = json.loads(
        engine.handle_tool_call(
            "context_search",
            {"query": "parser state handling"},
        )
    )

    assert result["compile_status"] == "compile_missing_or_unavailable"
    assert result["compile_error"] == "RuntimeAPIError: compiled repo not found"


def test_formsy_engine_compile_status_in_progress_does_not_start_duplicate_compile():
    engine = FormsyContextEngine()
    calls = []

    class FakeClient:
        last_error = ""

        async def compile_status(self, **kwargs):
            calls.append(("status", kwargs))
            return {"status": "in_progress", "revision": "rev"}

        async def compile_repo(self, **kwargs):
            calls.append(("compile", kwargs))
            return {"revision": "rev"}

        async def memory_prefetch(self, **kwargs):
            return {"memory_status": "miss"}

    engine._engine_client = FakeClient()
    engine._config = type("Config", (), {
        "repo_id": "ansible__ansible",
        "revision": "rev",
        "query_budget": 4000,
    })()
    engine._session_id = "session-123"

    result = json.loads(
        engine.handle_tool_call(
            "context_search",
            {"query": "parser state handling"},
        )
    )

    assert result["error_code"] == "MEMORY_COMPILE_IN_PROGRESS"
    assert result["compile_status"] == "compile_in_progress"
    assert [name for name, _ in calls] == ["status"]


def test_formsy_engine_compile_repo_tool_repairs_memory_compile():
    engine = FormsyContextEngine()
    calls = []

    class FakeClient:
        last_error = ""

        async def compile_status(self, **kwargs):
            calls.append(("status", kwargs))
            return None

        async def compile_repo(self, **kwargs):
            calls.append(("compile", kwargs))
            return {"revision": "rev__query_bounded__abc"}

    engine._engine_client = FakeClient()
    engine._config = type("Config", (), {
        "repo_id": "ansible__ansible",
        "revision": "rev",
        "query_budget": 4000,
    })()
    engine._session_id = "session-123"

    result = json.loads(
        engine.handle_tool_call(
            "formsy_compile_repo",
            {"query": "parser state handling"},
        )
    )

    assert result["ok"] is True
    assert result["compile_status"] == "compiled"
    assert result["next_step"] == "context_search"
    assert result["revision"] == "rev__query_bounded__abc"
    assert [name for name, _ in calls] == ["status", "compile"]


def test_formsy_engine_degraded_guidance_canonicalizes_absolute_memory_hints():
    engine = FormsyContextEngine()

    packet = engine._build_degraded_guidance_packet(
        query="PlayIterator public state type",
        payload={},
        metadata={
            "grounding_phase": "seed",
            "memory_query_hints": [
                "/Users/wayneliu/dev/ansible/lib/ansible/executor/play_iterator.py",
                "lib/ansible/executor/play_iterator.py",
            ],
        },
    )

    assert packet["target_candidates"] == ["lib/ansible/executor/play_iterator.py"]
    assert packet["next_tool_directive"]["args"]["path"] == (
        "lib/ansible/executor/play_iterator.py"
    )
    assert packet["next_tool_directive"]["enforcement"] == "suggested"
    assert "required_next_tool" not in packet


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
    engine.on_user_turn(
        "<pr_description>Fix username validator regex.</pr_description>"
        "<instructions>Use context_search proactively.</instructions>"
    )

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
    assert seed_result["retrieval_decision"]["seed_required"] is False
    assert seed_result["retrieval_decision"]["seed_followed"] is True
    assert seed_result["retrieval_decision"]["first_nonseed_tool"] == "none"
    assert seed_result["retrieval_decision"]["context_read_required"] is True
    assert seed_result["retrieval_decision"]["context_read_followed"] is False
    assert seed_result["retrieval_decision"]["pending_followup_tool"] == "context_read"
    status_after_seed = engine.get_retrieval_status()
    assert status_after_seed["seed_required"] is False
    assert status_after_seed["seed_followed"] is True
    assert status_after_seed["first_nonseed_tool"] == "none"
    assert status_after_seed["context_read_required"] is True
    assert status_after_seed["context_read_followed"] is False
    assert status_after_seed["pending_followup_tool"] == "context_read"
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
    assert status_after_read["context_read_required"] is False
    assert status_after_read["context_read_followed"] is True
    assert status_after_read["pending_followup_tool"] == "grounded_search"
    assert status_after_read["grounded_search_required"] is True
    assert status_after_read["grounded_search_followed"] is False
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
    assert grounded_result["retrieval_decision"]["context_read_required"] is False
    assert grounded_result["retrieval_decision"]["context_read_followed"] is True
    assert grounded_result["retrieval_decision"]["pending_followup_tool"] == "none"
    assert grounded_result["retrieval_decision"]["grounded_search_required"] is False
    assert grounded_result["retrieval_decision"]["grounded_search_followed"] is True
    assert grounded_result["retrieval_decision"]["contradiction_found"] is False
    assert grounded_result["retrieval_decision"]["target_conflict"] is False
    assert engine.get_tool_block_message("terminal", {"command": "ls"}) is not None
    assert engine.get_tool_block_message("context_search", {"query": "more"}) is not None
    assert engine.get_tool_block_message("terminal", {"command": "grep -R UsernameValidator django"}) is not None
    assert engine.get_tool_block_message("patch", {"path": "django/contrib/auth/validators.py"}) is None
    assert engine.get_tool_block_message("search_files", {"query": "validators"}) is not None
    assert engine.get_tool_block_message("read_file", {"path": "django/contrib/auth/other.py"}) is None
    assert engine.get_tool_block_message("context_read", {"path": "django/contrib/auth/other.py"}) is None
    assert engine.get_tool_block_message("context_read", {"path": "tests/staticfiles_tests/test_storage.py"}) is None
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
    assert status["context_read_required"] is False
    assert status["context_read_followed"] is True
    assert status["pending_followup_tool"] == "none"
    assert status["grounded_search_required"] is False
    assert status["grounded_search_followed"] is True
    assert status["retry_calls"] == 0
    assert status["legacy_calls"] == 0
    assert status["accepted_targets"] == ["django/contrib/auth/validators.py"]
    assert status["blocked_tool_reason"]


def test_formsy_engine_read_file_satisfies_pending_context_read_for_same_target():
    engine = FormsyContextEngine()
    engine._retrieval_state = "inspect_candidates"
    engine._context_read_required = True
    engine._context_read_followed = False
    engine._retrieval_trace.accepted_targets = ["lib/ansible/executor/play_iterator.py"]
    engine._retrieval_trace.exploration_closed = True

    engine.observe_tool_result(
        "read_file",
        {"path": "/testbed/lib/ansible/executor/play_iterator.py"},
        json.dumps({"content": "class PlayIterator: pass"}),
    )

    status = engine.get_retrieval_status()
    assert status["context_read_required"] is False
    assert status["context_read_followed"] is True
    assert status["pending_followup_tool"] == "grounded_search"
    assert "lib/ansible/executor/play_iterator.py" in status["grounded_files"]


def test_formsy_engine_accept_done_clears_pending_retrieval_summary():
    engine = FormsyContextEngine()
    engine._retrieval_state = "inspect_candidates"
    engine._context_read_required = True
    engine._context_read_followed = False

    engine.observe_tool_result(
        "formsy_verify_completion",
        {},
        json.dumps({
            "decision": "ACCEPT_DONE",
            "completion_audit": {
                "audit_status": "verified",
                "gate_decision": "ACCEPT_DONE",
            },
        }) + "\n\n## FormSy Constraint Protocol\n- Decision: PATCH_ALLOWED_WITH_WARNINGS",
    )

    status = engine.get_retrieval_status()
    assert status["coding_status"] == "verified"
    assert status["context_read_required"] is False
    assert status["pending_followup_tool"] == "none"


def test_formsy_engine_forwards_accept_done_to_memory_provider():
    engine = FormsyContextEngine()
    observed_payloads = []

    class FakeProvider:
        def record_completion_verifier_result(self, payload):
            observed_payloads.append(payload)

    engine._memory_manager = type("Manager", (), {"providers": [FakeProvider()]})()

    engine.observe_tool_result(
        "formsy_verify_completion",
        {},
        json.dumps({
            "decision": "ACCEPT_DONE",
            "completion_audit": {
                "audit_status": "verified",
                "gate_decision": "ACCEPT_DONE",
                "memory_write_allowed": True,
                "evidence": {"latest_diff_hash": "sha256:abc"},
            },
        }) + "\n\n## FormSy Constraint Protocol\n- Decision: PATCH_ALLOWED_WITH_WARNINGS",
    )

    assert len(observed_payloads) == 1
    assert observed_payloads[0]["decision"] == "ACCEPT_DONE"
    assert observed_payloads[0]["completion_audit"]["gate_decision"] == "ACCEPT_DONE"


def test_formsy_engine_promotes_resolved_tocs_over_stale_suggested_actions():
    engine = FormsyContextEngine()

    class FakeClient:
        async def memory_search(self, **kwargs):
            return {
                "extra_context": (
                    "## FormSy Guidance\n\n"
                    "### Suggested Actions\n"
                    "- Inspect primary context file: lib/ansible/utils/display.py.\n"
                    "- Patch strategy: Inspect and fix lib/ansible/utils/display.py.\n"
                ),
                "matches": [{"path": "lib/ansible/utils/display.py"}],
                "coverage": "partial",
                "guidance": {
                    "tocs_delivery": {"resolved": True},
                    "tocs": {
                        "mode": "repair_ready_exact",
                        "must_read_files": [
                            "lib/ansible/module_utils/urls.py",
                            "lib/ansible/modules/uri.py",
                        ],
                        "candidate_tests": [
                            {
                                "path": "test/units/module_utils/urls/test_gzip.py",
                                "selector": (
                                    "test/units/module_utils/urls/test_gzip.py::"
                                    "test_Request_open_gzip"
                                ),
                            }
                        ],
                    },
                },
            }

        async def memory_read(self, **kwargs):
            return {"content": ""}

    engine._engine_client = FakeClient()
    engine._config = type("Config", (), {
        "repo_id": "ansible__ansible",
        "revision": "latest",
        "query_budget": 4000,
        "timeout_s": 120,
        "workspace_id": "ws_test",
    })()
    engine._session_id = "session-123"

    payload = json.loads(engine.handle_tool_call("context_search", {"query": "gzip response"}))

    assert payload["tocs_repair_targets"] == ["lib/ansible/module_utils/urls.py"]
    assert payload["accepted_targets"] == ["lib/ansible/module_utils/urls.py"]
    assert "### TOCS Priority" in payload["extra_context"]
    assert "Resolved TOCS supersedes stale suggested actions" in payload["extra_context"]
    assert payload["extra_context"].index("### TOCS Priority") < payload["extra_context"].index("### Suggested Actions")


def test_formsy_engine_tocs_fallback_with_poor_coverage_suggests_local_read_file():
    engine = FormsyContextEngine()

    class FakeClient:
        async def memory_search(self, **kwargs):
            return {
                "extra_context": (
                    "## FormSy Guidance\n\n"
                    "- Target conflict\n"
                    "- Resolved TOCS target is not yet backed by current context evidence; "
                    "read `lib/ansible/module_utils/urls.py` before patching.\n"
                    "- NEXT SUGGESTED TOOL: context_read path=lib/ansible/module_utils/urls.py\n"
                ),
                "matches": [],
                "coverage": "poor",
                "missing_context": ["No structured file or symbol matches were selected."],
                "guidance": {
                    "freshness": "stale",
                    "retrieval_state": "inspect_seed_result",
                    "preferred_next_step": "context_search",
                    "target_conflict": True,
                    "tocs_delivery": {"resolved": True},
                    "tocs": {
                        "lane_b_mode": "diagnostic_source_fallback",
                        "source_resolution_status": "fallback",
                        "repair_targets": [
                            "lib/ansible/module_utils/urls.py",
                            "lib/ansible/modules/uri.py",
                            "lib/ansible/modules/get_url.py",
                        ],
                        "must_read_files": [
                            {"path": "lib/ansible/module_utils/urls.py"},
                            {"path": "lib/ansible/modules/uri.py"},
                            {"path": "lib/ansible/modules/get_url.py"},
                        ],
                        "candidate_tests": [
                            {
                                "test_id": (
                                    "test/units/module_utils/urls/test_gzip.py::"
                                    "test_Request_open_gzip"
                                ),
                                "test_source_mode": "history_basename_fallback",
                            }
                        ],
                    },
                },
            }

    engine._engine_client = FakeClient()
    engine._config = type("Config", (), {
        "repo_id": "ansible__ansible",
        "revision": "latest",
        "query_budget": 4000,
        "timeout_s": 120,
        "workspace_id": "ws_test",
    })()
    engine._session_id = "session-123"

    payload = json.loads(engine.handle_tool_call("context_search", {"query": "gzip response"}))

    assert payload["accepted_targets"] == [
        "lib/ansible/module_utils/urls.py",
        "lib/ansible/modules/uri.py",
        "lib/ansible/modules/get_url.py",
    ]
    assert payload["preferred_next_step"] == "read_file"
    assert payload["next_tool_directive"]["tool"] == "read_file"
    assert payload["next_tool_directive"]["args"] == {
        "path": "lib/ansible/module_utils/urls.py"
    }
    assert "NEXT SUGGESTED TOOL: context_read" not in payload["extra_context"]
    assert "NEXT SUGGESTED TOOL: read_file path=lib/ansible/module_utils/urls.py" in payload["extra_context"]
    assert payload["retrieval_decision"]["context_read_required"] is False


def test_formsy_engine_records_ignored_seed_guidance_in_observe_only_mode():
    engine = FormsyContextEngine()
    engine._config = EngineConfig(retrieval_gate="observe_only")

    engine.on_user_turn(
        "<pr_description>Standardize PlayIterator state representation.</pr_description>"
        "<instructions>Use context_search proactively.</instructions>"
    )

    before = engine.get_retrieval_status()
    assert before["seed_required"] is True
    assert before["seed_followed"] is False
    assert before["first_nonseed_tool"] == "none"

    assert engine.get_tool_block_message(
        "read_file",
        {"path": "lib/ansible/executor/play_iterator.py"},
    ) is None

    after = engine.get_retrieval_status()
    assert after["seed_required"] is True
    assert after["seed_followed"] is False
    assert after["first_nonseed_tool"] == "read_file"


def test_formsy_engine_context_read_falls_back_to_search_snippet():
    engine = FormsyContextEngine()
    read_calls = []

    class FakeClient:
        async def memory_search(self, **kwargs):
            return {
                "extra_context": (
                    "### django/contrib/auth/validators.py:1-25 (Promoted from direct query match.)\n"
                    "```python\n"
                    "class ASCIIUsernameValidator:\n"
                    "    regex = r'^[\\w.@+-]+$'\n"
                    "```\n"
                ),
                "matches": [{"path": "django/contrib/auth/validators.py"}],
                "coverage": "partial",
            }

        async def memory_read(self, **kwargs):
            read_calls.append(kwargs)
            return None

    engine._engine_client = FakeClient()
    engine._config = type("Config", (), {
        "repo_id": "django__django-11099",
        "revision": "latest",
        "query_budget": 4000,
    })()
    engine._session_id = "session-123"

    seed_result = json.loads(
        engine.handle_tool_call(
            "context_search",
            {
                "query": "username validator regex",
                "metadata": {
                    "retrieval_mode": "symbolic",
                    "grounding_phase": "seed",
                    "response_format": "bundle",
                },
            },
        )
    )
    assert seed_result["preferred_next_step"] == "context_read"

    read_result = engine.handle_tool_call(
        "context_read",
        {
            "path": "django/contrib/auth/validators.py",
            "start_line": 1,
            "end_line": 30,
        },
    )

    assert read_calls
    assert "ok: true" in read_result
    assert "class ASCIIUsernameValidator" in read_result
    assert "lines: 1-25" in read_result


def test_formsy_engine_allows_memory_backed_actions_before_new_retrieval():
    engine = FormsyContextEngine()

    class FakeProvider:
        def get_context_hints(self):
            return {"memory_status": "hit", "memory_artifact_ids": ["ctx-prior"]}

    engine._memory_manager = type("Manager", (), {"providers": [FakeProvider()]})()

    assert engine.get_tool_block_message(
        "terminal",
        {"command": "echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT && cat patch.txt"},
    ) is None
    assert engine.get_tool_block_message(
        "patch",
        {
            "path": "django/contrib/auth/validators.py",
            "old_string": "old",
            "new_string": "new",
        },
    ) is None
    assert engine.get_tool_block_message("terminal", {"command": "grep -R UsernameValidator django"}) is not None


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


def test_formsy_engine_extracts_must_edit_from_context_bundle_primary_files():
    bundle = {
        "primary_files": [
            {
                "path": "django/contrib/postgres/validators.py",
                "priority": "likely_edit",
            },
            {
                "path": "django/contrib/auth/validators.py",
                "priority": "must_edit",
            },
        ],
    }

    assert FormsyContextEngine._extract_bundle_must_edit(bundle) == [
        "django/contrib/auth/validators.py"
    ]


def test_formsy_engine_allows_patch_after_reading_bundle_must_edit_target():
    engine = FormsyContextEngine()

    class FakeClient:
        async def memory_search(self, **kwargs):
            return {
                "extra_context": "Seed validator notes",
                "matches": [
                    {"path": "django/contrib/auth/validators.py"},
                    {"path": "tests/auth_tests/test_validators.py"},
                    {"path": "django/contrib/auth/models.py"},
                ],
                "coverage": "partial",
                "bundle": {
                    "primary_files": [
                        {
                            "path": "django/contrib/auth/validators.py",
                            "priority": "must_edit",
                        }
                    ],
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
    assert seed_result["retrieval_decision"]["preferred_edit_targets"] == [
        "django/contrib/auth/validators.py"
    ]

    engine.handle_tool_call(
        "context_read",
        {
            "path": "django/contrib/auth/validators.py",
            "repo_id": "django__django-14053",
            "start_line": 1,
            "end_line": 25,
        },
    )

    assert engine.get_tool_block_message(
        "patch",
        {"path": "django/contrib/auth/validators.py"},
    ) is None
    status = engine.get_retrieval_status()
    assert status["retrieval_state"] == "grounded"
    assert status["accepted_targets"] == ["django/contrib/auth/validators.py"]
    assert status["last_gate_failure"] == {}
    assert engine.get_tool_block_message(
        "patch",
        {"path": "django/contrib/auth/models.py"},
    ) is not None


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


def test_formsy_engine_does_not_lock_non_patchable_seed_primary_target():
    engine = FormsyContextEngine()

    class FakeClient:
        async def memory_search(self, **kwargs):
            if kwargs["metadata"].get("grounding_phase") == "grounded":
                return {
                    "extra_context": "Grounded iptables notes",
                    "symbolic_prompt": (
                        "Formal Semantics:\niptables chain management should update iptables.py\n"
                        "Constraints:\npreserve existing module argument behavior\n"
                        "Retrieval Strategy:\ninspect iptables module\n"
                        "Retrieved Facts:\niptables.py contains chain handling"
                    ),
                    "matches": [{"path": "lib/ansible/modules/iptables.py"}],
                    "coverage": "good",
                    "retrieval_state": "grounded",
                    "preferred_next_step": "edit",
                    "accepted_targets": ["lib/ansible/modules/iptables.py"],
                    "exploration_closed": True,
                    "guidance": {
                        "can_patch_now": True,
                        "accepted_edit_targets": ["lib/ansible/modules/iptables.py"],
                        "primary_edit_target": "lib/ansible/modules/iptables.py",
                    },
                    "bundle": {
                        "primary_files": [
                            {"path": "lib/ansible/modules/iptables.py", "priority": "must_edit"}
                        ],
                        "must_edit": ["lib/ansible/modules/iptables.py"],
                    },
                }
            return {
                "extra_context": "Weak seed notes",
                "symbolic_prompt": (
                    "Formal Semantics:\nseed result is not patch-ready\n"
                    "Retrieval Strategy:\nretry with grounded evidence"
                ),
                "matches": [{"path": "lib/ansible/utils/hashing.py"}],
                "coverage": "partial",
                "retrieval_state": "retry_symbolic_search",
                "preferred_next_step": "context_search",
                "guidance": {
                    "can_patch_now": False,
                    "accepted_edit_targets": [],
                    "primary_edit_target": "lib/ansible/utils/hashing.py",
                },
                "bundle": {
                    "primary_files": [
                        {"path": "lib/ansible/utils/hashing.py", "priority": "must_edit"}
                    ],
                    "must_edit": ["lib/ansible/utils/hashing.py"],
                },
            }

    engine._engine_client = FakeClient()
    engine._config = type("Config", (), {
        "repo_id": "ansible__ansible",
        "revision": "latest",
        "query_budget": 4000,
    })()
    engine._session_id = "session-123"

    seed_result = json.loads(
        engine.handle_tool_call(
            "context_search",
            {
                "query": "iptables chain management check mode creation deletion append remove ansible module",
                "repo_id": "ansible__ansible",
                "metadata": {
                    "retrieval_mode": "symbolic",
                    "grounding_phase": "seed",
                    "response_format": "bundle",
                },
            },
        )
    )

    assert seed_result["accepted_targets"] == []
    assert seed_result["exploration_closed"] is False

    grounded_result = json.loads(
        engine.handle_tool_call(
            "context_search",
            {
                "query": "confirm iptables chain management edit target",
                "repo_id": "ansible__ansible",
                "metadata": {
                    "retrieval_mode": "symbolic",
                    "grounding_phase": "grounded",
                    "response_format": "bundle",
                    "grounded_files": ["lib/ansible/modules/iptables.py"],
                },
            },
        )
    )

    assert grounded_result["retrieval_state"] == "grounded"
    assert grounded_result["preferred_next_step"] == "edit"
    assert grounded_result["accepted_targets"] == ["lib/ansible/modules/iptables.py"]
    assert grounded_result["exploration_closed"] is True
    assert grounded_result["retrieval_decision"]["accepted_targets"] == [
        "lib/ansible/modules/iptables.py"
    ]
    assert engine.get_retrieval_status()["accepted_targets"] == [
        "lib/ansible/modules/iptables.py"
    ]


def test_formsy_engine_clears_stale_locked_target_when_grounded_candidate_conflicts():
    engine = FormsyContextEngine()
    engine._sync_trace_state(state="grounded")
    engine._set_accepted_targets(["lib/ansible/utils/hashing.py"])
    engine._grounded_files = ["lib/ansible/utils/hashing.py"]
    engine._context_read_followed = True

    class FakeClient:
        async def memory_search(self, **kwargs):
            return {
                "extra_context": "Grounded candidate points at the iptables module.",
                "symbolic_prompt": (
                    "Formal Semantics:\niptables chain management should update iptables.py\n"
                    "Constraints:\ndo not invent unrelated helper APIs\n"
                    "Retrieval Strategy:\nread the direct module candidate before patching\n"
                    "Retrieved Facts:\niptables.py contains the module argument spec"
                ),
                "matches": [{"path": "lib/ansible/modules/iptables.py"}],
                "coverage": "partial",
                "retrieval_state": "inspect_candidates",
                "preferred_next_step": "context_read",
                "accepted_targets": [],
                "exploration_closed": False,
                "guidance": {
                    "target_conflict": True,
                    "stale_bundle_targets": ["lib/ansible/utils/hashing.py"],
                    "candidate_targets": ["lib/ansible/modules/iptables.py"],
                    "accepted_edit_targets": [],
                },
            }

    engine._engine_client = FakeClient()
    engine._config = type("Config", (), {
        "repo_id": "ansible__ansible",
        "revision": "latest",
        "query_budget": 4000,
    })()
    engine._session_id = "session-123"

    result = json.loads(
        engine.handle_tool_call(
            "context_search",
            {
                "query": "confirm iptables chain management edit target",
                "repo_id": "ansible__ansible",
                "metadata": {
                    "retrieval_mode": "symbolic",
                    "grounding_phase": "grounded",
                    "response_format": "bundle",
                },
            },
        )
    )

    assert result["retrieval_state"] == "inspect_candidates"
    assert result["preferred_next_step"] == "context_read"
    assert result["accepted_targets"] == []
    assert result["exploration_closed"] is False
    assert result["stale_accepted_targets"] == ["lib/ansible/utils/hashing.py"]
    assert result["candidate_targets"] == ["lib/ansible/modules/iptables.py"]
    assert result["retrieval_decision"]["accepted_targets"] == []
    assert result["retrieval_decision"]["context_read_required"] is True
    assert result["retrieval_decision"]["context_read_followed"] is False
    assert result["retrieval_decision"]["direct_match_files"] == [
        "lib/ansible/modules/iptables.py"
    ]

    status = engine.get_retrieval_status()
    assert status["accepted_targets"] == []
    assert status["retrieval_state"] == "inspect_candidates"
    assert engine.get_tool_block_message(
        "context_read",
        {"path": "lib/ansible/modules/iptables.py"},
    ) is None
    blocked = engine.get_tool_block_message(
        "patch",
        {"path": "lib/ansible/utils/hashing.py"},
    )
    assert blocked is not None
    assert "use context_read on a candidate" in blocked


def test_formsy_engine_does_not_lock_weak_seed_bundle_must_edit_as_accepted_target():
    engine = FormsyContextEngine()

    class FakeClient:
        async def memory_search(self, **kwargs):
            if kwargs["metadata"].get("grounding_phase") == "grounded":
                return {
                    "extra_context": "Grounded iptables notes",
                    "symbolic_prompt": (
                        "Formal Semantics:\niptables chain management should update iptables.py\n"
                        "Constraints:\npreserve existing module argument behavior\n"
                        "Retrieval Strategy:\ninspect iptables module\n"
                        "Retrieved Facts:\niptables.py contains chain handling"
                    ),
                    "matches": [{"path": "lib/ansible/modules/iptables.py"}],
                    "coverage": "good",
                    "retrieval_state": "grounded",
                    "preferred_next_step": "edit",
                    "accepted_targets": ["lib/ansible/modules/iptables.py"],
                    "exploration_closed": True,
                    "guidance": {
                        "can_patch_now": True,
                        "accepted_edit_targets": ["lib/ansible/modules/iptables.py"],
                    },
                    "bundle": {
                        "primary_files": [
                            {"path": "lib/ansible/modules/iptables.py", "priority": "must_edit"}
                        ],
                        "must_edit": ["lib/ansible/modules/iptables.py"],
                    },
                }
            return {
                "extra_context": "Weak seed notes",
                "symbolic_prompt": (
                    "Formal Semantics:\nseed result is not patch-ready\n"
                    "Retrieval Strategy:\nread candidates before patching"
                ),
                "matches": [{"path": "lib/ansible/utils/hashing.py"}],
                "coverage": "partial",
                "retrieval_state": "inspect_candidates",
                "preferred_next_step": "context_read",
                "accepted_targets": ["lib/ansible/utils/hashing.py"],
                "exploration_closed": True,
                "guidance": {
                    "can_patch_now": False,
                    "accepted_edit_targets": [],
                },
                "bundle": {
                    "primary_files": [
                        {"path": "lib/ansible/utils/hashing.py", "priority": "must_edit"}
                    ],
                    "must_edit": ["lib/ansible/utils/hashing.py"],
                },
            }

    engine._engine_client = FakeClient()
    engine._config = type("Config", (), {
        "repo_id": "ansible__ansible",
        "revision": "latest",
        "query_budget": 4000,
    })()
    engine._session_id = "session-123"

    seed_result = json.loads(
        engine.handle_tool_call(
            "context_search",
            {
                "query": "iptables chain management check mode creation deletion append remove ansible module",
                "repo_id": "ansible__ansible",
                "metadata": {
                    "retrieval_mode": "symbolic",
                    "grounding_phase": "seed",
                    "response_format": "bundle",
                },
            },
        )
    )

    assert seed_result["accepted_targets"] == []
    assert seed_result["exploration_closed"] is False
    assert seed_result["retrieval_decision"]["bundle_must_edit"] == [
        "lib/ansible/utils/hashing.py"
    ]
    assert engine.get_tool_block_message(
        "patch",
        {"path": "lib/ansible/utils/hashing.py"},
    ) is not None

    grounded_result = json.loads(
        engine.handle_tool_call(
            "context_search",
            {
                "query": "confirm iptables chain management edit target",
                "repo_id": "ansible__ansible",
                "metadata": {
                    "retrieval_mode": "symbolic",
                    "grounding_phase": "grounded",
                    "response_format": "bundle",
                    "grounded_files": ["lib/ansible/modules/iptables.py"],
                },
            },
        )
    )

    assert grounded_result["retrieval_state"] == "grounded"
    assert grounded_result["preferred_next_step"] == "edit"
    assert grounded_result["accepted_targets"] == ["lib/ansible/modules/iptables.py"]
    assert grounded_result["exploration_closed"] is True
    assert engine.get_tool_block_message(
        "patch",
        {"path": "lib/ansible/modules/iptables.py"},
    ) is None


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
    assert engine.get_tool_block_message("patch", {"path": "django/contrib/auth/validators.py"}) is None


def test_formsy_engine_keeps_degraded_recovery_exploration_open_after_target_read():
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
    assert status["retrieval_state"] == "degraded_recovery"
    assert status["grounded_files"] == ["django/contrib/auth/validators.py"]
    assert status["accepted_targets"] == []
    assert status["exploration_closed"] is False
    assert engine.get_tool_block_message(
        "read_file",
        {"path": "django/contrib/auth/forms.py"},
    ) is None
    assert engine.get_tool_block_message(
        "terminal",
        {"command": "grep -rn UsernameValidator django/contrib/auth"},
    ) is None
    assert engine.get_tool_block_message("patch", {"path": "django/contrib/auth/validators.py"}) is None
    assert engine.get_tool_block_message("patch", {"path": "django/contrib/auth/forms.py"}) is not None


def test_formsy_engine_degraded_recovery_read_file_does_not_close_exploration():
    engine = FormsyContextEngine()
    engine._sync_trace_state(state="degraded_recovery")

    engine.observe_tool_result(
        "read_file",
        {"path": "lib/ansible/executor/play_iterator.py"},
        json.dumps({"content": "class HostState: ..."}),
    )

    status = engine.get_retrieval_status()
    assert status["retrieval_state"] == "degraded_recovery"
    assert status["grounded_files"] == ["lib/ansible/executor/play_iterator.py"]
    assert status["accepted_targets"] == []
    assert status["exploration_closed"] is False
    assert engine.get_tool_block_message(
        "read_file",
        {"path": "lib/ansible/plugins/strategy/linear.py"},
    ) is None


def test_formsy_engine_degraded_recovery_enforces_probe_budget_and_patch_threshold():
    engine = FormsyContextEngine()
    engine._sync_trace_state(state="degraded_recovery")
    engine._degraded_guidance_packet = {
        "mode": "degraded_recovery",
        "target_candidates": ["lib/ansible/executor/play_iterator.py"],
        "likely_edit_files": ["lib/ansible/executor/play_iterator.py"],
        "recommended_first_reads": ["lib/ansible/executor/play_iterator.py"],
        "probe_budget": {
            "search_files": 1,
            "read_file": 2,
            "terminal_or_execute_code": 2,
        },
        "patch_now_threshold": {"grounded_source_reads": 1},
    }

    assert engine.get_tool_block_message(
        "search_files",
        {"pattern": "PlayIterator", "path": "lib/ansible"},
    ) is None
    engine.observe_tool_result(
        "search_files",
        {"pattern": "PlayIterator", "path": "lib/ansible"},
        json.dumps({"total_count": 1}),
    )
    blocked_search = engine.get_tool_block_message(
        "search_files",
        {"pattern": "FAILED_", "path": "lib/ansible"},
    )

    assert engine.get_tool_block_message(
        "execute_code",
        {"code": "print('probe 1')"},
    ) is None
    engine.observe_tool_result(
        "execute_code",
        {"code": "print('probe 1')"},
        json.dumps({"status": "success"}),
    )
    assert engine.get_tool_block_message(
        "terminal",
        {"command": "python3 - <<'PY'\nprint('probe 2')\nPY"},
    ) is None
    engine.observe_tool_result(
        "terminal",
        {"command": "python3 - <<'PY'\nprint('probe 2')\nPY"},
        json.dumps({"output": "ok", "exit_code": 0}),
    )
    blocked_probe = engine.get_tool_block_message(
        "execute_code",
        {"code": "print('probe 3')"},
    )

    assert engine.get_tool_block_message(
        "read_file",
        {"path": "lib/ansible/executor/play_iterator.py"},
    ) is None
    engine.observe_tool_result(
        "read_file",
        {"path": "lib/ansible/executor/play_iterator.py"},
        json.dumps({"content": "class PlayIterator: ..."}),
    )
    edit_target = engine.get_tool_block_message(
        "patch",
        {"path": "lib/ansible/executor/play_iterator.py"},
    )
    edit_other = engine.get_tool_block_message(
        "patch",
        {"path": "lib/ansible/plugins/strategy/linear.py"},
    )

    assert blocked_search is not None
    assert "degraded recovery search_files budget is exhausted" in blocked_search
    assert blocked_probe is not None
    assert "degraded recovery probe budget is exhausted" in blocked_probe
    assert edit_target is None
    assert edit_other is not None
    assert "patch the grounded target" in edit_other
    assert engine.get_tool_block_message(
        "terminal",
        {"command": "grep -rn \"PlayIterator\\.\" lib/ansible/executor/ lib/ansible/plugins/strategy/"},
    ) is not None


def test_formsy_engine_degraded_patch_allowed_prefers_accepted_edit_targets():
    engine = FormsyContextEngine()
    engine._sync_trace_state(state="degraded_recovery")
    engine._degraded_guidance_packet = {
        "mode": "degraded_recovery",
        "target_candidates": [
            "lib/ansible/executor/play_iterator.py",
            "lib/ansible/plugins/strategy/linear.py",
        ],
        "accepted_edit_targets": ["lib/ansible/executor/play_iterator.py"],
        "likely_edit_files": ["lib/ansible/executor/play_iterator.py"],
        "probe_budget": {
            "search_files": 1,
            "read_file": 2,
            "terminal_or_execute_code": 2,
        },
        "patch_now_threshold": {"grounded_source_reads": 1},
    }
    engine.observe_tool_result(
        "read_file",
        {"path": "lib/ansible/executor/play_iterator.py"},
        json.dumps({"content": "class PlayIterator: ..."}),
    )
    engine.observe_tool_result(
        "read_file",
        {"path": "lib/ansible/plugins/strategy/linear.py"},
        json.dumps({"content": "class StrategyModule: ..."}),
    )

    edit_primary = engine.get_tool_block_message(
        "patch",
        {"path": "lib/ansible/executor/play_iterator.py"},
    )
    edit_context_file = engine.get_tool_block_message(
        "patch",
        {"path": "lib/ansible/plugins/strategy/linear.py"},
    )

    assert edit_primary is None
    assert edit_context_file is not None
    assert "unrelated editing remains blocked" in edit_context_file


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
    engine._retrieval_trace.seed_calls = 1

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


def test_formsy_engine_context_read_reuses_resolved_query_bounded_compile_revision():
    engine = FormsyContextEngine()
    calls = []

    class FakeClient:
        async def compile_status(self, **kwargs):
            return None

        async def compile_repo(self, **kwargs):
            return {
                "repo_id": kwargs["repo_id"],
                "revision": "abc123__query_bounded__searchsig",
                "parsed_file_count": len(kwargs["files"]),
            }

        async def memory_search(self, **kwargs):
            calls.append(("search", kwargs))
            return {
                "extra_context": "## FormSy Guidance\n- ONLY EDIT: parser.py",
                "matches": [{"path": "parser.py", "score": 1.0}],
                "coverage": "partial",
                "retrieval_state": "inspect_candidates",
                "preferred_next_step": "context_read",
                "accepted_targets": ["parser.py"],
                "exploration_closed": True,
            }

        async def memory_read(self, **kwargs):
            calls.append(("read", kwargs))
            return {
                "path": "parser.py",
                "start_line": 1,
                "end_line": 1,
                "content": "def parse(): pass",
            }

    engine._engine_client = FakeClient()
    engine._config = type("Config", (), {
        "repo_id": "django__django",
        "revision": "latest",
        "query_budget": 4000,
        "timeout_s": 120,
    })()
    engine._session_id = "session-123"

    search_payload = json.loads(
        engine.handle_tool_call("context_search", {"query": "parser state"})
    )
    assert search_payload["preferred_next_step"] == "context_read"

    read_result = engine.handle_tool_call("context_read", {"path": "parser.py"})

    assert "ok: true" in read_result
    read_call = [kwargs for name, kwargs in calls if name == "read"][0]
    assert read_call["revision"] == "abc123__query_bounded__searchsig"


def test_formsy_engine_sends_confirmed_source_reads_after_context_read():
    engine = FormsyContextEngine()
    calls = []

    class FakeClient:
        async def compile_status(self, **kwargs):
            return {"parsed_file_count": 1000, "metadata": {}}

        async def memory_search(self, **kwargs):
            calls.append(("search", kwargs))
            return {
                "coverage": "partial",
                "matches": [{"path": "lib/ansible/modules/iptables.py"}],
                "accepted_targets": ["lib/ansible/modules/iptables.py"],
                "exploration_closed": True,
                "retrieval_state": "inspect_candidates",
                "preferred_next_step": "context_read",
            }

        async def memory_read(self, **kwargs):
            calls.append(("read", kwargs))
            return {
                "path": "lib/ansible/modules/iptables.py",
                "start_line": 1,
                "end_line": 20,
                "content": "DOCUMENTATION = '---'\nargument_spec = dict(state=dict(type='str'))",
            }

    engine._engine_client = FakeClient()
    engine._config = type("Config", (), {
        "repo_id": "ansible__ansible",
        "revision": "latest",
        "query_budget": 4000,
        "timeout_s": 120,
    })()
    engine._session_id = "session-123"

    engine.handle_tool_call(
        "context_search",
        {
            "query": "iptables chain management",
            "metadata": {
                "retrieval_mode": "symbolic",
                "grounding_phase": "seed",
            },
        },
    )
    engine.handle_tool_call(
        "context_read",
        {"path": "lib/ansible/modules/iptables.py"},
    )
    engine.handle_tool_call(
        "context_search",
        {
            "query": "confirm iptables interface shape",
            "metadata": {
                "retrieval_mode": "symbolic",
                "grounding_phase": "grounded",
                "grounded_files": ["lib/ansible/modules/iptables.py"],
            },
        },
    )

    second_search = [kwargs for name, kwargs in calls if name == "search"][1]
    confirmed_reads = second_search["metadata"]["confirmed_source_reads"]
    assert confirmed_reads == [
        {
            "path": "lib/ansible/modules/iptables.py",
            "start_line": 1,
            "end_line": 20,
            "content": "DOCUMENTATION = '---'\nargument_spec = dict(state=dict(type='str'))",
            "source": "context_read",
        }
    ]


def test_formsy_engine_memory_read_renders_context_meta_source_header():
    engine = FormsyContextEngine()

    class FakeClient:
        async def memory_read(self, **kwargs):
            return {
                "path": "parser.py",
                "start_line": 10,
                "end_line": 12,
                "total_lines": 30,
                "content": "def parse():\n    return state",
                "context_meta": {
                    "source": "compiled_repo",
                    "source_freshness": "compiled",
                    "working_tree_alignment": "unknown",
                    "read_key": "parser.py:10-12",
                },
            }

    engine._engine_client = FakeClient()
    engine._config = type("Config", (), {
        "repo_id": "django__django-14053",
        "revision": "latest",
        "query_budget": 4000,
    })()
    engine._session_id = "session-123"
    engine._retrieval_trace.seed_calls = 1

    result = engine.handle_tool_call(
        "context_read",
        {
            "path": "parser.py",
            "repo_id": "django__django-14053",
            "start_line": 10,
            "end_line": 12,
        },
    )

    assert result.startswith("## FormSy Context Source")
    assert "source: compiled_repo" in result
    assert "source_freshness: compiled" in result
    assert "working_tree_alignment: unknown" in result
    assert "read_key: parser.py:10-12" in result
    assert "verify current workspace before patching" in result
    assert "ok: true" in result


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


def test_formsy_config_loads_tocs_lookup_identity_from_env(tmp_path, monkeypatch):
    lookup_identity = {
        "tocs_case_id": "ansible_gzip_response_decompress",
        "tocs_run_profile": "tocs-p0-local",
        "repo_id": "ansible__ansible",
        "base_revision": "abc123",
    }
    monkeypatch.setenv("FORMSY_TOCS_LOOKUP_IDENTITY", json.dumps(lookup_identity))

    config = EngineConfigManager(tmp_path).load_config({"formsy": {}})

    assert config.tocs_lookup_identity == lookup_identity


def test_formsy_engine_allows_explicit_terminal_supplemental_read_after_grounding():
    engine = FormsyContextEngine()
    engine._sync_trace_state(state="grounded")
    engine._set_accepted_targets(
        ["django/contrib/staticfiles/storage.py"],
        test_plan_files=["tests/staticfiles_tests/test_storage.py"],
    )

    assert engine.get_tool_block_message(
        "terminal",
        {"command": "cat django/contrib/staticfiles/storage.py"},
    ) is None
    assert engine.get_tool_block_message(
        "terminal",
        {"command": "sed -n '1,120p' tests/staticfiles_tests/test_storage.py"},
    ) is None
    assert engine.get_tool_block_message(
        "terminal",
        {"command": "cat patch.txt"},
    ) is None

    supplemental_path = "django/contrib/staticfiles/management/commands/collectstatic.py"
    allowed = engine.get_tool_block_message(
        "terminal",
        {"command": f"cat {supplemental_path}"},
    )

    assert allowed is None
    assert supplemental_path in engine._retrieval_trace.supplemental_read_files


def test_formsy_engine_blocks_terminal_write_bypass_after_grounding():
    engine = FormsyContextEngine()
    engine._sync_trace_state(state="grounded")
    engine._set_accepted_targets(
        ["django/contrib/staticfiles/storage.py"],
        test_plan_files=["tests/staticfiles_tests/test_storage.py"],
    )

    blocked = engine.get_tool_block_message(
        "terminal",
        {"command": "cat > reproduce.py <<'PY'\nprint('repro')\nPY"},
    )

    assert blocked is not None
    assert "terminal writes are blocked" in blocked


def test_formsy_engine_allows_safe_patch_artifact_git_diff_after_grounding_without_unsafe_redirect():
    engine = FormsyContextEngine()
    engine._sync_trace_state(state="grounded")
    engine._set_accepted_targets(["lib/ansible/executor/play_iterator.py"])
    accepted_patch = (
        "diff --git a/lib/ansible/executor/play_iterator.py b/lib/ansible/executor/play_iterator.py\n"
        "--- a/lib/ansible/executor/play_iterator.py\n"
        "+++ b/lib/ansible/executor/play_iterator.py\n"
        "@@ -1 +1 @@\n"
        "-old\n"
        "+new\n"
    )
    unrelated_patch = (
        "diff --git a/lib/ansible/plugins/strategy/free.py b/lib/ansible/plugins/strategy/free.py\n"
        "--- a/lib/ansible/plugins/strategy/free.py\n"
        "+++ b/lib/ansible/plugins/strategy/free.py\n"
        "@@ -1 +1 @@\n"
        "-old\n"
        "+new\n"
    )
    patch_artifact_command = (
        "cd /Users/wayneliu/dev/ansible && "
        "git diff -- lib/ansible/executor/play_iterator.py > patch.txt && cat patch.txt"
    )

    allowed = engine.get_tool_block_message(
        "terminal",
        {
            "command": (
                "cd /Users/wayneliu/dev/ansible && "
                "git diff -- lib/ansible/executor/play_iterator.py"
            )
        },
    )
    patch_artifact = engine.get_tool_block_message(
        "terminal",
        {"command": patch_artifact_command},
    )
    tee_artifact = engine.get_tool_block_message(
        "terminal",
        {
            "command": (
                "cd /Users/wayneliu/dev/ansible && "
                "git diff -- lib/ansible/executor/play_iterator.py | tee patch.txt"
            )
        },
    )
    unsafe_redirect = engine.get_tool_block_message(
        "terminal",
        {
            "command": (
                "cd /Users/wayneliu/dev/ansible && "
                "git diff -- lib/ansible/executor/play_iterator.py > "
                "lib/ansible/executor/play_iterator.py"
            )
        },
    )
    unsafe_pathspec = engine.get_tool_block_message(
        "terminal",
        {
            "command": (
                "cd /Users/wayneliu/dev/ansible && "
                "git diff -- lib/ansible/plugins/strategy/free.py > patch.txt"
            )
        },
    )
    write_patch_artifact = engine.get_tool_block_message(
        "write_file",
        {"path": "patch.txt", "content": accepted_patch},
    )
    unsafe_write_patch_artifact = engine.get_tool_block_message(
        "write_file",
        {"path": "patch.txt", "content": unrelated_patch},
    )
    arbitrary_patch_txt = engine.get_tool_block_message(
        "write_file",
        {"path": "patch.txt", "content": "not a diff"},
    )

    assert allowed is None
    assert patch_artifact is None
    assert tee_artifact is None
    assert write_patch_artifact is None
    assert unsafe_redirect is not None
    assert "terminal writes are blocked" in unsafe_redirect
    assert unsafe_pathspec is not None
    assert "terminal writes are blocked" in unsafe_pathspec
    assert unsafe_write_patch_artifact is not None
    assert "editing is limited to accepted targets" in unsafe_write_patch_artifact
    assert arbitrary_patch_txt is not None
    assert "editing is limited to accepted targets" in arbitrary_patch_txt

    for _ in range(2):
        engine.observe_tool_result(
            "terminal",
            {"command": patch_artifact_command},
            json.dumps({"output": "diff", "exit_code": 0, "error": None}),
        )
    assert engine.get_tool_block_message(
        "terminal",
        {"command": patch_artifact_command},
    ) is None


def test_formsy_engine_write_scope_policy_allows_delivery_artifact_locations():
    engine = FormsyContextEngine()
    engine._sync_trace_state(state="grounded")
    engine._set_accepted_targets(["lib/ansible/executor/play_iterator.py"])
    accepted_patch = (
        "diff --git a/lib/ansible/executor/play_iterator.py b/lib/ansible/executor/play_iterator.py\n"
        "--- a/lib/ansible/executor/play_iterator.py\n"
        "+++ b/lib/ansible/executor/play_iterator.py\n"
        "@@ -1 +1 @@\n"
        "-old\n"
        "+new\n"
    )
    unrelated_patch = (
        "diff --git a/lib/ansible/plugins/strategy/free.py b/lib/ansible/plugins/strategy/free.py\n"
        "--- a/lib/ansible/plugins/strategy/free.py\n"
        "+++ b/lib/ansible/plugins/strategy/free.py\n"
        "@@ -1 +1 @@\n"
        "-old\n"
        "+new\n"
    )

    write_artifact = engine.get_tool_block_message(
        "write_file",
        {
            "path": ".formsy/artifacts/task-1/submission.patch",
            "content": accepted_patch,
        },
    )
    terminal_artifact = engine.get_tool_block_message(
        "terminal",
        {
            "command": (
                "git diff -- lib/ansible/executor/play_iterator.py "
                "> .formsy/artifacts/task-1/submission.patch"
            )
        },
    )
    unsafe_content = engine.get_tool_block_message(
        "write_file",
        {
            "path": ".formsy/artifacts/task-1/submission.patch",
            "content": unrelated_patch,
        },
    )

    assert write_artifact is None
    assert terminal_artifact is None
    assert unsafe_content is not None
    assert "editing is limited to accepted targets" in unsafe_content


def test_formsy_engine_write_scope_policy_allows_scratch_writes_only_in_formsy_tmp():
    engine = FormsyContextEngine()
    engine._sync_trace_state(state="grounded")
    engine._set_accepted_targets(["lib/ansible/executor/play_iterator.py"])

    scratch_write = engine.get_tool_block_message(
        "write_file",
        {"path": ".formsy/tmp/task-1/repro.py", "content": "print('repro')\n"},
    )
    root_write = engine.get_tool_block_message(
        "write_file",
        {"path": "repro.py", "content": "print('repro')\n"},
    )
    escaping_scratch_write = engine.get_tool_block_message(
        "write_file",
        {"path": ".formsy/tmp/task-1/../../repro.py", "content": "print('repro')\n"},
    )

    assert scratch_write is None
    assert root_write is not None
    assert "editing is limited to accepted targets" in root_write
    assert escaping_scratch_write is not None
    assert "editing is limited to accepted targets" in escaping_scratch_write


def test_formsy_engine_treats_testbed_absolute_paths_as_workspace_relative():
    engine = FormsyContextEngine()
    engine._config = EngineConfig(retrieval_gate="observe_only")

    scratch_script = engine.get_tool_block_message(
        "write_file",
        {"path": "/testbed/test_patch.py", "content": "print('ok')\n"},
    )

    assert scratch_script is None
    assert WriteScopePolicy.normalize_repo_path("/testbed/lib/ansible/executor/play_iterator.py") == (
        "lib/ansible/executor/play_iterator.py"
    )


def test_formsy_engine_treats_absolute_paths_under_cwd_as_workspace_relative(tmp_path, monkeypatch):
    workspace = tmp_path / "repo"
    target = workspace / "lib/ansible/executor/play_iterator.py"
    target.parent.mkdir(parents=True)
    target.write_text("# source\n")
    monkeypatch.chdir(workspace)

    assert WriteScopePolicy.normalize_repo_path(str(target)) == (
        "lib/ansible/executor/play_iterator.py"
    )

    engine = FormsyContextEngine()
    engine._config = EngineConfig(retrieval_gate="observe_only")

    allowed = engine.get_tool_block_message(
        "patch",
        {"path": str(target), "old_string": "# source\n", "new_string": "# changed\n"},
    )

    assert allowed is None


def test_formsy_engine_allows_cd_prefixed_python3_pytest_after_grounding():
    engine = FormsyContextEngine()
    engine._sync_trace_state(state="grounded")
    engine._set_accepted_targets(["lib/ansible/executor/play_iterator.py"])

    allowed = engine.get_tool_block_message(
        "terminal",
        {
            "command": (
                "cd /testbed && python3 -m pytest "
                "test/units/executor/test_play_iterator.py -x -q --tb=short"
            )
        },
    )
    blocked = engine.get_tool_block_message(
        "terminal",
        {"command": "cd /testbed && python3 -c \"print('probe')\""},
    )

    assert allowed is None
    assert blocked is not None
    assert "runtime probes are limited to accepted target modules" in blocked


def test_formsy_engine_allows_bounded_runtime_probe_for_accepted_target_module_after_grounding():
    engine = FormsyContextEngine()
    engine._sync_trace_state(state="grounded")
    engine._set_accepted_targets(["lib/ansible/executor/play_iterator.py"])

    command = (
        "python3 -c \"import os; os.chdir('/testbed'); "
        "from ansible.executor.play_iterator import PlayIterator; "
        "print(PlayIterator.ITERATING_SETUP); print(PlayIterator.FAILED_SETUP)\""
    )

    assert engine.get_tool_block_message("terminal", {"command": command}) is None
    assert engine._retrieval_trace.runtime_probe_commands == [command]


def test_formsy_engine_blocks_runtime_probe_for_unaccepted_module_after_grounding():
    engine = FormsyContextEngine()
    engine._sync_trace_state(state="grounded")
    engine._set_accepted_targets(["lib/ansible/executor/play_iterator.py"])

    blocked = engine.get_tool_block_message(
        "terminal",
        {
            "command": (
                "python3 -c \"from ansible.plugins.strategy.linear import StrategyModule; "
                "print(StrategyModule)\""
            )
        },
    )

    assert blocked is not None
    assert "runtime probes are limited to accepted target modules" in blocked


def test_formsy_engine_enforces_runtime_probe_budget_after_grounding():
    engine = FormsyContextEngine()
    engine._sync_trace_state(state="grounded")
    engine._set_accepted_targets(["lib/ansible/executor/play_iterator.py"])
    engine._retrieval_trace.runtime_probe_limit = 2

    first = (
        "python3 -c \"from ansible.executor.play_iterator import PlayIterator; "
        "print(PlayIterator.ITERATING_SETUP)\""
    )
    second = (
        "python3 -c \"from ansible.executor.play_iterator import PlayIterator; "
        "print(PlayIterator.FAILED_SETUP)\""
    )
    third = (
        "python3 -c \"from ansible.executor.play_iterator import PlayIterator; "
        "print(PlayIterator.ITERATING_TASKS)\""
    )

    assert engine.get_tool_block_message("terminal", {"command": first}) is None
    assert engine.get_tool_block_message("terminal", {"command": second}) is None
    blocked = engine.get_tool_block_message("terminal", {"command": third})

    assert blocked is not None
    assert "runtime probe budget is exhausted" in blocked


def test_formsy_engine_blocks_terminal_source_introspection_after_grounding():
    engine = FormsyContextEngine()
    engine._sync_trace_state(state="grounded")
    engine._set_accepted_targets(
        ["django/contrib/staticfiles/storage.py"],
        test_plan_files=["tests/staticfiles_tests/test_storage.py"],
    )

    inspect_blocked = engine.get_tool_block_message(
        "terminal",
        {
            "command": (
                "python3 -c \"import inspect; "
                "from django.contrib.staticfiles.storage import HashedFilesMixin; "
                "print(inspect.getsource(HashedFilesMixin.post_process))\""
            )
        },
    )
    open_blocked = engine.get_tool_block_message(
        "terminal",
        {"command": "python3 -c \"with open('django/contrib/staticfiles/storage.py') as f: print(f.readlines())\""},
    )

    assert inspect_blocked is not None
    assert "source introspection is blocked" in inspect_blocked
    assert open_blocked is not None
    assert "source introspection is blocked" in open_blocked


def test_formsy_engine_keeps_grounded_after_reading_accepted_target():
    engine = FormsyContextEngine()
    engine._sync_trace_state(state="grounded")
    engine._set_accepted_targets(
        ["django/contrib/staticfiles/storage.py"],
        test_plan_files=["tests/staticfiles_tests/test_storage.py"],
    )

    engine._record_context_read(
        "django/contrib/staticfiles/storage.py",
        {"path": "django/contrib/staticfiles/storage.py", "content": "source"},
    )

    status = engine.get_retrieval_status()
    assert status["retrieval_state"] == "grounded"
    assert engine._grounded_search_required is False
    assert engine.get_tool_block_message("terminal", {"command": "pwd && ls -la"}) is None
    assert engine.get_tool_block_message(
        "terminal",
        {"command": "python3 -c \"import os; print(os.getcwd())\""},
    ) is None
    blocked = engine.get_tool_block_message(
        "terminal",
        {"command": "python3 -c \"print('hello')\""},
    )
    assert blocked is not None
    assert "runtime probes are limited to accepted target modules" in blocked


def test_formsy_engine_blocks_repeated_passed_test_until_next_edit():
    engine = FormsyContextEngine()
    engine._sync_trace_state(state="grounded")
    engine._set_accepted_targets(["django/contrib/staticfiles/storage.py"])
    command = "python3 tests/runtests.py staticfiles_tests -v 1"

    assert engine.get_tool_block_message("terminal", {"command": command}) is None
    engine.observe_tool_result(
        "terminal",
        {"command": command},
        json.dumps({"exit_code": 0, "output": "OK", "error": None}),
    )

    blocked = engine.get_tool_block_message("terminal", {"command": command})

    assert blocked is not None
    assert "already passed" in blocked

    engine.observe_tool_result("patch", {"path": "django/contrib/staticfiles/storage.py"}, "")
    assert engine.get_tool_block_message("terminal", {"command": command}) is None


def test_formsy_engine_blocks_generic_terminal_after_grounding():
    engine = FormsyContextEngine()
    engine._sync_trace_state(state="grounded")
    engine._set_accepted_targets(
        ["django/contrib/staticfiles/storage.py"],
        test_plan_files=["tests/staticfiles_tests/test_storage.py"],
    )

    assert engine.get_tool_block_message(
        "terminal",
        {"command": "cat django/contrib/staticfiles/storage.py"},
    ) is None
    assert engine.get_tool_block_message(
        "terminal",
        {"command": "python3 tests/runtests.py staticfiles_tests.test_storage -v 2"},
    ) is None
    blocked = engine.get_tool_block_message("terminal", {"command": "ls django"})

    assert blocked is not None
    assert "terminal commands after grounding are limited" in blocked


def test_formsy_engine_allows_grounded_read_only_context_but_not_edits():
    engine = FormsyContextEngine()

    class FakeClient:
        async def compile_status(self, **kwargs):
            return {"parsed_file_count": 1000, "metadata": {}}

        async def memory_search(self, **kwargs):
            return {
                "coverage": "partial",
                "matches": [
                    {"path": "lib/ansible/executor/play_iterator.py"},
                    {"path": "lib/ansible/plugins/strategy/linear.py"},
                ],
                "accepted_targets": ["lib/ansible/executor/play_iterator.py"],
                "exploration_closed": True,
                "retrieval_state": "grounded",
                "preferred_next_step": "edit",
                "bundle": {
                    "primary_files": [
                        {
                            "path": "lib/ansible/executor/play_iterator.py",
                            "priority": "must_edit",
                        }
                    ],
                },
            }

    engine._engine_client = FakeClient()
    engine._config = type("Config", (), {
        "repo_id": "ansible__ansible",
        "revision": "cd64e0b070f8630e1dcc021e594ed42ea7afe304",
        "query_budget": 4000,
    })()

    result = json.loads(
        engine.handle_tool_call(
            "context_search",
            {
                "query": "PlayIterator strategy state usage",
                "metadata": {
                    "retrieval_mode": "symbolic",
                    "grounding_phase": "grounded",
                    "grounded_files": ["lib/ansible/executor/play_iterator.py"],
                },
            },
        )
    )

    assert result["retrieval_state"] == "grounded"
    assert engine.get_tool_block_message(
        "context_read",
        {"path": "lib/ansible/plugins/strategy/linear.py"},
    ) is None
    assert engine.get_tool_block_message(
        "read_file",
        {"path": "lib/ansible/plugins/strategy/linear.py"},
    ) is None
    assert engine.get_tool_block_message(
        "terminal",
        {
            "command": (
                "cd /testbed && grep -n \"ITERATING_\\|FAILED_\" "
                "lib/ansible/plugins/strategy/linear.py | head -n 40"
            )
        },
    ) is None

    blocked = engine.get_tool_block_message(
        "write_file",
        {"path": "lib/ansible/plugins/strategy/linear.py"},
    )

    assert blocked is not None
    assert "editing is limited to accepted targets" in blocked

    supplemental_grep = engine.get_tool_block_message(
        "terminal",
        {
            "command": (
                "cd /testbed && grep -n \"ITERATING_\\|FAILED_\" "
                "lib/ansible/plugins/strategy/__init__.py | head -n 40"
            )
        },
    )

    assert supplemental_grep is None
    assert "lib/ansible/plugins/strategy/__init__.py" in engine._retrieval_trace.supplemental_read_files


def test_formsy_engine_allows_resolved_tocs_must_read_and_candidate_tests():
    engine = FormsyContextEngine()

    class FakeClient:
        async def memory_search(self, **kwargs):
            return {
                "coverage": "partial",
                "matches": [
                    {"path": "lib/ansible/utils/display.py"},
                ],
                "accepted_targets": ["lib/ansible/utils/display.py"],
                "exploration_closed": True,
                "retrieval_state": "retry_symbolic_search",
                "preferred_next_step": "context_read",
                "guidance": {
                    "tocs_delivery": {
                        "requested": True,
                        "resolved": True,
                        "artifact_resolution_mode": "latest_gated_case_profile",
                    },
                    "tocs": {
                        "must_read_files": [
                            {"path": "lib/ansible/module_utils/urls.py"},
                            {"path": "lib/ansible/modules/uri.py"},
                            {"path": "test/units/module_utils/urls/test_gzip.py"},
                        ],
                        "candidate_tests": [
                            {
                                "test_id": (
                                    "test/units/module_utils/urls/test_gzip.py"
                                    "::test_Request_open_gzip"
                                ),
                                "command": (
                                    "pytest test/units/module_utils/urls/test_gzip.py"
                                    "::test_Request_open_gzip"
                                ),
                                "semantic_coverage_hints": [
                                    "covers opt-out/raw encoded payload behavior",
                                    "covers incremental read(size) reader semantics",
                                ],
                            }
                        ],
                    },
                },
            }

    engine._engine_client = FakeClient()
    engine._config = type("Config", (), {
        "repo_id": "ansible__ansible",
        "revision": "v173091e2e36d38c978002990795f66cfc0af30ad",
        "query_budget": 4000,
    })()

    result = json.loads(
        engine.handle_tool_call(
            "context_search",
            {
                "query": "Request.open gzip Content-Encoding decompress=False",
                "metadata": {
                    "retrieval_mode": "symbolic",
                    "grounding_phase": "seed",
                    "response_format": "bundle",
                },
            },
        )
    )

    assert result["accepted_targets"] == ["lib/ansible/utils/display.py"]
    assert engine.get_tool_block_message(
        "context_read",
        {"path": "lib/ansible/module_utils/urls.py"},
    ) is None
    assert engine.get_tool_block_message(
        "context_read",
        {"path": "test/units/module_utils/urls/test_gzip.py"},
    ) is None
    status = engine.get_retrieval_status()
    assert "lib/ansible/module_utils/urls.py" in status["retrieval_trace"]["read_only_context_files"]
    assert "test/units/module_utils/urls/test_gzip.py" in status["test_plan_files"]
    blocked = engine.get_tool_block_message(
        "write_file",
        {"path": "lib/ansible/module_utils/urls.py"},
    )
    assert blocked is not None
    assert "editing is limited to accepted targets" in blocked


def test_formsy_engine_projects_resolved_tocs_repair_target_over_stale_contract_target(
    monkeypatch,
):
    engine = FormsyContextEngine()
    captured = {}

    class FakeClient:
        async def memory_search(self, **kwargs):
            return {
                "coverage": "partial",
                "matches": [{"path": "lib/ansible/utils/display.py"}],
                "accepted_targets": ["lib/ansible/utils/display.py"],
                "exploration_closed": True,
                "retrieval_state": "retry_symbolic_search",
                "preferred_next_step": "context_read",
                "guidance": {
                    "tocs_delivery": {
                        "requested": True,
                        "resolved": True,
                        "artifact_resolution_mode": "latest_gated_case_profile",
                    },
                    "tocs": {
                        "lane_b_mode": "repair_ready_exact",
                        "must_read_files": [
                            {"path": "lib/ansible/module_utils/urls.py"},
                            {"path": "lib/ansible/modules/uri.py"},
                            {"path": "lib/ansible/modules/get_url.py"},
                            {"path": "test/units/module_utils/urls/test_gzip.py"},
                        ],
                        "candidate_tests": [
                            {
                                "test_id": (
                                    "test/units/module_utils/urls/test_gzip.py"
                                    "::test_Request_open_gzip"
                                ),
                                "command": (
                                    "pytest test/units/module_utils/urls/test_gzip.py"
                                    "::test_Request_open_gzip"
                                ),
                                "semantic_coverage_hints": [
                                    "covers opt-out/raw encoded payload behavior",
                                    "covers incremental read(size) reader semantics",
                                ],
                            }
                        ],
                    },
                },
            }

    class FakeCoordinator:
        def compile_context_bundle(self, **kwargs):
            captured.update(kwargs)
            return "## FormSy Constraint Protocol\n- State: PATCH_ALLOWED_WITH_WARNINGS"

        def observe_tool_result(self, *args, **kwargs):
            return None

    monkeypatch.setattr(
        "plugins.context_engine.formsy.engine._get_constraint_keeper_coordinator",
        lambda: FakeCoordinator(),
    )

    engine._engine_client = FakeClient()
    engine._config = type("Config", (), {
        "repo_id": "ansible__ansible",
        "revision": "v173091e2e36d38c978002990795f66cfc0af30ad",
        "query_budget": 4000,
        "workspace_id": "local",
    })()

    result = json.loads(
        engine.handle_tool_call(
            "context_search",
            {
                "query": "Request.open gzip Content-Encoding decompress=False",
                "metadata": {
                    "retrieval_mode": "symbolic",
                    "grounding_phase": "seed",
                    "response_format": "bundle",
                },
            },
        )
    )

    assert result["accepted_targets"] == ["lib/ansible/module_utils/urls.py"]
    assert result["tocs_repair_targets"] == ["lib/ansible/module_utils/urls.py"]
    assert result["tocs_contract_projection"] == {
        "source": "resolved_tocs",
        "reason": "repair_ready_exact",
        "replaced_accepted_targets": ["lib/ansible/utils/display.py"],
    }
    assert "Coverage hints:" in result["extra_context"]
    assert "incremental read(size) reader semantics" in result["extra_context"]
    assert captured["search_payload"]["accepted_targets"] == [
        "lib/ansible/module_utils/urls.py"
    ]
    assert captured["context_bundle"]["primary_files"] == [
        {"path": "lib/ansible/module_utils/urls.py", "priority": "must_edit"}
    ]
    assert engine.get_tool_block_message(
        "patch",
        {"path": "lib/ansible/module_utils/urls.py"},
    ) is None
    assert engine.get_tool_block_message(
        "patch",
        {"path": "lib/ansible/modules/uri.py"},
    ) is not None


def test_formsy_engine_promotes_single_resolved_tocs_must_read_without_candidate_tests(
    monkeypatch,
):
    engine = FormsyContextEngine()
    captured = {}

    class FakeClient:
        async def memory_search(self, **kwargs):
            return {
                "coverage": "partial",
                "matches": [{"path": "lib/ansible/utils/display.py"}],
                "accepted_targets": ["lib/ansible/utils/display.py"],
                "exploration_closed": True,
                "retrieval_state": "retry_symbolic_search",
                "preferred_next_step": "context_read",
                "guidance": {
                    "tocs_delivery": {
                        "requested": True,
                        "resolved": True,
                    },
                    "tocs": {
                        "lane_b_mode": "repair_ready_exact",
                        "must_read_files": [
                            {"path": "lib/ansible/modules/iptables.py"},
                        ],
                    },
                },
            }

    class FakeCoordinator:
        def compile_context_bundle(self, **kwargs):
            captured.update(kwargs)
            return ""

        def observe_tool_result(self, *args, **kwargs):
            return None

    monkeypatch.setattr(
        "plugins.context_engine.formsy.engine._get_constraint_keeper_coordinator",
        lambda: FakeCoordinator(),
    )

    engine._engine_client = FakeClient()
    engine._config = type("Config", (), {
        "repo_id": "ansible__ansible",
        "revision": "latest",
        "query_budget": 4000,
        "workspace_id": "local",
    })()

    result = json.loads(
        engine.handle_tool_call(
            "context_search",
            {
                "query": "iptables chain management",
                "metadata": {
                    "retrieval_mode": "symbolic",
                    "grounding_phase": "seed",
                    "response_format": "bundle",
                },
            },
        )
    )

    assert result["accepted_targets"] == ["lib/ansible/modules/iptables.py"]
    assert result["tocs_repair_targets"] == ["lib/ansible/modules/iptables.py"]
    assert result["tocs_contract_projection"] == {
        "source": "resolved_tocs",
        "reason": "repair_ready_exact",
        "replaced_accepted_targets": ["lib/ansible/utils/display.py"],
    }
    assert captured["search_payload"]["accepted_targets"] == [
        "lib/ansible/modules/iptables.py"
    ]


def test_formsy_engine_projects_edit_next_step_when_resolved_tocs_closes_exploration(
    monkeypatch,
):
    engine = FormsyContextEngine()

    class FakeClient:
        async def memory_search(self, **kwargs):
            return {
                "coverage": "partial",
                "matches": [{"path": "lib/ansible/utils/display.py"}],
                "accepted_targets": ["lib/ansible/utils/display.py"],
                "exploration_closed": True,
                "retrieval_state": "retry_symbolic_search",
                "preferred_next_step": "context_search",
                "guidance": {
                    "tocs_delivery": {"resolved": True},
                    "tocs": {
                        "lane_b_mode": "repair_ready_exact",
                        "must_read_files": [
                            {"path": "lib/ansible/module_utils/urls.py"},
                            {"path": "test/units/module_utils/urls/test_gzip.py"},
                        ],
                        "candidate_tests": [
                            {"path": "test/units/module_utils/urls/test_gzip.py"}
                        ],
                    },
                },
            }

    class FakeCoordinator:
        def compile_context_bundle(self, **kwargs):
            return ""

        def observe_tool_result(self, *args, **kwargs):
            return None

    monkeypatch.setattr(
        "plugins.context_engine.formsy.engine._get_constraint_keeper_coordinator",
        lambda: FakeCoordinator(),
    )

    engine._engine_client = FakeClient()
    engine._config = type("Config", (), {
        "repo_id": "ansible__ansible",
        "revision": "latest",
        "query_budget": 4000,
        "workspace_id": "local",
    })()

    result = json.loads(
        engine.handle_tool_call(
            "context_search",
            {
                "query": "Request.open gzip response decompress",
                "metadata": {
                    "retrieval_mode": "symbolic",
                    "grounding_phase": "seed",
                    "response_format": "bundle",
                },
            },
        )
    )

    assert result["accepted_targets"] == ["lib/ansible/module_utils/urls.py"]
    assert result["exploration_closed"] is True
    assert result["retrieval_state"] == "grounded"
    assert result["preferred_next_step"] == "edit"
    assert result["retrieval_decision"]["decision"] == "grounded"


def test_formsy_engine_allows_limited_supplemental_read_after_grounding_but_not_edits():
    engine = FormsyContextEngine()

    class FakeClient:
        async def compile_status(self, **kwargs):
            return {"parsed_file_count": 1000, "metadata": {}}

        async def memory_search(self, **kwargs):
            return {
                "coverage": "partial",
                "matches": [
                    {"path": "lib/ansible/executor/play_iterator.py"},
                    {"path": "lib/ansible/plugins/strategy/linear.py"},
                ],
                "accepted_targets": ["lib/ansible/executor/play_iterator.py"],
                "exploration_closed": True,
                "retrieval_state": "grounded",
                "preferred_next_step": "edit",
                "bundle": {
                    "primary_files": [
                        {
                            "path": "lib/ansible/executor/play_iterator.py",
                            "priority": "must_edit",
                        }
                    ],
                },
            }

    engine._engine_client = FakeClient()
    engine._config = type("Config", (), {
        "repo_id": "ansible__ansible",
        "revision": "cd64e0b070f8630e1dcc021e594ed42ea7afe304",
        "query_budget": 4000,
    })()

    engine.handle_tool_call(
        "context_search",
        {
            "query": "PlayIterator public state constants",
            "metadata": {
                "retrieval_mode": "symbolic",
                "grounding_phase": "grounded",
                "grounded_files": ["lib/ansible/executor/play_iterator.py"],
            },
        },
    )

    supplemental_path = "lib/ansible/plugins/strategy/free.py"
    assert engine.get_tool_block_message("read_file", {"path": supplemental_path}) is None
    assert supplemental_path in engine._retrieval_trace.supplemental_read_files
    assert engine.get_tool_block_message("context_read", {"path": supplemental_path}) is None
    assert engine.get_tool_block_message(
        "terminal",
        {"command": f"grep -n \"PlayIterator\\.\" {supplemental_path}"},
    ) is None
    assert engine.get_tool_block_message(
        "terminal",
        {"command": f"python -m py_compile {supplemental_path} && echo ok"},
    ) is None

    edit_blocked = engine.get_tool_block_message("patch", {"path": supplemental_path})
    unrelated_compile_blocked = engine.get_tool_block_message(
        "terminal",
        {"command": "python -m py_compile lib/ansible/plugins/connection/ssh.py"},
    )

    assert edit_blocked is not None
    assert "editing is limited to accepted targets" in edit_blocked
    assert unrelated_compile_blocked is not None
    assert "terminal commands after grounding are limited" in unrelated_compile_blocked


def test_formsy_engine_enforces_supplemental_read_budget_after_grounding():
    engine = FormsyContextEngine()
    engine._sync_trace_state(state="grounded")
    engine._set_accepted_targets(["lib/ansible/executor/play_iterator.py"])

    supplemental_paths = [
        "lib/ansible/plugins/strategy/free.py",
        "lib/ansible/plugins/strategy/debug.py",
        "lib/ansible/plugins/strategy/linear.py",
        "lib/ansible/plugins/strategy/host_pinned.py",
        "lib/ansible/plugins/strategy/mitogen_linear.py",
    ]
    for path in supplemental_paths:
        assert engine.get_tool_block_message("read_file", {"path": path}) is None

    blocked = engine.get_tool_block_message(
        "read_file",
        {"path": "lib/ansible/plugins/strategy/extra.py"},
    )

    assert blocked is not None
    assert "supplemental read budget is exhausted" in blocked
    assert engine._retrieval_trace.supplemental_read_files == supplemental_paths


def test_formsy_engine_blocks_unsafe_supplemental_read_paths_after_grounding():
    engine = FormsyContextEngine()
    engine._sync_trace_state(state="grounded")
    engine._set_accepted_targets(["lib/ansible/executor/play_iterator.py"])

    unsafe_paths = [
        "/tmp/free.py",
        "../free.py",
        "lib/ansible/plugins/strategy",
        "lib/ansible/plugins/strategy/*.py",
        "lib/ansible/plugins/strategy/free.py other.py",
    ]

    for path in unsafe_paths:
        blocked = engine.get_tool_block_message("read_file", {"path": path})
        assert blocked is not None
        assert "supplemental read requires one explicit repo-relative file path" in blocked

    recursive_grep = engine.get_tool_block_message(
        "terminal",
        {"command": "grep -R \"PlayIterator\" lib/ansible/plugins/strategy"},
    )
    multi_file_cat = engine.get_tool_block_message(
        "terminal",
        {
            "command": (
                "cat lib/ansible/plugins/strategy/free.py "
                "lib/ansible/plugins/strategy/linear.py"
            )
        },
    )
    multi_file_grep = engine.get_tool_block_message(
        "terminal",
        {
            "command": (
                "grep -n \"PlayIterator\" lib/ansible/plugins/strategy/free.py "
                "lib/ansible/plugins/strategy/linear.py"
            )
        },
    )

    assert recursive_grep is not None
    assert "broad grep/find/search commands are blocked" in recursive_grep
    assert multi_file_cat is not None
    assert "terminal commands after grounding are limited" in multi_file_cat
    assert multi_file_grep is not None
    assert "broad grep/find/search commands are blocked" in multi_file_grep


def test_formsy_engine_does_not_replace_accepted_target_with_later_context_search():
    engine = FormsyContextEngine()

    class FakeClient:
        async def compile_status(self, **kwargs):
            return {"parsed_file_count": 1000, "metadata": {}}

        async def memory_search(self, **kwargs):
            query = kwargs["query"]
            if "display.deprecated" in query:
                return {
                    "coverage": "partial",
                    "matches": [
                        {"path": "lib/ansible/utils/display.py"},
                    ],
                    "accepted_targets": ["lib/ansible/utils/display.py"],
                    "exploration_closed": True,
                    "retrieval_state": "grounded",
                    "preferred_next_step": "edit",
                    "bundle": {
                        "primary_files": [
                            {
                                "path": "lib/ansible/utils/display.py",
                                "priority": "must_edit",
                            }
                        ],
                    },
                }
            return {
                "coverage": "partial",
                "matches": [
                    {"path": "lib/ansible/executor/play_iterator.py"},
                    {"path": "lib/ansible/plugins/strategy/linear.py"},
                ],
                "accepted_targets": ["lib/ansible/executor/play_iterator.py"],
                "exploration_closed": True,
                "retrieval_state": "grounded",
                "preferred_next_step": "edit",
                "bundle": {
                    "primary_files": [
                        {
                            "path": "lib/ansible/executor/play_iterator.py",
                            "priority": "must_edit",
                        }
                    ],
                },
            }

    engine._engine_client = FakeClient()
    engine._config = type("Config", (), {
        "repo_id": "ansible__ansible",
        "revision": "cd64e0b070f8630e1dcc021e594ed42ea7afe304",
        "query_budget": 4000,
    })()

    first = json.loads(
        engine.handle_tool_call(
            "context_search",
            {
                "query": "PlayIterator states",
                "metadata": {
                    "retrieval_mode": "symbolic",
                    "grounding_phase": "grounded",
                    "grounded_files": ["lib/ansible/executor/play_iterator.py"],
                },
            },
        )
    )
    assert first["accepted_targets"] == ["lib/ansible/executor/play_iterator.py"]

    second = json.loads(
        engine.handle_tool_call(
            "context_search",
            {
                "query": "display.deprecated ansible utils display",
                "metadata": {
                    "retrieval_mode": "symbolic",
                    "grounding_phase": "grounded",
                    "grounded_files": ["lib/ansible/utils/display.py"],
                },
            },
        )
    )

    assert second["accepted_targets"] == ["lib/ansible/executor/play_iterator.py"]
    assert engine.get_tool_block_message(
        "read_file",
        {"path": "lib/ansible/executor/play_iterator.py"},
    ) is None
    assert engine.get_tool_block_message(
        "patch",
        {"path": "lib/ansible/executor/play_iterator.py"},
    ) is None
    assert engine.get_tool_block_message(
        "read_file",
        {"path": "lib/ansible/utils/display.py"},
    ) is None

    blocked = engine.get_tool_block_message(
        "patch",
        {"path": "lib/ansible/utils/display.py"},
    )

    assert blocked is not None
    assert "editing is limited to accepted targets" in blocked


def test_formsy_engine_replaces_seed_accepted_target_with_later_effective_target():
    engine = FormsyContextEngine()

    class FakeClient:
        async def compile_status(self, **kwargs):
            return {"parsed_file_count": 1000, "metadata": {}}

        async def memory_search(self, **kwargs):
            query = kwargs["query"]
            if "gzip response" in query:
                return {
                    "coverage": "partial",
                    "matches": [
                        {"path": "lib/ansible/utils/display.py"},
                    ],
                    "accepted_targets": ["lib/ansible/utils/display.py"],
                    "exploration_closed": True,
                    "retrieval_state": "inspect_candidates",
                    "preferred_next_step": "context_read",
                    "bundle": {
                        "primary_files": [
                            {
                                "path": "lib/ansible/utils/display.py",
                                "priority": "must_edit",
                            }
                        ],
                    },
                }
            return {
                "coverage": "partial",
                "matches": [
                    {"path": "lib/ansible/module_utils/urls.py"},
                ],
                "accepted_targets": ["lib/ansible/module_utils/urls.py"],
                "exploration_closed": True,
                "retrieval_state": "inspect_candidates",
                "preferred_next_step": "context_read",
                "bundle": {
                    "primary_files": [
                        {
                            "path": "lib/ansible/module_utils/urls.py",
                            "priority": "must_edit",
                        }
                    ],
                },
            }

    engine._engine_client = FakeClient()
    engine._config = type("Config", (), {
        "repo_id": "ansible__ansible",
        "revision": "cd64e0b070f8630e1dcc021e594ed42ea7afe304",
        "query_budget": 4000,
    })()

    first = json.loads(
        engine.handle_tool_call(
            "context_search",
            {
                "query": "gzip response should decompress response body",
                "metadata": {
                    "retrieval_mode": "symbolic",
                    "grounding_phase": "seed",
                },
            },
        )
    )
    assert first["accepted_targets"] == ["lib/ansible/utils/display.py"]

    second = json.loads(
        engine.handle_tool_call(
            "context_search",
            {
                "query": "urls Request.open gzip decompress module_utils",
                "metadata": {
                    "retrieval_mode": "symbolic",
                    "grounding_phase": "seed",
                },
            },
        )
    )

    assert second["accepted_targets"] == ["lib/ansible/module_utils/urls.py"]
    assert second["retrieval_decision"]["accepted_targets"] == [
        "lib/ansible/module_utils/urls.py"
    ]
    assert engine.get_tool_block_message(
        "patch",
        {"path": "lib/ansible/module_utils/urls.py"},
    ) is None


def test_formsy_engine_blocks_execute_code_tool_bypass_after_grounding():
    engine = FormsyContextEngine()
    engine._sync_trace_state(state="grounded")
    engine._set_accepted_targets(
        ["lib/ansible/executor/play_iterator.py"],
        test_plan_files=["test/units/executor/test_play_iterator.py"],
    )

    search_blocked = engine.get_tool_block_message(
        "execute_code",
        {
            "code": (
                "from hermes_tools import search_files\n"
                "search_files(pattern='PlayIterator', path='lib/ansible')\n"
            )
        },
    )
    write_blocked = engine.get_tool_block_message(
        "execute_code",
        {
            "code": (
                "with open('lib/ansible/executor/play_iterator.py', 'w') as f:\n"
                "    f.write('new source')\n"
            )
        },
    )
    walk_blocked = engine.get_tool_block_message(
        "execute_code",
        {
            "code": (
                "import os\n"
                "for root, dirs, files in os.walk('lib/ansible'):\n"
                "    print(root)\n"
            )
        },
    )
    read_blocked = engine.get_tool_block_message(
        "execute_code",
        {
            "code": (
                "with open('lib/ansible/plugins/strategy/linear.py') as f:\n"
                "    print(f.read())\n"
            )
        },
    )
    sanity_allowed = engine.get_tool_block_message(
        "execute_code",
        {"code": "import os\nprint(os.getcwd())\n"},
    )

    assert search_blocked is not None
    assert "execute_code cannot use hermes_tools" in search_blocked
    assert write_blocked is not None
    assert "execute_code file writes are blocked" in write_blocked
    assert walk_blocked is not None
    assert "execute_code filesystem reads are blocked" in walk_blocked
    assert read_blocked is not None
    assert "execute_code filesystem reads are blocked" in read_blocked
    assert sanity_allowed is None


def test_formsy_engine_allows_one_grounded_recovery_search_after_test_failure():
    engine = FormsyContextEngine()
    engine._sync_trace_state(state="grounded")
    engine._set_accepted_targets(
        ["django/contrib/staticfiles/storage.py"],
        test_plan_files=["tests/staticfiles_tests/test_storage.py"],
    )
    engine._test_plan_commands = [
        "python3 tests/runtests.py staticfiles_tests.test_storage -v 2"
    ]
    calls = []

    class FakeClient:
        async def compile_status(self, **kwargs):
            return {"parsed_file_count": 1000, "metadata": {}}

        async def memory_search(self, **kwargs):
            calls.append(kwargs)
            return {
                "coverage": "partial",
                "matches": [{"path": "django/contrib/staticfiles/storage.py"}],
                "bundle": {
                    "primary_files": [
                        {
                            "path": "django/contrib/staticfiles/storage.py",
                            "priority": "must_edit",
                        }
                    ],
                },
                "accepted_targets": ["django/contrib/staticfiles/storage.py"],
                "exploration_closed": True,
                "retrieval_state": "grounded",
            }

    engine._engine_client = FakeClient()
    engine._config = type("Config", (), {
        "repo_id": "django__django",
        "revision": "latest",
        "query_budget": 4000,
    })()
    engine._session_id = "session-123"
    engine.observe_tool_result(
        "terminal",
        {"command": "python3 tests/runtests.py staticfiles_tests.test_storage -v 2"},
        json.dumps({"exit_code": 1, "output": "FAILED (failures=2)", "error": None}),
    )

    assert engine.get_tool_block_message(
        "context_search",
        {
            "query": "collectstatic post_process yield exception handling faulty.css",
            "metadata": {"grounding_phase": "seed", "retrieval_mode": "symbolic"},
        },
    ) is None

    engine.handle_tool_call(
        "context_search",
        {
            "query": "collectstatic post_process yield exception handling faulty.css",
            "metadata": {"grounding_phase": "seed", "retrieval_mode": "symbolic"},
        },
    )

    assert calls[-1]["metadata"]["grounding_phase"] == "grounded"
    assert calls[-1]["metadata"]["grounded_files"] == [
        "django/contrib/staticfiles/storage.py"
    ]
    assert calls[-1]["metadata"]["test_failure_recovery"] is True
    assert engine.get_tool_block_message(
        "context_search",
        {"query": "another search"},
    ) is not None


def test_formsy_engine_blocks_repeated_non_test_terminal_probe():
    engine = FormsyContextEngine()
    engine._sync_trace_state(state="degraded_recovery")
    command = 'python3 -c "print(1)"'

    assert engine.get_tool_block_message("terminal", {"command": command}) is None
    engine.observe_tool_result(
        "terminal",
        {"command": command},
        json.dumps({"exit_code": 0, "output": "1\n", "error": None}),
    )
    assert engine.get_tool_block_message("terminal", {"command": command}) is None
    engine.observe_tool_result(
        "terminal",
        {"command": command},
        json.dumps({"exit_code": 0, "output": "1\n", "error": None}),
    )

    blocked = engine.get_tool_block_message("terminal", {"command": command})

    assert blocked is not None
    assert "already ran twice" in blocked
    assert engine.get_tool_block_message(
        "terminal",
        {"command": "python tests/runtests.py staticfiles_tests.test_storage -v 2"},
    ) is None

    engine.observe_tool_result("patch", {"path": "django/contrib/staticfiles/storage.py"}, "")
    assert engine.get_tool_block_message("terminal", {"command": command}) is None


def test_formsy_engine_blocks_repeated_non_test_terminal_probe_without_observed_results():
    engine = FormsyContextEngine()
    engine._sync_trace_state(state="grounded")
    engine._set_accepted_targets(["lib/ansible/module_utils/urls.py"])
    command = (
        'cd /Users/wayneliu/dev/ansible && grep -n "Gzip\\|gzip\\|decompress\\|'
        'Content-Encoding" lib/ansible/module_utils/urls.py | head -n 40'
    )

    assert engine.get_tool_block_message("terminal", {"command": command}) is None
    assert engine.get_tool_block_message("terminal", {"command": command}) is None
    blocked = engine.get_tool_block_message("terminal", {"command": command})

    assert blocked is not None
    assert "already ran twice" in blocked
    assert "patch the accepted target" in blocked


def test_formsy_engine_reuses_existing_compile_for_same_query():
    query = "HashedFilesMixin post_process duplicate yields"
    engine = FormsyContextEngine()
    calls = []

    class FakeClient:
        async def compile_status(self, **kwargs):
            calls.append(("status", kwargs))
            return {
                "repo_id": "django__django",
                "revision": "abc123",
                "parsed_file_count": 260,
                "metadata": {
                    "compile_profile": "interactive_context_search",
                    "query_signature": FormsyContextEngine._query_signature(query),
                },
            }

        async def compile_repo(self, **kwargs):
            calls.append(("compile", kwargs))
            raise AssertionError("compile_repo should be skipped")

    engine._engine_client = FakeClient()

    assert engine._ensure_memory_compiled(
        repo_id="django__django",
        revision="abc123",
        query=query,
        session_id="session-123",
    ) is True
    assert [name for name, _ in calls] == ["status"]


def test_formsy_engine_compile_status_does_not_reuse_different_query_bounded_compile():
    engine = FormsyContextEngine()

    status = {
        "repo_id": "django__django",
        "revision": "abc123",
        "parsed_file_count": 260,
        "metadata": {
            "compile_profile": "interactive_context_search",
            "query": "auth validators username regex",
        },
    }

    assert engine._existing_compile_satisfies_query(
        status,
        "HashedFilesMixin post_process duplicate yields",
    ) is False


def test_formsy_engine_in_memory_compile_identity_is_query_scoped(monkeypatch):
    engine = FormsyContextEngine()
    old_query = "auth validators username regex"
    new_query = "HashedFilesMixin post_process duplicate yields"
    calls = []

    class FakeClient:
        async def compile_status(self, **kwargs):
            calls.append(("status", kwargs))
            return {
                "repo_id": "django__django",
                "revision": "abc123",
                "parsed_file_count": 260,
                "metadata": {
                    "compile_profile": "interactive_context_search",
                    "query_signature": FormsyContextEngine._query_signature(old_query),
                },
            }

        async def compile_repo(self, **kwargs):
            calls.append(("compile", kwargs))
            return {
                "repo_id": kwargs["repo_id"],
                "revision": kwargs["revision"],
                "parsed_file_count": len(kwargs["files"]),
            }

    monkeypatch.setattr(
        FormsyContextEngine,
        "_collect_memory_source_files",
        staticmethod(lambda root, query="": [{
            "path": "django/contrib/staticfiles/storage.py",
            "content": "class HashedFilesMixin: pass\n",
            "language": "python",
            "is_test": False,
        }]),
    )

    engine._engine_client = FakeClient()
    engine._memory_compiled_identity = (
        "django__django",
        "abc123",
        FormsyContextEngine._query_signature(old_query),
    )

    assert engine._ensure_memory_compiled(
        repo_id="django__django",
        revision="abc123",
        query=new_query,
        session_id="session-123",
    ) is True
    assert [name for name, _ in calls] == ["status", "compile"]
    assert engine._memory_compiled_identity == (
        "django__django",
        "abc123",
        FormsyContextEngine._query_signature(new_query),
    )


def test_formsy_engine_reuses_large_non_interactive_compile_status():
    status = {
        "repo_id": "django__django",
        "revision": "abc123",
        "parsed_file_count": 1000,
        "metadata": {},
    }

    assert FormsyContextEngine._existing_compile_satisfies_query(
        status,
        "HashedFilesMixin post_process duplicate yields",
    ) is True


def test_formsy_engine_does_not_reuse_old_query_bounded_compile_for_different_query():
    status = {
        "repo_id": "django__django",
        "revision": "abc123",
        "parsed_file_count": 500,
        "metadata": {
            "query": "auth validators username regex",
            "source_file_count": 500,
        },
    }

    assert FormsyContextEngine._existing_compile_satisfies_query(
        status,
        "HashedFilesMixin post_process duplicate yields",
    ) is False
