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
        return {
            "protocol_text": "## FormSy Constraint Protocol\n- State: PATCH_ALLOWED_WITH_WARNINGS"
        }

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
        self.calls.append(
            ("status", {"task_id": task_id, "run_id": run_id, "session_id": session_id})
        )
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
    coordinator = ConstraintKeeperCoordinator(
        client=client, spool_root=tmp_path, identity=_identity()
    )

    coordinator.ensure_task_started()
    coordinator.ensure_task_started()

    assert [name for name, _ in client.calls] == ["task_start"]


def test_compile_context_bundle_returns_protocol_text(tmp_path):
    coordinator = ConstraintKeeperCoordinator(
        client=FakeClient(), spool_root=tmp_path, identity=_identity()
    )

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
            self.calls.append(
                ("compile", {"payload": payload, "session_id": session_id})
            )
            return {
                "task_id": "task-1",
                "run_id": "run-1",
                "decision": "PATCH_ALLOWED_WITH_WARNINGS",
                "protocol": {
                    "state": "PATCH_ALLOWED_WITH_WARNINGS",
                    "gate_decision": "PATCH_ALLOWED_WITH_WARNINGS",
                    "summary": "Patch allowed on accepted target.",
                    "blocking_conditions": ["Do not edit unrelated files."],
                    "required_next_actions": [
                        "Edit lib/ansible/executor/play_iterator.py."
                    ],
                    "suggested_queries": ["tests covering PlayIterator states"],
                },
            }

    coordinator = ConstraintKeeperCoordinator(
        client=ServerShapeClient(), spool_root=tmp_path, identity=_identity()
    )

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
    coordinator = ConstraintKeeperCoordinator(
        client=FakeClient(), spool_root=tmp_path, identity=_identity()
    )
    coordinator.latest_protocol_text = (
        "## FormSy Constraint Protocol\n- State: RECOVERY_OPEN"
    )

    first = coordinator.transform_tool_result(
        "terminal", {}, "original", session_id="sess-1"
    )
    second = coordinator.transform_tool_result(
        "terminal", {}, "original", session_id="sess-1"
    )

    assert first == "original\n\n## FormSy Constraint Protocol\n- State: RECOVERY_OPEN"
    assert second is None


def test_pre_llm_call_context_returns_short_recovery_reminder(tmp_path):
    coordinator = ConstraintKeeperCoordinator(
        client=FakeClient(), spool_root=tmp_path, identity=_identity()
    )
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
    coordinator = ConstraintKeeperCoordinator(
        client=FakeClient(), spool_root=tmp_path, identity=_identity()
    )
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
    assert (
        'Call: context_search({"query": "Fix PlayIterator public states"})'
        in first["context"]
    )
    assert "advisory" in first["context"].lower()
    assert second is None


def test_pre_llm_seed_logs_delivery_observability_once(tmp_path, caplog):
    coordinator = ConstraintKeeperCoordinator(
        client=FakeClient(), spool_root=tmp_path, identity=_identity()
    )
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
        record
        for record in caplog.records
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


def test_validation_only_delegate_turn_does_not_replace_parent_identity(tmp_path):
    client = FakeClient()
    coordinator = ConstraintKeeperCoordinator(
        client=client, spool_root=tmp_path, identity=_identity()
    )
    parent_task = (
        "In ansible, iptables chain management should handle chain creation and "
        "deletion while respecting check mode."
    )
    coordinator.on_user_turn(user_message=parent_task, session_id="parent-session")
    parent_identity = coordinator.identity

    coordinator.on_user_turn(
        user_message=(
            "Run the iptables unit tests in the Ansible repo at "
            "/Users/wayneliu/dev/ansible and report the result. Use the "
            "appropriate pytest command and PYTHONPATH setup."
        ),
        session_id="delegate-session",
    )
    result = coordinator.verify_completion(session_id="parent-session")

    verify_calls = [call for call in client.calls if call[0] == "verify"]
    assert verify_calls
    assert coordinator.identity == parent_identity
    assert verify_calls[-1][1]["payload"]["task_id"] == parent_identity.task_id
    assert verify_calls[-1][1]["payload"]["run_id"] == parent_identity.run_id
    bootstrap = verify_calls[-1][1]["payload"]["completion_bootstrap"]
    assert bootstrap["instruction"] == parent_task
    assert "Run the iptables unit tests" not in bootstrap["instruction"]


def test_execution_instruction_only_turn_does_not_become_task_goal(tmp_path):
    client = FakeClient()
    coordinator = ConstraintKeeperCoordinator(
        client=client, spool_root=tmp_path, identity=_identity()
    )
    coordinator.on_user_turn(
        user_message=(
            "Do not stop to ask design questions. Make the best implementation "
            "decision from the existing module style, patch the code, add focused "
            "unit tests, run the tests, and only then report uncertainty if any "
            "remains."
        ),
        session_id="sess-1",
    )
    real_task = (
        "In ansible, iptables chain management should handle chain creation and "
        "deletion while respecting check mode.\nExpected behavior:\n"
        "- chain creation and deletion should use the correct chain-management commands."
    )
    coordinator.on_user_turn(user_message=real_task, session_id="sess-1")

    coordinator.verify_completion(session_id="sess-1")

    verify_calls = [call for call in client.calls if call[0] == "verify"]
    bootstrap = verify_calls[-1][1]["payload"]["completion_bootstrap"]
    assert bootstrap["instruction"] == real_task
    assert "Do not stop to ask design questions" not in bootstrap["instruction"]


def test_compile_context_bundle_records_strong_direct_matches_as_accepted_targets(tmp_path):
    coordinator = ConstraintKeeperCoordinator(
        client=FakeClient(), spool_root=tmp_path, identity=_identity()
    )

    coordinator.compile_context_bundle(
        query="chain_management module option",
        instruction="fix chain management",
        query_plan={"retrieval_mode": "symbolic"},
        context_bundle={"bundle_id": "bundle-1"},
        search_payload={
            "coverage": "partial",
            "matches": [
                {
                    "path": "lib/ansible/modules/iptables.py",
                    "kind": "direct_query_match",
                    "symbol": "chain_management",
                    "why_relevant": "explicit symbol anchor matched the compiled source snapshot.",
                },
                {
                    "path": "test/units/modules/test_iptables.py",
                    "kind": "direct_query_match",
                    "symbol": "chain_management",
                },
            ],
        },
    )
    coordinator.verify_completion(session_id="sess-1")

    verify_calls = [call for call in coordinator.client.calls if call[0] == "verify"]
    bootstrap = verify_calls[-1][1]["payload"]["completion_bootstrap"]
    assert bootstrap["context_bundle_hint"]["accepted_targets"] == [
        "lib/ansible/modules/iptables.py"
    ]


def test_compile_context_bundle_promotes_first_real_query_to_task_goal(tmp_path):
    client = FakeClient()
    coordinator = ConstraintKeeperCoordinator(
        client=client, spool_root=tmp_path, identity=_identity()
    )
    coordinator.on_user_turn(
        user_message=(
            "Do not stop to ask design questions. Make the best implementation "
            "decision from the existing module style, patch the code, add focused "
            "unit tests, run the tests, and only then report uncertainty if any "
            "remains."
        ),
        session_id="sess-1",
    )
    task_query = (
        "In ansible, iptables chain management should handle chain creation "
        "and deletion while respecting check mode."
    )

    coordinator.compile_context_bundle(
        query=task_query,
        instruction="",
        query_plan={"retrieval_mode": "symbolic"},
        context_bundle={"bundle_id": "bundle-1"},
        search_payload={"query": task_query},
        session_id="sess-1",
    )
    coordinator.verify_completion(session_id="sess-1")

    verify_calls = [call for call in client.calls if call[0] == "verify"]
    bootstrap = verify_calls[-1][1]["payload"]["completion_bootstrap"]
    assert bootstrap["instruction"] == task_query
    assert "Do not stop to ask design questions" not in bootstrap["instruction"]


def test_compile_context_bundle_replaces_stale_policy_goal_with_real_query(tmp_path):
    client = FakeClient()
    coordinator = ConstraintKeeperCoordinator(
        client=client, spool_root=tmp_path, identity=_identity()
    )
    coordinator._latest_user_task_text = (  # noqa: SLF001 - regression fixture.
        "Do not stop to ask design questions. Make the best implementation "
        "decision from the existing module style, patch the code, add focused "
        "unit tests, run the tests, and only then report uncertainty if any "
        "remains."
    )
    task_query = (
        "In ansible, iptables chain management should handle chain creation "
        "and deletion while respecting check mode."
    )

    coordinator.compile_context_bundle(
        query=task_query,
        instruction="",
        query_plan={"retrieval_mode": "symbolic"},
        context_bundle={"bundle_id": "bundle-1"},
        search_payload={"query": task_query},
        session_id="sess-1",
    )
    coordinator.verify_completion(session_id="sess-1")

    verify_calls = [call for call in client.calls if call[0] == "verify"]
    bootstrap = verify_calls[-1][1]["payload"]["completion_bootstrap"]
    assert bootstrap["instruction"] == task_query
    assert "Do not stop to ask design questions" not in bootstrap["instruction"]


def test_compile_context_bundle_accepts_specific_basename_anchor_not_generic_commands(
    tmp_path,
):
    coordinator = ConstraintKeeperCoordinator(
        client=FakeClient(), spool_root=tmp_path, identity=_identity()
    )

    coordinator.compile_context_bundle(
        query=(
            "In ansible, iptables chain management should use the correct "
            "chain-management commands."
        ),
        instruction="fix chain management",
        query_plan={"retrieval_mode": "symbolic"},
        context_bundle={"bundle_id": "bundle-1"},
        search_payload={
            "query": (
                "In ansible, iptables chain management should use the correct "
                "chain-management commands."
            ),
            "coverage": "partial",
            "matches": [
                {
                    "path": "hacking/build_library/build_ansible/commands.py",
                    "kind": "direct_query_match",
                    "why_relevant": "exact basename query anchor matched the file name.",
                },
                {
                    "path": "lib/ansible/modules/iptables.py",
                    "kind": "direct_query_match",
                    "why_relevant": "exact basename query anchor matched the file name.",
                },
            ],
        },
    )
    coordinator.verify_completion(session_id="sess-1")

    verify_calls = [call for call in coordinator.client.calls if call[0] == "verify"]
    bootstrap = verify_calls[-1][1]["payload"]["completion_bootstrap"]
    assert bootstrap["context_bundle_hint"]["accepted_targets"] == [
        "lib/ansible/modules/iptables.py"
    ]


def test_pre_llm_seed_logs_advisory_uptake_miss_on_first_effective_deviation(
    tmp_path, caplog
):
    coordinator = ConstraintKeeperCoordinator(
        client=FakeClient(), spool_root=tmp_path, identity=_identity()
    )
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
        record
        for record in caplog.records
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


def test_server_next_tool_directive_logs_stable_fallback_action_id_on_uptake_miss(
    tmp_path, caplog
):
    coordinator = ConstraintKeeperCoordinator(
        client=FakeClient(), spool_root=tmp_path, identity=_identity()
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
        record
        for record in caplog.records
        if "event=advisory_uptake_missed" in record.getMessage()
    ]
    assert len(missed_records) == 1
    message = missed_records[0].getMessage()
    assert "action_id=context_read.next" in message
    assert "expected_tool=context_read" in message
    assert "actual_tool=read_file" in message


def test_server_next_tool_directive_logs_same_target_read_file_fallback_satisfied(
    tmp_path, caplog
):
    coordinator = ConstraintKeeperCoordinator(
        client=FakeClient(), spool_root=tmp_path, identity=_identity()
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
            {
                "path": "/Users/wayneliu/dev/ansible/lib/ansible/executor/play_iterator.py"
            },
            '{"content": "source"}',
            session_id="sess-1",
        )

    satisfied_records = [
        record
        for record in caplog.records
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
        record
        for record in caplog.records
        if "event=advisory_uptake_missed" in record.getMessage()
    ]
    assert missed_records == []


def test_pre_llm_seed_materializes_pending_action_and_prefixes_missed_reminder(
    tmp_path,
):
    coordinator = ConstraintKeeperCoordinator(
        client=FakeClient(), spool_root=tmp_path, identity=_identity()
    )
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
    coordinator = ConstraintKeeperCoordinator(
        client=FakeClient(), spool_root=tmp_path, identity=_identity()
    )
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
    assert (
        coordinator.transform_tool_result(
            "context_search", {}, "context-search-result", session_id="sess-1"
        )
        is None
    )

    coordinator.observe_tool_result(
        "read_file",
        {"path": "lib/ansible/executor/play_iterator.py"},
        '{"content": "source"}',
        session_id="sess-1",
    )
    assert (
        coordinator.transform_tool_result(
            "read_file", {}, "read-result", session_id="sess-1"
        )
        is None
    )


def test_skill_view_marks_formsy_context_skill_body_loaded(tmp_path, caplog):
    coordinator = ConstraintKeeperCoordinator(
        client=FakeClient(), spool_root=tmp_path, identity=_identity()
    )

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
    coordinator = ConstraintKeeperCoordinator(
        client=FakeClient(), spool_root=tmp_path, identity=_identity()
    )
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
    coordinator = ConstraintKeeperCoordinator(
        client=FakeClient(), spool_root=tmp_path, identity=_identity()
    )
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


