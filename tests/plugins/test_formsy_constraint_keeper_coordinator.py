from __future__ import annotations

import asyncio
import json
import logging

from plugins.formsy.constraint_keeper.coordinator import ConstraintKeeperCoordinator
from plugins.formsy.identity import derive_formsy_identity


class FakeClient:
    def __init__(self):
        self.calls = []
        self.verify_response = {"gate_decision": "accepted"}
        self.compile_error: Exception | None = None
        self.verify_error: Exception | None = None

    async def task_start(self, **kwargs):
        self.calls.append(("task_start", kwargs))
        return {"ok": True}

    async def compile_constraints(self, payload, session_id=""):
        self.calls.append(("compile", {"payload": payload, "session_id": session_id}))
        if self.compile_error:
            raise self.compile_error
        return {"protocol_text": "## FormSy Constraint Protocol\n- State: PATCH_ALLOWED_WITH_WARNINGS"}

    async def observe(self, payload, session_id=""):
        self.calls.append(("observe", {"payload": payload, "session_id": session_id}))
        return {"ok": True}

    async def verify_completion(self, payload, session_id=""):
        self.calls.append(("verify", {"payload": payload, "session_id": session_id}))
        if self.verify_error:
            raise self.verify_error
        return self.verify_response

    async def recover(self, payload, session_id=""):
        self.calls.append(("recover", {"payload": payload, "session_id": session_id}))
        return {"protocol_text": "recover now"}

    async def status(self, task_id, run_id, session_id=""):
        self.calls.append(("status", {"task_id": task_id, "run_id": run_id, "session_id": session_id}))
        return {"task_id": task_id, "run_id": run_id}


def _identity():
    return derive_formsy_identity(
        session_id="sess-1",
        task_id="task-1",
        run_id="run-1",
        repo_id="repo-1",
        revision="rev-1",
        workspace_id="ws-1",
    )


def test_coordinator_lazy_starts_task_once(tmp_path):
    client = FakeClient()
    coordinator = ConstraintKeeperCoordinator(client=client, spool_root=tmp_path, identity=_identity())

    coordinator.ensure_task_started()
    coordinator.ensure_task_started()

    assert [name for name, _ in client.calls] == ["task_start"]


def test_compile_context_bundle_returns_protocol_text(tmp_path):
    coordinator = ConstraintKeeperCoordinator(client=FakeClient(), spool_root=tmp_path, identity=_identity())

    text = coordinator.compile_context_bundle(
        query="forms model save",
        instruction="fix the bug",
        query_plan={"query": "forms model save"},
        context_bundle={"bundle_id": "bundle-1"},
        search_payload={"matches": [{"path": "django/forms/models.py"}]},
    )

    assert text.startswith("## FormSy Constraint Protocol")
    assert coordinator.latest_protocol_text == text


def test_compile_context_bundle_renders_server_protocol_bundle(tmp_path):
    class ServerShapeClient(FakeClient):
        async def compile_constraints(self, payload, session_id=""):
            self.calls.append(("compile", {"payload": payload, "session_id": session_id}))
            return {
                "task_id": "task-1",
                "run_id": "run-1",
                "decision": "PATCH_ALLOWED_WITH_WARNINGS",
                "protocol": {
                    "state": "PATCH_ALLOWED_WITH_WARNINGS",
                    "gate_decision": "PATCH_ALLOWED_WITH_WARNINGS",
                    "summary": "Patch allowed on accepted target.",
                    "blocking_conditions": ["Do not edit unrelated files."],
                    "required_next_actions": ["Edit lib/ansible/executor/play_iterator.py."],
                    "suggested_queries": ["tests covering PlayIterator states"],
                },
            }

    coordinator = ConstraintKeeperCoordinator(client=ServerShapeClient(), spool_root=tmp_path, identity=_identity())

    text = coordinator.compile_context_bundle(
        query="PlayIterator states",
        instruction="standardize state representation",
        query_plan={"retrieval_mode": "symbolic"},
        context_bundle={"bundle_id": "bundle-1"},
        search_payload={"matches": [{"path": "lib/ansible/executor/play_iterator.py"}]},
    )

    assert text.startswith("## FormSy Constraint Protocol")
    assert "- State: PATCH_ALLOWED_WITH_WARNINGS" in text
    assert "- Decision: PATCH_ALLOWED_WITH_WARNINGS" in text
    assert "Do not edit unrelated files." in text
    assert "Edit lib/ansible/executor/play_iterator.py." in text
    assert coordinator.latest_protocol_text == text


def test_transform_tool_result_injects_new_protocol_once(tmp_path):
    coordinator = ConstraintKeeperCoordinator(client=FakeClient(), spool_root=tmp_path, identity=_identity())
    coordinator.latest_protocol_text = "## FormSy Constraint Protocol\n- State: RECOVERY_OPEN"

    first = coordinator.transform_tool_result("terminal", {}, "original", session_id="sess-1")
    second = coordinator.transform_tool_result("terminal", {}, "original", session_id="sess-1")

    assert first == "original\n\n## FormSy Constraint Protocol\n- State: RECOVERY_OPEN"
    assert second is None


def test_pre_llm_call_context_returns_short_recovery_reminder(tmp_path):
    coordinator = ConstraintKeeperCoordinator(client=FakeClient(), spool_root=tmp_path, identity=_identity())
    coordinator.latest_protocol_text = "## FormSy Constraint Protocol\n- State: RECOVERY_OPEN\n- Required: rerun context_search"
    coordinator.recovery_open = True

    context = coordinator.pre_llm_call_context(session_id="sess-1")

    assert context == {
        "context": (
            "FormSy recovery is still open. Follow the latest Constraint Protocol "
            "before editing or final submission."
        )
    }


def test_pre_llm_call_context_bootstraps_context_search_once(tmp_path):
    coordinator = ConstraintKeeperCoordinator(client=FakeClient(), spool_root=tmp_path, identity=_identity())
    coordinator.on_user_turn(
        user_message=(
            "<pr_description>\n# Title\nFix PlayIterator public states\n</pr_description>"
        ),
        session_id="sess-1",
    )

    first = coordinator.pre_llm_call_context(session_id="sess-1")
    second = coordinator.pre_llm_call_context(session_id="sess-1")

    assert first is not None
    assert "FormSy recommended next action" in first["context"]
    assert "Action ID: grounding.seed.1" in first["context"]
    assert 'Call: context_search({"query": "Fix PlayIterator public states"})' in first["context"]
    assert "advisory" in first["context"].lower()
    assert second is None


def test_pre_llm_seed_logs_delivery_observability_once(tmp_path, caplog):
    coordinator = ConstraintKeeperCoordinator(client=FakeClient(), spool_root=tmp_path, identity=_identity())
    coordinator.on_user_turn(
        user_message=(
            "<pr_description>\n# Title\nFix PlayIterator public states\n</pr_description>"
        ),
        session_id="sess-1",
    )

    with caplog.at_level(logging.INFO, logger="formsy.constraint_keeper"):
        first = coordinator.pre_llm_call_context(session_id="sess-1")
        second = coordinator.pre_llm_call_context(session_id="sess-1")

    assert first is not None
    assert second is None
    delivery_records = [
        record for record in caplog.records
        if "event=pre_llm_projection_delivered" in record.getMessage()
    ]
    assert len(delivery_records) == 1
    message = delivery_records[0].getMessage()
    assert "action_id=grounding.seed.1" in message
    assert "surface=pre_llm" in message
    assert "session_id=sess-1" in message
    assert "task_id=" in message
    assert "run_id=" in message
    assert "delivery_count=1" in message
    assert "context_len=" in message
    assert "context_hash=" in message


def test_pre_llm_seed_logs_advisory_uptake_miss_on_first_effective_deviation(tmp_path, caplog):
    coordinator = ConstraintKeeperCoordinator(client=FakeClient(), spool_root=tmp_path, identity=_identity())
    coordinator.on_user_turn(
        user_message=(
            "<pr_description>\n# Title\nFix PlayIterator public states\n</pr_description>"
        ),
        session_id="sess-1",
    )

    with caplog.at_level(logging.INFO, logger="formsy.constraint_keeper"):
        seed = coordinator.pre_llm_call_context(session_id="sess-1")
        coordinator.observe_tool_result(
            "read_file",
            {"path": "lib/ansible/executor/play_iterator.py"},
            '{"content": "source"}',
            session_id="sess-1",
        )
        coordinator.observe_tool_result(
            "search_files",
            {"pattern": "ITERATING_", "path": "lib/ansible"},
            '{"total_count": 3}',
            session_id="sess-1",
        )

    assert seed is not None
    missed_records = [
        record for record in caplog.records
        if "event=advisory_uptake_missed" in record.getMessage()
    ]
    assert len(missed_records) == 1
    message = missed_records[0].getMessage()
    assert "action_id=grounding.seed.1" in message
    assert "expected_tool=context_search" in message
    assert "actual_tool=read_file" in message
    assert "session_id=sess-1" in message
    assert "task_id=" in message
    assert "run_id=" in message
    assert "deviation_count=1" in message
    assert "delivery_count=1" in message


def test_server_next_tool_directive_logs_stable_fallback_action_id_on_uptake_miss(tmp_path, caplog):
    coordinator = ConstraintKeeperCoordinator(client=FakeClient(), spool_root=tmp_path, identity=_identity())
    guidance_result = {
        "ok": True,
        "guidance_packet": {
            "mode": "degraded_recovery",
            "required_next_tool": {
                "tool": "context_read",
                "args": {"path": "lib/ansible/executor/play_iterator.py"},
                "reason": "Validate hinted target.",
            },
            "probe_budget": {
                "search_files": 1,
                "read_file": 2,
                "terminal_or_execute_code": 2,
            },
        },
    }

    with caplog.at_level(logging.INFO, logger="formsy.constraint_keeper"):
        coordinator.observe_tool_result(
            "context_search",
            {"query": "PlayIterator states"},
            guidance_result,
            session_id="sess-1",
        )
        coordinator.transform_tool_result(
            "context_search",
            {},
            "context-search-result",
            session_id="sess-1",
        )
        coordinator.observe_tool_result(
            "read_file",
            {"path": "lib/ansible/plugins/strategy/__init__.py"},
            '{"content": "source"}',
            session_id="sess-1",
        )

    missed_records = [
        record for record in caplog.records
        if "event=advisory_uptake_missed" in record.getMessage()
    ]
    assert len(missed_records) == 1
    message = missed_records[0].getMessage()
    assert "action_id=context_read.next" in message
    assert "expected_tool=context_read" in message
    assert "actual_tool=read_file" in message


