from __future__ import annotations

import json

from plugins.context_engine.formsy.engine import FormsyContextEngine


class FakeSearchClient:
    async def memory_search(self, **kwargs):
        return {
            "matches": [{"path": "django/forms/models.py", "score": 0.9}],
            "coverage": "partial",
            "bundle": {
                "bundle_id": "bundle-1",
                "primary_files": ["django/forms/models.py"],
            },
            "test_plan": {"commands": ["python tests/runtests.py forms_tests"]},
        }


def _engine():
    engine = FormsyContextEngine()
    engine._engine_client = FakeSearchClient()
    engine._config = type("Config", (), {
        "repo_id": "django__django",
        "revision": "rev-1",
        "query_budget": 4000,
        "timeout_s": 120,
    })()
    engine._session_id = "sess-1"
    engine._ensure_memory_compiled = lambda **kwargs: True
    engine._memory_compile_revision = "rev-1"
    return engine


def test_context_search_appends_constraint_protocol_text(monkeypatch):
    engine = _engine()

    class Coordinator:
        def compile_context_bundle(self, **kwargs):
            return "## FormSy Constraint Protocol\n- State: PATCH_ALLOWED_WITH_WARNINGS"

    monkeypatch.setattr(
        "plugins.context_engine.formsy.engine._get_constraint_keeper_coordinator",
        lambda: Coordinator(),
    )

    result = engine.handle_tool_call("context_search", {"query": "forms model save"})

    data = json.loads(result)
    assert data["ok"] is True
    assert data["constraint_protocol_text"] == "## FormSy Constraint Protocol\n- State: PATCH_ALLOWED_WITH_WARNINGS"


def test_context_search_keeps_payload_when_constraint_compile_fails(monkeypatch):
    engine = _engine()

    class Coordinator:
        def compile_context_bundle(self, **kwargs):
            raise RuntimeError("compile timeout")

    monkeypatch.setattr(
        "plugins.context_engine.formsy.engine._get_constraint_keeper_coordinator",
        lambda: Coordinator(),
    )

    result = engine.handle_tool_call("context_search", {"query": "forms model save"})

    data = json.loads(result)
    assert data["ok"] is True
    assert data["matches"] == [{"path": "django/forms/models.py", "score": 0.9}]
    assert "Constraint Protocol compilation unavailable" in data["constraint_protocol_warning"]


def test_context_search_promotes_grounded_bundle_for_constraint_compile(monkeypatch):
    engine = _engine()

    class GroundedSearchClient:
        async def memory_search(self, **kwargs):
            return {
                "matches": [{"path": "lib/ansible/executor/play_iterator.py", "score": 0.9}],
                "coverage": "partial",
                "accepted_targets": ["lib/ansible/executor/play_iterator.py"],
                "exploration_closed": True,
                "bundle": {
                    "bundle_id": "bundle-1",
                    "coverage": "sufficient_for_reading",
                    "primary_files": [
                        {
                            "path": "lib/ansible/executor/play_iterator.py",
                            "priority": "must_edit",
                            "symbols": ["PlayIterator"],
                        }
                    ],
                },
                "test_plan": {"commands": ["pytest test/units/executor/test_play_iterator.py"]},
            }

    captured = {}

    class Coordinator:
        def compile_context_bundle(self, **kwargs):
            captured.update(kwargs)
            return "## FormSy Constraint Protocol\n- State: PATCH_ALLOWED_WITH_WARNINGS"

    engine._engine_client = GroundedSearchClient()
    monkeypatch.setattr(
        "plugins.context_engine.formsy.engine._get_constraint_keeper_coordinator",
        lambda: Coordinator(),
    )

    result = engine.handle_tool_call(
        "context_search",
        {
            "query": "PlayIterator state names",
            "metadata": {
                "retrieval_mode": "symbolic",
                "grounding_phase": "grounded",
                "grounded_files": ["lib/ansible/executor/play_iterator.py"],
            },
        },
    )

    data = json.loads(result)
    assert data["ok"] is True
    assert captured["context_bundle"]["coverage"] == "sufficient_for_first_patch"
    assert captured["context_bundle"]["test_plan"] == {
        "commands": ["pytest test/units/executor/test_play_iterator.py"]
    }