def test_bootstrap_gate_does_not_block_broad_exploration_without_context_search(
    tmp_path,
):
    coordinator = ConstraintKeeperCoordinator(
        client=FakeClient(), spool_root=tmp_path, identity=_identity()
    )

    assert (
        coordinator.pre_tool_call_block_message(
            "terminal",
            {"command": "pwd && ls"},
            session_id="sess-1",
        )
        is None
    )
    coordinator.observe_tool_result(
        "terminal",
        {"command": "pwd && ls"},
        '{"exit_code": 0, "output": "/repo"}',
        session_id="sess-1",
    )

    assert (
        coordinator.pre_tool_call_block_message(
            "read_file",
            {"path": "lib/ansible/executor/play_iterator.py"},
            session_id="sess-1",
        )
        is None
    )
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
    coordinator = ConstraintKeeperCoordinator(
        client=FakeClient(), spool_root=tmp_path, identity=_identity()
    )

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
    coordinator = ConstraintKeeperCoordinator(
        client=FakeClient(), spool_root=tmp_path, identity=_identity()
    )

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
    coordinator = ConstraintKeeperCoordinator(
        client=FakeClient(), spool_root=tmp_path, identity=_identity()
    )

    message = coordinator.pre_tool_call_block_message(
        "context_read",
        {"path": "lib/ansible/executor/play_iterator.py"},
        session_id="sess-1",
    )

    assert message is None


def test_final_submit_closes_attempt_without_blocking_next_edit(tmp_path):
    client = FakeClient()
    coordinator = ConstraintKeeperCoordinator(
        client=client, spool_root=tmp_path, identity=_identity()
    )
    coordinator.observe_tool_result(
        "context_search",
        {"query": "PlayIterator public states"},
        '{"ok": true}',
        session_id="sess-1",
    )
    coordinator.latest_protocol_text = (
        "## FormSy Constraint Protocol\n- State: PATCH_ALLOWED_WITH_WARNINGS"
    )

    assert (
        coordinator.pre_tool_call_block_message(
            "terminal",
            {"command": "echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT && cat patch.txt"},
            session_id="sess-1",
        )
        is None
    )
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
    coordinator = ConstraintKeeperCoordinator(
        client=FakeClient(), spool_root=tmp_path, identity=_identity()
    )
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
    coordinator = ConstraintKeeperCoordinator(
        client=FakeClient(), spool_root=tmp_path, identity=_identity()
    )
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

    assert (
        coordinator.pre_tool_call_block_message(
            "terminal",
            {"command": "cat /Users/wayneliu/dev/ansible/patch.txt"},
            session_id="sess-1",
        )
        is None
    )
    coordinator.observe_tool_result(
        "terminal",
        {"command": "cat /Users/wayneliu/dev/ansible/patch.txt"},
        '{"exit_code": 0, "output": "diff --git a/lib/ansible/executor/play_iterator.py"}',
        session_id="sess-1",
    )

    assert (
        coordinator.transform_tool_result(
            "terminal",
            {},
            '{"output": "diff --git a/lib/ansible/executor/play_iterator.py"}',
            session_id="sess-1",
        )
        is None
    )


def test_context_read_after_closed_attempt_is_advisory_not_blocked(tmp_path):
    coordinator = ConstraintKeeperCoordinator(
        client=FakeClient(), spool_root=tmp_path, identity=_identity()
    )
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
    coordinator = ConstraintKeeperCoordinator(
        client=FakeClient(), spool_root=tmp_path, identity=_identity()
    )
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