def test_server_next_tool_directive_logs_same_target_read_file_fallback_satisfied(tmp_path, caplog):
    coordinator = ConstraintKeeperCoordinator(client=FakeClient(), spool_root=tmp_path, identity=_identity())
    guidance_result = {
        "ok": True,
        "guidance_packet": {
            "mode": "degraded_recovery",
            "required_next_tool": {
                "tool": "context_read",
                "args": {"path": "lib/ansible/executor/play_iterator.py"},
                "reason": "Validate hinted target.",
            },
            "probe_budget": {
                "search_files": 1,
                "read_file": 2,
                "terminal_or_execute_code": 2,
            },
        },
    }

    with caplog.at_level(logging.INFO, logger="formsy.constraint_keeper"):
        coordinator.observe_tool_result(
            "context_search",
            {"query": "PlayIterator states"},
            guidance_result,
            session_id="sess-1",
        )
        coordinator.transform_tool_result(
            "context_search",
            {},
            "context-search-result",
            session_id="sess-1",
        )
        coordinator.observe_tool_result(
            "read_file",
            {"path": "/Users/wayneliu/dev/ansible/lib/ansible/executor/play_iterator.py"},
            '{"content": "source"}',
            session_id="sess-1",
        )

    satisfied_records = [
        record for record in caplog.records
        if "event=advisory_uptake_satisfied_via_fallback" in record.getMessage()
    ]
    assert len(satisfied_records) == 1
    message = satisfied_records[0].getMessage()
    assert "action_id=context_read.next" in message
    assert "expected_tool=context_read" in message
    assert "actual_tool=read_file" in message
    assert "path=lib/ansible/executor/play_iterator.py" in message
    assert "session_id=sess-1" in message
    assert "task_id=task-1" in message
    assert "run_id=run-1" in message

    missed_records = [
        record for record in caplog.records
        if "event=advisory_uptake_missed" in record.getMessage()
    ]
    assert missed_records == []


def test_pre_llm_seed_materializes_pending_action_and_prefixes_missed_reminder(tmp_path):
    coordinator = ConstraintKeeperCoordinator(client=FakeClient(), spool_root=tmp_path, identity=_identity())
    coordinator.on_user_turn(
        user_message=(
            "<pr_description>\n# Title\nStandardize `PlayIterator` state representation "
            "with a public type and preserve backward compatibility\n</pr_description>"
        ),
        session_id="sess-1",
    )

    seed = coordinator.pre_llm_call_context(session_id="sess-1")
    assert seed is not None
    assert "Action ID: grounding.seed.1" in seed["context"]

    coordinator.observe_tool_result(
        "read_file",
        {"path": "lib/ansible/executor/play_iterator.py"},
        '{"content": "source"}',
        session_id="sess-1",
    )
    transformed = coordinator.transform_tool_result(
        "read_file",
        {},
        "large-source-output",
        session_id="sess-1",
    )

    assert transformed is not None
    assert transformed.startswith("FormSy next action still pending")
    assert "\n---\n\nlarge-source-output" in transformed
    assert "FormSy recommended next action" not in transformed
    assert "Standardize `PlayIterator` state representation" in transformed


def test_context_search_satisfies_pre_llm_seed_pending_action(tmp_path):
    coordinator = ConstraintKeeperCoordinator(client=FakeClient(), spool_root=tmp_path, identity=_identity())
    coordinator.on_user_turn(
        user_message="<pr_description>Fix PlayIterator public states</pr_description>",
        session_id="sess-1",
    )
    assert coordinator.pre_llm_call_context(session_id="sess-1") is not None

    coordinator.observe_tool_result(
        "context_search",
        {"query": "Fix PlayIterator public states"},
        '{"ok": true}',
        session_id="sess-1",
    )
    assert coordinator.transform_tool_result(
        "context_search", {}, "context-search-result", session_id="sess-1"
    ) is None

    coordinator.observe_tool_result(
        "read_file",
        {"path": "lib/ansible/executor/play_iterator.py"},
        '{"content": "source"}',
        session_id="sess-1",
    )
    assert coordinator.transform_tool_result(
        "read_file", {}, "read-result", session_id="sess-1"
    ) is None


def test_skill_view_marks_formsy_context_skill_body_loaded(tmp_path, caplog):
    coordinator = ConstraintKeeperCoordinator(client=FakeClient(), spool_root=tmp_path, identity=_identity())

    with caplog.at_level(logging.INFO, logger="formsy.constraint_keeper"):
        coordinator.observe_tool_result(
            "skill_view",
            {"name": "formsy-context"},
            "## FormSy Context\nUse context_search first.",
            session_id="sess-1",
        )

    assert coordinator.get_skill_uptake_status() == {
        "skill_name": "formsy-context",
        "skill_visibility": "skill_view_loaded",
        "skill_body_loaded": True,
    }
    assert "event=skill_uptake_observed" in caplog.text
    assert "skill_visibility=skill_view_loaded" in caplog.text


def test_pre_llm_bootstrap_projects_formsy_context_skill_capsule(tmp_path, caplog):
    coordinator = ConstraintKeeperCoordinator(client=FakeClient(), spool_root=tmp_path, identity=_identity())
    coordinator.on_user_turn(
        user_message="<pr_description>Fix PlayIterator public states</pr_description>",
        session_id="sess-1",
    )

    with caplog.at_level(logging.INFO, logger="formsy.constraint_keeper"):
        context = coordinator.pre_llm_call_context(session_id="sess-1")

    assert context is not None
    assert "FormSy Context skill capsule" in context["context"]
    assert "Use context_search before broad source exploration" in context["context"]
    assert coordinator.get_skill_uptake_status() == {
        "skill_name": "formsy-context",
        "skill_visibility": "plugin_projected",
        "skill_body_loaded": True,
    }
    assert "event=skill_uptake_observed" in caplog.text
    assert "skill_visibility=plugin_projected" in caplog.text


def test_context_search_observation_suppresses_bootstrap_guidance(tmp_path):
    coordinator = ConstraintKeeperCoordinator(client=FakeClient(), spool_root=tmp_path, identity=_identity())
    coordinator.on_user_turn(
        user_message="<pr_description>Fix PlayIterator public states</pr_description>",
        session_id="sess-1",
    )
    coordinator.observe_tool_result(
        "context_search",
        {"query": "Fix PlayIterator public states"},
        '{"ok": true}',
        session_id="sess-1",
    )

    assert coordinator.pre_llm_call_context(session_id="sess-1") is None


def test_bootstrap_gate_does_not_block_broad_exploration_without_context_search(tmp_path):
    coordinator = ConstraintKeeperCoordinator(client=FakeClient(), spool_root=tmp_path, identity=_identity())

    assert coordinator.pre_tool_call_block_message(
        "terminal",
        {"command": "pwd && ls"},
        session_id="sess-1",
    ) is None
    coordinator.observe_tool_result(
        "terminal",
        {"command": "pwd && ls"},
        '{"exit_code": 0, "output": "/repo"}',
        session_id="sess-1",
    )

    assert coordinator.pre_tool_call_block_message(
        "read_file",
        {"path": "lib/ansible/executor/play_iterator.py"},
        session_id="sess-1",
    ) is None
    coordinator.observe_tool_result(
        "read_file",
        {"path": "lib/ansible/executor/play_iterator.py"},
        '{"content": "source"}',
        session_id="sess-1",
    )

    message = coordinator.pre_tool_call_block_message(
        "search_files",
        {"pattern": "ITERATING_", "path": "lib/ansible"},
        session_id="sess-1",
    )

    assert message is None


def test_bootstrap_gate_does_not_count_allowed_pre_calls_into_a_block(tmp_path):
    coordinator = ConstraintKeeperCoordinator(client=FakeClient(), spool_root=tmp_path, identity=_identity())

    first = coordinator.pre_tool_call_block_message(
        "read_file",
        {"path": "lib/ansible/executor/play_iterator.py"},
        session_id="sess-1",
    )
    orientation = coordinator.pre_tool_call_block_message(
        "terminal",
        {"command": "pwd && ls -la"},
        session_id="sess-1",
    )
    second = coordinator.pre_tool_call_block_message(
        "search_files",
        {"pattern": "ITERATING_", "path": "lib/ansible"},
        session_id="sess-1",
    )

    assert first is None
    assert orientation is None
    assert second is None


def test_bootstrap_gate_does_not_block_patch_until_context_search(tmp_path):
    coordinator = ConstraintKeeperCoordinator(client=FakeClient(), spool_root=tmp_path, identity=_identity())

    blocked = coordinator.pre_tool_call_block_message(
        "patch",
        {"path": "lib/ansible/executor/play_iterator.py"},
        session_id="sess-1",
    )
    coordinator.observe_tool_result(
        "context_search",
        {"query": "PlayIterator public state type compatibility"},
        '{"ok": true}',
        session_id="sess-1",
    )
    unblocked = coordinator.pre_tool_call_block_message(
        "patch",
        {"path": "lib/ansible/executor/play_iterator.py"},
        session_id="sess-1",
    )

    assert blocked is None
    assert unblocked is None


def test_bootstrap_gate_does_not_block_context_read_until_seed_context_search(tmp_path):
    coordinator = ConstraintKeeperCoordinator(client=FakeClient(), spool_root=tmp_path, identity=_identity())

    message = coordinator.pre_tool_call_block_message(
        "context_read",
        {"path": "lib/ansible/executor/play_iterator.py"},
        session_id="sess-1",
    )

    assert message is None


def test_final_submit_closes_attempt_without_blocking_next_edit(tmp_path):
    client = FakeClient()
    coordinator = ConstraintKeeperCoordinator(client=client, spool_root=tmp_path, identity=_identity())
    coordinator.observe_tool_result(
        "context_search",
        {"query": "PlayIterator public states"},
        '{"ok": true}',
        session_id="sess-1",
    )
    coordinator.latest_protocol_text = "## FormSy Constraint Protocol\n- State: PATCH_ALLOWED_WITH_WARNINGS"

    assert coordinator.pre_tool_call_block_message(
        "terminal",
        {"command": "echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT && cat patch.txt"},
        session_id="sess-1",
    ) is None
    coordinator.observe_tool_result(
        "terminal",
        {"command": "echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT && cat patch.txt"},
        '{"exit_code": 0, "output": "COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"}',
        session_id="sess-1",
    )

    message = coordinator.pre_tool_call_block_message(
        "patch",
        {"path": "lib/ansible/executor/play_iterator.py"},
        session_id="sess-1",
    )

    assert message is None
    assert coordinator.latest_protocol_text == ""