def test_context_search_promotes_partial_grounded_bundle_for_constraint_compile(monkeypatch):
    engine = _engine()

    class PartialGroundedSearchClient:
        async def memory_search(self, **kwargs):
            return {
                "matches": [{"path": "lib/ansible/module_utils/urls.py", "score": 0.9}],
                "coverage": "partial",
                "accepted_targets": ["lib/ansible/module_utils/urls.py"],
                "bundle": {
                    "bundle_id": "bundle-1",
                    "coverage": "partial",
                    "primary_files": [
                        {
                            "path": "lib/ansible/module_utils/urls.py",
                            "priority": "must_edit",
                        }
                    ],
                },
            }

    captured = {}

    class Coordinator:
        def compile_context_bundle(self, **kwargs):
            captured.update(kwargs)
            return "## FormSy Constraint Protocol\n- State: PATCH_ALLOWED_WITH_WARNINGS"

    engine._engine_client = PartialGroundedSearchClient()
    monkeypatch.setattr(
        "plugins.context_engine.formsy.engine._get_constraint_keeper_coordinator",
        lambda: Coordinator(),
    )

    result = engine.handle_tool_call(
        "context_search",
        {
            "query": "Request.open gzip Content-Encoding decompress=False",
            "metadata": {
                "retrieval_mode": "symbolic",
                "grounding_phase": "grounded",
            },
        },
    )

    data = json.loads(result)
    assert data["ok"] is True
    assert captured["context_bundle"]["coverage"] == "sufficient_for_first_patch"


def test_context_search_uses_task_instruction_for_constraint_compile(monkeypatch):
    engine = _engine()
    task = (
        "<pr_description>Standardize PlayIterator state representation with a public type. "
        "There must be a single public and namespaced way to reference iterator run "
        "states and failure states.</pr_description>"
    )
    engine.on_user_turn(task)

    captured = {}

    class Coordinator:
        def compile_context_bundle(self, **kwargs):
            captured.update(kwargs)
            return "## FormSy Constraint Protocol\n- State: PATCH_ALLOWED_WITH_WARNINGS"

    monkeypatch.setattr(
        "plugins.context_engine.formsy.engine._get_constraint_keeper_coordinator",
        lambda: Coordinator(),
    )

    result = engine.handle_tool_call(
        "context_search",
        {
            "query": "PlayIterator ITERATING_SETUP FAILED_SETUP",
            "metadata": {
                "retrieval_mode": "symbolic",
                "grounding_phase": "grounded",
                "grounded_files": ["lib/ansible/executor/play_iterator.py"],
            },
        },
    )

    data = json.loads(result)
    assert data["ok"] is True
    assert "public and namespaced way" in captured["instruction"]


def test_context_search_injects_user_task_description_without_system_prompt():
    captured = {}

    class CapturingSearchClient:
        async def memory_search(self, **kwargs):
            captured.update(kwargs)
            return {
                "matches": [{"path": "lib/ansible/modules/iptables.py", "score": 0.9}],
                "coverage": "partial",
            }

    engine = _engine()
    engine._engine_client = CapturingSearchClient()
    engine._context["system_prompt"] = "SYSTEM PROMPT MUST NOT LEAK"
    engine.on_user_turn(
        "In ansible, iptables chain management should handle chain creation and deletion.\n"
        "Expected behavior:\n"
        "- chain creation and deletion should use the correct chain-management commands.\n"
        "- check mode should report intended changes without performing destructive operations.\n"
        "- existing rule append/remove behavior should keep working."
    )

    result = engine.handle_tool_call(
        "context_search",
        {
            "query": "Ansible iptables module chain creation deletion check mode",
            "metadata": {
                "full_task_description": "SYSTEM PROMPT MUST NOT LEAK",
                "system_prompt": "SYSTEM PROMPT MUST NOT LEAK",
            },
        },
    )

    data = json.loads(result)
    metadata = captured["metadata"]
    assert data["ok"] is True
    assert "existing rule append/remove behavior" in metadata["full_task_description"]
    assert metadata["task_description_source"] == "hermes_user_turn"
    assert "SYSTEM PROMPT MUST NOT LEAK" not in metadata["full_task_description"]
    assert "system_prompt" not in metadata