def test_closed_attempt_source_read_does_not_emit_grounding_without_new_attempt(
    tmp_path,
):
    coordinator = ConstraintKeeperCoordinator(
        client=FakeClient(), spool_root=tmp_path, identity=_identity()
    )
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

    assert (
        coordinator.pre_tool_call_block_message(
            "terminal",
            {"command": "pwd && ls -la"},
            session_id="sess-1",
        )
        is None
    )
    coordinator.observe_tool_result(
        "terminal",
        {"command": "pwd && ls -la"},
        '{"exit_code": 0, "output": "/repo"}',
        session_id="sess-1",
    )
    assert (
        coordinator.transform_tool_result(
            "terminal", {}, "terminal-result", session_id="sess-1"
        )
        is None
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
    second = coordinator.transform_tool_result(
        "read_file", {}, "read-result", session_id="sess-1"
    )

    assert first is None
    assert second is None


def test_grounding_action_card_prefers_task_title_over_tool_noise(tmp_path):
    coordinator = ConstraintKeeperCoordinator(
        client=FakeClient(), spool_root=tmp_path, identity=_identity()
    )
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
    coordinator = ConstraintKeeperCoordinator(
        client=FakeClient(), spool_root=tmp_path, identity=_identity()
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
    assert "PlayIteratorRunState" in card
    assert "lib/ansible/executor/play_iterator.py" in card
    assert "/Users/wayneliu/dev/ansible" not in card
    assert "grep -n" not in card


def test_grounding_action_card_followup_source_read_gets_one_pending_next_action_reminder(
    tmp_path,
):
    coordinator = ConstraintKeeperCoordinator(
        client=FakeClient(), spool_root=tmp_path, identity=_identity()
    )
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
    assert (
        coordinator.transform_tool_result(
            "search_files", {}, "search-result", session_id="sess-1"
        )
        is None
    )


def test_grounding_pending_next_action_is_satisfied_by_context_search(tmp_path):
    coordinator = ConstraintKeeperCoordinator(
        client=FakeClient(), spool_root=tmp_path, identity=_identity()
    )
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
    assert coordinator.transform_tool_result(
        "read_file", {}, "read-result", session_id="sess-1"
    )

    coordinator.observe_tool_result(
        "context_search",
        {"query": "Standardize `PlayIterator` state representation"},
        '{"ok": true}',
        session_id="sess-1",
    )
    assert (
        coordinator.transform_tool_result(
            "context_search", {}, "context-search-result", session_id="sess-1"
        )
        is None
    )

    coordinator.observe_tool_result(
        "read_file",
        {"path": "lib/ansible/executor/play_iterator.py"},
        '{"content": "source again"}',
        session_id="sess-1",
    )
    assert (
        coordinator.transform_tool_result(
            "read_file", {}, "read-result-2", session_id="sess-1"
        )
        is None
    )


def test_unknown_terminal_does_not_consume_pending_next_action_reminder(tmp_path):
    coordinator = ConstraintKeeperCoordinator(
        client=FakeClient(), spool_root=tmp_path, identity=_identity()
    )
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
    assert coordinator.transform_tool_result(
        "read_file", {}, "read-result", session_id="sess-1"
    )

    coordinator.observe_tool_result(
        "terminal",
        {"command": "rg PlayIterator lib/ansible/executor/play_iterator.py"},
        '{"exit_code": 0, "output": "matches"}',
        session_id="sess-1",
    )
    assert (
        coordinator.transform_tool_result(
            "terminal", {}, "terminal-result", session_id="sess-1"
        )
        is None
    )

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
    coordinator = ConstraintKeeperCoordinator(
        client=FakeClient(), spool_root=tmp_path, identity=_identity()
    )
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


def test_transform_tool_result_injects_guidance_after_exploration_without_context_search(
    tmp_path,
):
    coordinator = ConstraintKeeperCoordinator(
        client=FakeClient(), spool_root=tmp_path, identity=_identity()
    )
    coordinator.on_user_turn(
        user_message="<pr_description>Fix PlayIterator public states</pr_description>",
        session_id="sess-1",
    )
    # The initial bootstrap was not enough; the agent is exploring with shell/read tools.
    coordinator.pre_llm_call_context(session_id="sess-1")
    coordinator.observe_tool_result(
        "terminal",
        {"command": "grep -R PlayIterator lib/ansible"},
        "matches",
        session_id="sess-1",
    )
    coordinator.observe_tool_result(
        "read_file",
        {"path": "lib/ansible/executor/play_iterator.py"},
        "source",
        session_id="sess-1",
    )
    coordinator.observe_tool_result(
        "terminal",
        {"command": "grep -R FAILED_SETUP lib/ansible"},
        "matches",
        session_id="sess-1",
    )

    first = coordinator.transform_tool_result(
        "terminal", {}, "original", session_id="sess-1"
    )
    second = coordinator.transform_tool_result(
        "terminal", {}, "original", session_id="sess-1"
    )

    assert first is not None
    assert first.startswith("FormSy next action still pending")
    assert "\n---\n\noriginal" in first
    assert "context_search" in first
    assert "Action ID: grounding.seed.1" in first
    assert "Recommended next tool call" in first
    assert "Completion Gate will verify" in first
    assert second is None


def test_transform_tool_result_injects_guidance_after_repeated_terminal_failures(
    tmp_path,
):
    client = FakeClient()
    coordinator = ConstraintKeeperCoordinator(
        client=client, spool_root=tmp_path, identity=_identity()
    )
    coordinator.on_user_turn(
        user_message="<pr_description>Fix PlayIterator public states</pr_description>",
        session_id="sess-1",
    )
    result = '{"output": "ModuleNotFoundError: ansible.executor is not a package", "exit_code": 1}'
    for _ in range(3):
        coordinator.observe_tool_result(
            "terminal",
            {
                "command": "python3 -c 'from ansible.executor.play_iterator import PlayIterator'"
            },
            result,
            session_id="sess-1",
        )

    transformed = coordinator.transform_tool_result(
        "terminal", {}, "original", session_id="sess-1"
    )

    assert transformed is not None
    assert "Repeated terminal failures" in transformed
    assert "context_search" in transformed
    assert "formsy_recover" in transformed
    assert [name for name, _ in client.calls if name == "recover"] == []


def test_pre_tool_call_blocks_repeated_identical_terminal_probe(tmp_path):
    coordinator = ConstraintKeeperCoordinator(
        client=FakeClient(),
        spool_root=tmp_path,
        identity=_identity(),
    )
    command = (
        'python3 -c "import inspect; '
        "from ansible.module_utils.urls import Request; "
        'print(inspect.signature(Request.open))"'
    )
    result = {
        "exit_code": 0,
        "output": "sig (self, method, url, data=None)",
        "error": None,
    }

    coordinator.observe_tool_result(
        "terminal", {"command": command}, result, session_id="sess-1"
    )
    coordinator.observe_tool_result(
        "terminal", {"command": command}, result, session_id="sess-1"
    )

    message = coordinator.pre_tool_call_block_message(
        "terminal",
        {"command": command},
        session_id="sess-1",
    )

    assert message is not None
    assert "Repeated identical terminal probe" in message
    assert "Do not run the same probe again" in message
    assert command in message


def test_pre_tool_call_does_not_block_repeated_validation_command(tmp_path):
    coordinator = ConstraintKeeperCoordinator(
        client=FakeClient(),
        spool_root=tmp_path,
        identity=_identity(),
    )
    command = "python3 -m pytest test/units/module_utils/urls/test_Request.py -q"
    result = {"exit_code": 0, "output": "10 passed", "error": None}

    coordinator.observe_tool_result(
        "terminal", {"command": command}, result, session_id="sess-1"
    )
    coordinator.observe_tool_result(
        "terminal", {"command": command}, result, session_id="sess-1"
    )

    assert (
        coordinator.pre_tool_call_block_message(
            "terminal",
            {"command": command},
            session_id="sess-1",
        )
        is None
    )


def test_transform_tool_result_injects_guidance_after_repeated_execute_code_probes(
    tmp_path,
):
    coordinator = ConstraintKeeperCoordinator(
        client=FakeClient(), spool_root=tmp_path, identity=_identity()
    )
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

    transformed = coordinator.transform_tool_result(
        "execute_code", {}, "original", session_id="sess-1"
    )
    repeat = coordinator.transform_tool_result(
        "execute_code", {}, "original", session_id="sess-1"
    )

    assert transformed is not None
    assert "Repeated isolated code probes" in transformed
    assert "patch" in transformed
    assert "without a context_search result" not in transformed
    assert repeat is None


def test_compile_context_bundle_failure_returns_warning(tmp_path):
    client = FakeClient()
    client.compile_error = RuntimeError("server down")
    coordinator = ConstraintKeeperCoordinator(
        client=client, spool_root=tmp_path, identity=_identity()
    )

    text = coordinator.compile_context_bundle(
        query="forms model save",
        instruction="fix the bug",
        query_plan={"query": "forms model save"},
        context_bundle={"bundle_id": "bundle-1"},
        search_payload={"matches": [{"path": "django/forms/models.py"}]},
    )

    assert "Constraint Protocol compilation unavailable" in text


def test_observe_tool_result_queues_validation_success(tmp_path):
    coordinator = ConstraintKeeperCoordinator(
        client=FakeClient(), spool_root=tmp_path, identity=_identity()
    )

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
    coordinator = ConstraintKeeperCoordinator(
        client=FakeClient(), spool_root=tmp_path, identity=_identity()
    )

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
        diff_provider=lambda: (
            "diff --git a/app.py b/app.py\n--- a/app.py\n+++ b/app.py\n"
        ),
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
    assert pending[0]["payload"]["unified_diff"].startswith(
        "diff --git a/app.py b/app.py"
    )


def test_edit_diff_carries_contract_identity_captured_before_edit(tmp_path):
    class ContractClient(FakeClient):
        def __init__(self):
            super().__init__()
            self.revision = 0

        async def compile_constraints(self, payload, session_id=""):
            self.revision += 1
            targets = ["pkg/a.py", "pkg/b.py"] if self.revision == 1 else ["pkg/a.py"]
            return {
                "protocol_text": "## FormSy Constraint Protocol\n- State: PATCH_ALLOWED",
                "protocol": {
                    "contract_set_id": "contract_scope",
                    "contract_revision": self.revision,
                    "patch_scope_id": f"contract_scope:patch:{self.revision}",
                },
                "contracts": {
                    "patch": {
                        "accepted_targets": targets,
                        "strict_target_scope": False,
                    }
                },
            }

    client = ContractClient()
    coordinator = ConstraintKeeperCoordinator(
        client=client,
        spool_root=tmp_path,
        identity=_identity(),
        diff_provider=lambda: (
            "diff --git a/pkg/a.py b/pkg/a.py\n"
            "diff --git a/pkg/b.py b/pkg/b.py\n"
        ),
    )
    compile_args = {
        "query": "requested behavior",
        "instruction": "Implement the requested behavior.",
        "query_plan": {"query": "requested behavior"},
        "context_bundle": {"bundle_id": "bundle"},
        "search_payload": {},
    }
    coordinator.compile_context_bundle(**compile_args)
    assert coordinator.pre_tool_call_block_message(
        "patch", {"path": "pkg/a.py"}, session_id="sess-1"
    ) is None
    coordinator.compile_context_bundle(**compile_args)

    coordinator.observe_tool_result(
        "patch", {"path": "pkg/a.py"}, "ok", session_id="sess-1"
    )

    pending = coordinator.spool.pending("task-1", "run-1")
    diff_event = [event for event in pending if event["event_kind"] == "diff_observed"][-1]
    assert diff_event["payload"]["authorized_contract_set_id"] == "contract_scope"
    assert diff_event["payload"]["authorized_contract_revision"] == 1
    assert "patch_scope" not in diff_event["payload"]


def test_local_scope_guard_allows_advisory_outside_target(tmp_path):
    class AdvisoryClient(FakeClient):
        async def compile_constraints(self, payload, session_id=""):
            return {
                "protocol_text": "## FormSy Constraint Protocol\n- State: PATCH_ALLOWED",
                "protocol": {
                    "contract_set_id": "contract_scope",
                    "contract_revision": 1,
                },
                "contracts": {
                    "patch": {
                        "accepted_targets": ["pkg/a.py"],
                        "strict_target_scope": False,
                    }
                },
            }

    coordinator = ConstraintKeeperCoordinator(
        client=AdvisoryClient(), spool_root=tmp_path, identity=_identity()
    )
    coordinator.compile_context_bundle(
        query="requested behavior",
        instruction="Implement it.",
        query_plan={},
        context_bundle={"bundle_id": "bundle"},
        search_payload={},
    )

    assert coordinator._local_changed_files_scope_guard(  # noqa: SLF001
        {"changed_files": ["pkg/a.py", "pkg/b.py"], "diff_hash": "sha256:1"}
    ) is None


def test_local_scope_guard_blocks_strict_outside_target(tmp_path):
    class StrictClient(FakeClient):
        async def compile_constraints(self, payload, session_id=""):
            return {
                "protocol_text": "## FormSy Constraint Protocol\n- State: PATCH_ALLOWED",
                "protocol": {
                    "contract_set_id": "contract_scope",
                    "contract_revision": 1,
                },
                "contracts": {
                    "patch": {
                        "accepted_targets": ["pkg/a.py"],
                        "strict_target_scope": True,
                    }
                },
            }

    coordinator = ConstraintKeeperCoordinator(
        client=StrictClient(), spool_root=tmp_path, identity=_identity()
    )
    coordinator.compile_context_bundle(
        query="requested behavior",
        instruction="Implement it.",
        query_plan={},
        context_bundle={"bundle_id": "bundle"},
        search_payload={},
    )

    result = coordinator._local_changed_files_scope_guard(  # noqa: SLF001
        {"changed_files": ["pkg/a.py", "pkg/b.py"], "diff_hash": "sha256:1"}
    )
    assert result is not None
    assert result["decision"] == "NEED_MORE_VALIDATION"


def test_local_scope_guard_keeps_diff_scope_after_context_refresh(tmp_path):
    class RefreshClient(FakeClient):
        def __init__(self):
            super().__init__()
            self.revision = 0

        async def compile_constraints(self, payload, session_id=""):
            self.revision += 1
            targets = ["pkg/a.py", "pkg/b.py"] if self.revision == 1 else ["pkg/a.py"]
            return {
                "protocol_text": "## FormSy Constraint Protocol\n- State: PATCH_ALLOWED",
                "protocol": {
                    "contract_set_id": "contract_scope",
                    "contract_revision": self.revision,
                },
                "contracts": {
                    "patch": {
                        "accepted_targets": targets,
                        "strict_target_scope": True,
                    }
                },
            }

    coordinator = ConstraintKeeperCoordinator(
        client=RefreshClient(),
        spool_root=tmp_path,
        identity=_identity(),
        diff_provider=lambda: (
            "diff --git a/pkg/a.py b/pkg/a.py\n"
            "diff --git a/pkg/b.py b/pkg/b.py\n"
        ),
    )
    compile_args = {
        "query": "requested behavior",
        "instruction": "Implement it.",
        "query_plan": {},
        "context_bundle": {"bundle_id": "bundle"},
        "search_payload": {},
    }
    coordinator.compile_context_bundle(**compile_args)
    coordinator.pre_tool_call_block_message(
        "patch", {"path": "pkg/a.py"}, session_id="sess-1"
    )
    coordinator.observe_tool_result(
        "patch", {"path": "pkg/a.py"}, "ok", session_id="sess-1"
    )
    coordinator.compile_context_bundle(**compile_args)

    assert coordinator._local_changed_files_scope_guard(  # noqa: SLF001
        {"changed_files": ["pkg/a.py", "pkg/b.py"], "diff_hash": "sha256:1"}
    ) is None


def test_repair_projection_marks_next_compile_as_repair_patch(tmp_path):
    client = FakeClient()
    client.verify_response = {
        "decision": "NEED_MORE_VALIDATION",
        "completion_audit": {
            "projection": {"next_action_kind": "repair_patch"}
        },
    }
    coordinator = ConstraintKeeperCoordinator(
        client=client, spool_root=tmp_path, identity=_identity()
    )

    coordinator.verify_completion(session_id="sess-1")
    coordinator.compile_context_bundle(
        query="repair requested behavior",
        instruction="Repair the patch.",
        query_plan={},
        context_bundle={"bundle_id": "bundle"},
        search_payload={},
    )

    compile_call = [call for call in client.calls if call[0] == "compile"][-1]
    assert compile_call[1]["payload"]["compile_reason"] == "repair_patch"


def test_observe_tool_result_attaches_changed_file_source_snapshots(tmp_path):
    coordinator = ConstraintKeeperCoordinator(
        client=FakeClient(),
        spool_root=tmp_path,
        identity=_identity(),
        diff_provider=lambda: (
            "diff --git a/app.py b/app.py\n--- a/app.py\n+++ b/app.py\n"
        ),
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
        diff_provider=lambda: (
            "diff --git a/app.py b/app.py\n--- a/app.py\n+++ b/app.py\n"
        ),
    )

    coordinator.observe_tool_result(
        "apply_patch", {"patch": "*** Begin Patch"}, "ok", session_id="sess-1"
    )
    coordinator.observe_tool_result(
        "apply_patch", {"patch": "*** Begin Patch"}, "ok", session_id="sess-1"
    )

    pending = coordinator.spool.pending("task-1", "run-1")
    assert [event["event_kind"] for event in pending] == ["diff_observed"]


def test_verify_completion_flushes_diff_done_claim_then_verifies(tmp_path):
    client = FakeClient()
    coordinator = ConstraintKeeperCoordinator(
        client=client,
        spool_root=tmp_path,
        identity=_identity(),
        diff_provider=lambda: (
            "diff --git a/app.py b/app.py\n--- a/app.py\n+++ b/app.py\n"
        ),
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
    assert observed_kinds == [
        "diff_observed",
        "test_result",
        "completion_bootstrap_observed",
        "done_claim",
    ]
    assert client.calls[-1][0] == "verify"


def test_flush_pending_sends_server_compatible_observe_payload(tmp_path):
    client = FakeClient()
    coordinator = ConstraintKeeperCoordinator(
        client=client, spool_root=tmp_path, identity=_identity()
    )

    coordinator.observe_tool_result(
        "terminal",
        {"command": "python -m pytest tests/forms", "exit_code": 0},
        "1 passed",
        session_id="sess-1",
    )
    coordinator.flush_pending()

    observe_payload = [
        call[1]["payload"] for call in client.calls if call[0] == "observe"
    ][0]
    assert set(observe_payload) == {"event"}
    assert observe_payload["event"]["task_id"] == "task-1"
    assert observe_payload["event"]["run_id"] == "run-1"
    assert observe_payload["event"]["event_kind"] == "test_result"


def test_observe_tool_result_reports_low_sensitive_tool_observed(tmp_path):
    client = FakeClient()
    coordinator = ConstraintKeeperCoordinator(
        client=client, spool_root=tmp_path, identity=_identity()
    )

    coordinator.observe_tool_result(
        "read_file",
        {"path": "lib/ansible/executor/play_iterator.py", "offset": 1, "limit": 500},
        '{"content": "source text that must not be reported"}',
        session_id="sess-1",
    )

    observe_payloads = [
        call[1]["payload"] for call in client.calls if call[0] == "observe"
    ]
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
            self.calls.append(
                ("observe", {"payload": payload, "session_id": session_id})
            )
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

    coordinator = ConstraintKeeperCoordinator(
        client=ProtocolClient(), spool_root=tmp_path, identity=_identity()
    )

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
            self.calls.append(
                ("observe", {"payload": payload, "session_id": session_id})
            )
            return {
                "decision": "NEED_CONTEXT",
                "protocol": {
                    "state": "NEED_CONTEXT",
                    "gate_decision": "NEED_CONTEXT",
                    "summary": "Broad exploration budget is exhausted; refresh context guidance.",
                    "required_next_actions": [
                        "Call context_search with PlayIterator public state type compatibility."
                    ],
                    "suggested_queries": [
                        "PlayIterator public state type compatibility"
                    ],
                },
            }

    coordinator = ConstraintKeeperCoordinator(
        client=NeedContextClient(), spool_root=tmp_path, identity=_identity()
    )

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
    coordinator = ConstraintKeeperCoordinator(
        client=FakeClient(), spool_root=tmp_path, identity=_identity()
    )
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
    assert (
        coordinator.pre_tool_call_block_message(
            "execute_code",
            {"code": "print('probe 1')"},
            session_id="sess-1",
        )
        is None
    )
    coordinator.observe_tool_result(
        "execute_code",
        {"code": "print('probe 1')"},
        '{"status": "success"}',
        session_id="sess-1",
    )
    assert (
        coordinator.pre_tool_call_block_message(
            "terminal",
            {"command": "python3 - <<'PY'\nprint('probe 2')\nPY"},
            session_id="sess-1",
        )
        is None
    )
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
    coordinator = ConstraintKeeperCoordinator(
        client=FakeClient(), spool_root=tmp_path, identity=_identity()
    )
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
    assert (
        "NEXT SUGGESTED TOOL: context_read path=lib/ansible/executor/play_iterator.py"
        in transformed
    )
    assert "NEXT REQUIRED TOOL" not in transformed


def test_read_file_same_target_satisfies_suggested_context_read_directive(tmp_path):
    coordinator = ConstraintKeeperCoordinator(
        client=FakeClient(), spool_root=tmp_path, identity=_identity()
    )
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
    assert (
        coordinator.transform_tool_result(
            "context_search", {}, "original", session_id="sess-1"
        )
        is not None
    )

    coordinator.observe_tool_result(
        "read_file",
        {"path": "lib/ansible/executor/play_iterator.py"},
        '{"ok": true, "content": "class HostState: pass"}',
        session_id="sess-1",
    )

    assert coordinator._active_next_tool_directive is None
    assert (
        coordinator.transform_tool_result(
            "read_file",
            {"path": "lib/ansible/executor/play_iterator.py"},
            '{"ok": true, "content": "class HostState: pass"}',
            session_id="sess-1",
        )
        is None
    )


def test_context_read_satisfies_suggested_directive_and_clears_pending_card(tmp_path):
    coordinator = ConstraintKeeperCoordinator(
        client=FakeClient(), spool_root=tmp_path, identity=_identity()
    )
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
    coordinator = ConstraintKeeperCoordinator(
        client=FakeClient(), spool_root=tmp_path, identity=_identity()
    )
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
        coordinator.observe_tool_result(
            "context_read", args, result, session_id="sess-1"
        )
        assert (
            coordinator.transform_tool_result(
                "context_read", args, result, session_id="sess-1"
            )
            is None
        )
    coordinator.observe_tool_result("context_read", args, result, session_id="sess-1")
    fused = coordinator.transform_tool_result(
        "context_read", args, result, session_id="sess-1"
    )

    assert fused is not None
    payload = json.loads(fused)
    assert payload["ok"] is True
    assert payload["fused"] is True
    assert payload["content"] == ""
    assert (
        payload["context_meta"]["read_key"]
        == "lib/ansible/executor/play_iterator.py:95-100"
    )
    assert "Do not call context_read" in " ".join(payload["advisory"])


def test_degraded_probe_budget_does_not_block_before_or_after_patch_edit(tmp_path):
    coordinator = ConstraintKeeperCoordinator(
        client=FakeClient(), spool_root=tmp_path, identity=_identity()
    )
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
    assert (
        coordinator.pre_tool_call_block_message(
            "terminal",
            {"command": "python3 - <<'PY'\nprint('probe again')\nPY"},
            session_id="sess-1",
        )
        is None
    )

    coordinator.observe_tool_result(
        "patch",
        {"path": "lib/ansible/executor/play_iterator.py"},
        '{"success": true}',
        session_id="sess-1",
    )

    assert (
        coordinator.pre_tool_call_block_message(
            "terminal",
            {"command": "python3 - <<'PY'\nprint('probe after edit')\nPY"},
            session_id="sess-1",
        )
        is None
    )


def test_degraded_probe_budget_does_not_block_after_failed_patch(tmp_path):
    coordinator = ConstraintKeeperCoordinator(
        client=FakeClient(), spool_root=tmp_path, identity=_identity()
    )
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
    coordinator = ConstraintKeeperCoordinator(
        client=FakeClient(), spool_root=tmp_path, identity=_identity()
    )
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

    assert (
        coordinator.pre_tool_call_block_message(
            "terminal",
            {"command": "pytest test/units/executor/test_play_iterator.py"},
            session_id="sess-1",
        )
        is None
    )
    assert (
        coordinator.pre_tool_call_block_message(
            "terminal",
            {"command": "git diff -- lib/ansible/plugins/strategy/__init__.py"},
            session_id="sess-1",
        )
        is None
    )
    assert (
        coordinator.pre_tool_call_block_message(
            "terminal",
            {
                "command": "python -m py_compile lib/ansible/plugins/strategy/__init__.py"
            },
            session_id="sess-1",
        )
        is None
    )
    assert (
        coordinator.pre_tool_call_block_message(
            "terminal",
            {"command": "git diff --stat"},
            session_id="sess-1",
        )
        is None
    )
    assert (
        coordinator.pre_tool_call_block_message(
            "terminal",
            {"command": "wc -l patch.txt"},
            session_id="sess-1",
        )
        is None
    )
    assert (
        coordinator.pre_tool_call_block_message(
            "terminal",
            {"command": "head -n 40 patch.txt"},
            session_id="sess-1",
        )
        is None
    )
    assert (
        coordinator.pre_tool_call_block_message(
            "terminal",
            {"command": "cd /Users/wayneliu/dev/ansible && wc -l patch.txt"},
            session_id="sess-1",
        )
        is None
    )
    assert (
        coordinator.pre_tool_call_block_message(
            "terminal",
            {"command": "cd /Users/wayneliu/dev/ansible && head -50 patch.txt"},
            session_id="sess-1",
        )
        is None
    )
    assert (
        coordinator.pre_tool_call_block_message(
            "terminal",
            {
                "command": "cd /Users/wayneliu/dev/ansible && grep -E '^[+-]{3}' patch.txt | sort -u"
            },
            session_id="sess-1",
        )
        is None
    )
    assert (
        coordinator.pre_tool_call_block_message(
            "terminal",
            {"command": "cd /Users/wayneliu/dev/ansible && cat patch.txt"},
            session_id="sess-1",
        )
        is None
    )


def test_final_submit_bypasses_degraded_context_refresh_gate(tmp_path):
    coordinator = ConstraintKeeperCoordinator(
        client=FakeClient(), spool_root=tmp_path, identity=_identity()
    )
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
        "required_next_actions": [
            "Call context_search before more source exploration."
        ],
        "suggested_queries": ["PlayIterator public states"],
    }

    message = coordinator.pre_tool_call_block_message(
        "terminal",
        {"command": "echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT && cat patch.txt"},
        session_id="sess-1",
    )

    assert message is None


def test_degraded_probe_budget_discourages_repeated_full_diff_output(tmp_path):
    coordinator = ConstraintKeeperCoordinator(
        client=FakeClient(), spool_root=tmp_path, identity=_identity()
    )
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

    assert (
        coordinator.pre_tool_call_block_message(
            "terminal",
            {"command": "git diff"},
            session_id="sess-1",
        )
        is None
    )
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
    assert (
        coordinator.pre_tool_call_block_message(
            "terminal",
            {"command": "git diff -- lib/ansible/executor/play_iterator.py"},
            session_id="sess-1",
        )
        is None
    )


def test_next_tool_directive_suggests_context_read_without_blocking_exploration(
    tmp_path,
):
    coordinator = ConstraintKeeperCoordinator(
        client=FakeClient(), spool_root=tmp_path, identity=_identity()
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

    assert (
        coordinator.pre_tool_call_block_message(
            "context_read",
            {"path": "lib/ansible/executor/play_iterator.py"},
            session_id="sess-1",
        )
        is None
    )
    coordinator.observe_tool_result(
        "context_read",
        {"path": "lib/ansible/executor/play_iterator.py"},
        '{"ok": true, "path": "lib/ansible/executor/play_iterator.py"}',
        session_id="sess-1",
    )

    assert (
        coordinator.pre_tool_call_block_message(
            "terminal",
            {"command": "pytest test/units/executor/test_play_iterator.py"},
            session_id="sess-1",
        )
        is None
    )


def test_next_tool_directive_allows_same_target_fallback_after_context_read_failure(
    tmp_path,
):
    coordinator = ConstraintKeeperCoordinator(
        client=FakeClient(), spool_root=tmp_path, identity=_identity()
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

    assert (
        coordinator.pre_tool_call_block_message(
            "read_file",
            {"path": "lib/ansible/executor/play_iterator.py"},
            session_id="sess-1",
        )
        is None
    )
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
    assert (
        coordinator.pre_tool_call_block_message(
            "terminal",
            {"command": "pytest test/units/executor/test_play_iterator.py"},
            session_id="sess-1",
        )
        is None
    )


def test_failed_context_read_directive_fails_open_to_avoid_retry_deadlock(tmp_path):
    coordinator = ConstraintKeeperCoordinator(
        client=FakeClient(), spool_root=tmp_path, identity=_identity()
    )
    guidance_result = {
        "ok": True,
        "guidance_packet": {
            "mode": "degraded_recovery",
            "target_candidates": ["lib/ansible/executor/play_iterator.py"],
            "required_next_tool": {
                "tool": "context_read",
                "args": {
                    "path": "Users/wayneliu/dev/ansible/lib/ansible/executor/play_iterator.py"
                },
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

    assert (
        coordinator.pre_tool_call_block_message(
            "read_file",
            {"path": "lib/ansible/executor/play_iterator.py"},
            session_id="sess-1",
        )
        is None
    )
    coordinator.observe_tool_result(
        "read_file",
        {"path": "lib/ansible/executor/play_iterator.py"},
        "class PlayIterator:\n    pass\n",
        session_id="sess-1",
    )

    assert (
        coordinator.pre_tool_call_block_message(
            "patch",
            {"path": "lib/ansible/executor/play_iterator.py"},
            session_id="sess-1",
        )
        is None
    )


def test_recover_and_verify_send_server_compatible_payloads(tmp_path):
    client = FakeClient()
    coordinator = ConstraintKeeperCoordinator(
        client=client, spool_root=tmp_path, identity=_identity()
    )

    coordinator.recover(reason="same failure", session_id="sess-1")
    coordinator.verify_completion(session_id="sess-1")

    recover_payload = [
        call[1]["payload"] for call in client.calls if call[0] == "recover"
    ][0]
    verify_payload = [
        call[1]["payload"] for call in client.calls if call[0] == "verify"
    ][0]
    assert recover_payload == {
        "task_id": "task-1",
        "run_id": "run-1",
        "reason": "same failure",
    }
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

    verify_payload = [
        call[1]["payload"] for call in client.calls if call[0] == "verify"
    ][-1]
    bootstrap = verify_payload["completion_bootstrap"]
    assert (
        bootstrap["instruction"] == "<pr_description>Fix app behavior</pr_description>"
    )
    assert bootstrap["instruction_freshness"] == "current_run"
    assert bootstrap["unified_diff"] == diff
    assert bootstrap["changed_files"] == ["app.py"]
    assert bootstrap["post_patch_sources"] == {"app.py": source}
    assert bootstrap["diff_hash"].startswith("sha256:")
    assert bootstrap["source_snapshot_hashes"]["app.py"].startswith("sha256:")


def test_successful_validation_observes_current_diff_before_test_result(tmp_path):
    client = FakeClient()
    diff = """diff --git a/lib/ansible/module_utils/urls.py b/lib/ansible/module_utils/urls.py
--- a/lib/ansible/module_utils/urls.py
+++ b/lib/ansible/module_utils/urls.py
@@ -1,2 +1,3 @@
+HAS_GZIP = True
 def open_url():
     return None
"""
    coordinator = ConstraintKeeperCoordinator(
        client=client,
        spool_root=tmp_path,
        identity=_identity(),
        diff_provider=lambda: diff,
    )

    coordinator.observe_tool_result(
        "terminal",
        {"command": "cd /repo && python3 test_gzip_validation.py"},
        {"exit_code": 0, "output": "All tests passed!"},
        session_id="sess-1",
    )
    coordinator.flush_pending()

    observed = [
        call[1]["payload"]["event"] for call in client.calls if call[0] == "observe"
    ]
    event_kinds = [event["event_kind"] for event in observed]
    assert event_kinds.index("diff_observed") < event_kinds.index("test_result")
    test_event = next(
        event for event in observed if event["event_kind"] == "test_result"
    )
    assert test_event["payload"]["diff_context_hash"].startswith("sha256:")


def test_verify_completion_sends_resolved_tocs_context_hint(tmp_path):
    class StaleCompileClient(FakeClient):
        async def compile_constraints(self, payload, session_id=""):
            self.calls.append(
                ("compile", {"payload": payload, "session_id": session_id})
            )
            return {
                "contracts": {
                    "patch": {
                        "accepted_targets": ["lib/ansible/utils/display.py"],
                    }
                },
                "protocol": {
                    "gate_decision": "PATCH_ALLOWED_WITH_WARNINGS",
                    "summary": "stale ordinary target",
                },
            }

    client = StaleCompileClient()
    diff = """diff --git a/lib/ansible/modules/iptables.py b/lib/ansible/modules/iptables.py
--- a/lib/ansible/modules/iptables.py
+++ b/lib/ansible/modules/iptables.py
@@ -1,2 +1,3 @@
+CHAIN_STATE = "present"
 def main():
     pass
"""
    coordinator = ConstraintKeeperCoordinator(
        client=client,
        spool_root=tmp_path,
        identity=_identity(),
        diff_provider=lambda: diff,
        source_provider=lambda paths: {
            "lib/ansible/modules/iptables.py": "def main():\n    pass\n",
        },
    )
    coordinator.on_user_turn(
        user_message="<pr_description>Fix chain management behavior</pr_description>",
        session_id="sess-1",
    )
    coordinator.compile_context_bundle(
        query="chain management",
        instruction="fix chain management behavior",
        query_plan={},
        context_bundle={
            "primary_files": [
                {
                    "path": "lib/ansible/utils/display.py",
                    "symbols": ["Display"],
                    "priority": "must_edit",
                }
            ],
        },
        search_payload={
            "accepted_targets": ["lib/ansible/utils/display.py"],
            "guidance": {
                "tocs": {
                    "delivery": {"resolved": True},
                    "must_read_files": [
                        {
                            "path": "lib/ansible/modules/iptables.py",
                            "source_role": "implementation",
                        }
                    ],
                }
            },
        },
    )

    coordinator.verify_completion(session_id="sess-1")

    verify_payload = [
        call[1]["payload"] for call in client.calls if call[0] == "verify"
    ][-1]
    hint = verify_payload["completion_bootstrap"]["context_bundle_hint"]
    assert hint["guidance"]["tocs"]["delivery"]["resolved"] is True
    assert hint["tocs_repair_targets"] == ["lib/ansible/modules/iptables.py"]
    assert hint["accepted_targets"] == ["lib/ansible/modules/iptables.py"]


def test_verify_completion_observes_bootstrap_summary_before_server_verify(tmp_path):
    class StaleCompileClient(FakeClient):
        async def compile_constraints(self, payload, session_id=""):
            self.calls.append(
                ("compile", {"payload": payload, "session_id": session_id})
            )
            return {
                "contracts": {
                    "patch": {
                        "accepted_targets": ["lib/ansible/utils/display.py"],
                    }
                }
            }

    client = StaleCompileClient()
    diff = """diff --git a/lib/ansible/modules/iptables.py b/lib/ansible/modules/iptables.py
--- a/lib/ansible/modules/iptables.py
+++ b/lib/ansible/modules/iptables.py
@@ -1,2 +1,3 @@
+CHAIN_STATE = "present"
 def main():
     pass
"""
    coordinator = ConstraintKeeperCoordinator(
        client=client,
        spool_root=tmp_path,
        identity=_identity(),
        diff_provider=lambda: diff,
        source_provider=lambda paths: {
            "lib/ansible/modules/iptables.py": "def main():\n    pass\n",
        },
    )
    coordinator.on_user_turn(
        user_message="<pr_description>Fix chain management behavior</pr_description>",
        session_id="sess-1",
    )
    coordinator.compile_context_bundle(
        query="chain management",
        instruction="fix chain management behavior",
        query_plan={},
        context_bundle={},
        search_payload={
            "guidance": {
                "tocs": {
                    "delivery": {"resolved": True},
                    "must_read_files": [
                        {
                            "path": "lib/ansible/modules/iptables.py",
                            "source_role": "implementation",
                        }
                    ],
                }
            },
        },
    )

    coordinator.verify_completion(session_id="sess-1")

    observe_events = [
        call[1]["payload"]["event"] for call in client.calls if call[0] == "observe"
    ]
    bootstrap_events = [
        event
        for event in observe_events
        if event.get("event_kind") == "completion_bootstrap_observed"
    ]
    assert bootstrap_events
    payload = bootstrap_events[-1]["payload"]
    assert payload["completion_bootstrap_present"] is True
    assert payload["context_bundle_hint_present"] is True
    assert payload["tocs_repair_targets"] == ["lib/ansible/modules/iptables.py"]
    assert payload["accepted_targets"] == ["lib/ansible/modules/iptables.py"]
    assert payload["changed_files"] == ["lib/ansible/modules/iptables.py"]
    assert payload["diff_hash"].startswith("sha256:")
    assert "unified_diff" not in payload
    assert "post_patch_sources" not in payload

    observe_indices = [
        index
        for index, call in enumerate(client.calls)
        if call[0] == "observe"
        and call[1]["payload"]["event"].get("event_kind")
        == "completion_bootstrap_observed"
    ]
    verify_indices = [
        index for index, call in enumerate(client.calls) if call[0] == "verify"
    ]
    assert observe_indices[-1] < verify_indices[-1]


def test_verify_completion_blocks_suspicious_module_assignment_deletion_before_server_accept(
    tmp_path,
):
    client = FakeClient()
    client.verify_response = {
        "decision": "ACCEPT_DONE",
        "protocol": {
            "summary": "Server would accept.",
            "blocking_conditions": [],
            "required_next_actions": [],
        },
    }
    diff = """diff --git a/lib/ansible/module_utils/urls.py b/lib/ansible/module_utils/urls.py
--- a/lib/ansible/module_utils/urls.py
+++ b/lib/ansible/module_utils/urls.py
@@ -187,7 +187,28 @@ try:
 except ImportError:
     HAS_GSSAPI = False

-GSSAPI_IMP_ERR = None
+try:
+    import gzip
+    HAS_GZIP = True
+except ImportError:
+    HAS_GZIP = False

 try:
     import gssapi
"""
    coordinator = ConstraintKeeperCoordinator(
        client=client,
        spool_root=tmp_path,
        identity=_identity(),
        diff_provider=lambda: diff,
    )

    result = coordinator.verify_completion(session_id="sess-1")

    assert result["decision"] == "NEED_MORE_VALIDATION"
    assert result["protocol"]["blocking_conditions"] == [
        "Patch semantic guard found suspicious module-level assignment deletion: "
        "lib/ansible/module_utils/urls.py removes GSSAPI_IMP_ERR without replacement."
    ]
    assert result["protocol"]["required_next_actions"] == [
        "Review the diff and restore or intentionally replace the removed module-level assignment."
    ]
    assert not [call for call in client.calls if call[0] == "verify"]


def test_verify_completion_allows_module_assignment_replacement(tmp_path):
    client = FakeClient()
    diff = """diff --git a/app.py b/app.py
--- a/app.py
+++ b/app.py
@@ -1,4 +1,4 @@
-FEATURE_FLAG = False
+FEATURE_FLAG = True
 def run():
     return FEATURE_FLAG
"""
    coordinator = ConstraintKeeperCoordinator(
        client=client,
        spool_root=tmp_path,
        identity=_identity(),
        diff_provider=lambda: diff,
    )

    result = coordinator.verify_completion(session_id="sess-1")

    assert result == {"gate_decision": "accepted"}
    assert [call for call in client.calls if call[0] == "verify"]


def test_verify_completion_blocks_warning_bearing_validation_before_server_accept(
    tmp_path,
):
    client = FakeClient()
    client.verify_response = {"decision": "ACCEPT_DONE"}
    diff = """diff --git a/lib/ansible/module_utils/urls.py b/lib/ansible/module_utils/urls.py
--- a/lib/ansible/module_utils/urls.py
+++ b/lib/ansible/module_utils/urls.py
@@ -1,3 +1,4 @@
 def open_url():
+    return "gzip"
     return "plain"
"""
    coordinator = ConstraintKeeperCoordinator(
        client=client,
        spool_root=tmp_path,
        identity=_identity(),
        diff_provider=lambda: diff,
    )
    coordinator.observe_tool_result(
        "terminal",
        {"command": "python3 -m pytest test/units/module_utils/urls/test_gzip.py -v"},
        {
            "exit_code": 0,
            "output": (
                "test_Request_open_gzip PASSED\n"
                "PytestUnraisableExceptionWarning: Exception ignored in: <http.client.HTTPResponse object>\n"
                "ValueError: I/O operation on closed file.\n"
                "4 passed in 0.34s\n"
            ),
        },
        session_id="sess-1",
    )

    result = coordinator.verify_completion(session_id="sess-1")

    assert result["decision"] == "NEED_MORE_VALIDATION"
    assert result["completion_audit"]["evidence"]["local_patch_semantic_guard"] == (
        "warning_bearing_validation"
    )
    assert any(
        "warning-bearing validation output" in item
        for item in result["protocol"]["blocking_conditions"]
    )
    assert not [call for call in client.calls if call[0] == "verify"]


def test_verify_completion_blocks_product_diff_outside_accepted_targets_before_server_accept(
    tmp_path,
):
    class StrictScopeClient(FakeClient):
        async def compile_constraints(self, payload, session_id=""):
            return {
                "protocol_text": "## FormSy Constraint Protocol\n- State: PATCH_ALLOWED",
                "protocol": {
                    "contract_set_id": "contract_scope",
                    "contract_revision": 1,
                },
                "contracts": {
                    "patch": {
                        "accepted_targets": [
                            "lib/ansible/module_utils/urls.py"
                        ],
                        "strict_target_scope": True,
                    }
                },
            }

    client = StrictScopeClient()
    client.verify_response = {"decision": "ACCEPT_DONE"}
    diff = """diff --git a/lib/ansible/module_utils/urls.py b/lib/ansible/module_utils/urls.py
--- a/lib/ansible/module_utils/urls.py
+++ b/lib/ansible/module_utils/urls.py
@@ -1,2 +1,3 @@
+HAS_GZIP = True
 def open_url():
     return None
diff --git a/lib/ansible/module_utils/unrelated.py b/lib/ansible/module_utils/unrelated.py
--- a/lib/ansible/module_utils/unrelated.py
+++ b/lib/ansible/module_utils/unrelated.py
@@ -1,2 +1,3 @@
+SIDE_EFFECT = True
 def unrelated():
     pass
"""
    coordinator = ConstraintKeeperCoordinator(
        client=client,
        spool_root=tmp_path,
        identity=_identity(),
        diff_provider=lambda: diff,
    )
    coordinator.compile_context_bundle(
        query="Request.open gzip",
        instruction="fix gzip decoding",
        query_plan={},
        context_bundle={"bundle_id": "bundle-1"},
        search_payload={
            "accepted_targets": ["lib/ansible/module_utils/urls.py"],
            "tocs_repair_targets": ["lib/ansible/module_utils/urls.py"],
        },
    )
    coordinator.observe_tool_result(
        "terminal",
        {"command": "python3 -m pytest test/units/module_utils/urls/test_gzip.py -q"},
        {"exit_code": 0, "output": "4 passed"},
        session_id="sess-1",
    )

    result = coordinator.verify_completion(session_id="sess-1")

    assert result["decision"] == "NEED_MORE_VALIDATION"
    assert result["completion_audit"]["evidence"]["local_patch_semantic_guard"] == (
        "changed_files_outside_accepted_targets"
    )
    assert result["completion_audit"]["evidence"]["outside_accepted_targets"] == [
        "lib/ansible/module_utils/unrelated.py"
    ]
    assert result["protocol"]["required_next_actions"] == [
        (
            "Revert or remove changes outside accepted targets: "
            "lib/ansible/module_utils/unrelated.py."
        ),
        "Keep patch edits limited to accepted targets: lib/ansible/module_utils/urls.py.",
        (
            "Do not modify tests to satisfy validation unless FormSy explicitly "
            "updates accepted targets."
        ),
        (
            "After the outside-target diff is gone, rerun the relevant validation "
            "and call Completion Verifier again."
        ),
    ]
    projection = ConstraintKeeperCoordinator._completion_audit_projection_text(result)
    assert "- Local guard: changed_files_outside_accepted_targets" in projection
    assert (
        "- Outside accepted targets: lib/ansible/module_utils/unrelated.py"
        in projection
    )
    assert "- Accepted targets: lib/ansible/module_utils/urls.py" in projection
    assert not [call for call in client.calls if call[0] == "verify"]


def test_verify_completion_allows_validation_collateral_outside_accepted_targets_to_server(
    tmp_path,
):
    client = FakeClient()
    client.verify_response = {
        "decision": "NEED_MORE_VALIDATION",
        "completion_audit": {
            "audit_status": "needs_validation",
            "gate_decision": "NEED_MORE_VALIDATION",
            "evidence": {
                "accepted_targets": ["lib/ansible/modules/iptables.py"],
                "validation_collateral": ["test/units/modules/test_iptables.py"],
            },
        },
    }
    diff = """diff --git a/lib/ansible/modules/iptables.py b/lib/ansible/modules/iptables.py
--- a/lib/ansible/modules/iptables.py
+++ b/lib/ansible/modules/iptables.py
@@ -1,2 +1,3 @@
+CHAIN_MANAGEMENT = True
 def main():
     pass
diff --git a/test/units/modules/test_iptables.py b/test/units/modules/test_iptables.py
--- a/test/units/modules/test_iptables.py
+++ b/test/units/modules/test_iptables.py
@@ -1,2 +1,3 @@
+def test_chain_management():
+    pass
"""
    coordinator = ConstraintKeeperCoordinator(
        client=client,
        spool_root=tmp_path,
        identity=_identity(),
        diff_provider=lambda: diff,
    )
    coordinator.compile_context_bundle(
        query="iptables chain management",
        instruction="add chain management support",
        query_plan={},
        context_bundle={"bundle_id": "bundle-1"},
        search_payload={
            "accepted_targets": ["lib/ansible/modules/iptables.py"],
            "tocs_repair_targets": ["lib/ansible/modules/iptables.py"],
        },
    )
    coordinator.observe_tool_result(
        "terminal",
        {"command": "python3 -m pytest test/units/modules/test_iptables.py -q"},
        {"exit_code": 0, "output": "27 passed"},
        session_id="sess-1",
    )

    result = coordinator.verify_completion(session_id="sess-1")

    assert result["completion_audit"]["evidence"]["validation_collateral"] == [
        "test/units/modules/test_iptables.py"
    ]
    assert (
        result["completion_audit"]["evidence"].get("local_patch_semantic_guard") is None
    )
    assert [call for call in client.calls if call[0] == "verify"]


def test_verify_completion_does_not_local_block_written_existing_test_collateral(
    tmp_path,
):
    client = FakeClient()
    client.verify_response = {
        "decision": "ACCEPT_DONE",
        "completion_audit": {
            "audit_status": "verified",
            "gate_decision": "ACCEPT_DONE",
            "evidence": {
                "accepted_targets": ["lib/ansible/module_utils/urls.py"],
                "validation_collateral": [
                    "test/units/module_utils/urls/test_Request.py"
                ],
            },
        },
    }
    diff = """diff --git a/lib/ansible/module_utils/urls.py b/lib/ansible/module_utils/urls.py
--- a/lib/ansible/module_utils/urls.py
+++ b/lib/ansible/module_utils/urls.py
@@ -1,2 +1,3 @@
+HAS_GZIP = True
 def open_url():
     return None
diff --git a/test/units/module_utils/urls/test_Request.py b/test/units/module_utils/urls/test_Request.py
--- a/test/units/module_utils/urls/test_Request.py
+++ b/test/units/module_utils/urls/test_Request.py
@@ -1,2 +1,3 @@
+def test_Request_open_gzip():
+    pass
"""
    coordinator = ConstraintKeeperCoordinator(
        client=client,
        spool_root=tmp_path,
        identity=_identity(),
        diff_provider=lambda: diff,
    )
    coordinator.compile_context_bundle(
        query="Request.open gzip",
        instruction="fix gzip decoding",
        query_plan={},
        context_bundle={"bundle_id": "bundle-1"},
        search_payload={
            "accepted_targets": ["lib/ansible/module_utils/urls.py"],
            "tocs_repair_targets": ["lib/ansible/module_utils/urls.py"],
        },
    )
    coordinator.observe_tool_result(
        "patch",
        {"path": "/repo/test/units/module_utils/urls/test_Request.py"},
        {"success": True},
        session_id="sess-1",
    )
    coordinator.observe_tool_result(
        "terminal",
        {
            "command": "python3 -m pytest test/units/module_utils/urls/test_Request.py -q"
        },
        {"exit_code": 0, "output": "35 passed"},
        session_id="sess-1",
    )

    result = coordinator.verify_completion(session_id="sess-1")

    assert result["decision"] == "ACCEPT_DONE"
    assert [call for call in client.calls if call[0] == "verify"]


def test_verify_completion_blocks_unreviewed_written_validation_script(tmp_path):
    client = FakeClient()
    client.verify_response = {"decision": "ACCEPT_DONE"}
    diff = """diff --git a/lib/ansible/module_utils/urls.py b/lib/ansible/module_utils/urls.py
--- a/lib/ansible/module_utils/urls.py
+++ b/lib/ansible/module_utils/urls.py
@@ -1,2 +1,3 @@
+HAS_GZIP = True
 def open_url():
     return None
"""
    validation_script = tmp_path / "test_gzip_validation.py"
    coordinator = ConstraintKeeperCoordinator(
        client=client,
        spool_root=tmp_path / "spool",
        identity=_identity(),
        diff_provider=lambda: diff,
    )
    coordinator.compile_context_bundle(
        query="Request.open gzip",
        instruction="fix gzip decoding",
        query_plan={},
        context_bundle={"bundle_id": "bundle-1"},
        search_payload={"accepted_targets": ["lib/ansible/module_utils/urls.py"]},
    )
    coordinator.observe_tool_result(
        "write_file",
        {
            "path": str(validation_script),
            "content": "print('All tests passed!')\n",
        },
        {"ok": True},
        session_id="sess-1",
    )
    validation_script.write_text("print('All tests passed!')\n", encoding="utf-8")
    coordinator.observe_tool_result(
        "terminal",
        {"command": f"python3 {validation_script}"},
        {"exit_code": 0, "output": "All tests passed!"},
        session_id="sess-1",
    )

    result = coordinator.verify_completion(session_id="sess-1")

    assert result["decision"] == "NEED_MORE_VALIDATION"
    assert result["completion_audit"]["evidence"]["local_patch_semantic_guard"] == (
        "unreviewed_validation_collateral"
    )
    assert result["completion_audit"]["evidence"]["validation_collateral"] == [
        str(validation_script)
    ]
    assert result["completion_audit"]["projection"]["next_action_kind"] == (
        "cleanup_or_review_validation_collateral"
    )
    assert result["completion_audit"]["projection"]["agent_loop_terminal"] is False
    assert (
        "Do not claim completion while still-existing ad-hoc validation files are unreviewed."
        in result["completion_audit"]["projection"]["forbidden_actions"]
    )
    assert (
        "Validation evidence exists, but this run also wrote validation collateral "
        in result["protocol"]["blocking_conditions"][0]
    )
    assert not [call for call in client.calls if call[0] == "verify"]


def test_verify_completion_includes_written_product_test_file_in_diff(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    client = FakeClient()
    client.verify_response = {"decision": "ACCEPT_DONE"}
    diff = """diff --git a/lib/ansible/module_utils/urls.py b/lib/ansible/module_utils/urls.py
--- a/lib/ansible/module_utils/urls.py
+++ b/lib/ansible/module_utils/urls.py
@@ -1,2 +1,3 @@
+HAS_GZIP = True
 def open_url():
     return None
"""
    test_path = "test/units/module_utils/urls/test_gzip.py"
    test_source = "def test_gzip_response_decoded():\n    assert True\n"
    target = tmp_path / test_path
    target.parent.mkdir(parents=True)
    target.write_text(test_source, encoding="utf-8")
    coordinator = ConstraintKeeperCoordinator(
        client=client,
        spool_root=tmp_path / "spool",
        identity=_identity(),
        diff_provider=lambda: diff,
    )
    coordinator.compile_context_bundle(
        query="Request.open gzip",
        instruction="fix gzip decoding",
        query_plan={},
        context_bundle={"bundle_id": "bundle-1"},
        search_payload={"accepted_targets": ["lib/ansible/module_utils/urls.py"]},
    )
    coordinator.observe_tool_result(
        "write_file",
        {
            "path": test_path,
            "content": test_source,
        },
        {"ok": True},
        session_id="sess-1",
    )
    coordinator.observe_tool_result(
        "terminal",
        {"command": f"python3 -m pytest {test_path} -q"},
        {"exit_code": 0, "output": "1 passed"},
        session_id="sess-1",
    )

    result = coordinator.verify_completion(session_id="sess-1")

    assert result["decision"] == "ACCEPT_DONE"
    verify_calls = [call for call in client.calls if call[0] == "verify"]
    assert verify_calls
    completion_bootstrap = verify_calls[-1][1]["payload"]["completion_bootstrap"]
    assert test_path in completion_bootstrap["changed_files"]
    assert (
        f"diff --git a/{test_path} b/{test_path}"
        in completion_bootstrap["unified_diff"]
    )


def test_verify_completion_ignores_deleted_temporary_validation_script(tmp_path):
    client = FakeClient()
    client.verify_response = {"decision": "ACCEPT_DONE"}
    diff = """diff --git a/lib/ansible/module_utils/urls.py b/lib/ansible/module_utils/urls.py
--- a/lib/ansible/module_utils/urls.py
+++ b/lib/ansible/module_utils/urls.py
@@ -1,2 +1,3 @@
+HAS_GZIP = True
 def open_url():
     return None
"""
    validation_script = tmp_path / "test_gzip_validation.py"
    coordinator = ConstraintKeeperCoordinator(
        client=client,
        spool_root=tmp_path / "spool",
        identity=_identity(),
        diff_provider=lambda: diff,
    )
    coordinator.compile_context_bundle(
        query="Request.open gzip",
        instruction="fix gzip decoding",
        query_plan={},
        context_bundle={"bundle_id": "bundle-1"},
        search_payload={"accepted_targets": ["lib/ansible/module_utils/urls.py"]},
    )
    coordinator.observe_tool_result(
        "write_file",
        {
            "path": str(validation_script),
            "content": "print('All tests passed!')\n",
        },
        {"ok": True},
        session_id="sess-1",
    )
    validation_script.write_text("print('All tests passed!')\n", encoding="utf-8")
    coordinator.observe_tool_result(
        "terminal",
        {"command": f"python3 {validation_script}"},
        {"exit_code": 0, "output": "All tests passed!"},
        session_id="sess-1",
    )
    validation_script.unlink()

    result = coordinator.verify_completion(session_id="sess-1")

    assert result["decision"] == "ACCEPT_DONE"
    assert [call for call in client.calls if call[0] == "verify"]


def test_verify_completion_blocks_unresolved_failed_validation_after_narrow_pass(
    tmp_path,
):
    client = FakeClient()
    client.verify_response = {"decision": "ACCEPT_DONE"}
    diff = """diff --git a/lib/ansible/module_utils/urls.py b/lib/ansible/module_utils/urls.py
--- a/lib/ansible/module_utils/urls.py
+++ b/lib/ansible/module_utils/urls.py
@@ -1,2 +1,3 @@
+HAS_GZIP = True
 def open_url():
     return None
"""
    coordinator = ConstraintKeeperCoordinator(
        client=client,
        spool_root=tmp_path,
        identity=_identity(),
        diff_provider=lambda: diff,
    )
    coordinator.compile_context_bundle(
        query="Request.open gzip",
        instruction="fix gzip decoding",
        query_plan={},
        context_bundle={"bundle_id": "bundle-1"},
        search_payload={"accepted_targets": ["lib/ansible/module_utils/urls.py"]},
    )
    coordinator.observe_tool_result(
        "terminal",
        {"command": "python3 -m pytest test/units/module_utils/urls -q"},
        {"exit_code": 1, "output": "7 failed, 76 passed"},
        session_id="sess-1",
    )
    coordinator.observe_tool_result(
        "terminal",
        {"command": "python3 -m pytest test/units/module_utils/urls/test_gzip.py -q"},
        {"exit_code": 0, "output": "4 passed"},
        session_id="sess-1",
    )

    result = coordinator.verify_completion(session_id="sess-1")

    assert result["decision"] == "NEED_MORE_VALIDATION"
    assert result["completion_audit"]["evidence"]["local_patch_semantic_guard"] == (
        "unresolved_failed_validation"
    )
    assert result["completion_audit"]["evidence"]["failed_validation_commands"] == [
        "python3 -m pytest test/units/module_utils/urls -q"
    ]
    assert not [call for call in client.calls if call[0] == "verify"]


def test_verify_completion_ignores_repo_external_pytest_collection_probe_after_focused_pass(
    tmp_path,
):
    client = FakeClient()
    client.verify_response = {"decision": "ACCEPT_DONE"}
    diff = """diff --git a/lib/ansible/modules/iptables.py b/lib/ansible/modules/iptables.py
--- a/lib/ansible/modules/iptables.py
+++ b/lib/ansible/modules/iptables.py
@@ -1,2 +1,3 @@
+CHAIN_MANAGEMENT_FIXED = True
 def main():
     return None
diff --git a/test/units/modules/test_iptables.py b/test/units/modules/test_iptables.py
--- a/test/units/modules/test_iptables.py
+++ b/test/units/modules/test_iptables.py
@@ -1,2 +1,5 @@
+def test_chain_creation_check_mode():
+    assert True
"""
    coordinator = ConstraintKeeperCoordinator(
        client=client,
        spool_root=tmp_path,
        identity=_identity(),
        diff_provider=lambda: diff,
    )
    coordinator.compile_context_bundle(
        query="iptables chain management check mode",
        instruction="fix iptables chain management",
        query_plan={},
        context_bundle={"bundle_id": "bundle-1"},
        search_payload={"accepted_targets": ["lib/ansible/modules/iptables.py"]},
    )
    coordinator.observe_tool_result(
        "terminal",
        {"command": 'pytest -v /tmp/orig_iptables_test.py -k "chain_creation or chain_deletion"'},
        {
            "exit_code": 2,
            "output": (
                "ERROR collecting /tmp/orig_iptables_test.py\n"
                "E   ModuleNotFoundError: No module named 'units'\n"
                "!!!!!!!!!!!!!!!!!!!! Interrupted: 1 error during collection !!!!!!!!!!!!!!!!!!!!\n"
                "1 error in 0.23s"
            ),
        },
        session_id="sess-1",
    )
    coordinator.observe_tool_result(
        "terminal",
        {"command": 'pytest -v test/units/modules/test_iptables.py -k "chain"'},
        {"exit_code": 0, "output": "7 passed, 22 deselected"},
        session_id="sess-1",
    )

    result = coordinator.verify_completion(session_id="sess-1")

    assert result["decision"] == "ACCEPT_DONE"
    assert [call for call in client.calls if call[0] == "verify"]


def test_verify_completion_ignores_broad_unrelated_failed_validation_after_focused_pass(
    tmp_path,
):
    client = FakeClient()
    client.verify_response = {"decision": "ACCEPT_DONE"}
    diff = """diff --git a/lib/ansible/module_utils/urls.py b/lib/ansible/module_utils/urls.py
--- a/lib/ansible/module_utils/urls.py
+++ b/lib/ansible/module_utils/urls.py
@@ -1,2 +1,3 @@
+HAS_GZIP = True
 def open_url():
     return None
diff --git a/test/units/module_utils/urls/test_Request.py b/test/units/module_utils/urls/test_Request.py
--- a/test/units/module_utils/urls/test_Request.py
+++ b/test/units/module_utils/urls/test_Request.py
@@ -1,2 +1,5 @@
+def test_request_open_gzip_response_decompress():
+    assert True
"""
    coordinator = ConstraintKeeperCoordinator(
        client=client,
        spool_root=tmp_path,
        identity=_identity(),
        diff_provider=lambda: diff,
    )
    focused_command = (
        "python3 -m pytest "
        "test/units/module_utils/urls/test_Request.py::"
        "test_request_open_gzip_response_decompress -q"
    )
    coordinator.compile_context_bundle(
        query="Request.open gzip",
        instruction="fix gzip decoding",
        query_plan={},
        context_bundle={"bundle_id": "bundle-1"},
        search_payload={
            "accepted_targets": ["lib/ansible/module_utils/urls.py"],
            "guidance": {
                "tocs": {
                    "candidate_tests": [
                        {
                            "test_id": (
                                "test/units/module_utils/urls/test_Request.py::"
                                "test_request_open_gzip_response_decompress"
                            ),
                            "command": focused_command,
                        }
                    ]
                }
            },
        },
    )
    coordinator.observe_tool_result(
        "terminal",
        {"command": "python3 -m pytest test/units/module_utils/urls -q"},
        {
            "exit_code": 1,
            "output": (
                "FAILED test/units/module_utils/urls/test_fetch_url.py::test_proxy_error\n"
                "FAILED test/units/module_utils/urls/test_uri.py::test_legacy_timeout\n"
                "2 failed, 81 passed"
            ),
        },
        session_id="sess-1",
    )
    coordinator.observe_tool_result(
        "terminal",
        {"command": focused_command},
        {"exit_code": 0, "output": "1 passed"},
        session_id="sess-1",
    )

    result = coordinator.verify_completion(session_id="sess-1")

    assert result["decision"] == "ACCEPT_DONE"
    assert [call for call in client.calls if call[0] == "verify"]


def test_verify_completion_allows_explicitly_narrowed_validation_after_unrelated_failed_test(
    tmp_path,
):
    client = FakeClient()
    client.verify_response = {"decision": "ACCEPT_DONE"}
    diff = """diff --git a/lib/ansible/module_utils/urls.py b/lib/ansible/module_utils/urls.py
--- a/lib/ansible/module_utils/urls.py
+++ b/lib/ansible/module_utils/urls.py
@@ -1,2 +1,3 @@
+HAS_GZIP = True
 def open_url():
     return None
diff --git a/test/units/module_utils/urls/test_Request.py b/test/units/module_utils/urls/test_Request.py
--- a/test/units/module_utils/urls/test_Request.py
+++ b/test/units/module_utils/urls/test_Request.py
@@ -1,2 +1,5 @@
+def test_request_open_decompress_default():
+    assert True
diff --git a/test/units/module_utils/urls/test_fetch_url.py b/test/units/module_utils/urls/test_fetch_url.py
--- a/test/units/module_utils/urls/test_fetch_url.py
+++ b/test/units/module_utils/urls/test_fetch_url.py
@@ -1,2 +1,3 @@
+def test_fetch_url_signature_keeps_default():
+    assert True
"""
    coordinator = ConstraintKeeperCoordinator(
        client=client,
        spool_root=tmp_path,
        identity=_identity(),
        diff_provider=lambda: diff,
    )
    coordinator.compile_context_bundle(
        query="Request.open gzip",
        instruction="fix gzip decoding",
        query_plan={},
        context_bundle={"bundle_id": "bundle-1"},
        search_payload={"accepted_targets": ["lib/ansible/module_utils/urls.py"]},
    )
    failed_command = (
        "python -m pytest test/units/module_utils/urls/test_gzip.py "
        "test/units/module_utils/urls/test_Request.py "
        "test/units/module_utils/urls/test_fetch_url.py -v"
    )
    narrowed_command = (
        "python -m pytest test/units/module_utils/urls/test_gzip.py "
        "test/units/module_utils/urls/test_Request.py "
        "test/units/module_utils/urls/test_fetch_url.py "
        "-v --deselect "
        "test/units/module_utils/urls/test_fetch_url.py::test_fetch_url_cookies"
    )
    coordinator.observe_tool_result(
        "terminal",
        {"command": failed_command},
        {
            "exit_code": 1,
            "output": (
                "FAILED test/units/module_utils/urls/test_fetch_url.py::"
                "test_fetch_url_cookies - AssertionError\n"
                "1 failed, 56 passed"
            ),
        },
        session_id="sess-1",
    )
    coordinator.observe_tool_result(
        "terminal",
        {
            "command": (
                "python -m pytest test/units/module_utils/urls/"
                "test_fetch_url.py::test_fetch_url_cookies -v -p no:randomly"
            )
        },
        {
            "exit_code": 1,
            "output": (
                "FAILED test/units/module_utils/urls/test_fetch_url.py::"
                "test_fetch_url_cookies - AssertionError"
            ),
        },
        session_id="sess-1",
    )
    coordinator.observe_tool_result(
        "terminal",
        {"command": narrowed_command},
        {"exit_code": 0, "output": "56 passed, 1 deselected"},
        session_id="sess-1",
    )

    result = coordinator.verify_completion(session_id="sess-1")

    assert result["decision"] == "ACCEPT_DONE"
    assert [call for call in client.calls if call[0] == "verify"]


def test_verify_completion_ignores_repeated_broad_suite_failure_after_deselect_pass(
    tmp_path,
):
    client = FakeClient()
    client.verify_response = {"decision": "ACCEPT_DONE"}
    diff = """diff --git a/lib/ansible/module_utils/urls.py b/lib/ansible/module_utils/urls.py
--- a/lib/ansible/module_utils/urls.py
+++ b/lib/ansible/module_utils/urls.py
@@ -1,2 +1,3 @@
+HAS_GZIP = True
 def open_url():
     return None
diff --git a/test/units/module_utils/urls/test_Request.py b/test/units/module_utils/urls/test_Request.py
--- a/test/units/module_utils/urls/test_Request.py
+++ b/test/units/module_utils/urls/test_Request.py
@@ -1,2 +1,5 @@
+def test_Request_open_gzip():
+    assert True
diff --git a/test/units/module_utils/urls/test_fetch_url.py b/test/units/module_utils/urls/test_fetch_url.py
--- a/test/units/module_utils/urls/test_fetch_url.py
+++ b/test/units/module_utils/urls/test_fetch_url.py
@@ -1,2 +1,5 @@
+def test_fetch_url_decompress_default():
+    assert True
"""
    coordinator = ConstraintKeeperCoordinator(
        client=client,
        spool_root=tmp_path,
        identity=_identity(),
        diff_provider=lambda: diff,
    )
    coordinator.compile_context_bundle(
        query="Request.open gzip",
        instruction="fix gzip decoding",
        query_plan={},
        context_bundle={"bundle_id": "bundle-1"},
        search_payload={"accepted_targets": ["lib/ansible/module_utils/urls.py"]},
    )
    failed_output = (
        "FAILED test/units/module_utils/urls/test_RedirectHandlerFactory.py::"
        "test_redir_http_error_308_urllib2\n"
        "FAILED test/units/module_utils/urls/test_fetch_url.py::"
        "test_fetch_url_cookies\n"
        "FAILED test/units/module_utils/urls/test_prepare_multipart.py::"
        "test_prepare_multipart\n"
        "3 failed, 71 passed"
    )
    coordinator.observe_tool_result(
        "terminal",
        {"command": "python -m pytest test/units/module_utils/urls/ -v"},
        {"exit_code": 1, "output": failed_output},
        session_id="sess-1",
    )
    coordinator.observe_tool_result(
        "terminal",
        {
            "command": (
                "python -m pytest test/units/module_utils/urls/ -v "
                "--deselect test/units/module_utils/urls/test_fetch_url.py::"
                "test_fetch_url_cookies "
                "--deselect test/units/module_utils/urls/"
                "test_RedirectHandlerFactory.py::test_redir_http_error_308_urllib2 "
                "--deselect test/units/module_utils/urls/test_prepare_multipart.py::"
                "test_prepare_multipart"
            )
        },
        {"exit_code": 0, "output": "71 passed, 3 deselected"},
        session_id="sess-1",
    )
    coordinator.observe_tool_result(
        "terminal",
        {"command": "python -m pytest test/units/module_utils/urls/ -v"},
        {"exit_code": 1, "output": failed_output},
        session_id="sess-1",
    )

    result = coordinator.verify_completion(session_id="sess-1")

    assert result["decision"] == "ACCEPT_DONE"
    assert [call for call in client.calls if call[0] == "verify"]


def test_verify_completion_accepts_equivalent_venv_pytest_after_python3_failure(
    tmp_path,
):
    client = FakeClient()
    client.verify_response = {"decision": "ACCEPT_DONE"}
    diff = """diff --git a/lib/ansible/module_utils/urls.py b/lib/ansible/module_utils/urls.py
--- a/lib/ansible/module_utils/urls.py
+++ b/lib/ansible/module_utils/urls.py
@@ -1,2 +1,3 @@
+HAS_GZIP = True
 def open_url():
     return None
"""
    coordinator = ConstraintKeeperCoordinator(
        client=client,
        spool_root=tmp_path,
        identity=_identity(),
        diff_provider=lambda: diff,
    )
    coordinator.compile_context_bundle(
        query="Request.open gzip",
        instruction="fix gzip decoding",
        query_plan={},
        context_bundle={"bundle_id": "bundle-1"},
        search_payload={"accepted_targets": ["lib/ansible/module_utils/urls.py"]},
    )
    coordinator.observe_tool_result(
        "terminal",
        {
            "command": (
                "PYTHONPATH=/Users/wayneliu/dev/ansible/lib python3 -m pytest "
                "test/units/module_utils/urls/test_gzip.py "
                "test/units/module_utils/urls/test_Request.py -v"
            )
        },
        {
            "exit_code": 4,
            "output": "ModuleNotFoundError: No module named 'ansible.module_utils.six.moves'",
        },
        session_id="sess-1",
    )
    coordinator.observe_tool_result(
        "terminal",
        {
            "command": (
                "PYTHONPATH=/Users/wayneliu/dev/ansible/lib "
                "/Users/wayneliu/dev/ansible/.venv/bin/python -m pytest "
                "test/units/module_utils/urls/test_gzip.py "
                "test/units/module_utils/urls/test_Request.py -v"
            )
        },
        {"exit_code": 0, "output": "38 passed"},
        session_id="sess-1",
    )

    result = coordinator.verify_completion(session_id="sess-1")

    assert result["decision"] == "ACCEPT_DONE"
    assert [call for call in client.calls if call[0] == "verify"]


def test_verify_completion_accepts_python_m_pytest_after_pytest_launcher_failures(
    tmp_path,
):
    client = FakeClient()
    client.verify_response = {"decision": "ACCEPT_DONE"}
    diff = """diff --git a/lib/ansible/module_utils/urls.py b/lib/ansible/module_utils/urls.py
--- a/lib/ansible/module_utils/urls.py
+++ b/lib/ansible/module_utils/urls.py
@@ -1,2 +1,3 @@
+HAS_GZIP = True
 def open_url():
     return None
"""
    coordinator = ConstraintKeeperCoordinator(
        client=client,
        spool_root=tmp_path,
        identity=_identity(),
        diff_provider=lambda: diff,
    )
    coordinator.compile_context_bundle(
        query="Request.open gzip",
        instruction="fix gzip decoding",
        query_plan={},
        context_bundle={"bundle_id": "bundle-1"},
        search_payload={"accepted_targets": ["lib/ansible/module_utils/urls.py"]},
    )
    selector = "test/units/module_utils/urls/test_Request.py"
    coordinator.observe_tool_result(
        "terminal",
        {"command": f"pytest -v {selector}"},
        {"exit_code": 127, "output": "pytest: command not found"},
        session_id="sess-1",
    )
    coordinator.observe_tool_result(
        "terminal",
        {"command": f"/repo/.venv/bin/pytest -v {selector}"},
        {"exit_code": 2, "output": "No such file or directory"},
        session_id="sess-1",
    )
    coordinator.observe_tool_result(
        "terminal",
        {"command": f"python -m pytest -v {selector}"},
        {"exit_code": 0, "output": "35 passed"},
        session_id="sess-1",
    )

    result = coordinator.verify_completion(session_id="sess-1")

    assert result["decision"] == "ACCEPT_DONE"
    assert [call for call in client.calls if call[0] == "verify"]


def test_pre_tool_call_blocks_writing_candidate_test_outside_accepted_targets(tmp_path):
    coordinator = ConstraintKeeperCoordinator(
        client=FakeClient(),
        spool_root=tmp_path,
        identity=_identity(),
    )
    coordinator.compile_context_bundle(
        query="Request.open gzip",
        instruction="fix gzip decoding",
        query_plan={},
        context_bundle={"bundle_id": "bundle-1"},
        search_payload={
            "accepted_targets": ["lib/ansible/module_utils/urls.py"],
            "guidance": {
                "tocs": {
                    "candidate_tests": [
                        {
                            "test_id": (
                                "test/units/module_utils/urls/test_gzip.py::"
                                "test_Request_open_gzip"
                            ),
                        }
                    ],
                }
            },
        },
    )

    message = coordinator.pre_tool_call_block_message(
        "write_file",
        {
            "path": "test/units/module_utils/urls/test_gzip.py",
            "content": "reconstructed test",
        },
        session_id="sess-1",
    )

    assert message is not None
    assert "Candidate tests are validation obligations, not edit permission" in message
    assert "test/units/module_utils/urls/test_gzip.py" in message
    assert "lib/ansible/module_utils/urls.py" in message


def test_pre_tool_call_allows_candidate_test_when_validation_collateral(tmp_path):
    coordinator = ConstraintKeeperCoordinator(
        client=FakeClient(),
        spool_root=tmp_path,
        identity=_identity(),
    )
    coordinator.compile_context_bundle(
        query="Request.open gzip",
        instruction="fix gzip decoding",
        query_plan={},
        context_bundle={"bundle_id": "bundle-1"},
        search_payload={
            "contracts": {
                "patch": {
                    "accepted_targets": ["lib/ansible/module_utils/urls.py"],
                    "validation_collateral": [
                        "test/units/module_utils/urls/test_gzip.py"
                    ],
                }
            },
            "guidance": {
                "tocs": {
                    "candidate_tests": [
                        {
                            "test_id": (
                                "test/units/module_utils/urls/test_gzip.py::"
                                "test_Request_open_gzip"
                            ),
                        }
                    ],
                }
            },
        },
    )

    message = coordinator.pre_tool_call_block_message(
        "write_file",
        {
            "path": "test/units/module_utils/urls/test_gzip.py",
            "content": "focused validation test",
        },
        session_id="sess-1",
    )

    assert message is None


def test_pre_tool_call_blocks_edits_after_human_review_requested(tmp_path):
    coordinator = ConstraintKeeperCoordinator(
        client=FakeClient(),
        spool_root=tmp_path,
        identity=_identity(),
    )
    coordinator.compile_context_bundle(
        query="Request.open gzip",
        instruction="fix gzip decoding",
        query_plan={},
        context_bundle={"bundle_id": "bundle-1"},
        search_payload={"accepted_targets": ["lib/ansible/module_utils/urls.py"]},
    )
    coordinator.observe_tool_result(
        "formsy_request_human_review",
        {"reason": "focused validation is blocked"},
        json.dumps(
            {
                "ok": True,
                "requested": True,
                "reason": "focused validation is blocked",
            }
        ),
        session_id="sess-1",
    )

    message = coordinator.pre_tool_call_block_message(
        "patch",
        {
            "path": "test/units/module_utils/urls/test_fetch_url.py",
            "old": "assert old",
            "new": "assert new",
        },
        session_id="sess-1",
    )

    assert message is not None
    assert "Human review has already been requested" in message
    assert "Do not continue patching" in message


def test_pre_tool_call_blocks_terminal_candidate_test_redirection_outside_accepted_targets(
    tmp_path,
):
    coordinator = ConstraintKeeperCoordinator(
        client=FakeClient(),
        spool_root=tmp_path,
        identity=_identity(),
    )
    coordinator.compile_context_bundle(
        query="request response decoding",
        instruction="fix response decoding",
        query_plan={},
        context_bundle={"bundle_id": "bundle-1"},
        search_payload={
            "accepted_targets": ["lib/package/client.py"],
            "guidance": {
                "tocs": {
                    "candidate_tests": [
                        {
                            "test_id": "tests/package/test_client_response.py::test_decoding",
                        }
                    ],
                }
            },
        },
    )

    message = coordinator.pre_tool_call_block_message(
        "terminal",
        {
            "command": (
                "cat > tests/package/test_client_response.py <<'EOF'\n"
                "def test_decoding():\n"
                "    pass\n"
                "EOF"
            ),
        },
        session_id="sess-1",
    )

    assert message is not None
    assert "Candidate tests are validation obligations, not edit permission" in message
    assert "tests/package/test_client_response.py" in message
    assert "lib/package/client.py" in message


def test_pre_tool_call_blocks_terminal_python_candidate_test_reconstruction(tmp_path):
    coordinator = ConstraintKeeperCoordinator(
        client=FakeClient(),
        spool_root=tmp_path,
        identity=_identity(),
    )
    coordinator.compile_context_bundle(
        query="Request.open gzip",
        instruction="fix gzip decoding",
        query_plan={},
        context_bundle={"bundle_id": "bundle-1"},
        search_payload={
            "accepted_targets": ["lib/ansible/module_utils/urls.py"],
            "guidance": {
                "tocs": {
                    "candidate_tests": [
                        {
                            "test_id": (
                                "test/units/module_utils/urls/test_gzip.py::"
                                "test_Request_open_gzip"
                            ),
                        }
                    ],
                }
            },
        },
    )

    message = coordinator.pre_tool_call_block_message(
        "terminal",
        {
            "command": (
                'python3 -c "\n'
                "import os\n"
                "test_dir = 'test/units/module_utils/urls'\n"
                "os.makedirs(test_dir, exist_ok=True)\n"
                "with open(os.path.join(test_dir, 'test_gzip.py'), 'wb') as f:\n"
                "    f.write(b'reconstructed test')\n"
                '"'
            ),
        },
        session_id="sess-1",
    )

    assert message is not None
    assert "Candidate tests are validation obligations, not edit permission" in message
    assert "test/units/module_utils/urls/test_gzip.py" in message
    assert "lib/ansible/module_utils/urls.py" in message


def test_pre_tool_call_allows_candidate_test_when_accepted_target(tmp_path):
    coordinator = ConstraintKeeperCoordinator(
        client=FakeClient(),
        spool_root=tmp_path,
        identity=_identity(),
    )
    coordinator.compile_context_bundle(
        query="Request.open gzip",
        instruction="fix gzip decoding",
        query_plan={},
        context_bundle={"bundle_id": "bundle-1"},
        search_payload={
            "accepted_targets": ["test/units/module_utils/urls/test_gzip.py"],
            "guidance": {
                "tocs": {
                    "candidate_tests": [
                        {
                            "test_id": (
                                "test/units/module_utils/urls/test_gzip.py::"
                                "test_Request_open_gzip"
                            ),
                        }
                    ],
                }
            },
        },
    )

    message = coordinator.pre_tool_call_block_message(
        "write_file",
        {
            "path": "test/units/module_utils/urls/test_gzip.py",
            "content": "accepted test edit",
        },
        session_id="sess-1",
    )

    assert message is None


def test_verify_completion_repeated_unresolved_failed_validation_enters_loop_guard(
    tmp_path,
):
    client = FakeClient()
    client.verify_response = {"decision": "ACCEPT_DONE"}
    diff = """diff --git a/lib/ansible/module_utils/urls.py b/lib/ansible/module_utils/urls.py
--- a/lib/ansible/module_utils/urls.py
+++ b/lib/ansible/module_utils/urls.py
@@ -1,2 +1,3 @@
+HAS_GZIP = True
 def open_url():
     return None
"""
    coordinator = ConstraintKeeperCoordinator(
        client=client,
        spool_root=tmp_path,
        identity=_identity(),
        diff_provider=lambda: diff,
    )
    coordinator.compile_context_bundle(
        query="Request.open gzip",
        instruction="fix gzip decoding",
        query_plan={},
        context_bundle={"bundle_id": "bundle-1"},
        search_payload={"accepted_targets": ["lib/ansible/module_utils/urls.py"]},
    )
    failed_command = "python3 -m pytest test/units/module_utils/urls/test_Request.py -q"
    passed_candidate = "python3 -m pytest test/units/module_utils/urls/test_gzip.py -q"
    coordinator.observe_tool_result(
        "terminal",
        {"command": failed_command},
        {"exit_code": 1, "output": "FAILED test_Request.py"},
        session_id="sess-1",
    )
    coordinator.observe_tool_result(
        "terminal",
        {"command": passed_candidate},
        {"exit_code": 0, "output": "5 passed"},
        session_id="sess-1",
    )

    first = coordinator.verify_completion(session_id="sess-1")
    second = coordinator.verify_completion(session_id="sess-1")

    assert first["completion_audit"]["evidence"]["local_patch_semantic_guard"] == (
        "unresolved_failed_validation"
    )
    assert second["completion_audit"]["evidence"]["local_patch_semantic_guard"] == (
        "repeated_unresolved_failed_validation"
    )
    assert second["completion_audit"]["audit_status"] == "blocked_repeated"
    assert second["completion_audit"]["evidence"]["repeat_count"] == 2
    assert second["completion_audit"]["evidence"]["failed_validation_commands"] == [
        failed_command
    ]
    assert any(
        "Do not rerun already passing candidate tests" in action
        for action in second["protocol"]["required_next_actions"]
    )
    assert any(
        failed_command in action
        for action in second["protocol"]["required_next_actions"]
    )
    assert not [call for call in client.calls if call[0] == "verify"]


def test_completion_projection_includes_failed_validation_recovery_commands():
    result = {
        "decision": "NEED_MORE_VALIDATION",
        "completion_audit": {
            "audit_status": "blocked",
            "gate_decision": "NEED_MORE_VALIDATION",
            "evidence": {
                "latest_diff_hash": "sha256:abc",
                "local_patch_semantic_guard": "repeated_unresolved_failed_validation",
                "repeat_count": 3,
                "failed_validation_commands": [
                    "python3 -m pytest test/units/module_utils/urls/test_Request.py -q",
                    "python3 -m pytest test/units/module_utils/urls/test_fetch_url.py -q",
                ],
            },
        },
    }

    projection = ConstraintKeeperCoordinator._completion_audit_projection_text(result)

    assert "- Local guard: repeated_unresolved_failed_validation" in projection
    assert "- Repeat count: 3" in projection
    assert "- Failed validation commands:" in projection
    assert (
        "python3 -m pytest test/units/module_utils/urls/test_Request.py -q"
        in projection
    )
    assert (
        "python3 -m pytest test/units/module_utils/urls/test_fetch_url.py -q"
        in projection
    )


def test_verify_completion_prioritizes_failed_exact_candidate_test_feedback(tmp_path):
    client = FakeClient()
    client.verify_response = {"decision": "ACCEPT_DONE"}
    diff = """diff --git a/lib/ansible/module_utils/urls.py b/lib/ansible/module_utils/urls.py
--- a/lib/ansible/module_utils/urls.py
+++ b/lib/ansible/module_utils/urls.py
@@ -1,2 +1,3 @@
+HAS_GZIP = True
 def open_url():
     return None
"""
    coordinator = ConstraintKeeperCoordinator(
        client=client,
        spool_root=tmp_path,
        identity=_identity(),
        diff_provider=lambda: diff,
    )
    coordinator.compile_context_bundle(
        query="Request.open gzip",
        instruction="fix gzip decoding",
        query_plan={},
        context_bundle={"bundle_id": "bundle-1"},
        search_payload={
            "accepted_targets": ["lib/ansible/module_utils/urls.py"],
            "guidance": {
                "tocs": {
                    "delivery": {"resolved": True},
                    "candidate_tests": [
                        {
                            "test_id": (
                                "test/units/module_utils/urls/test_gzip.py::"
                                "test_GzipDecodedReader_read_amt"
                            ),
                            "command": (
                                "python3 -m pytest "
                                "test/units/module_utils/urls/test_gzip.py::"
                                "test_GzipDecodedReader_read_amt -q"
                            ),
                        }
                    ],
                }
            },
        },
    )
    exact_command = (
        "python3 -m pytest "
        "test/units/module_utils/urls/test_gzip.py::"
        "test_GzipDecodedReader_read_amt -q"
    )
    coordinator.observe_tool_result(
        "terminal",
        {"command": exact_command},
        {"exit_code": 1, "output": "FAILED test_GzipDecodedReader_read_amt"},
        session_id="sess-1",
    )
    coordinator.observe_tool_result(
        "terminal",
        {"command": "python3 -m pytest test/units/module_utils/urls -q"},
        {"exit_code": 1, "output": "7 failed, 76 passed"},
        session_id="sess-1",
    )

    result = coordinator.verify_completion(session_id="sess-1")

    assert result["decision"] == "NEED_MORE_VALIDATION"
    assert result["completion_audit"]["evidence"]["local_patch_semantic_guard"] == (
        "failed_exact_candidate_tests"
    )
    assert result["completion_audit"]["evidence"]["failed_candidate_test_commands"] == [
        exact_command
    ]
    assert any(
        "Repair the failing exact candidate test before broad validation" in action
        for action in result["protocol"]["required_next_actions"]
    )
    assert not [call for call in client.calls if call[0] == "verify"]


def test_verify_completion_allows_failed_validation_after_same_command_passes(tmp_path):
    client = FakeClient()
    client.verify_response = {"decision": "ACCEPT_DONE"}
    diff = """diff --git a/lib/ansible/module_utils/urls.py b/lib/ansible/module_utils/urls.py
--- a/lib/ansible/module_utils/urls.py
+++ b/lib/ansible/module_utils/urls.py
@@ -1,2 +1,3 @@
+HAS_GZIP = True
 def open_url():
     return None
"""
    coordinator = ConstraintKeeperCoordinator(
        client=client,
        spool_root=tmp_path,
        identity=_identity(),
        diff_provider=lambda: diff,
    )
    coordinator.compile_context_bundle(
        query="Request.open gzip",
        instruction="fix gzip decoding",
        query_plan={},
        context_bundle={"bundle_id": "bundle-1"},
        search_payload={"accepted_targets": ["lib/ansible/module_utils/urls.py"]},
    )
    command = "python3 -m pytest test/units/module_utils/urls -q"
    coordinator.observe_tool_result(
        "terminal",
        {"command": command},
        {"exit_code": 1, "output": "7 failed, 76 passed"},
        session_id="sess-1",
    )
    coordinator.observe_tool_result(
        "terminal",
        {"command": command},
        {"exit_code": 0, "output": "83 passed"},
        session_id="sess-1",
    )

    result = coordinator.verify_completion(session_id="sess-1")

    assert result == {"decision": "ACCEPT_DONE"}
    assert [call for call in client.calls if call[0] == "verify"]


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


def test_formsy_verify_completion_accepted_revalidates_next_user_task(tmp_path):
    coordinator = ConstraintKeeperCoordinator(
        client=FakeClient(),
        spool_root=tmp_path,
        identity=_identity(),
        fail_closed_on_submit=True,
    )
    accepted = {
        "decision": "ACCEPT_DONE",
        "protocol": {
            "state": "DONE_ACCEPTED",
            "gate_decision": "ACCEPT_DONE",
            "summary": "Completion proof satisfies P0 contracts.",
        },
        "completion_audit": {
            "audit_status": "verified",
            "gate_decision": "ACCEPT_DONE",
            "memory_write_allowed": True,
        },
    }

    coordinator.observe_tool_result(
        "context_search",
        {"query": "Fix PlayIterator public states"},
        '{"ok": true}',
        session_id="sess-1",
    )
    coordinator.observe_tool_result(
        "formsy_verify_completion",
        {},
        accepted,
        session_id="sess-1",
    )

    coordinator.on_user_turn(
        user_message="<pr_description>Fix PlayIterator public states</pr_description>",
        session_id="sess-1",
    )
    next_context = coordinator.pre_llm_call_context(session_id="sess-1")

    assert next_context is not None
    assert "FormSy workspace revalidation required" in next_context["context"]
    assert (
        "Do not claim this task is already completed from session history alone."
        in next_context["context"]
    )
    assert "git status --short" in next_context["context"]
    assert "context_search" in next_context["context"]


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


def test_accepted_completion_projection_does_not_default_missing_decision_to_accept():
    text = ConstraintKeeperCoordinator._accepted_completion_projection_text(
        {"protocol": {"summary": "Verifier returned no decision."}}
    )

    assert "ACCEPT_DONE" not in text
    assert "- Decision: MISSING_VERIFIER_DECISION" in text
    assert "Verifier returned no decision." in text


def test_post_llm_call_replaces_fake_completion_verifier_accept_claim(tmp_path):
    coordinator = ConstraintKeeperCoordinator(
        client=FakeClient(),
        spool_root=tmp_path,
        identity=_identity(),
        fail_closed_on_submit=True,
    )

    directive = coordinator.post_llm_call_final_response_directive(
        assistant_response=(
            "Done. Implemented the fix.\n\n"
            "Completion Verifier: ACCEPT_DONE."
        ),
        session_id="sess-1",
    )

    assert directive == {
        "action": "replace_final_response",
        "final_response": (
            "FormSy Finish Gate was not called. The patch may be implemented, "
            "but completion is not verified yet. Call formsy_verify_completion "
            "before reporting ACCEPT_DONE."
        ),
    }


def test_post_llm_call_replaces_unverified_completion_claim_after_diff(tmp_path):
    coordinator = ConstraintKeeperCoordinator(
        client=FakeClient(),
        spool_root=tmp_path,
        identity=_identity(),
        fail_closed_on_submit=True,
        diff_provider=lambda: (
            "diff --git a/lib/example.py b/lib/example.py\n"
            "--- a/lib/example.py\n"
            "+++ b/lib/example.py\n"
            "@@ -1 +1 @@\n"
            "-old\n"
            "+new\n"
        ),
    )
    coordinator.observe_tool_result(
        "apply_patch",
        {"patch": "*** Begin Patch\n*** Update File: lib/example.py"},
        "ok",
        session_id="sess-1",
    )

    directive = coordinator.post_llm_call_final_response_directive(
        assistant_response="Implemented the fix and verified the focused tests pass.",
        session_id="sess-1",
    )

    assert directive == {
        "action": "replace_final_response",
        "final_response": (
            "FormSy Finish Gate was not called. The patch may be implemented, "
            "but completion is not verified yet. Call formsy_verify_completion "
            "before reporting done."
        ),
    }


def test_pre_tool_call_blocks_repeated_empty_process_list_loop(tmp_path):
    coordinator = ConstraintKeeperCoordinator(
        client=FakeClient(), spool_root=tmp_path, identity=_identity()
    )

    for _ in range(3):
        coordinator.observe_tool_result(
            "process",
            {"action": "list"},
            {"processes": []},
            session_id="sess-1",
        )

    message = coordinator.pre_tool_call_block_message(
        "process",
        {"action": "list"},
        session_id="sess-1",
    )

    assert message is not None
    assert "repeated empty process list" in message
    assert "run focused validation" in message
    assert "formsy_verify_completion" in message


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
        diff_provider=lambda: (
            "diff --git a/app.py b/app.py\n--- a/app.py\n+++ b/app.py\n"
        ),
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
    coordinator = ConstraintKeeperCoordinator(
        client=client, spool_root=tmp_path, identity=_identity()
    )

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
        diff_provider=lambda: (
            "diff --git a/app.py b/app.py\n--- a/app.py\n+++ b/app.py\n"
        ),
    )

    coordinator.observe_tool_result(
        "apply_patch", {"patch": "*** Begin Patch"}, "ok", session_id="sess-1"
    )
    coordinator.observe_tool_result(
        "terminal",
        {"command": "python -m pytest tests/forms", "exit_code": 1},
        "AssertionError: same failure",
        session_id="sess-1",
    )

    failure = [
        event
        for event in coordinator.spool.pending("task-1", "run-1")
        if event["event_kind"] == "failure"
    ][0]
    assert failure["payload"]["diff_context_hash"] == coordinator.latest_diff_hash


def test_pre_tool_call_blocks_execute_code_read_file_write_file_bridge(tmp_path):
    coordinator = ConstraintKeeperCoordinator(
        client=FakeClient(), spool_root=tmp_path, identity=_identity()
    )

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
    coordinator = ConstraintKeeperCoordinator(
        client=FakeClient(), spool_root=tmp_path, identity=_identity()
    )

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


def test_pre_tool_call_allows_execute_code_temp_file_write_for_validation(tmp_path):
    coordinator = ConstraintKeeperCoordinator(
        client=FakeClient(), spool_root=tmp_path, identity=_identity()
    )

    message = coordinator.pre_tool_call_block_message(
        "execute_code",
        {
            "code": """
from pathlib import Path
import subprocess

probe = Path('/tmp/formsy_validation_probe.py')
probe.write_text("print('ok')\\n")
res = subprocess.run(['/usr/bin/python3', str(probe)], capture_output=True, text=True)
assert res.stdout.strip() == 'ok'
"""
        },
        session_id="sess-1",
    )

    assert message is None


def test_pre_tool_call_allows_execute_code_read_only_validation(tmp_path):
    coordinator = ConstraintKeeperCoordinator(
        client=FakeClient(), spool_root=tmp_path, identity=_identity()
    )

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