def test_final_submit_after_closed_attempt_does_not_block_bookkeeping_submit(tmp_path):
    coordinator = ConstraintKeeperCoordinator(client=FakeClient(), spool_root=tmp_path, identity=_identity())
    coordinator.observe_tool_result(
        "context_search",
        {"query": "PlayIterator public states"},
        '{"ok": true}',
        session_id="sess-1",
    )
    coordinator.observe_tool_result(
        "terminal",
        {"command": "echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT && cat patch.txt"},
        '{"exit_code": 0, "output": "COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"}',
        session_id="sess-1",
    )

    message = coordinator.pre_tool_call_block_message(
        "terminal",
        {"command": "echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT && cat /tmp/patch.txt"},
        session_id="sess-1",
    )

    assert message is None


def test_post_completion_patch_inspection_does_not_emit_grounding_card(tmp_path):
    coordinator = ConstraintKeeperCoordinator(client=FakeClient(), spool_root=tmp_path, identity=_identity())
    coordinator.observe_tool_result(
        "context_search",
        {"query": "PlayIterator public states"},
        '{"ok": true}',
        session_id="sess-1",
    )
    coordinator.observe_tool_result(
        "terminal",
        {"command": "echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT && cat patch.txt"},
        '{"exit_code": 0, "output": "COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"}',
        session_id="sess-1",
    )

    assert coordinator.pre_tool_call_block_message(
        "terminal",
        {"command": "cat /Users/wayneliu/dev/ansible/patch.txt"},
        session_id="sess-1",
    ) is None
    coordinator.observe_tool_result(
        "terminal",
        {"command": "cat /Users/wayneliu/dev/ansible/patch.txt"},
        '{"exit_code": 0, "output": "diff --git a/lib/ansible/executor/play_iterator.py"}',
        session_id="sess-1",
    )

    assert coordinator.transform_tool_result(
        "terminal",
        {},
        '{"output": "diff --git a/lib/ansible/executor/play_iterator.py"}',
        session_id="sess-1",
    ) is None


def test_context_read_after_closed_attempt_is_advisory_not_blocked(tmp_path):
    coordinator = ConstraintKeeperCoordinator(client=FakeClient(), spool_root=tmp_path, identity=_identity())
    coordinator.observe_tool_result(
        "context_search",
        {"query": "PlayIterator public states"},
        '{"ok": true}',
        session_id="sess-1",
    )
    coordinator.observe_tool_result(
        "terminal",
        {"command": "echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT && cat patch.txt"},
        '{"exit_code": 0, "output": "COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"}',
        session_id="sess-1",
    )

    read_message = coordinator.pre_tool_call_block_message(
        "context_read",
        {"path": "lib/ansible/executor/play_iterator.py"},
        session_id="sess-1",
    )
    patch_message = coordinator.pre_tool_call_block_message(
        "patch",
        {"path": "lib/ansible/executor/play_iterator.py"},
        session_id="sess-1",
    )

    assert read_message is None
    assert patch_message is None


def test_pre_llm_after_closed_attempt_returns_bootstrap_guidance(tmp_path):
    coordinator = ConstraintKeeperCoordinator(client=FakeClient(), spool_root=tmp_path, identity=_identity())
    coordinator.observe_tool_result(
        "context_search",
        {"query": "PlayIterator public states"},
        '{"ok": true}',
        session_id="sess-1",
    )
    coordinator.observe_tool_result(
        "terminal",
        {"command": "echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT && cat patch.txt"},
        '{"exit_code": 0, "output": "COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"}',
        session_id="sess-1",
    )

    context = coordinator.pre_llm_call_context(session_id="sess-1")

    assert context is not None
    assert "context_search" in context["context"]


def test_closed_attempt_source_read_does_not_emit_grounding_without_new_attempt(tmp_path):
    coordinator = ConstraintKeeperCoordinator(client=FakeClient(), spool_root=tmp_path, identity=_identity())
    coordinator.observe_tool_result(
        "context_search",
        {"query": "PlayIterator public states"},
        '{"ok": true}',
        session_id="sess-1",
    )
    coordinator.observe_tool_result(
        "terminal",
        {"command": "echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT && cat patch.txt"},
        '{"exit_code": 0, "output": "COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"}',
        session_id="sess-1",
    )

    assert coordinator.pre_tool_call_block_message(
        "terminal",
        {"command": "pwd && ls -la"},
        session_id="sess-1",
    ) is None
    coordinator.observe_tool_result(
        "terminal",
        {"command": "pwd && ls -la"},
        '{"exit_code": 0, "output": "/repo"}',
        session_id="sess-1",
    )
    assert coordinator.transform_tool_result(
        "terminal", {}, "terminal-result", session_id="sess-1"
    ) is None

    coordinator.observe_tool_result(
        "read_file",
        {"path": "lib/ansible/executor/play_iterator.py"},
        '{"content": "source"}',
        session_id="sess-1",
    )
    first = coordinator.transform_tool_result(
        "read_file", {}, "read-result", session_id="sess-1"
    )
    second = coordinator.transform_tool_result(
        "read_file", {}, "read-result", session_id="sess-1"
    )

    assert first is None
    assert second is None


def test_grounding_action_card_prefers_task_title_over_tool_noise(tmp_path):
    coordinator = ConstraintKeeperCoordinator(client=FakeClient(), spool_root=tmp_path, identity=_identity())
    coordinator.on_user_turn(
        user_message=(
            "<pr_description>\n# Title\nStandardize `PlayIterator` state representation "
            "with a public type and preserve backward compatibility\n</pr_description>"
        ),
        session_id="sess-1",
    )
    coordinator.observe_tool_result(
        "terminal",
        {
            "command": (
                "cd /Users/wayneliu/dev/ansible && grep -n "
                '"class PlayIteratorRunState" lib/ansible/executor/play_iterator.py'
            )
        },
        '{"exit_code": 1, "output": ""}',
        session_id="sess-1",
    )

    card = coordinator.transform_tool_result(
        "terminal", {}, "terminal-result", session_id="sess-1"
    )

    assert card is not None
    assert "Standardize `PlayIterator` state representation" in card
    assert "/Users/wayneliu/dev/ansible" not in card
    assert "grep -n" not in card
    assert "lib/ansible/executor/play_iterator.py" not in card


def test_grounding_action_card_sanitizes_tool_query_when_task_text_missing(tmp_path):
    coordinator = ConstraintKeeperCoordinator(client=FakeClient(), spool_root=tmp_path, identity=_identity())
    coordinator.observe_tool_result(
        "terminal",
        {
            "command": (
                "cd /Users/wayneliu/dev/ansible && grep -n "
                '"class PlayIteratorRunState" lib/ansible/executor/play_iterator.py'
            )
        },
        '{"exit_code": 1, "output": ""}',
        session_id="sess-1",
    )

    card = coordinator.transform_tool_result(
        "terminal", {}, "terminal-result", session_id="sess-1"
    )

    assert card is not None
    assert "PlayIteratorRunState" in card
    assert "lib/ansible/executor/play_iterator.py" in card
    assert "/Users/wayneliu/dev/ansible" not in card
    assert "grep -n" not in card


def test_grounding_action_card_followup_source_read_gets_one_pending_next_action_reminder(tmp_path):
    coordinator = ConstraintKeeperCoordinator(client=FakeClient(), spool_root=tmp_path, identity=_identity())
    coordinator.on_user_turn(
        user_message=(
            "<pr_description>\n# Title\nStandardize `PlayIterator` state representation "
            "with a public type and preserve backward compatibility\n</pr_description>"
        ),
        session_id="sess-1",
    )

    coordinator.observe_tool_result(
        "read_file",
        {"path": "lib/ansible/executor/play_iterator.py"},
        '{"content": "source"}',
        session_id="sess-1",
    )
    first = coordinator.transform_tool_result(
        "read_file", {}, "read-result", session_id="sess-1"
    )
    assert first is not None
    assert first.startswith("FormSy grounding action card")
    assert "\n---\n\nread-result" in first
    assert "FormSy grounding action card" in first
    assert "Recommended next tool call: context_search" in first

    coordinator.observe_tool_result(
        "read_file",
        {"path": "lib/ansible/executor/play_iterator.py"},
        '{"content": "source again"}',
        session_id="sess-1",
    )
    reminder = coordinator.transform_tool_result(
        "read_file", {}, "read-result-2", session_id="sess-1"
    )

    assert reminder is not None
    assert reminder.startswith("FormSy next action still pending")
    assert "\n---\n\nread-result-2" in reminder
    assert "FormSy next action still pending" in reminder
    assert "Recommended next tool call: context_search" in reminder
    assert "Standardize `PlayIterator` state representation" in reminder
    assert "advisory only" in reminder

    coordinator.observe_tool_result(
        "search_files",
        {"path": "lib/ansible/executor/play_iterator.py", "pattern": "PlayIterator"},
        '{"matches": []}',
        session_id="sess-1",
    )
    assert coordinator.transform_tool_result(
        "search_files", {}, "search-result", session_id="sess-1"
    ) is None


def test_grounding_pending_next_action_is_satisfied_by_context_search(tmp_path):
    coordinator = ConstraintKeeperCoordinator(client=FakeClient(), spool_root=tmp_path, identity=_identity())
    coordinator.on_user_turn(
        user_message=(
            "<pr_description>\n# Title\nStandardize `PlayIterator` state representation "
            "with a public type and preserve backward compatibility\n</pr_description>"
        ),
        session_id="sess-1",
    )
    coordinator.observe_tool_result(
        "read_file",
        {"path": "lib/ansible/executor/play_iterator.py"},
        '{"content": "source"}',
        session_id="sess-1",
    )
    assert coordinator.transform_tool_result("read_file", {}, "read-result", session_id="sess-1")

    coordinator.observe_tool_result(
        "context_search",
        {"query": "Standardize `PlayIterator` state representation"},
        '{"ok": true}',
        session_id="sess-1",
    )
    assert coordinator.transform_tool_result(
        "context_search", {}, "context-search-result", session_id="sess-1"
    ) is None

    coordinator.observe_tool_result(
        "read_file",
        {"path": "lib/ansible/executor/play_iterator.py"},
        '{"content": "source again"}',
        session_id="sess-1",
    )
    assert coordinator.transform_tool_result(
        "read_file", {}, "read-result-2", session_id="sess-1"
    ) is None


def test_unknown_terminal_does_not_consume_pending_next_action_reminder(tmp_path):
    coordinator = ConstraintKeeperCoordinator(client=FakeClient(), spool_root=tmp_path, identity=_identity())
    coordinator.on_user_turn(
        user_message=(
            "<pr_description>\n# Title\nStandardize `PlayIterator` state representation "
            "with a public type and preserve backward compatibility\n</pr_description>"
        ),
        session_id="sess-1",
    )
    coordinator.observe_tool_result(
        "read_file",
        {"path": "lib/ansible/executor/play_iterator.py"},
        '{"content": "source"}',
        session_id="sess-1",
    )
    assert coordinator.transform_tool_result("read_file", {}, "read-result", session_id="sess-1")

    coordinator.observe_tool_result(
        "terminal",
        {"command": "rg PlayIterator lib/ansible/executor/play_iterator.py"},
        '{"exit_code": 0, "output": "matches"}',
        session_id="sess-1",
    )
    assert coordinator.transform_tool_result(
        "terminal", {}, "terminal-result", session_id="sess-1"
    ) is None

    coordinator.observe_tool_result(
        "search_files",
        {"path": "lib/ansible/executor/play_iterator.py", "pattern": "PlayIterator"},
        '{"matches": []}',
        session_id="sess-1",
    )
    reminder = coordinator.transform_tool_result(
        "search_files", {}, "search-result", session_id="sess-1"
    )
    assert reminder is not None
    assert "FormSy next action still pending" in reminder


def test_compile_context_bundle_marks_retrieval_seen_for_guidance(tmp_path):
    coordinator = ConstraintKeeperCoordinator(client=FakeClient(), spool_root=tmp_path, identity=_identity())
    coordinator.on_user_turn(
        user_message="<pr_description>Fix PlayIterator public states</pr_description>",
        session_id="sess-1",
    )

    text = coordinator.compile_context_bundle(
        query="Fix PlayIterator public states",
        instruction="fix the bug",
        query_plan={"retrieval_mode": "symbolic"},
        context_bundle={"bundle_id": "bundle-1"},
        search_payload={"matches": [{"path": "lib/ansible/executor/play_iterator.py"}]},
        session_id="sess-1",
    )

    assert text.startswith("## FormSy Constraint Protocol")
    assert coordinator.pre_llm_call_context(session_id="sess-1") is None


def test_transform_tool_result_injects_guidance_after_exploration_without_context_search(tmp_path):
    coordinator = ConstraintKeeperCoordinator(client=FakeClient(), spool_root=tmp_path, identity=_identity())
    coordinator.on_user_turn(
        user_message="<pr_description>Fix PlayIterator public states</pr_description>",
        session_id="sess-1",
    )
    # The initial bootstrap was not enough; the agent is exploring with shell/read tools.
    coordinator.pre_llm_call_context(session_id="sess-1")
    coordinator.observe_tool_result("terminal", {"command": "grep -R PlayIterator lib/ansible"}, "matches", session_id="sess-1")
    coordinator.observe_tool_result("read_file", {"path": "lib/ansible/executor/play_iterator.py"}, "source", session_id="sess-1")
    coordinator.observe_tool_result("terminal", {"command": "grep -R FAILED_SETUP lib/ansible"}, "matches", session_id="sess-1")

    first = coordinator.transform_tool_result("terminal", {}, "original", session_id="sess-1")
    second = coordinator.transform_tool_result("terminal", {}, "original", session_id="sess-1")

    assert first is not None
    assert first.startswith("FormSy next action still pending")
    assert "\n---\n\noriginal" in first
    assert "context_search" in first
    assert "Action ID: grounding.seed.1" in first
    assert "Recommended next tool call" in first
    assert "Completion Gate will verify" in first
    assert second is None


def test_transform_tool_result_injects_guidance_after_repeated_terminal_failures(tmp_path):
    client = FakeClient()
    coordinator = ConstraintKeeperCoordinator(client=client, spool_root=tmp_path, identity=_identity())
    coordinator.on_user_turn(
        user_message="<pr_description>Fix PlayIterator public states</pr_description>",
        session_id="sess-1",
    )
    result = '{"output": "ModuleNotFoundError: ansible.executor is not a package", "exit_code": 1}'
    for _ in range(3):
        coordinator.observe_tool_result(
            "terminal",
            {"command": "python3 -c 'from ansible.executor.play_iterator import PlayIterator'"},
            result,
            session_id="sess-1",
        )

    transformed = coordinator.transform_tool_result("terminal", {}, "original", session_id="sess-1")

    assert transformed is not None
    assert "Repeated terminal failures" in transformed
    assert "context_search" in transformed
    assert "formsy_recover" in transformed
    assert [name for name, _ in client.calls if name == "recover"] == []


def test_transform_tool_result_injects_guidance_after_repeated_execute_code_probes(tmp_path):
    coordinator = ConstraintKeeperCoordinator(client=FakeClient(), spool_root=tmp_path, identity=_identity())
    coordinator.on_user_turn(
        user_message="<pr_description>Fix PlayIterator public states</pr_description>",
        session_id="sess-1",
    )
    coordinator.observe_tool_result(
        "context_search",
        {"query": "Fix PlayIterator public states"},
        '{"ok": true}',
        session_id="sess-1",
    )

    for idx in range(5):
        coordinator.observe_tool_result(
            "execute_code",
            {"code": f"print({idx})"},
            '{"status": "success", "output": "ok"}',
            session_id="sess-1",
        )

    transformed = coordinator.transform_tool_result("execute_code", {}, "original", session_id="sess-1")
    repeat = coordinator.transform_tool_result("execute_code", {}, "original", session_id="sess-1")

    assert transformed is not None
    assert "Repeated isolated code probes" in transformed
    assert "patch" in transformed
    assert "without a context_search result" not in transformed
    assert repeat is None


def test_compile_context_bundle_failure_returns_warning(tmp_path):
    client = FakeClient()
    client.compile_error = RuntimeError("server down")
    coordinator = ConstraintKeeperCoordinator(client=client, spool_root=tmp_path, identity=_identity())

    text = coordinator.compile_context_bundle(
        query="forms model save",
        instruction="fix the bug",
        query_plan={"query": "forms model save"},
        context_bundle={"bundle_id": "bundle-1"},
        search_payload={"matches": [{"path": "django/forms/models.py"}]},
    )

    assert "Constraint Protocol compilation unavailable" in text


def test_observe_tool_result_queues_validation_success(tmp_path):
    coordinator = ConstraintKeeperCoordinator(client=FakeClient(), spool_root=tmp_path, identity=_identity())

    coordinator.observe_tool_result(
        "terminal",
        {"command": "python -m pytest tests/forms", "exit_code": 0},
        "1 passed",
        session_id="sess-1",
    )

    pending = coordinator.spool.pending("task-1", "run-1")
    assert pending[0]["event_kind"] == "test_result"
    assert pending[0]["payload"]["passed"] is True


def test_observe_tool_result_queues_python_compile_validation_success(tmp_path):
    coordinator = ConstraintKeeperCoordinator(client=FakeClient(), spool_root=tmp_path, identity=_identity())

    coordinator.observe_tool_result(
        "terminal",
        {
            "command": (
                "cd /Users/wayneliu/dev/ansible && python3 -m py_compile "
                "lib/ansible/executor/play_iterator.py && echo Syntax OK"
            ),
            "exit_code": 0,
        },
        '{"output": "Syntax OK", "exit_code": 0, "error": null}',
        session_id="sess-1",
    )

    pending = coordinator.spool.pending("task-1", "run-1")
    assert pending[0]["event_kind"] == "test_result"
    assert pending[0]["payload"]["passed"] is True
    assert "py_compile" in pending[0]["payload"]["command"]


def test_observe_tool_result_captures_diff_after_edit_surface(tmp_path):
    coordinator = ConstraintKeeperCoordinator(
        client=FakeClient(),
        spool_root=tmp_path,
        identity=_identity(),
        diff_provider=lambda: "diff --git a/app.py b/app.py\n--- a/app.py\n+++ b/app.py\n",
    )

    coordinator.observe_tool_result(
        "apply_patch",
        {"patch": "*** Begin Patch"},
        "ok",
        session_id="sess-1",
    )

    pending = coordinator.spool.pending("task-1", "run-1")
    assert [event["event_kind"] for event in pending] == ["diff_observed"]
    assert pending[0]["payload"]["changed_files"] == ["app.py"]
    assert pending[0]["payload"]["unified_diff"].startswith("diff --git a/app.py b/app.py")


def test_observe_tool_result_attaches_changed_file_source_snapshots(tmp_path):
    coordinator = ConstraintKeeperCoordinator(
        client=FakeClient(),
        spool_root=tmp_path,
        identity=_identity(),
        diff_provider=lambda: "diff --git a/app.py b/app.py\n--- a/app.py\n+++ b/app.py\n",
        source_provider=lambda paths: {"app.py": "print('current')\n"},
    )

    coordinator.observe_tool_result(
        "apply_patch",
        {"patch": "*** Begin Patch"},
        "ok",
        session_id="sess-1",
    )

    pending = coordinator.spool.pending("task-1", "run-1")
    assert pending[0]["payload"]["post_patch_sources"] == {
        "app.py": "print('current')\n"
    }


def test_observe_tool_result_deduplicates_identical_edit_diffs(tmp_path):
    coordinator = ConstraintKeeperCoordinator(
        client=FakeClient(),
        spool_root=tmp_path,
        identity=_identity(),
        diff_provider=lambda: "diff --git a/app.py b/app.py\n--- a/app.py\n+++ b/app.py\n",
    )

    coordinator.observe_tool_result("apply_patch", {"patch": "*** Begin Patch"}, "ok", session_id="sess-1")
    coordinator.observe_tool_result("apply_patch", {"patch": "*** Begin Patch"}, "ok", session_id="sess-1")

    pending = coordinator.spool.pending("task-1", "run-1")
    assert [event["event_kind"] for event in pending] == ["diff_observed"]


def test_verify_completion_flushes_diff_done_claim_then_verifies(tmp_path):
    client = FakeClient()
    coordinator = ConstraintKeeperCoordinator(
        client=client,
        spool_root=tmp_path,
        identity=_identity(),
        diff_provider=lambda: "diff --git a/app.py b/app.py\n--- a/app.py\n+++ b/app.py\n",
    )

    coordinator.observe_tool_result(
        "terminal",
        {"command": "python -m pytest tests/forms", "exit_code": 0},
        "1 passed",
        session_id="sess-1",
    )
    result = coordinator.verify_completion(session_id="sess-1")

    assert result == {"gate_decision": "accepted"}
    observed_kinds = [
        call[1]["payload"]["event"]["event_kind"]
        for call in client.calls
        if call[0] == "observe"
    ]
    assert observed_kinds == ["test_result", "diff_observed", "done_claim"]
    assert client.calls[-1][0] == "verify"


def test_flush_pending_sends_server_compatible_observe_payload(tmp_path):
    client = FakeClient()
    coordinator = ConstraintKeeperCoordinator(client=client, spool_root=tmp_path, identity=_identity())

    coordinator.observe_tool_result(
        "terminal",
        {"command": "python -m pytest tests/forms", "exit_code": 0},
        "1 passed",
        session_id="sess-1",
    )
    coordinator.flush_pending()

    observe_payload = [call[1]["payload"] for call in client.calls if call[0] == "observe"][0]
    assert set(observe_payload) == {"event"}
    assert observe_payload["event"]["task_id"] == "task-1"
    assert observe_payload["event"]["run_id"] == "run-1"
    assert observe_payload["event"]["event_kind"] == "test_result"


def test_observe_tool_result_reports_low_sensitive_tool_observed(tmp_path):
    client = FakeClient()
    coordinator = ConstraintKeeperCoordinator(client=client, spool_root=tmp_path, identity=_identity())

    coordinator.observe_tool_result(
        "read_file",
        {"path": "lib/ansible/executor/play_iterator.py", "offset": 1, "limit": 500},
        '{"content": "source text that must not be reported"}',
        session_id="sess-1",
    )

    observe_payloads = [call[1]["payload"] for call in client.calls if call[0] == "observe"]
    assert len(observe_payloads) == 1
    event = observe_payloads[0]["event"]
    assert event["event_kind"] == "tool_observed"
    assert event["trust"] == "plugin_observed"
    assert event["payload"] == {
        "tool_name": "read_file",
        "path": "lib/ansible/executor/play_iterator.py",
        "offset": 1,
        "limit": 500,
    }


def test_observe_response_protocol_is_injected_after_tool_result(tmp_path):
    class ProtocolClient(FakeClient):
        async def observe(self, payload, session_id=""):
            self.calls.append(("observe", {"payload": payload, "session_id": session_id}))
            return {
                "decision": "PATCH_ALLOWED_WITH_WARNINGS",
                "protocol": {
                    "state": "PATCH_ALLOWED_WITH_WARNINGS",
                    "gate_decision": "PATCH_ALLOWED_WITH_WARNINGS",
                    "summary": "Broad exploration budget reached.",
                    "required_next_actions": [
                        "Call context_search with PlayIterator public state type compatibility."
                    ],
                },
            }

    coordinator = ConstraintKeeperCoordinator(client=ProtocolClient(), spool_root=tmp_path, identity=_identity())

    coordinator.observe_tool_result(
        "search_files",
        {"pattern": "FAILED_", "path": "lib/ansible/plugins/strategy"},
        '{"total_count": 5}',
        session_id="sess-1",
    )
    transformed = coordinator.transform_tool_result(
        "search_files",
        {},
        "original",
        session_id="sess-1",
    )

    assert transformed is not None
    assert "Broad exploration budget reached." in transformed
    assert "Call context_search" in transformed


def test_server_need_context_directive_is_injected_without_blocking_tools(tmp_path):
    class NeedContextClient(FakeClient):
        async def observe(self, payload, session_id=""):
            self.calls.append(("observe", {"payload": payload, "session_id": session_id}))
            return {
                "decision": "NEED_CONTEXT",
                "protocol": {
                    "state": "NEED_CONTEXT",
                    "gate_decision": "NEED_CONTEXT",
                    "summary": "Broad exploration budget is exhausted; refresh context guidance.",
                    "required_next_actions": [
                        "Call context_search with PlayIterator public state type compatibility."
                    ],
                    "suggested_queries": ["PlayIterator public state type compatibility"],
                },
            }

    coordinator = ConstraintKeeperCoordinator(client=NeedContextClient(), spool_root=tmp_path, identity=_identity())

    coordinator.observe_tool_result(
        "read_file",
        {"path": "lib/ansible/executor/play_iterator.py"},
        '{"content": "source"}',
        session_id="sess-1",
    )

    search_message = coordinator.pre_tool_call_block_message(
        "search_files",
        {"pattern": "FAILED_", "path": "lib/ansible"},
        session_id="sess-1",
    )
    edit_message = coordinator.pre_tool_call_block_message(
        "write_file",
        {"path": "lib/ansible/executor/play_iterator.py", "content": "patch"},
        session_id="sess-1",
    )
    transformed = coordinator.transform_tool_result(
        "read_file",
        {"path": "lib/ansible/executor/play_iterator.py"},
        '{"content": "source"}',
        session_id="sess-1",
    )
    coordinator.observe_tool_result(
        "context_search",
        {"query": "PlayIterator public state type compatibility"},
        '{"ok": true}',
        session_id="sess-1",
    )
    unblocked = coordinator.pre_tool_call_block_message(
        "search_files",
        {"pattern": "FAILED_", "path": "lib/ansible"},
        session_id="sess-1",
    )

    assert search_message is None
    assert edit_message is None
    assert transformed is not None
    assert "context_search" in transformed
    assert "PlayIterator public state type compatibility" in transformed
    assert unblocked is None


def test_degraded_guidance_packet_records_probe_budget_without_blocking(tmp_path):
    coordinator = ConstraintKeeperCoordinator(client=FakeClient(), spool_root=tmp_path, identity=_identity())
    guidance_result = {
        "ok": False,
        "recovery_mode": "degraded_recovery",
        "guidance_packet": {
            "mode": "degraded_recovery",
            "target_candidates": ["lib/ansible/executor/play_iterator.py"],
            "probe_budget": {
                "search_files": 1,
                "read_file": 2,
                "terminal_or_execute_code": 2,
            },
            "patch_now_threshold": {"grounded_source_reads": 1},
        },
    }

    coordinator.observe_tool_result(
        "context_search",
        {"query": "PlayIterator states"},
        guidance_result,
        session_id="sess-1",
    )
    assert coordinator.pre_tool_call_block_message(
        "execute_code",
        {"code": "print('probe 1')"},
        session_id="sess-1",
    ) is None
    coordinator.observe_tool_result(
        "execute_code",
        {"code": "print('probe 1')"},
        '{"status": "success"}',
        session_id="sess-1",
    )
    assert coordinator.pre_tool_call_block_message(
        "terminal",
        {"command": "python3 - <<'PY'\nprint('probe 2')\nPY"},
        session_id="sess-1",
    ) is None
    coordinator.observe_tool_result(
        "terminal",
        {"command": "python3 - <<'PY'\nprint('probe 2')\nPY"},
        '{"exit_code": 0, "output": "ok"}',
        session_id="sess-1",
    )

    message = coordinator.pre_tool_call_block_message(
        "execute_code",
        {"code": "print('probe 3')"},
        session_id="sess-1",
    )

    assert message is None


def test_degraded_next_tool_directive_is_suggested_not_blocking(tmp_path):
    coordinator = ConstraintKeeperCoordinator(client=FakeClient(), spool_root=tmp_path, identity=_identity())
    guidance_result = {
        "ok": False,
        "recovery_mode": "degraded_recovery",
        "guidance_packet": {
            "mode": "degraded_recovery",
            "target_candidates": ["lib/ansible/executor/play_iterator.py"],
            "required_next_tool": {
                "tool": "context_read",
                "args": {"path": "lib/ansible/executor/play_iterator.py"},
                "reason": "Validate hinted target.",
            },
            "probe_budget": {
                "search_files": 1,
                "read_file": 2,
                "terminal_or_execute_code": 2,
            },
        },
    }

    coordinator.observe_tool_result(
        "context_search",
        {"query": "PlayIterator states"},
        guidance_result,
        session_id="sess-1",
    )
    message = coordinator.pre_tool_call_block_message(
        "terminal",
        {"command": "git status --short"},
        session_id="sess-1",
    )
    transformed = coordinator.transform_tool_result(
        "context_search",
        {},
        "original",
        session_id="sess-1",
    )

    assert message is None
    assert transformed is not None
    assert "NEXT SUGGESTED TOOL: context_read path=lib/ansible/executor/play_iterator.py" in transformed
    assert "NEXT REQUIRED TOOL" not in transformed


def test_read_file_same_target_satisfies_suggested_context_read_directive(tmp_path):
    coordinator = ConstraintKeeperCoordinator(client=FakeClient(), spool_root=tmp_path, identity=_identity())
    guidance_result = {
        "ok": False,
        "recovery_mode": "degraded_recovery",
        "guidance_packet": {
            "mode": "degraded_recovery",
            "target_candidates": ["lib/ansible/executor/play_iterator.py"],
            "next_tool_directive": {
                "tool": "context_read",
                "args": {"path": "lib/ansible/executor/play_iterator.py"},
                "reason": "Validate hinted target.",
                "enforcement": "suggested",
            },
            "probe_budget": {
                "search_files": 1,
                "read_file": 2,
                "terminal_or_execute_code": 2,
            },
        },
    }

    coordinator.observe_tool_result(
        "context_search",
        {"query": "PlayIterator states"},
        guidance_result,
        session_id="sess-1",
    )
    assert coordinator.transform_tool_result("context_search", {}, "original", session_id="sess-1") is not None

    coordinator.observe_tool_result(
        "read_file",
        {"path": "lib/ansible/executor/play_iterator.py"},
        '{"ok": true, "content": "class HostState: pass"}',
        session_id="sess-1",
    )

    assert coordinator._active_next_tool_directive is None
    assert coordinator.transform_tool_result(
        "read_file",
        {"path": "lib/ansible/executor/play_iterator.py"},
        '{"ok": true, "content": "class HostState: pass"}',
        session_id="sess-1",
    ) is None


def test_context_read_satisfies_suggested_directive_and_clears_pending_card(tmp_path):
    coordinator = ConstraintKeeperCoordinator(client=FakeClient(), spool_root=tmp_path, identity=_identity())
    guidance_result = {
        "ok": False,
        "recovery_mode": "degraded_recovery",
        "guidance_packet": {
            "mode": "degraded_recovery",
            "target_candidates": ["lib/ansible/executor/play_iterator.py"],
            "next_tool_directive": {
                "tool": "context_read",
                "args": {"path": "lib/ansible/executor/play_iterator.py"},
                "reason": "Validate hinted target.",
                "enforcement": "suggested",
            },
            "probe_budget": {
                "search_files": 1,
                "read_file": 2,
                "terminal_or_execute_code": 2,
            },
        },
    }

    coordinator.observe_tool_result(
        "context_search",
        {"query": "PlayIterator states"},
        guidance_result,
        session_id="sess-1",
    )
    coordinator.observe_tool_result(
        "context_read",
        {"path": "lib/ansible/executor/play_iterator.py"},
        "## FormSy Context Source\nok: true\npath: lib/ansible/executor/play_iterator.py\n```python\nclass HostState: pass\n```",
        session_id="sess-1",
    )

    transformed = coordinator.transform_tool_result(
        "read_file",
        {"path": "/Users/wayneliu/dev/ansible/lib/ansible/executor/play_iterator.py"},
        '{"content": "     1|class HostState: pass"}',
        session_id="sess-1",
    )

    assert coordinator._active_next_tool_directive is None
    assert transformed is None


def test_repeated_context_read_fuses_without_failed_read(tmp_path):
    coordinator = ConstraintKeeperCoordinator(client=FakeClient(), spool_root=tmp_path, identity=_identity())
    coordinator.observe_tool_result(
        "context_search",
        {"query": "PlayIterator states"},
        '{"ok": true}',
        session_id="sess-1",
    )
    args = {
        "path": "lib/ansible/executor/play_iterator.py",
        "start_line": 95,
        "end_line": 100,
    }
    result = '{"ok": true, "path": "lib/ansible/executor/play_iterator.py", "content": "source"}'

    for _ in range(4):
        coordinator.observe_tool_result("context_read", args, result, session_id="sess-1")
        assert coordinator.transform_tool_result("context_read", args, result, session_id="sess-1") is None
    coordinator.observe_tool_result("context_read", args, result, session_id="sess-1")
    fused = coordinator.transform_tool_result("context_read", args, result, session_id="sess-1")

    assert fused is not None
    payload = json.loads(fused)
    assert payload["ok"] is True
    assert payload["fused"] is True
    assert payload["content"] == ""
    assert payload["context_meta"]["read_key"] == "lib/ansible/executor/play_iterator.py:95-100"
    assert "Do not call context_read" in " ".join(payload["advisory"])


def test_degraded_probe_budget_does_not_block_before_or_after_patch_edit(tmp_path):
    coordinator = ConstraintKeeperCoordinator(client=FakeClient(), spool_root=tmp_path, identity=_identity())
    guidance_result = {
        "ok": False,
        "recovery_mode": "degraded_recovery",
        "guidance_packet": {
            "mode": "degraded_recovery",
            "target_candidates": ["lib/ansible/executor/play_iterator.py"],
            "probe_budget": {
                "search_files": 1,
                "read_file": 1,
                "terminal_or_execute_code": 1,
            },
        },
    }

    coordinator.observe_tool_result(
        "context_search",
        {"query": "PlayIterator states"},
        guidance_result,
        session_id="sess-1",
    )
    coordinator.observe_tool_result(
        "terminal",
        {"command": "python3 - <<'PY'\nprint('probe')\nPY"},
        '{"exit_code": 0, "output": "ok"}',
        session_id="sess-1",
    )
    assert coordinator.pre_tool_call_block_message(
        "terminal",
        {"command": "python3 - <<'PY'\nprint('probe again')\nPY"},
        session_id="sess-1",
    ) is None

    coordinator.observe_tool_result(
        "patch",
        {"path": "lib/ansible/executor/play_iterator.py"},
        '{"success": true}',
        session_id="sess-1",
    )

    assert coordinator.pre_tool_call_block_message(
        "terminal",
        {"command": "python3 - <<'PY'\nprint('probe after edit')\nPY"},
        session_id="sess-1",
    ) is None


def test_degraded_probe_budget_does_not_block_after_failed_patch(tmp_path):
    coordinator = ConstraintKeeperCoordinator(client=FakeClient(), spool_root=tmp_path, identity=_identity())
    guidance_result = {
        "ok": False,
        "recovery_mode": "degraded_recovery",
        "guidance_packet": {
            "mode": "degraded_recovery",
            "target_candidates": ["lib/ansible/executor/play_iterator.py"],
            "probe_budget": {
                "search_files": 1,
                "read_file": 1,
                "terminal_or_execute_code": 1,
            },
        },
    }

    coordinator.observe_tool_result(
        "context_search",
        {"query": "PlayIterator states"},
        guidance_result,
        session_id="sess-1",
    )
    coordinator.observe_tool_result(
        "terminal",
        {"command": "python3 - <<'PY'\nprint('probe')\nPY"},
        '{"exit_code": 0, "output": "ok"}',
        session_id="sess-1",
    )
    coordinator.observe_tool_result(
        "patch",
        {"path": "lib/ansible/executor/play_iterator.py"},
        '{"success": false, "message": "old_string not found"}',
        session_id="sess-1",
    )

    message = coordinator.pre_tool_call_block_message(
        "terminal",
        {"command": "python3 - <<'PY'\nprint('probe after failed edit')\nPY"},
        session_id="sess-1",
    )

    assert message is None


def test_degraded_probe_budget_allows_terminal_validation_after_exhaustion(tmp_path):
    coordinator = ConstraintKeeperCoordinator(client=FakeClient(), spool_root=tmp_path, identity=_identity())
    guidance_result = {
        "ok": False,
        "recovery_mode": "degraded_recovery",
        "guidance_packet": {
            "mode": "degraded_recovery",
            "target_candidates": ["lib/ansible/executor/play_iterator.py"],
            "probe_budget": {
                "search_files": 1,
                "read_file": 1,
                "terminal_or_execute_code": 1,
            },
        },
    }

    coordinator.observe_tool_result(
        "context_search",
        {"query": "PlayIterator states"},
        guidance_result,
        session_id="sess-1",
    )
    coordinator.observe_tool_result(
        "terminal",
        {"command": "python3 - <<'PY'\nprint('probe')\nPY"},
        '{"exit_code": 0, "output": "ok"}',
        session_id="sess-1",
    )

    assert coordinator.pre_tool_call_block_message(
        "terminal",
        {"command": "pytest test/units/executor/test_play_iterator.py"},
        session_id="sess-1",
    ) is None
    assert coordinator.pre_tool_call_block_message(
        "terminal",
        {"command": "git diff -- lib/ansible/plugins/strategy/__init__.py"},
        session_id="sess-1",
    ) is None
    assert coordinator.pre_tool_call_block_message(
        "terminal",
        {"command": "python -m py_compile lib/ansible/plugins/strategy/__init__.py"},
        session_id="sess-1",
    ) is None
    assert coordinator.pre_tool_call_block_message(
        "terminal",
        {"command": "git diff --stat"},
        session_id="sess-1",
    ) is None
    assert coordinator.pre_tool_call_block_message(
        "terminal",
        {"command": "wc -l patch.txt"},
        session_id="sess-1",
    ) is None
    assert coordinator.pre_tool_call_block_message(
        "terminal",
        {"command": "head -n 40 patch.txt"},
        session_id="sess-1",
    ) is None
    assert coordinator.pre_tool_call_block_message(
        "terminal",
        {"command": "cd /Users/wayneliu/dev/ansible && wc -l patch.txt"},
        session_id="sess-1",
    ) is None
    assert coordinator.pre_tool_call_block_message(
        "terminal",
        {"command": "cd /Users/wayneliu/dev/ansible && head -50 patch.txt"},
        session_id="sess-1",
    ) is None
    assert coordinator.pre_tool_call_block_message(
        "terminal",
        {"command": "cd /Users/wayneliu/dev/ansible && grep -E '^[+-]{3}' patch.txt | sort -u"},
        session_id="sess-1",
    ) is None
    assert coordinator.pre_tool_call_block_message(
        "terminal",
        {"command": "cd /Users/wayneliu/dev/ansible && cat patch.txt"},
        session_id="sess-1",
    ) is None


def test_final_submit_bypasses_degraded_context_refresh_gate(tmp_path):
    coordinator = ConstraintKeeperCoordinator(client=FakeClient(), spool_root=tmp_path, identity=_identity())
    coordinator.observe_tool_result(
        "context_search",
        {"query": "PlayIterator states"},
        {
            "ok": False,
            "recovery_mode": "degraded_recovery",
            "guidance_packet": {
                "mode": "degraded_recovery",
                "target_candidates": ["lib/ansible/executor/play_iterator.py"],
                "probe_budget": {
                    "search_files": 1,
                    "read_file": 1,
                    "terminal_or_execute_code": 1,
                },
            },
        },
        session_id="sess-1",
    )
    coordinator._active_context_directive = {
        "summary": "Server requested fresh context guidance.",
        "required_next_actions": ["Call context_search before more source exploration."],
        "suggested_queries": ["PlayIterator public states"],
    }

    message = coordinator.pre_tool_call_block_message(
        "terminal",
        {"command": "echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT && cat patch.txt"},
        session_id="sess-1",
    )

    assert message is None


def test_degraded_probe_budget_discourages_repeated_full_diff_output(tmp_path):
    coordinator = ConstraintKeeperCoordinator(client=FakeClient(), spool_root=tmp_path, identity=_identity())
    guidance_result = {
        "ok": False,
        "recovery_mode": "degraded_recovery",
        "guidance_packet": {
            "mode": "degraded_recovery",
            "target_candidates": ["lib/ansible/executor/play_iterator.py"],
            "accepted_edit_targets": ["lib/ansible/executor/play_iterator.py"],
            "probe_budget": {
                "search_files": 1,
                "read_file": 1,
                "terminal_or_execute_code": 3,
            },
        },
    }

    coordinator.observe_tool_result(
        "context_search",
        {"query": "PlayIterator states"},
        guidance_result,
        session_id="sess-1",
    )

    assert coordinator.pre_tool_call_block_message(
        "terminal",
        {"command": "git diff"},
        session_id="sess-1",
    ) is None
    coordinator.observe_tool_result(
        "terminal",
        {"command": "git diff"},
        '{"exit_code": 0, "output": "large diff"}',
        session_id="sess-1",
    )

    blocked = coordinator.pre_tool_call_block_message(
        "terminal",
        {"command": "git diff"},
        session_id="sess-1",
    )

    assert blocked is not None
    assert "git diff --stat" in blocked
    assert "git diff -- lib/ansible/executor/play_iterator.py" in blocked
    assert coordinator.pre_tool_call_block_message(
        "terminal",
        {"command": "git diff -- lib/ansible/executor/play_iterator.py"},
        session_id="sess-1",
    ) is None


def test_next_tool_directive_suggests_context_read_without_blocking_exploration(tmp_path):
    coordinator = ConstraintKeeperCoordinator(client=FakeClient(), spool_root=tmp_path, identity=_identity())
    guidance_result = {
        "ok": True,
        "guidance_packet": {
            "mode": "degraded_recovery",
            "required_next_tool": {
                "tool": "context_read",
                "args": {"path": "lib/ansible/executor/play_iterator.py"},
                "reason": "Validate hinted target.",
            },
            "probe_budget": {
                "search_files": 1,
                "read_file": 2,
                "terminal_or_execute_code": 2,
            },
        },
    }

    coordinator.observe_tool_result(
        "context_search",
        {"query": "PlayIterator states"},
        guidance_result,
        session_id="sess-1",
    )

    message = coordinator.pre_tool_call_block_message(
        "terminal",
        {"command": "grep -R PlayIterator lib/ansible"},
        session_id="sess-1",
    )
    transformed = coordinator.transform_tool_result(
        "context_search",
        {},
        "original",
        session_id="sess-1",
    )
    assert message is None
    assert transformed is not None
    assert "NEXT SUGGESTED TOOL" in transformed
    assert "context_read" in transformed
    assert "lib/ansible/executor/play_iterator.py" in transformed

    assert coordinator.pre_tool_call_block_message(
        "context_read",
        {"path": "lib/ansible/executor/play_iterator.py"},
        session_id="sess-1",
    ) is None
    coordinator.observe_tool_result(
        "context_read",
        {"path": "lib/ansible/executor/play_iterator.py"},
        '{"ok": true, "path": "lib/ansible/executor/play_iterator.py"}',
        session_id="sess-1",
    )

    assert coordinator.pre_tool_call_block_message(
        "terminal",
        {"command": "pytest test/units/executor/test_play_iterator.py"},
        session_id="sess-1",
    ) is None


def test_next_tool_directive_allows_same_target_fallback_after_context_read_failure(tmp_path):
    coordinator = ConstraintKeeperCoordinator(client=FakeClient(), spool_root=tmp_path, identity=_identity())
    guidance_result = {
        "ok": True,
        "guidance_packet": {
            "mode": "degraded_recovery",
            "required_next_tool": {
                "tool": "context_read",
                "args": {"path": "lib/ansible/executor/play_iterator.py"},
                "reason": "Validate hinted target.",
            },
            "probe_budget": {
                "search_files": 1,
                "read_file": 2,
                "terminal_or_execute_code": 2,
            },
        },
    }

    coordinator.observe_tool_result(
        "context_search",
        {"query": "PlayIterator states"},
        guidance_result,
        session_id="sess-1",
    )
    coordinator.observe_tool_result(
        "context_read",
        {"path": "lib/ansible/executor/play_iterator.py"},
        '{"ok": false, "error": "read timeout"}',
        session_id="sess-1",
    )

    assert coordinator.pre_tool_call_block_message(
        "read_file",
        {"path": "lib/ansible/executor/play_iterator.py"},
        session_id="sess-1",
    ) is None
    other_read_message = coordinator.pre_tool_call_block_message(
        "read_file",
        {"path": "lib/ansible/plugins/strategy/linear.py"},
        session_id="sess-1",
    )
    assert other_read_message is None

    coordinator.observe_tool_result(
        "read_file",
        {"path": "lib/ansible/executor/play_iterator.py"},
        "class PlayIterator:\n    pass\n",
        session_id="sess-1",
    )
    assert coordinator.pre_tool_call_block_message(
        "terminal",
        {"command": "pytest test/units/executor/test_play_iterator.py"},
        session_id="sess-1",
    ) is None


def test_failed_context_read_directive_fails_open_to_avoid_retry_deadlock(tmp_path):
    coordinator = ConstraintKeeperCoordinator(client=FakeClient(), spool_root=tmp_path, identity=_identity())
    guidance_result = {
        "ok": True,
        "guidance_packet": {
            "mode": "degraded_recovery",
            "target_candidates": ["lib/ansible/executor/play_iterator.py"],
            "required_next_tool": {
                "tool": "context_read",
                "args": {"path": "Users/wayneliu/dev/ansible/lib/ansible/executor/play_iterator.py"},
                "reason": "Validate hinted target.",
            },
            "probe_budget": {
                "search_files": 1,
                "read_file": 2,
                "terminal_or_execute_code": 2,
            },
        },
    }
    coordinator.observe_tool_result(
        "context_search",
        {"query": "PlayIterator states"},
        guidance_result,
        session_id="sess-1",
    )
    coordinator.observe_tool_result(
        "context_read",
        {"path": "Users/wayneliu/dev/ansible/lib/ansible/executor/play_iterator.py"},
        '{"ok": false, "error": "Formsy context read failed"}',
        session_id="sess-1",
    )

    retry_blocked = coordinator.pre_tool_call_block_message(
        "context_read",
        {"path": "Users/wayneliu/dev/ansible/lib/ansible/executor/play_iterator.py"},
        session_id="sess-1",
    )
    assert retry_blocked is None

    assert coordinator.pre_tool_call_block_message(
        "read_file",
        {"path": "lib/ansible/executor/play_iterator.py"},
        session_id="sess-1",
    ) is None
    coordinator.observe_tool_result(
        "read_file",
        {"path": "lib/ansible/executor/play_iterator.py"},
        "class PlayIterator:\n    pass\n",
        session_id="sess-1",
    )

    assert coordinator.pre_tool_call_block_message(
        "patch",
        {"path": "lib/ansible/executor/play_iterator.py"},
        session_id="sess-1",
    ) is None


def test_recover_and_verify_send_server_compatible_payloads(tmp_path):
    client = FakeClient()
    coordinator = ConstraintKeeperCoordinator(client=client, spool_root=tmp_path, identity=_identity())

    coordinator.recover(reason="same failure", session_id="sess-1")
    coordinator.verify_completion(session_id="sess-1")

    recover_payload = [call[1]["payload"] for call in client.calls if call[0] == "recover"][0]
    verify_payload = [call[1]["payload"] for call in client.calls if call[0] == "verify"][0]
    assert recover_payload == {"task_id": "task-1", "run_id": "run-1", "reason": "same failure"}
    assert verify_payload["task_id"] == "task-1"
    assert verify_payload["run_id"] == "run-1"
    assert verify_payload["completion_bootstrap"]["instruction_freshness"] == "unknown"


def test_verify_completion_sends_completion_bootstrap_evidence(tmp_path):
    client = FakeClient()
    diff = "diff --git a/app.py b/app.py\n--- a/app.py\n+++ b/app.py\n"
    source = "print('current')\n"
    coordinator = ConstraintKeeperCoordinator(
        client=client,
        spool_root=tmp_path,
        identity=_identity(),
        diff_provider=lambda: diff,
        source_provider=lambda paths: {"app.py": source},
    )
    coordinator.on_user_turn(
        user_message="<pr_description>Fix app behavior</pr_description>",
        session_id="sess-1",
    )

    coordinator.verify_completion(session_id="sess-1")

    verify_payload = [call[1]["payload"] for call in client.calls if call[0] == "verify"][-1]
    bootstrap = verify_payload["completion_bootstrap"]
    assert bootstrap["instruction"] == "<pr_description>Fix app behavior</pr_description>"
    assert bootstrap["instruction_freshness"] == "current_run"
    assert bootstrap["unified_diff"] == diff
    assert bootstrap["changed_files"] == ["app.py"]
    assert bootstrap["post_patch_sources"] == {"app.py": source}
    assert bootstrap["diff_hash"].startswith("sha256:")
    assert bootstrap["source_snapshot_hashes"]["app.py"].startswith("sha256:")


def test_final_submit_blocks_when_server_rejects(tmp_path):
    client = FakeClient()
    client.verify_response = {
        "decision": "NEED_MORE_VALIDATION",
        "protocol": {
            "summary": "Completion proof is incomplete.",
            "blocking_conditions": ["post-diff tests missing"],
            "required_next_actions": ["Run validation after latest diff."],
        },
    }
    coordinator = ConstraintKeeperCoordinator(
        client=client,
        spool_root=tmp_path,
        identity=_identity(),
        fail_closed_on_submit=True,
    )

    message = coordinator.pre_tool_call_block_message(
        "terminal",
        {"command": "echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"},
        session_id="sess-1",
    )

    assert message is not None
    assert "post-diff tests missing" in message
    assert "Run validation after latest diff." in message


def test_final_submit_block_message_includes_verifier_protocol_details(tmp_path):
    client = FakeClient()
    client.verify_response = {
        "decision": "NEED_MORE_VALIDATION",
        "protocol": {
            "summary": "Completion proof is incomplete.",
            "blocking_conditions": [
                "No server patch check after latest diff.",
                "No passing test after latest diff.",
            ],
            "required_next_actions": [
                "Submit unified diff evidence so the server can run PatchContract checks.",
                "Run blocking tests after the latest diff and submit test_result evidence.",
            ],
        },
    }
    coordinator = ConstraintKeeperCoordinator(
        client=client,
        spool_root=tmp_path,
        identity=_identity(),
        fail_closed_on_submit=True,
    )

    message = coordinator.pre_tool_call_block_message(
        "terminal",
        {"command": "echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT && cat patch.txt"},
        session_id="sess-1",
    )

    assert message is not None
    assert "Completion proof is incomplete." in message
    assert "No server patch check after latest diff." in message
    assert "Run blocking tests after the latest diff" in message


def test_final_submit_semantic_violation_adds_in_place_recovery_guidance(tmp_path):
    client = FakeClient()
    client.verify_response = {
        "decision": "NEED_MORE_VALIDATION",
        "protocol": {
            "summary": "Completion proof is incomplete.",
            "blocking_conditions": [
                "SemanticContract violation exists.",
                "internal legacy state alias usage remains in lib/ansible/executor/play_iterator.py",
            ],
            "required_next_actions": [
                "Update the patch so it satisfies semantic API contracts.",
            ],
        },
    }
    coordinator = ConstraintKeeperCoordinator(
        client=client,
        spool_root=tmp_path,
        identity=_identity(),
        fail_closed_on_submit=True,
    )

    message = coordinator.pre_tool_call_block_message(
        "terminal",
        {"command": "echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT && cat patch.txt"},
        session_id="sess-1",
    )

    assert message is not None
    assert "SemanticContract violation exists." in message
    assert "FormSy semantic recovery guidance" in message
    assert "Patch current diff in place" in message
    assert "do not reset or recreate the target file" in message
    assert "helper patch scripts" in message


def test_final_submit_allows_server_error_in_advisory_mapping(tmp_path):
    client = FakeClient()
    client.verify_error = RuntimeError("runtime api down")
    coordinator = ConstraintKeeperCoordinator(
        client=client,
        spool_root=tmp_path,
        identity=_identity(),
        fail_closed_on_submit=True,
    )

    message = coordinator.pre_tool_call_block_message(
        "terminal",
        {"command": "echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"},
        session_id="sess-1",
    )

    assert message is None


def test_final_submit_projects_accepted_completion_once(tmp_path):
    client = FakeClient()
    client.verify_response = {
        "decision": "ACCEPT_DONE",
        "protocol": {
            "state": "DONE_ACCEPTED",
            "gate_decision": "ACCEPT_DONE",
            "summary": "Completion proof satisfies P0 contracts.",
        },
    }
    coordinator = ConstraintKeeperCoordinator(
        client=client,
        spool_root=tmp_path,
        identity=_identity(),
        fail_closed_on_submit=True,
    )

    message = coordinator.pre_tool_call_block_message(
        "terminal",
        {"command": "echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"},
        session_id="sess-1",
    )
    first = coordinator.transform_tool_result(
        "terminal",
        {"command": "echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"},
        "submitted",
        session_id="sess-1",
    )
    second = coordinator.transform_tool_result(
        "terminal",
        {"command": "echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"},
        "submitted",
        session_id="sess-1",
    )

    assert message is None
    assert first is not None
    assert "## FormSy Completion Verifier" in first
    assert "- Decision: ACCEPT_DONE" in first
    assert "Completion proof satisfies P0 contracts." in first
    assert second is None


def test_final_submit_projects_completion_audit_when_present(tmp_path):
    client = FakeClient()
    client.verify_response = {
        "decision": "ACCEPT_DONE",
        "protocol": {
            "state": "DONE_ACCEPTED",
            "gate_decision": "ACCEPT_DONE",
            "summary": "Completion proof satisfies P0 contracts.",
        },
        "completion_audit": {
            "audit_status": "verified",
            "gate_decision": "ACCEPT_DONE",
            "verifier_id": "verifier-1",
            "protocol_id": "protocol-1",
            "evidence": {
                "latest_diff_event_id": "ev-diff",
                "latest_diff_hash": "sha256:diff123",
                "patch_check_event_id": "ev-patch",
                "validation_event_id": "ev-validation",
            },
            "memory_write_allowed": True,
            "memory_write_quality": "medium",
        },
    }
    coordinator = ConstraintKeeperCoordinator(
        client=client,
        spool_root=tmp_path,
        identity=_identity(),
        fail_closed_on_submit=True,
    )

    message = coordinator.pre_tool_call_block_message(
        "terminal",
        {"command": "echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"},
        session_id="sess-1",
    )
    first = coordinator.transform_tool_result(
        "terminal",
        {"command": "echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"},
        "submitted",
        session_id="sess-1",
    )

    assert message is None
    assert first is not None
    assert "## FormSy Completion Verifier" in first
    assert "- Audit status: verified" in first
    assert "- Gate decision: ACCEPT_DONE" in first
    assert "- Latest diff: sha256:diff123" in first
    assert "- Patch check: ev-patch" in first
    assert "- Validation: ev-validation" in first
    assert "- Memory write allowed: true" in first
    assert "- Memory write quality: medium" in first


def test_final_submit_projects_completion_verifier_unavailable_once(tmp_path):
    client = FakeClient()
    client.verify_error = RuntimeError("runtime api down")
    coordinator = ConstraintKeeperCoordinator(
        client=client,
        spool_root=tmp_path,
        identity=_identity(),
        fail_closed_on_submit=True,
    )

    message = coordinator.pre_tool_call_block_message(
        "terminal",
        {"command": "echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"},
        session_id="sess-1",
    )
    first = coordinator.transform_tool_result(
        "terminal",
        {"command": "echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"},
        "submitted",
        session_id="sess-1",
    )
    second = coordinator.transform_tool_result(
        "terminal",
        {"command": "echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"},
        "submitted",
        session_id="sess-1",
    )

    assert message is None
    assert first is not None
    assert "## FormSy Completion Verifier" in first
    assert "- Decision: completion_verification_unavailable" in first
    assert "do not write successful implementation memory" in first
    assert "RuntimeError: runtime api down" in first
    assert second is None


def test_final_submit_blocks_deterministic_hard_violation(tmp_path):
    client = FakeClient()
    client.verify_response = {
        "gate_decision": "REJECT_DONE",
        "protocol": {
            "summary": "Patch violates explicit forbidden path policy.",
            "blocking_conditions": [
                "Explicit forbidden path violation: patch writes .git/config.",
            ],
        },
    }
    coordinator = ConstraintKeeperCoordinator(
        client=client,
        spool_root=tmp_path,
        identity=_identity(),
        fail_closed_on_submit=True,
    )

    message = coordinator.pre_tool_call_block_message(
        "terminal",
        {"command": "echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"},
        session_id="sess-1",
    )

    assert message is not None
    assert "forbidden path" in message.lower()


def test_grounding_query_ignores_skill_update_meta_turn(tmp_path):
    coordinator = ConstraintKeeperCoordinator(
        client=FakeClient(),
        spool_root=tmp_path,
        identity=_identity(),
    )
    coordinator.on_user_turn(
        (
            "<pr_description>\n"
            "# Title\n"
            "Standardize `PlayIterator` state representation with a public type "
            "and preserve backward compatibility\n"
            "</pr_description>"
        ),
        session_id="sess-1",
    )
    coordinator.on_user_turn(
        (
            "Review the conversation above and update the skill library. "
            "Be ACTIVE — most sessions produce at least one skill update."
        ),
        session_id="sess-1",
    )

    coordinator.observe_tool_result(
        "terminal",
        {
            "command": (
                "cd /Users/wayneliu/dev/ansible && grep -n "
                '"class PlayIteratorRunState" lib/ansible/executor/play_iterator.py'
            )
        },
        '{"exit_code": 0, "output": ""}',
        session_id="sess-1",
    )
    first = coordinator.transform_tool_result(
        "terminal",
        {},
        "terminal-result",
        session_id="sess-1",
    )

    assert first is not None
    assert "FormSy grounding action card" in first
    assert "Standardize `PlayIterator` state representation" in first
    assert "Review the conversation above" not in first
    assert "skill library" not in first


def test_repeated_validation_failure_triggers_recovery_once(tmp_path):
    client = FakeClient()
    coordinator = ConstraintKeeperCoordinator(
        client=client,
        spool_root=tmp_path,
        identity=_identity(),
        diff_provider=lambda: "diff --git a/app.py b/app.py\n--- a/app.py\n+++ b/app.py\n",
    )
    args = {"command": "python -m pytest tests/forms", "exit_code": 1}
    output = "Traceback\nAssertionError: same failure"

    coordinator.observe_tool_result("terminal", args, output, session_id="sess-1")
    coordinator.observe_tool_result("terminal", args, output, session_id="sess-1")
    coordinator.observe_tool_result("terminal", args, output, session_id="sess-1")

    recover_calls = [call for call in client.calls if call[0] == "recover"]
    assert len(recover_calls) == 1
    assert "same failure" in recover_calls[0][1]["payload"]["reason"]
    assert coordinator.latest_protocol_text == "recover now"


def test_diagnostic_failure_does_not_trigger_automatic_recovery(tmp_path):
    client = FakeClient()
    coordinator = ConstraintKeeperCoordinator(client=client, spool_root=tmp_path, identity=_identity())

    coordinator.observe_tool_result(
        "terminal",
        {"command": "python - <<'PY'\nraise SystemExit(1)\nPY", "exit_code": 1},
        "diagnostic failed",
        session_id="sess-1",
    )
    coordinator.observe_tool_result(
        "terminal",
        {"command": "python - <<'PY'\nraise SystemExit(1)\nPY", "exit_code": 1},
        "diagnostic failed",
        session_id="sess-1",
    )

    assert [call for call in client.calls if call[0] == "recover"] == []


def test_failure_payload_includes_latest_diff_context_hash(tmp_path):
    coordinator = ConstraintKeeperCoordinator(
        client=FakeClient(),
        spool_root=tmp_path,
        identity=_identity(),
        diff_provider=lambda: "diff --git a/app.py b/app.py\n--- a/app.py\n+++ b/app.py\n",
    )

    coordinator.observe_tool_result("apply_patch", {"patch": "*** Begin Patch"}, "ok", session_id="sess-1")
    coordinator.observe_tool_result(
        "terminal",
        {"command": "python -m pytest tests/forms", "exit_code": 1},
        "AssertionError: same failure",
        session_id="sess-1",
    )

    failure = [
        event for event in coordinator.spool.pending("task-1", "run-1")
        if event["event_kind"] == "failure"
    ][0]
    assert failure["payload"]["diff_context_hash"] == coordinator.latest_diff_hash


def test_pre_tool_call_blocks_execute_code_read_file_write_file_bridge(tmp_path):
    coordinator = ConstraintKeeperCoordinator(client=FakeClient(), spool_root=tmp_path, identity=_identity())

    message = coordinator.pre_tool_call_block_message(
        "execute_code",
        {
            "code": """
from hermes_tools import read_file, write_file

content = read_file("lib/ansible/executor/play_iterator.py")["content"]
write_file("lib/ansible/executor/play_iterator.py", content)
"""
        },
        session_id="sess-1",
    )

    assert message is not None
    assert "read_file output is line-numbered display text" in message
    assert "Use read_file as a normal tool" in message
    assert "patch or write_file" in message


def test_pre_tool_call_blocks_execute_code_direct_source_write(tmp_path):
    coordinator = ConstraintKeeperCoordinator(client=FakeClient(), spool_root=tmp_path, identity=_identity())

    message = coordinator.pre_tool_call_block_message(
        "execute_code",
        {
            "code": """
path = '/Users/wayneliu/dev/ansible/lib/ansible/executor/play_iterator.py'
with open(path) as f:
    content = f.read()

new_content = content.replace('self.ITERATING_SETUP', 'self.RunState.SETUP')
with open(path, 'w') as f:
    f.writelines(new_content)
"""
        },
        session_id="sess-1",
    )

    assert message is not None
    assert "direct source writes inside execute_code" in message
    assert "Use patch" in message


def test_pre_tool_call_allows_execute_code_read_only_validation(tmp_path):
    coordinator = ConstraintKeeperCoordinator(client=FakeClient(), spool_root=tmp_path, identity=_identity())

    message = coordinator.pre_tool_call_block_message(
        "execute_code",
        {
            "code": """
import sys
sys.path.insert(0, '/Users/wayneliu/dev/ansible/lib')
from ansible.executor.play_iterator import PlayIterator
assert PlayIterator.RunState.SETUP == 0
"""
        },
        session_id="sess-1",
    )

    assert message is None
