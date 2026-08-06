"""Synchronous Hermes-facing coordinator for FormSy Constraint Keeper."""

from __future__ import annotations

import asyncio
import json
import inspect
import logging
import re
import shlex
import time
import uuid
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Callable

from plugins.formsy.identity import FormSyIdentity, derive_formsy_identity

from .evidence import (
    changed_files_from_diff,
    classify_terminal_result,
    hash_text,
    is_edit_surface,
    is_final_submit,
    is_validation_command,
)
from .spool import EvidenceSpool

DiffProvider = Callable[[], str]
SourceProvider = Callable[[list[str]], dict[str, str]]
logger = logging.getLogger("formsy.constraint_keeper")

_EXPLORATION_GUIDANCE = (
    "FormSy advisory guidance: broad shell/read exploration is already underway "
    "without a context_search result. Pause broad exploration and call "
    "context_search with the full task description or the key symbols before "
    "continuing."
)
_TERMINAL_FAILURE_GUIDANCE = (
    "FormSy advisory guidance: Repeated terminal failures detected. Do not keep "
    "retrying the same import/path probe. Use context_search for source evidence, "
    "switch to static source inspection, or call formsy_recover for a recovery "
    "protocol."
)
_CODE_PROBE_LOOP_GUIDANCE = (
    "FormSy advisory guidance: Repeated isolated code probes detected after "
    "context retrieval. Summarize the invariant you already learned, move to a "
    "minimal patch or focused source read, and call formsy_recover if the next "
    "step is still unclear."
)
_BOOTSTRAP_CONTEXT_SEARCH_BLOCK = (
    "FormSy Constraint Keeper requires one seed context_search before more "
    "source exploration or editing. Call context_search with the full task or "
    "PR description, then follow the returned guidance."
)
_BOOTSTRAP_CONTEXT_READ_BLOCK = (
    "FormSy Constraint Keeper requires one seed context_search before context_read "
    "for a fresh task. Call context_search with the full task or PR description first."
)
_EXECUTE_CODE_READ_WRITE_BRIDGE_BLOCK = (
    "FormSy Constraint Keeper blocked execute_code because hermes_tools.read_file "
    "output is line-numbered display text in this runtime and must not be copied "
    "into hermes_tools.write_file. Use read_file as a normal tool, inspect the "
    "displayed content, then use patch or write_file with explicitly authored raw "
    "content outside execute_code."
)
_EXECUTE_CODE_DIRECT_SOURCE_WRITE_BLOCK = (
    "FormSy Constraint Keeper blocked execute_code because direct source writes "
    "inside execute_code are hidden from normal patch/write_file review. Use patch "
    "for source edits, then run execute_code only for read-only validation."
)


class _GroundingState(str, Enum):
    NEEDS_GROUNDING = "needs_grounding"
    GROUNDING_ADVISED = "grounding_advised"
    GROUNDED = "grounded"
    CLOSED = "closed"


@dataclass(frozen=True)
class PatchAuthorizationScope:
    contract_set_id: str
    contract_revision: int
    accepted_targets: tuple[str, ...]
    strict_target_scope: bool


class ConstraintKeeperCoordinator:
    def __init__(
        self,
        *,
        client: Any,
        spool_root: str | Path,
        identity: FormSyIdentity | None = None,
        diff_provider: DiffProvider | None = None,
        source_provider: SourceProvider | None = None,
        fail_closed_on_submit: bool = True,
    ) -> None:
        self.client = client
        self.spool = EvidenceSpool(spool_root)
        self.identity = identity
        self.diff_provider = diff_provider or (lambda: "")
        self.source_provider = source_provider or (lambda _paths: {})
        self.fail_closed_on_submit = fail_closed_on_submit
        self.latest_protocol_text = ""
        self.latest_diff_hash = ""
        self.recovery_open = False
        self._started_runs: set[tuple[str, str]] = set()
        self._sequence = 0
        self._session_id = identity.session_id if identity else ""
        self._failure_counts: dict[str, int] = {}
        self._recovered_fingerprints: set[str] = set()
        self._last_injected_protocol_text = ""
        self._guidance_task_key = self._task_key()
        self._grounding_state = _GroundingState.NEEDS_GROUNDING
        self._bootstrap_guidance_injected = False
        self._context_search_seen = False
        self._bootstrap_source_exploration_reserved = False
        self._exploration_without_context_count = 0
        self._exploration_guidance_injected = False
        self._terminal_failure_count = 0
        self._terminal_failure_guidance_injected = False
        self._code_probe_count = 0
        self._code_probe_guidance_injected = False
        self._pending_guidance_text = ""
        self._pending_completion_projection_text = ""
        self._completion_revalidation_pending = False
        self._completion_verified = False
        self._task_closed = False
        self._active_context_directive: dict[str, Any] | None = None
        self._active_probe_budget_directive: dict[str, Any] | None = None
        self._active_next_tool_directive: dict[str, Any] | None = None
        self._next_tool_deviation_count = 0
        self._next_tool_visible_delivery_count = 0
        self._next_tool_failed = False
        self._context_read_counts: dict[str, int] = {}
        self._pending_tool_result_replacement = ""
        self._last_successful_terminal_probe: dict[str, Any] = {}
        self._probe_budget_counts: dict[str, int] = {
            "search_files": 0,
            "read_file": 0,
            "terminal_or_execute_code": 0,
        }
        self._empty_process_list_count = 0
        self._full_diff_stdout_count = 0
        self._latest_user_task_text = ""
        self._latest_grounding_query_text = ""
        self._latest_diff_payload: dict[str, Any] = {}
        self._latest_warning_bearing_validation: dict[str, Any] = {}
        self._latest_accepted_targets: list[str] = []
        self._active_patch_authorization: PatchAuthorizationScope | None = None
        self._edit_patch_authorization: PatchAuthorizationScope | None = None
        self._latest_diff_patch_authorization: PatchAuthorizationScope | None = None
        self._next_compile_reason = "context_refresh"
        self._latest_context_bundle_hint: dict[str, Any] = {}
        self._latest_candidate_test_commands: list[str] = []
        self._latest_candidate_test_paths: list[str] = []
        self._latest_validation_collateral_paths: list[str] = []
        self._human_review_requested: dict[str, str] = {}
        self._latest_passing_validations: list[dict[str, str]] = []
        self._unresolved_failed_validations: dict[str, dict[str, Any]] = {}
        self._completion_guard_repeat_counts: dict[str, int] = {}
        self._written_paths: list[str] = []
        self._skill_name = "formsy-context"
        self._skill_visibility = "unknown"
        self._skill_body_loaded = False

    def on_session_start(self, session_id: str = "", **_: Any) -> None:
        self._session_id = session_id or self._session_id

    def on_user_turn(
        self, user_message: str = "", session_id: str = "", **_: Any
    ) -> None:
        auxiliary_turn = self._is_auxiliary_task_text(user_message)
        if (
            isinstance(user_message, str)
            and user_message.strip()
            and not auxiliary_turn
        ):
            if self._active_patch_authorization is not None:
                self._next_compile_reason = "user_requirement_change"
            self._human_review_requested = {}
            self._latest_user_task_text = user_message
        self.ensure_identity(
            session_id=session_id,
            user_message=None if auxiliary_turn else user_message,
        )
        if self._task_closed:
            self._task_closed = False
            self._completion_verified = False
            self._reset_guidance_state(clear_protocol=True)
        self._reset_guidance_state_if_task_changed()

    def ensure_identity(
        self,
        *,
        session_id: str = "",
        task_id: str = "",
        user_message: Any = None,
        repo_id: str = "",
        revision: str = "",
        workspace_id: str = "",
    ) -> FormSyIdentity:
        if self.identity is None or task_id or user_message is not None:
            self.identity = derive_formsy_identity(
                session_id=session_id or self._session_id,
                task_id=task_id,
                user_message=user_message,
                repo_id=repo_id or (self.identity.repo_id if self.identity else ""),
                revision=revision or (self.identity.revision if self.identity else ""),
                workspace_id=workspace_id
                or (self.identity.workspace_id if self.identity else ""),
            )
            self._session_id = self.identity.session_id
        return self.identity

    def ensure_task_started(self, session_id: str = "", task_id: str = "") -> None:
        identity = self.ensure_identity(session_id=session_id, task_id=task_id)
        key = (identity.task_id, identity.run_id)
        if key in self._started_runs:
            return
        self._run_async(
            self.client.task_start(
                task=identity.to_task_ref(),
                workspace=identity.to_workspace_ref(),
                session_id=identity.session_id,
            )
        )
        self._started_runs.add(key)

    def compile_context_bundle(
        self,
        *,
        query: str,
        instruction: str,
        query_plan: dict[str, Any],
        context_bundle: dict[str, Any],
        search_payload: dict[str, Any],
        session_id: str = "",
        task_id: str = "",
        repo_id: str = "",
        revision: str = "",
        workspace_id: str = "",
    ) -> str:
        promoted_task_text = self._promotable_task_text(query, instruction)
        if promoted_task_text and (
            not self._latest_user_task_text.strip()
            or self._is_auxiliary_task_text(self._latest_user_task_text)
        ):
            self._latest_user_task_text = promoted_task_text
            self.ensure_identity(
                session_id=session_id,
                user_message=promoted_task_text,
                repo_id=repo_id,
                revision=revision,
                workspace_id=workspace_id,
            )
        identity = self.ensure_identity(
            session_id=session_id,
            task_id=task_id,
            repo_id=repo_id,
            revision=revision,
            workspace_id=workspace_id,
        )
        if query.strip():
            self._latest_grounding_query_text = query.strip()
        self._mark_context_retrieval_seen()
        try:
            self.ensure_task_started(session_id=identity.session_id)
            response = self._run_async(
                self.client.compile_constraints(
                    {
                        "task": identity.to_task_ref(),
                        "workspace": identity.to_workspace_ref(),
                        "instruction": instruction,
                        "query_plan": query_plan,
                        "context_bundle": context_bundle,
                        "search_payload": search_payload,
                        "query": query,
                        "compile_reason": self._next_compile_reason,
                    },
                    session_id=identity.session_id,
                )
            )
        except Exception as exc:
            return (
                "Constraint Protocol compilation unavailable. "
                f"ContextBundle is still usable, but completion verification may fail closed. "
                f"Reason: {exc.__class__.__name__}: {exc}"
            )

        self._record_context_bundle_hint(context_bundle, search_payload, response)
        self._record_contract_accepted_targets(search_payload)
        self._record_contract_accepted_targets(response)
        self._record_patch_authorization(response)
        self._record_contract_validation_collateral(search_payload)
        self._record_contract_validation_collateral(response)
        self._record_contract_candidate_tests(search_payload)
        self._record_contract_candidate_tests(response)
        self._record_contract_candidate_test_paths(search_payload)
        self._record_contract_candidate_test_paths(response)
        self._capture_server_directive(response)
        self._next_compile_reason = "context_refresh"
        protocol_text = self._protocol_text(response)
        self._set_protocol_text(protocol_text)
        return protocol_text

    def observe_tool_result(
        self,
        tool_name: str,
        args: dict[str, Any] | None,
        result: Any,
        *,
        session_id: str = "",
        task_id: str = "",
    ) -> None:
        self.ensure_identity(session_id=session_id, task_id=task_id)
        self._observe_skill_uptake(tool_name, args or {}, result)
        self._observe_guidance_signal(tool_name, args or {}, result)
        self._capture_guidance_packet(tool_name, result)
        self._record_human_review_request(tool_name, args or {}, result)
        if tool_name == "formsy_verify_completion" and self._is_accepted(
            _result_dict(result)
        ):
            self._mark_completion_accepted_for_revalidation()
        self._record_probe_budget_event(tool_name, args or {})
        self._record_successful_terminal_probe(tool_name, args or {}, result)
        self._record_empty_process_list_probe(tool_name, args or {}, result)
        self._record_written_path(tool_name, args or {}, result)
        observed = self._tool_observed_event(tool_name, args or {}, result)
        if observed:
            self._append_event(observed)
            self.flush_pending()
        if is_edit_surface(tool_name, args or {}):
            self._append_fresh_diff_if_changed()
            if _result_succeeded(result):
                self._reset_probe_budget_counts()
        if tool_name == "terminal":
            event = classify_terminal_result(args or {}, result)
            if event:
                if event.get("event_kind") in {"failure", "test_result"}:
                    self._append_fresh_diff_if_changed()
                if event.get("event_kind") == "failure":
                    event.setdefault("payload", {})["diff_context_hash"] = (
                        self.latest_diff_hash
                    )
                    self._record_failed_validation(event)
                if event.get("event_kind") == "test_result":
                    event.setdefault("payload", {})["diff_context_hash"] = (
                        self.latest_diff_hash
                    )
                    self._record_warning_bearing_validation(event)
                    self._clear_resolved_failed_validation(event)
                self._append_event(event)
                self._maybe_recover_from_failure(event)
        if is_final_submit(tool_name, args or {}) and _result_succeeded(result):
            self._mark_task_closed()

    def get_skill_uptake_status(self) -> dict[str, Any]:
        visibility = self._skill_visibility
        if visibility == "unknown" and self._skill_is_installed():
            visibility = "installed"
        return {
            "skill_name": self._skill_name,
            "skill_visibility": visibility,
            "skill_body_loaded": self._skill_body_loaded,
        }

    def transform_tool_result(
        self,
        tool_name: str,
        args: dict[str, Any] | None,
        result: str,
        *,
        session_id: str = "",
        task_id: str = "",
    ) -> str | None:
        if self._pending_tool_result_replacement:
            replacement = self._pending_tool_result_replacement
            self._pending_tool_result_replacement = ""
            return replacement
        prefix_additions: list[str] = []
        suffix_additions: list[str] = []
        if self._pending_guidance_text:
            prefix_additions.append(self._pending_guidance_text)
            self._pending_guidance_text = ""
        if (
            self.latest_protocol_text
            and self.latest_protocol_text != self._last_injected_protocol_text
        ):
            self._last_injected_protocol_text = self.latest_protocol_text
            suffix_additions.append(self.latest_protocol_text)
        if self._pending_completion_projection_text:
            suffix_additions.append(self._pending_completion_projection_text)
            self._pending_completion_projection_text = ""
        if prefix_additions or suffix_additions:
            transformed = result
            if prefix_additions:
                transformed = (
                    "\n\n---\n\n".join(prefix_additions) + f"\n\n---\n\n{transformed}"
                )
            if suffix_additions:
                transformed = f"{transformed}\n\n" + "\n\n".join(suffix_additions)
            return transformed
        return None

    def pre_llm_call_context(
        self, *, session_id: str = "", task_id: str = ""
    ) -> dict[str, str] | None:
        self.ensure_identity(session_id=session_id, task_id=task_id)
        if self._task_closed:
            self._task_closed = False
            self._completion_verified = False
            self._reset_guidance_state(clear_protocol=True)
        if not self.recovery_open:
            if not self._context_search_seen and not self._bootstrap_guidance_injected:
                self._bootstrap_guidance_injected = True
                query = self._grounding_query_hint("", {})
                directive = self._materialize_grounding_next_action(query)
                self._grounding_state = _GroundingState.GROUNDING_ADVISED
                self._next_tool_visible_delivery_count = 1
                action_card = self._recommended_next_action_card(directive)
                if self._completion_revalidation_pending:
                    action_card = (
                        f"{self._workspace_revalidation_card()}\n\n{action_card}"
                    )
                    self._completion_revalidation_pending = False
                context = self._with_formsy_context_skill_capsule(
                    action_card, session_id=session_id
                )
                self._log_pre_llm_projection_delivered(
                    action_id=str(directive.get("action_id") or ""),
                    context=context,
                    delivery_count=self._next_tool_visible_delivery_count,
                    session_id=session_id,
                )
                return {"context": context}
            return None
        return {
            "context": (
                "FormSy recovery is still open. Follow the latest Constraint Protocol "
                "before editing or final submission."
            )
        }

    def pre_tool_call_block_message(
        self,
        tool_name: str,
        args: dict[str, Any] | None,
        *,
        session_id: str = "",
        task_id: str = "",
    ) -> str | None:
        self.ensure_identity(session_id=session_id, task_id=task_id)
        if self._task_closed:
            return None
        final_submit = is_final_submit(tool_name, args or {})
        if not final_submit:
            human_review_message = self._human_review_requested_block_message(
                tool_name, args or {}
            )
            if human_review_message:
                return human_review_message
            direct_source_write_message = (
                self._execute_code_direct_source_write_block_message(
                    tool_name, args or {}
                )
            )
            if direct_source_write_message:
                return direct_source_write_message
            read_write_bridge_message = (
                self._execute_code_read_write_bridge_block_message(
                    tool_name, args or {}
                )
            )
            if read_write_bridge_message:
                return read_write_bridge_message
            candidate_test_write_message = self._candidate_test_write_block_message(
                tool_name, args or {}
            )
            if candidate_test_write_message:
                return candidate_test_write_message
            repeated_terminal_probe_message = (
                self._repeated_terminal_probe_block_message(tool_name, args or {})
            )
            if repeated_terminal_probe_message:
                return repeated_terminal_probe_message
            process_probe_message = self._repeated_empty_process_list_block_message(
                tool_name, args or {}
            )
            if process_probe_message:
                return process_probe_message
            probe_budget_message = self._probe_budget_block_message(
                tool_name, args or {}
            )
            if probe_budget_message:
                return probe_budget_message
            if is_edit_surface(tool_name, args or {}):
                self._edit_patch_authorization = self._active_patch_authorization
        if not self.fail_closed_on_submit or not final_submit:
            return None
        try:
            result = self.verify_completion(session_id=session_id, task_id=task_id)
        except Exception as exc:
            self._pending_completion_projection_text = (
                self._completion_unavailable_projection_text(exc)
            )
            self._append_policy_event(
                action="allowed_with_warning",
                reason=f"verify_completion unavailable: {exc.__class__.__name__}: {exc}",
                category="server_unavailable",
            )
            return None
        if self._is_accepted(result):
            self._completion_verified = True
            projection_text = self._accepted_completion_projection_text(result)
            if projection_text:
                self._pending_completion_projection_text = projection_text
            self._append_policy_event(
                action="allowed",
                reason=self._completion_summary(result)
                or self._completion_decision(result)
                or "Completion accepted.",
                category="completion_accepted",
            )
            return None
        self._append_policy_event(
            action="blocked",
            reason=self._rejection_message(result),
            category=self._legacy_rejection_category(result),
        )
        protocol_text = self._protocol_text(result)
        if protocol_text:
            self._set_protocol_text(protocol_text)
        return self._rejection_message(result)

    def post_llm_call_final_response_directive(
        self,
        *,
        assistant_response: str,
        session_id: str = "",
        task_id: str = "",
    ) -> dict[str, str] | None:
        self.ensure_identity(session_id=session_id, task_id=task_id)
        if self._completion_verified:
            return None
        if self._assistant_response_claims_completion_accept(assistant_response):
            return {
                "action": "replace_final_response",
                "final_response": (
                    "FormSy Finish Gate was not called. The patch may be implemented, "
                    "but completion is not verified yet. Call formsy_verify_completion "
                    "before reporting ACCEPT_DONE."
                ),
            }
        if self._assistant_response_claims_task_completion(
            assistant_response
        ) and self._has_unverified_current_diff():
            return {
                "action": "replace_final_response",
                "final_response": (
                    "FormSy Finish Gate was not called. The patch may be implemented, "
                    "but completion is not verified yet. Call formsy_verify_completion "
                    "before reporting done."
                ),
            }
        return None

    def verify_completion(
        self, *, session_id: str = "", task_id: str = ""
    ) -> dict[str, Any]:
        identity = self.ensure_identity(session_id=session_id, task_id=task_id)
        self.ensure_task_started(session_id=identity.session_id)
        self.flush_pending()
        diff_payload = self._append_fresh_diff_if_changed()
        semantic_guard_result = self._local_patch_semantic_guard(diff_payload)
        if semantic_guard_result is not None:
            return semantic_guard_result
        scope_guard_result = self._local_changed_files_scope_guard(diff_payload)
        if scope_guard_result is not None:
            return scope_guard_result
        written_collateral_guard_result = (
            self._local_unreviewed_written_validation_collateral_guard(diff_payload)
        )
        if written_collateral_guard_result is not None:
            return written_collateral_guard_result
        failed_candidate_guard_result = self._local_failed_candidate_test_guard(
            diff_payload
        )
        if failed_candidate_guard_result is not None:
            return failed_candidate_guard_result
        failed_validation_guard_result = self._local_unresolved_failed_validation_guard(
            diff_payload
        )
        if failed_validation_guard_result is not None:
            return failed_validation_guard_result
        validation_guard_result = self._local_validation_output_guard(diff_payload)
        if validation_guard_result is not None:
            return validation_guard_result
        completion_bootstrap = self._completion_bootstrap_payload(diff_payload)
        self._append_completion_bootstrap_observed(completion_bootstrap)
        self._append_event(
            {
                "event_kind": "done_claim",
                "trust": "agent_claimed",
                "payload": {"claimed_at_ms": _now_ms()},
            }
        )
        self.flush_pending()
        result = self._run_async(
            self.client.verify_completion(
                {
                    "task_id": identity.task_id,
                    "run_id": identity.run_id,
                    "completion_bootstrap": completion_bootstrap,
                },
                session_id=identity.session_id,
            )
        )
        self._capture_next_compile_reason(result)
        return result

    def _capture_next_compile_reason(self, response: Any) -> None:
        if not isinstance(response, dict):
            return
        audit = response.get("completion_audit")
        projection = audit.get("projection") if isinstance(audit, dict) else None
        if not isinstance(projection, dict):
            return
        if projection.get("next_action_kind") == "repair_patch":
            self._next_compile_reason = "repair_patch"

    def _record_contract_accepted_targets(self, payload: Any) -> None:
        tocs_targets = self._extract_resolved_tocs_repair_targets(payload)
        if tocs_targets:
            self._latest_accepted_targets = tocs_targets
            return
        if self._latest_context_bundle_hint.get("tocs_repair_targets"):
            return
        targets = self._extract_contract_accepted_targets(payload)
        if targets:
            self._latest_accepted_targets = targets

    def _record_patch_authorization(self, payload: Any) -> None:
        if not isinstance(payload, dict):
            return
        protocol = payload.get("protocol")
        if not isinstance(protocol, dict):
            protocol = payload
        contract_set_id = protocol.get("contract_set_id")
        contract_revision = protocol.get("contract_revision")
        if not isinstance(contract_set_id, str) or not isinstance(
            contract_revision, int
        ):
            return
        self._active_patch_authorization = PatchAuthorizationScope(
            contract_set_id=contract_set_id,
            contract_revision=contract_revision,
            accepted_targets=tuple(self._extract_contract_accepted_targets(payload)),
            strict_target_scope=self._extract_strict_target_scope(payload),
        )

    @classmethod
    def _extract_strict_target_scope(cls, payload: Any) -> bool:
        if not isinstance(payload, dict):
            return False
        patch = payload.get("patch")
        if isinstance(patch, dict) and isinstance(
            patch.get("strict_target_scope"), bool
        ):
            return bool(patch["strict_target_scope"])
        contracts = payload.get("contracts")
        if isinstance(contracts, dict):
            return cls._extract_strict_target_scope(contracts)
        contract = payload.get("contract")
        if isinstance(contract, dict):
            return cls._extract_strict_target_scope(contract)
        return False

    def _record_context_bundle_hint(
        self,
        context_bundle: dict[str, Any],
        search_payload: dict[str, Any],
        response: Any,
    ) -> None:
        hint: dict[str, Any] = {}
        if isinstance(context_bundle, dict):
            for key in (
                "bundle_id",
                "coverage",
                "primary_files",
                "must_edit",
                "test_plan",
            ):
                if key in context_bundle:
                    hint[key] = context_bundle[key]
        if isinstance(search_payload, dict):
            for key in (
                "query",
                "guidance",
                "candidate_tests",
                "tocs_contract_projection",
                "tocs_delivery",
            ):
                if key in search_payload:
                    hint[key] = search_payload[key]

        tocs_targets = self._extract_resolved_tocs_repair_targets(
            {"search_payload": search_payload, "response": response}
        )
        if tocs_targets:
            hint["tocs_repair_targets"] = tocs_targets
            hint["accepted_targets"] = tocs_targets
        else:
            accepted_targets = self._extract_contract_accepted_targets(
                {"search_payload": search_payload, "response": response}
            )
            if accepted_targets:
                hint["accepted_targets"] = accepted_targets

        if hint:
            self._latest_context_bundle_hint = hint

    def _record_written_path(
        self,
        tool_name: str,
        args: dict[str, Any],
        result: Any,
    ) -> None:
        if not _result_succeeded(result):
            return
        path = ""
        if tool_name in {"write_file", "patch"}:
            path = str(args.get("path") or "").strip()
        if path and path not in self._written_paths:
            self._written_paths.append(path)

    def _record_contract_candidate_tests(self, payload: Any) -> None:
        commands = self._extract_contract_candidate_test_commands(payload)
        if commands:
            self._latest_candidate_test_commands = commands

    def _record_contract_candidate_test_paths(self, payload: Any) -> None:
        paths = self._extract_contract_candidate_test_paths(payload)
        if paths:
            self._latest_candidate_test_paths = paths

    def _record_contract_validation_collateral(self, payload: Any) -> None:
        paths = self._extract_contract_validation_collateral(payload)
        if not paths:
            return
        merged = list(self._latest_validation_collateral_paths)
        for path in paths:
            if not any(_paths_equivalent(path, existing) for existing in merged):
                merged.append(path)
        self._latest_validation_collateral_paths = merged

    def _record_human_review_request(
        self,
        tool_name: str,
        args: dict[str, Any],
        result: Any,
    ) -> None:
        if tool_name != "formsy_request_human_review":
            return
        payload = _result_dict(result)
        if payload.get("requested") is not True:
            return
        reason = str(payload.get("reason") or args.get("reason") or "").strip()
        self._human_review_requested = {
            "reason": reason,
            "requested_at_ms": str(int(time.time() * 1000)),
        }

    def _human_review_requested_block_message(
        self,
        tool_name: str,
        args: dict[str, Any],
    ) -> str | None:
        if not self._human_review_requested:
            return None
        if tool_name in {"formsy_request_human_review", "formsy_constraint_status"}:
            return None
        continuation_tools = {
            "patch",
            "write_file",
            "terminal",
            "execute_code",
            "context_search",
            "context_read",
            "read_file",
            "search_files",
            "formsy_verify_completion",
            "formsy_recover",
        }
        if tool_name not in continuation_tools and not is_edit_surface(
            tool_name, args
        ):
            return None
        reason = self._human_review_requested.get("reason") or "review requested"
        self._append_policy_event(
            action="blocked",
            reason=f"human review already requested: {reason}",
            category="human_review_requested_stop",
        )
        return "\n".join(
            [
                "Human review has already been requested for this FormSy run.",
                "Do not continue patching, testing, searching, or recovery attempts in this turn.",
                f"Review reason: {reason}",
                "Stop and report the review request to the user.",
            ]
        )

    @classmethod
    def _extract_contract_accepted_targets(cls, payload: Any) -> list[str]:
        if not isinstance(payload, dict):
            return []
        values: list[str] = []
        for key in ("accepted_targets", "tocs_repair_targets"):
            values.extend(_string_list(payload.get(key)))
        patch = payload.get("patch")
        if isinstance(patch, dict):
            values.extend(_string_list(patch.get("accepted_targets")))
        contracts = payload.get("contracts")
        if isinstance(contracts, dict):
            values.extend(cls._extract_contract_accepted_targets(contracts))
        contract = payload.get("contract")
        if isinstance(contract, dict):
            values.extend(cls._extract_contract_accepted_targets(contract))
        search_payload = payload.get("search_payload")
        if isinstance(search_payload, dict):
            values.extend(cls._extract_contract_accepted_targets(search_payload))
        values.extend(
            cls._extract_strong_direct_match_targets(
                payload.get("matches"),
                query=payload.get("query") or payload.get("instruction"),
            )
        )
        normalized: list[str] = []
        for value in values:
            path = _repo_relative_source_path(value)
            if path and path not in normalized:
                normalized.append(path)
        return normalized

    @classmethod
    def _extract_strong_direct_match_targets(
        cls, matches: Any, *, query: Any = None
    ) -> list[str]:
        if not isinstance(matches, list):
            return []
        query_text = " ".join(str(query or "").lower().split())
        targets: list[str] = []
        for match in matches:
            if not isinstance(match, dict):
                continue
            if str(match.get("kind") or "").strip() != "direct_query_match":
                continue
            symbol = str(match.get("symbol") or "").strip()
            why = str(match.get("why_relevant") or "").lower()
            path = _repo_relative_source_path(str(match.get("path") or ""))
            if not path or cls._is_validation_collateral_path(path):
                continue
            strong_symbol = bool(symbol) or "explicit symbol anchor" in why
            strong_basename = (
                "exact basename query anchor" in why
                and cls._is_specific_basename_query_anchor(path, query_text)
            )
            if not strong_symbol and not strong_basename:
                continue
            if path not in targets:
                targets.append(path)
        return targets

    @classmethod
    def _is_specific_basename_query_anchor(cls, path: str, query_text: str) -> bool:
        filename = _normalize_path(path).rsplit("/", 1)[-1]
        stem = filename.rsplit(".", 1)[0].lower()
        if not stem or stem not in query_text:
            return False
        if stem in {
            "__init__",
            "app",
            "base",
            "client",
            "command",
            "commands",
            "common",
            "config",
            "core",
            "handler",
            "handlers",
            "helper",
            "helpers",
            "main",
            "manager",
            "models",
            "server",
            "service",
            "services",
            "test",
            "tests",
            "utils",
            "views",
        }:
            return False
        return True

    @classmethod
    def _extract_resolved_tocs_repair_targets(cls, payload: Any) -> list[str]:
        targets: list[str] = []

        def add_path(value: Any) -> None:
            path = _repo_relative_source_path(str(value or ""))
            if not path:
                return
            lowered = path.lower()
            if cls._is_validation_collateral_path(lowered):
                return
            if path not in targets:
                targets.append(path)

        def add_paths(values: Any) -> None:
            if isinstance(values, list):
                for item in values:
                    if isinstance(item, dict):
                        add_path(
                            item.get("path")
                            or item.get("file")
                            or item.get("source_path")
                            or item.get("target")
                        )
                    else:
                        add_path(item)
            else:
                add_path(values)

        def visit(value: Any, resolved: bool = False) -> None:
            if isinstance(value, dict):
                projection = value.get("tocs_contract_projection")
                projected_resolved = (
                    isinstance(projection, dict)
                    and str(projection.get("source") or "") == "resolved_tocs"
                )
                delivery = value.get("delivery") or value.get("tocs_delivery")
                delivery_resolved = (
                    isinstance(delivery, dict) and delivery.get("resolved") is True
                )
                current_resolved = resolved or projected_resolved or delivery_resolved

                for key in ("tocs_repair_targets", "repair_targets"):
                    add_paths(value.get(key))
                if current_resolved:
                    for key in ("accepted_targets", "must_edit", "must_read_files"):
                        add_paths(value.get(key))

                for nested_key in (
                    "guidance",
                    "tocs",
                    "search_payload",
                    "response",
                    "contracts",
                    "contract",
                    "patch",
                ):
                    if nested_key in value:
                        visit(value.get(nested_key), current_resolved)
            elif isinstance(value, list):
                for item in value:
                    visit(item, resolved)

        visit(payload)
        return targets

    def _record_warning_bearing_validation(self, event: dict[str, Any]) -> None:
        payload = event.get("payload")
        if not isinstance(payload, dict) or payload.get("passed") is not True:
            return
        output = str(payload.get("truncated_output") or "")
        warnings = self._validation_warning_markers(output)
        if not warnings:
            self._latest_warning_bearing_validation = {}
            return
        self._latest_warning_bearing_validation = {
            "command": str(payload.get("command") or ""),
            "output_hash": str(payload.get("output_hash") or ""),
            "warnings": warnings,
        }

    def _record_failed_validation(self, event: dict[str, Any]) -> None:
        payload = event.get("payload")
        if not isinstance(payload, dict) or payload.get("passed") is True:
            return
        command = str(payload.get("command") or "").strip()
        if not command or not is_validation_command(command):
            return
        output = str(
            payload.get("output")
            or payload.get("truncated_output")
            or payload.get("stderr")
            or payload.get("stdout")
            or ""
        )
        if self._is_repo_external_pytest_collection_probe(command, output):
            return
        diff_hash = self._current_diff_hash_for_guard()
        if not diff_hash:
            return
        self._unresolved_failed_validations[command] = {
            "command": command,
            "diff_hash": diff_hash,
            "output": output,
            "output_hash": str(payload.get("output_hash") or ""),
            "resolution_key": self._validation_resolution_key(command),
        }
        self._completion_guard_repeat_counts.pop(
            self._failed_validation_repeat_key(diff_hash, [command]),
            None,
        )

    @classmethod
    def _extract_contract_candidate_test_commands(cls, payload: Any) -> list[str]:
        commands: list[str] = []

        def visit(value: Any) -> None:
            if isinstance(value, dict):
                candidates = value.get("candidate_tests")
                if isinstance(candidates, list):
                    for candidate in candidates:
                        if not isinstance(candidate, dict):
                            continue
                        command = str(candidate.get("command") or "").strip()
                        test_id = str(candidate.get("test_id") or "").strip()
                        if not command and test_id:
                            command = f"pytest {test_id}"
                        if command and command not in commands:
                            commands.append(command)
                for nested_key in (
                    "guidance",
                    "tocs",
                    "tocs_delivery",
                    "search_payload",
                ):
                    if nested_key in value:
                        visit(value.get(nested_key))
            elif isinstance(value, list):
                for item in value:
                    visit(item)

        visit(payload)
        return commands

    @classmethod
    def _extract_contract_candidate_test_paths(cls, payload: Any) -> list[str]:
        paths: list[str] = []

        def add_path(value: str) -> None:
            path = _repo_relative_source_path(value.split("::", 1)[0])
            if path and path not in paths:
                paths.append(path)

        def visit(value: Any) -> None:
            if isinstance(value, dict):
                candidates = value.get("candidate_tests")
                if isinstance(candidates, list):
                    for candidate in candidates:
                        if not isinstance(candidate, dict):
                            continue
                        for key in (
                            "path",
                            "test_path",
                            "file",
                            "source_path",
                            "test_id",
                        ):
                            raw = str(candidate.get(key) or "").strip()
                            if raw:
                                add_path(raw)
                for nested_key in (
                    "guidance",
                    "tocs",
                    "tocs_delivery",
                    "search_payload",
                ):
                    if nested_key in value:
                        visit(value.get(nested_key))
            elif isinstance(value, list):
                for item in value:
                    visit(item)

        visit(payload)
        return paths

    @classmethod
    def _extract_contract_validation_collateral(cls, payload: Any) -> list[str]:
        if not isinstance(payload, dict):
            return []
        values: list[str] = []

        def add_paths(raw_values: Any) -> None:
            for value in _string_list(raw_values):
                path = _repo_relative_source_path(value.split("::", 1)[0])
                if path and path not in values:
                    values.append(path)

        add_paths(payload.get("validation_collateral"))
        patch = payload.get("patch")
        if isinstance(patch, dict):
            add_paths(patch.get("validation_collateral"))
        completion_audit = payload.get("completion_audit")
        if isinstance(completion_audit, dict):
            evidence = completion_audit.get("evidence")
            if isinstance(evidence, dict):
                add_paths(evidence.get("validation_collateral"))
        for nested_key in (
            "contracts",
            "contract",
            "search_payload",
            "response",
            "context_bundle",
        ):
            nested = payload.get(nested_key)
            if isinstance(nested, dict):
                for path in cls._extract_contract_validation_collateral(nested):
                    if path not in values:
                        values.append(path)
        return values

    def _clear_resolved_failed_validation(self, event: dict[str, Any]) -> None:
        payload = event.get("payload")
        if not isinstance(payload, dict) or payload.get("passed") is not True:
            return
        command = str(payload.get("command") or "").strip()
        if not command:
            return
        existing = self._unresolved_failed_validations.get(command)
        current_diff_hash = self._current_diff_hash_for_guard()
        if current_diff_hash:
            self._latest_passing_validations.append(
                {"command": command, "diff_hash": current_diff_hash}
            )
        if existing and existing.get("diff_hash") == current_diff_hash:
            self._unresolved_failed_validations.pop(command, None)
            self._completion_guard_repeat_counts.pop(
                self._failed_validation_repeat_key(
                    str(existing.get("diff_hash") or ""), [command]
                ),
                None,
            )
            return

        resolution_key = self._validation_resolution_key(command)
        deselected_selectors = _pytest_deselected_selectors_from_command(command)
        resolved_commands: list[str] = []
        for failed_command, failure in list(
            self._unresolved_failed_validations.items()
        ):
            if failure.get("diff_hash") != current_diff_hash:
                continue
            if (
                not resolution_key
                or failure.get("resolution_key") != resolution_key
            ) and not self._passing_command_deselected_failed_nodes(
                failure, deselected_selectors
            ):
                continue
            resolved_commands.append(failed_command)
            self._unresolved_failed_validations.pop(failed_command, None)
        for failed_command in resolved_commands:
            self._completion_guard_repeat_counts.pop(
                self._failed_validation_repeat_key(current_diff_hash, [failed_command]),
                None,
            )

    def _current_diff_hash_for_guard(self) -> str:
        latest = self._latest_diff_payload
        if isinstance(latest, dict) and latest.get("diff_hash"):
            return str(latest.get("diff_hash") or "")
        try:
            diff_text = self._diff_text_with_written_product_tests(
                self.diff_provider() or ""
            )
        except Exception:
            diff_text = ""
        return hash_text(diff_text) if diff_text.strip() else ""

    @classmethod
    def _validation_resolution_key(cls, command: str) -> str:
        effective = cls._effective_policy_command(str(command or ""))
        pytest_selectors = _pytest_selectors_from_command(effective)
        if pytest_selectors:
            return "pytest " + " ".join(pytest_selectors)
        try:
            tokens = shlex.split(effective)
        except ValueError:
            tokens = effective.split()
        while tokens and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", tokens[0]):
            tokens.pop(0)
        if tokens and tokens[0] == "env":
            tokens.pop(0)
            while tokens and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", tokens[0]):
                tokens.pop(0)
        if len(tokens) >= 3 and cls._is_python_executable_token(tokens[0]):
            if tokens[1:3] == ["-m", "pytest"]:
                tokens[0] = "python"
        return " ".join(tokens)

    @staticmethod
    def _is_python_executable_token(value: str) -> bool:
        token = str(value or "").strip().lower()
        if not token:
            return False
        name = token.rsplit("/", 1)[-1]
        return bool(re.fullmatch(r"python(?:\d+(?:\.\d+)?)?", name))

    @staticmethod
    def _validation_warning_markers(output: str) -> list[str]:
        text = str(output or "")
        markers = [
            "PytestUnraisableExceptionWarning",
            "ResourceWarning",
            "Exception ignored in:",
        ]
        return [marker for marker in markers if marker in text]

    def _local_changed_files_scope_guard(
        self,
        diff_payload: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        if not isinstance(diff_payload, dict):
            return None
        authorization = self._effective_local_patch_authorization()
        accepted_targets = (
            list(authorization.accepted_targets)
            if authorization is not None
            else list(self._latest_accepted_targets)
        )
        if not accepted_targets:
            return None
        changed_files = [
            _repo_relative_source_path(path)
            for path in diff_payload.get("changed_files") or []
        ]
        changed_files = [path for path in changed_files if path]
        validation_collateral = [
            path
            for path in changed_files
            if self._is_validation_collateral_path(path)
            and not any(_paths_equivalent(path, target) for target in accepted_targets)
        ]
        outside = [
            path
            for path in changed_files
            if not self._is_completion_auxiliary_path(path)
            and path not in validation_collateral
            and not any(_paths_equivalent(path, target) for target in accepted_targets)
        ]
        if not outside:
            return None
        if authorization is None or not authorization.strict_target_scope:
            return None
        outside_text = ", ".join(outside)
        accepted_text = ", ".join(accepted_targets)
        return {
            "decision": "NEED_MORE_VALIDATION",
            "protocol": {
                "summary": "Local patch semantic guard requires diff scope review before completion.",
                "blocking_conditions": [
                    (
                        "Latest diff changes files outside accepted targets: "
                        + outside_text
                    )
                ],
                "required_next_actions": [
                    f"Revert or remove changes outside accepted targets: {outside_text}.",
                    f"Keep patch edits limited to accepted targets: {accepted_text}.",
                    (
                        "Do not modify tests to satisfy validation unless FormSy "
                        "explicitly updates accepted targets."
                    ),
                    (
                        "After the outside-target diff is gone, rerun the relevant "
                        "validation and call Completion Verifier again."
                    ),
                ],
            },
            "completion_audit": {
                "audit_status": "blocked",
                "gate_decision": "NEED_MORE_VALIDATION",
                "evidence": {
                    "latest_diff_hash": str(diff_payload.get("diff_hash") or ""),
                    "changed_files": changed_files,
                    "accepted_targets": accepted_targets,
                    "outside_accepted_targets": outside,
                    "local_patch_semantic_guard": "changed_files_outside_accepted_targets",
                },
            },
        }

    def _effective_local_patch_authorization(
        self,
    ) -> PatchAuthorizationScope | None:
        return (
            self._latest_diff_patch_authorization
            or self._active_patch_authorization
        )

    @staticmethod
    def _is_completion_auxiliary_path(path: str) -> bool:
        normalized = _normalize_path(path)
        return normalized in {
            "patch.txt",
            "submission.patch",
            "submission.diff",
        } or normalized.startswith(".formsy/")

    @staticmethod
    def _is_validation_collateral_path(path: str) -> bool:
        normalized = _normalize_path(path)
        filename = normalized.rsplit("/", 1)[-1]
        return (
            "tests" in normalized.split("/")
            or normalized.startswith(("test/", "tests/"))
            or filename.startswith("test_")
            or filename.endswith("_test.py")
            or filename == "tests.py"
            or filename.endswith(".snap")
        )

    def _local_unreviewed_written_validation_collateral_guard(
        self,
        diff_payload: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        if not isinstance(diff_payload, dict):
            return None
        accepted_targets = list(self._latest_accepted_targets)
        changed_files = list(diff_payload.get("changed_files") or [])
        current_diff_paths = [
            _repo_relative_source_path(path) for path in changed_files
        ]
        current_diff_paths = [path for path in current_diff_paths if path]
        collateral = self._unreviewed_written_validation_collateral(
            accepted_targets=accepted_targets,
            authorized_validation_collateral=self._latest_validation_collateral_paths,
            current_diff_paths=current_diff_paths,
        )
        if not collateral:
            return None
        projection = {
            "decision": "NEED_MORE_VALIDATION",
            "agent_loop_terminal": False,
            "next_action_kind": "cleanup_or_review_validation_collateral",
            "next_action": (
                "Remove still-existing ad-hoc validation files, or explicitly "
                "report them as review-required validation collateral before "
                "claiming completion."
            ),
            "forbidden_actions": [
                "Do not claim completion while still-existing ad-hoc validation files are unreviewed.",
                "Do not treat temporary validation scripts as accepted product edits.",
                "Do not keep rerunning broad tests without resolving the collateral review blocker.",
            ],
            "evidence_to_report": [
                "changed_files",
                "accepted_targets",
                "validation_collateral",
                "latest focused validation command and result",
            ],
        }
        return {
            "decision": "NEED_MORE_VALIDATION",
            "protocol": {
                "summary": (
                    "Local completion guard requires validation collateral review."
                ),
                "blocking_conditions": [
                    (
                        "Validation evidence exists, but this run also wrote "
                        "validation collateral outside accepted targets: "
                        + ", ".join(collateral)
                    )
                ],
                "required_next_actions": [
                    (
                        "Remove temporary validation scripts or explicitly classify "
                        "them as review-required validation collateral."
                    ),
                    (
                        "Do not treat fallback candidate tests or ad-hoc validation "
                        "files as accepted product edits."
                    ),
                    (
                        "After cleanup or review, rerun focused validation and call "
                        "Completion Verifier again."
                    ),
                ],
            },
            "completion_audit": {
                "audit_status": "needs_review",
                "gate_decision": "NEED_MORE_VALIDATION",
                "evidence": {
                    "latest_diff_hash": str(diff_payload.get("diff_hash") or ""),
                    "changed_files": changed_files,
                    "accepted_targets": accepted_targets,
                    "validation_collateral": collateral,
                    "local_patch_semantic_guard": "unreviewed_validation_collateral",
                },
                "projection": projection,
            },
        }

    def _unreviewed_written_validation_collateral(
        self,
        *,
        accepted_targets: list[str],
        authorized_validation_collateral: list[str],
        current_diff_paths: list[str],
    ) -> list[str]:
        collateral: list[str] = []
        for path in self._written_paths:
            if not self._is_validation_collateral_path(path):
                continue
            if any(_paths_equivalent(path, target) for target in accepted_targets):
                continue
            if any(
                _paths_equivalent(path, authorized)
                for authorized in authorized_validation_collateral
            ):
                continue
            if any(_paths_equivalent(path, changed) for changed in current_diff_paths):
                continue
            if not self._written_path_still_exists(path):
                continue
            collateral.append(path)
        return collateral

    @staticmethod
    def _written_path_still_exists(path: str) -> bool:
        normalized = str(path or "").strip()
        if not normalized:
            return False
        candidate = Path(normalized).expanduser()
        if candidate.is_absolute():
            return candidate.exists()
        return True

    def _local_unresolved_failed_validation_guard(
        self,
        diff_payload: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        if not isinstance(diff_payload, dict):
            return None
        diff_hash = str(diff_payload.get("diff_hash") or "")
        if not diff_hash:
            return None
        failures = [
            failure
            for failure in self._unresolved_failed_validations.values()
            if failure.get("diff_hash") == diff_hash
            and not self._is_broad_unrelated_validation_failure(failure, diff_hash)
        ]
        if not failures:
            return None
        commands = [
            str(failure.get("command") or "")
            for failure in failures
            if str(failure.get("command") or "").strip()
        ]
        repeat_key = self._failed_validation_repeat_key(diff_hash, commands)
        repeat_count = self._completion_guard_repeat_counts.get(repeat_key, 0) + 1
        self._completion_guard_repeat_counts[repeat_key] = repeat_count
        repeated = repeat_count > 1
        guard = (
            "repeated_unresolved_failed_validation"
            if repeated
            else "unresolved_failed_validation"
        )
        blocking = [
            "A broader validation command failed after the latest diff and was not rerun successfully."
        ]
        if repeated:
            blocking.append(
                "Completion Verifier has already reported this same unresolved validation blocker for the current diff."
            )
        actions = [
            "Rerun the failed validation command(s) successfully after the current diff.",
            "Do not rerun already passing candidate tests as a substitute for the failed command(s).",
            "Do not use git stash or otherwise hide the current diff when collecting completion evidence.",
        ]
        actions.extend(
            f"Required failed validation command: {command}" for command in commands
        )
        if repeated:
            actions.append(
                "Stop calling Completion Verifier until one required failed validation command passes or the validation contract is explicitly narrowed."
            )
        return {
            "decision": "NEED_MORE_VALIDATION",
            "protocol": {
                "summary": "Local patch semantic guard requires unresolved failed validation to be closed.",
                "blocking_conditions": blocking,
                "required_next_actions": actions,
            },
            "completion_audit": {
                "audit_status": "blocked_repeated" if repeated else "blocked",
                "gate_decision": "NEED_MORE_VALIDATION",
                "evidence": {
                    "latest_diff_hash": diff_hash,
                    "changed_files": list(diff_payload.get("changed_files") or []),
                    "failed_validation_commands": commands,
                    "repeat_count": repeat_count,
                    "preferred_next_step": "rerun_failed_validation_command",
                    "local_patch_semantic_guard": guard,
                },
            },
        }

    @classmethod
    def _is_repo_external_pytest_collection_probe(
        cls, command: str, output: str
    ) -> bool:
        lowered_output = str(output or "").lower()
        if not (
            "error collecting" in lowered_output
            or "error during collection" in lowered_output
        ):
            return False
        if (
            "modulenotfounderror" not in lowered_output
            and "importerror" not in lowered_output
        ):
            return False
        selectors = _pytest_selectors_from_command(command)
        if not selectors:
            return False
        for selector in selectors:
            path = selector.split("::", 1)[0]
            if not cls._is_repo_external_temp_path(path):
                return False
        return True

    @staticmethod
    def _is_repo_external_temp_path(path: str) -> bool:
        candidate = str(path or "").strip()
        if not candidate:
            return False
        if not candidate.startswith("/"):
            return False
        normalized = candidate.replace("\\", "/")
        return (
            normalized.startswith("/tmp/")
            or normalized.startswith("/private/tmp/")
            or "/var/folders/" in normalized
        )

    @staticmethod
    def _failed_validation_repeat_key(diff_hash: str, commands: list[str]) -> str:
        normalized_commands = sorted(
            command.strip() for command in commands if command.strip()
        )
        return json.dumps(
            {"diff_hash": diff_hash, "commands": normalized_commands},
            sort_keys=True,
        )

    def _is_broad_unrelated_validation_failure(
        self, failure: dict[str, Any], diff_hash: str
    ) -> bool:
        if self._failure_resolved_by_prior_deselected_passing_validation(
            failure, diff_hash
        ):
            return True
        failed_paths = self._explicit_failed_validation_paths(
            str(failure.get("output") or "")
        )
        if not failed_paths:
            return False
        protected_paths = self._focused_validation_paths_for_diff(diff_hash)
        if not protected_paths:
            return False
        changed_test_paths = {
            _repo_relative_source_path(path)
            for path in self._latest_diff_payload.get("changed_files", [])
            if self._is_test_path(path)
        }
        protected_paths.update(path for path in changed_test_paths if path)
        if not protected_paths:
            return False
        return all(
            not any(_paths_equivalent(failed, protected) for protected in protected_paths)
            for failed in failed_paths
        )

    def _failure_resolved_by_prior_deselected_passing_validation(
        self, failure: dict[str, Any], diff_hash: str
    ) -> bool:
        for validation in self._latest_passing_validations:
            if validation.get("diff_hash") != diff_hash:
                continue
            command = str(validation.get("command") or "")
            deselected_selectors = _pytest_deselected_selectors_from_command(command)
            if self._passing_command_deselected_failed_nodes(
                failure, deselected_selectors
            ):
                return True
        return False

    def _focused_validation_paths_for_diff(self, diff_hash: str) -> set[str]:
        paths: set[str] = {
            _repo_relative_source_path(path)
            for path in self._latest_candidate_test_paths
            if path
        }
        candidate_selectors: list[str] = []
        for command in self._latest_candidate_test_commands:
            candidate_selectors.extend(_pytest_selectors_from_command(command))
        for validation in self._latest_passing_validations:
            if validation.get("diff_hash") != diff_hash:
                continue
            command = str(validation.get("command") or "")
            selectors = _pytest_selectors_from_command(command)
            if not selectors:
                continue
            if "::" not in command and all("::" not in selector for selector in selectors):
                continue
            for selector in selectors:
                paths.add(_repo_relative_source_path(selector.split("::", 1)[0]))
        for selector in candidate_selectors:
            paths.add(_repo_relative_source_path(selector.split("::", 1)[0]))
        return {path for path in paths if path}

    @staticmethod
    def _explicit_failed_validation_paths(output: str) -> set[str]:
        paths: set[str] = set()
        for node in ConstraintKeeperCoordinator._explicit_failed_validation_nodes(
            output
        ):
            path = _repo_relative_source_path(node.split("::", 1)[0])
            if path:
                paths.add(path)
        return paths

    @staticmethod
    def _explicit_failed_validation_nodes(output: str) -> set[str]:
        nodes: set[str] = set()
        for line in str(output or "").splitlines():
            stripped = line.strip()
            if not stripped.startswith("FAILED "):
                continue
            parts = stripped.split()
            if len(parts) < 2:
                continue
            raw_node = parts[1].strip()
            if not raw_node:
                continue
            path, sep, node = raw_node.partition("::")
            path = _repo_relative_source_path(path)
            if path:
                nodes.add(f"{path}{sep}{node}" if sep else path)
        return nodes

    @classmethod
    def _passing_command_deselected_failed_nodes(
        cls,
        failure: dict[str, Any],
        deselected_selectors: list[str],
    ) -> bool:
        if not deselected_selectors:
            return False
        failed_nodes = cls._explicit_failed_validation_nodes(
            str(failure.get("output") or "")
        )
        if not failed_nodes:
            return False
        return all(
            any(
                _pytest_selector_covers_failed_node(selector, failed_node)
                for selector in deselected_selectors
            )
            for failed_node in failed_nodes
        )

    @staticmethod
    def _is_test_path(file_path: str) -> bool:
        lowered = str(file_path or "").lower()
        return (
            lowered.startswith("test/")
            or lowered.startswith("tests/")
            or "/test/" in lowered
            or "/tests/" in lowered
            or lowered.endswith("_test.py")
            or lowered.endswith("test.py")
        )

    def _local_failed_candidate_test_guard(
        self,
        diff_payload: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        if not isinstance(diff_payload, dict):
            return None
        diff_hash = str(diff_payload.get("diff_hash") or "")
        if not diff_hash or not self._latest_candidate_test_commands:
            return None
        failed_commands = [
            str(failure.get("command") or "")
            for failure in self._unresolved_failed_validations.values()
            if failure.get("diff_hash") == diff_hash
            and self._is_candidate_test_command(str(failure.get("command") or ""))
        ]
        failed_commands = [command for command in failed_commands if command.strip()]
        if not failed_commands:
            return None
        return {
            "decision": "NEED_MORE_VALIDATION",
            "protocol": {
                "summary": (
                    "Local patch semantic guard requires failing exact candidate "
                    "tests to be repaired before completion."
                ),
                "blocking_conditions": [
                    (
                        "An exact candidate test failed after the latest diff; "
                        "this target-specific failure has priority over broad "
                        "validation narrowing."
                    )
                ],
                "required_next_actions": [
                    (
                        "Repair the failing exact candidate test before broad "
                        "validation or baseline comparison."
                    ),
                    "Rerun the exact candidate test successfully after the patch.",
                    "Call Completion Verifier again only after the candidate test passes.",
                ],
            },
            "completion_audit": {
                "audit_status": "blocked",
                "gate_decision": "NEED_MORE_VALIDATION",
                "evidence": {
                    "latest_diff_hash": diff_hash,
                    "changed_files": list(diff_payload.get("changed_files") or []),
                    "failed_candidate_test_commands": failed_commands,
                    "candidate_test_commands": list(
                        self._latest_candidate_test_commands
                    ),
                    "local_patch_semantic_guard": "failed_exact_candidate_tests",
                },
            },
        }

    def _is_candidate_test_command(self, command: str) -> bool:
        normalized = _normalize_command_for_match(command)
        if not normalized:
            return False
        for candidate in self._latest_candidate_test_commands:
            candidate_normalized = _normalize_command_for_match(candidate)
            if not candidate_normalized:
                continue
            if normalized == candidate_normalized:
                return True
            selector = _pytest_selector_from_command(candidate_normalized)
            if selector and selector in normalized:
                return True
        return False

    def _local_validation_output_guard(
        self,
        diff_payload: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        warning = self._latest_warning_bearing_validation
        if not warning:
            return None
        command = str(warning.get("command") or "validation")
        markers = _string_list(warning.get("warnings"))
        marker_text = ", ".join(markers) if markers else "warning"
        return {
            "decision": "NEED_MORE_VALIDATION",
            "protocol": {
                "summary": "Local patch semantic guard requires clean validation output before completion.",
                "blocking_conditions": [
                    (
                        "Latest validation passed but produced warning-bearing validation output: "
                        f"{marker_text} in `{command}`."
                    )
                ],
                "required_next_actions": [
                    "Fix the warning or rerun validation with clean output before calling Completion Verifier."
                ],
            },
            "completion_audit": {
                "audit_status": "blocked",
                "gate_decision": "NEED_MORE_VALIDATION",
                "evidence": {
                    "latest_diff_hash": str(
                        (diff_payload or {}).get("diff_hash") or ""
                    ),
                    "changed_files": list(
                        (diff_payload or {}).get("changed_files") or []
                    ),
                    "validation_command": command,
                    "validation_output_hash": str(warning.get("output_hash") or ""),
                    "validation_warning_markers": markers,
                    "local_patch_semantic_guard": "warning_bearing_validation",
                },
            },
        }

    def _local_patch_semantic_guard(
        self,
        diff_payload: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        if not isinstance(diff_payload, dict):
            return None
        diff_text = str(
            diff_payload.get("unified_diff") or diff_payload.get("diff") or ""
        )
        violations = self._suspicious_deleted_module_assignments(diff_text)
        if not violations:
            return None
        blocking = [
            (
                "Patch semantic guard found suspicious module-level assignment deletion: "
                f"{violation['path']} removes {violation['name']} without replacement."
            )
            for violation in violations
        ]
        return {
            "decision": "NEED_MORE_VALIDATION",
            "protocol": {
                "summary": "Local patch semantic guard requires diff review before completion.",
                "blocking_conditions": blocking,
                "required_next_actions": [
                    "Review the diff and restore or intentionally replace the removed module-level assignment."
                ],
            },
            "completion_audit": {
                "audit_status": "blocked",
                "gate_decision": "NEED_MORE_VALIDATION",
                "evidence": {
                    "latest_diff_hash": str(diff_payload.get("diff_hash") or ""),
                    "changed_files": list(diff_payload.get("changed_files") or []),
                    "local_patch_semantic_guard": "suspicious_module_assignment_deletion",
                },
            },
        }

    @classmethod
    def _suspicious_deleted_module_assignments(
        cls, diff_text: str
    ) -> list[dict[str, str]]:
        violations: list[dict[str, str]] = []
        current_path = ""
        deleted_by_file: dict[str, set[str]] = {}
        added_by_file: dict[str, set[str]] = {}
        for line in str(diff_text or "").splitlines():
            if line.startswith("diff --git "):
                current_path = cls._diff_git_b_path(line)
                continue
            if not current_path or line.startswith(("--- ", "+++ ", "@@ ")):
                continue
            if line.startswith("-"):
                name = cls._module_assignment_name(line[1:])
                if name:
                    deleted_by_file.setdefault(current_path, set()).add(name)
            elif line.startswith("+"):
                name = cls._module_assignment_name(line[1:])
                if name:
                    added_by_file.setdefault(current_path, set()).add(name)
        for path, deleted_names in deleted_by_file.items():
            added_names = added_by_file.get(path, set())
            for name in sorted(deleted_names - added_names):
                violations.append({"path": path, "name": name})
        return violations

    @staticmethod
    def _diff_git_b_path(line: str) -> str:
        parts = str(line or "").split()
        if len(parts) < 4:
            return ""
        path = parts[3]
        return path[2:] if path.startswith("b/") else path

    @staticmethod
    def _module_assignment_name(line: str) -> str:
        text = str(line or "")
        if not text or text[:1].isspace():
            return ""
        match = re.match(r"([A-Za-z_][A-Za-z0-9_]*)\s*=", text)
        if not match:
            return ""
        name = match.group(1)
        if name.startswith("__") and name.endswith("__"):
            return ""
        return name

    def recover(
        self, *, reason: str = "", session_id: str = "", task_id: str = ""
    ) -> dict[str, Any]:
        identity = self.ensure_identity(session_id=session_id, task_id=task_id)
        self.ensure_task_started(session_id=identity.session_id)
        response = self._run_async(
            self.client.recover(
                {
                    "task_id": identity.task_id,
                    "run_id": identity.run_id,
                    "reason": reason,
                },
                session_id=identity.session_id,
            )
        )
        self._set_protocol_text(self._protocol_text(response), recovery_open=True)
        return response

    def status(self, *, session_id: str = "", task_id: str = "") -> dict[str, Any]:
        identity = self.ensure_identity(session_id=session_id, task_id=task_id)
        return self._run_async(
            self.client.status(
                identity.task_id, identity.run_id, session_id=identity.session_id
            )
        )

    def flush_pending(self) -> None:
        identity = self.ensure_identity()
        for event in self.spool.pending(identity.task_id, identity.run_id):
            response = self._run_async(
                self.client.observe(
                    {"event": self._server_event(event, identity=identity)},
                    session_id=identity.session_id,
                )
            )
            self._capture_server_directive(response)
            protocol_text = self._protocol_text(response)
            if protocol_text:
                self._set_protocol_text(protocol_text)
            self.spool.mark_acked(
                task_id=identity.task_id,
                run_id=identity.run_id,
                event_id=str(event.get("event_id") or ""),
            )

    def _append_fresh_diff_if_changed(self) -> dict[str, Any] | None:
        diff_text = self._diff_text_with_written_product_tests(
            self.diff_provider() or ""
        )
        if not diff_text.strip():
            return None
        diff_hash = hash_text(diff_text)
        changed_files = changed_files_from_diff(diff_text)
        post_patch_sources: dict[str, str] = {}
        try:
            post_patch_sources = self.source_provider(changed_files) or {}
        except Exception:
            post_patch_sources = {}
        source_snapshot_hashes = {
            path: hash_text(source)
            for path, source in post_patch_sources.items()
            if isinstance(path, str) and isinstance(source, str)
        }
        payload = {
            "unified_diff": diff_text,
            "diff": diff_text,
            "diff_hash": diff_hash,
            "changed_files": changed_files,
        }
        authorization = (
            self._edit_patch_authorization
            or self._latest_diff_patch_authorization
            or self._active_patch_authorization
        )
        if authorization is not None:
            payload["authorized_contract_set_id"] = authorization.contract_set_id
            payload["authorized_contract_revision"] = (
                authorization.contract_revision
            )
        if post_patch_sources:
            payload["post_patch_sources"] = post_patch_sources
        if source_snapshot_hashes:
            payload["source_snapshot_hashes"] = source_snapshot_hashes
        self._latest_diff_payload = payload
        if diff_hash == self.latest_diff_hash:
            return payload
        self.latest_diff_hash = diff_hash
        self._latest_diff_patch_authorization = authorization
        self._edit_patch_authorization = None
        self._append_event(
            {
                "event_kind": "diff_observed",
                "trust": "plugin_observed",
                "payload": payload,
            }
        )
        return payload

    def _diff_text_with_written_product_tests(self, diff_text: str) -> str:
        text = str(diff_text or "")
        changed = set(changed_files_from_diff(text))
        extra_diffs: list[str] = []
        for written_path in self._written_paths:
            path = _repo_relative_source_path(written_path)
            if not path or path in changed:
                continue
            if not self._is_repo_test_suite_path(path):
                continue
            source = self._read_written_path_text(written_path, path)
            if source is None:
                continue
            extra_diffs.append(self._new_file_diff(path, source))
            changed.add(path)
        if not extra_diffs:
            return text
        prefix = text.rstrip()
        suffix = "\n".join(extra_diffs)
        return f"{prefix}\n{suffix}\n" if prefix else f"{suffix}\n"

    @staticmethod
    def _is_repo_test_suite_path(path: str) -> bool:
        normalized = _normalize_path(path)
        filename = normalized.rsplit("/", 1)[-1]
        return (
            normalized.startswith(("test/", "tests/"))
            and (
                filename.startswith("test_")
                or filename.endswith("_test.py")
                or filename == "tests.py"
            )
        )

    @staticmethod
    def _read_written_path_text(written_path: str, repo_path: str) -> str | None:
        candidates: list[Path] = []
        raw_path = Path(str(written_path or "")).expanduser()
        if raw_path.is_absolute():
            candidates.append(raw_path)
        candidates.append(Path(repo_path))
        candidates.append(Path(str(written_path or "")))
        for candidate in candidates:
            try:
                if not candidate.is_file():
                    continue
                data = candidate.read_bytes()
            except Exception:
                continue
            if len(data) > 256 * 1024:
                return None
            return data.decode("utf-8", errors="replace")
        return None

    @staticmethod
    def _new_file_diff(path: str, source: str) -> str:
        lines = source.splitlines()
        header = [
            f"diff --git a/{path} b/{path}",
            "new file mode 100644",
            "index 0000000..0000000",
            "--- /dev/null",
            f"+++ b/{path}",
            f"@@ -0,0 +1,{len(lines)} @@",
        ]
        body = [f"+{line}" for line in lines]
        if source.endswith("\n"):
            return "\n".join([*header, *body])
        return "\n".join([*header, *body, "\\ No newline at end of file"])

    def _completion_bootstrap_payload(
        self, diff_payload: dict[str, Any] | None
    ) -> dict[str, Any]:
        payload = diff_payload or self._latest_diff_payload or {}
        instruction = self._latest_user_task_text.strip()
        freshness = "current_run" if instruction else "unknown"
        post_patch_sources = payload.get("post_patch_sources")
        source_snapshot_hashes = payload.get("source_snapshot_hashes")
        result = {
            "instruction": instruction,
            "instruction_freshness": freshness,
            "unified_diff": str(payload.get("unified_diff") or ""),
            "changed_files": list(payload.get("changed_files") or []),
            "post_patch_sources": post_patch_sources
            if isinstance(post_patch_sources, dict)
            else {},
            "diff_hash": str(payload.get("diff_hash") or ""),
            "source_snapshot_hashes": (
                source_snapshot_hashes
                if isinstance(source_snapshot_hashes, dict)
                else {}
            ),
        }
        if self._latest_context_bundle_hint:
            result["context_bundle_hint"] = dict(self._latest_context_bundle_hint)
        return result

    def _append_completion_bootstrap_observed(
        self,
        completion_bootstrap: dict[str, Any],
    ) -> None:
        context_bundle_hint = completion_bootstrap.get("context_bundle_hint")
        hint_payload = (
            context_bundle_hint if isinstance(context_bundle_hint, dict) else {}
        )
        payload = {
            "completion_bootstrap_present": True,
            "instruction_freshness": completion_bootstrap.get("instruction_freshness"),
            "diff_hash": completion_bootstrap.get("diff_hash"),
            "changed_files": _string_list(completion_bootstrap.get("changed_files")),
            "context_bundle_hint_present": bool(hint_payload),
            "context_bundle_hint_keys": sorted(str(key) for key in hint_payload.keys()),
            "tocs_repair_targets": _string_list(
                hint_payload.get("tocs_repair_targets")
            ),
            "accepted_targets": _string_list(hint_payload.get("accepted_targets")),
        }
        self._append_event(
            {
                "event_kind": "completion_bootstrap_observed",
                "trust": "plugin_observed",
                "payload": payload,
            }
        )

    def _append_event(self, event: dict[str, Any]) -> dict[str, Any]:
        identity = self.ensure_identity()
        self._sequence += 1
        enriched = {
            "event_id": event.get("event_id") or f"ev_{uuid.uuid4().hex}",
            "task_id": identity.task_id,
            "run_id": identity.run_id,
            "sequence": self._sequence,
            "timestamp_ms": event.get("timestamp_ms") or _now_ms(),
            **event,
        }
        self.spool.append(
            task_id=identity.task_id, run_id=identity.run_id, event=enriched
        )
        return enriched

    def _append_policy_event(
        self, *, action: str, reason: str, category: str
    ) -> dict[str, Any]:
        return self._append_event(
            {
                "event_kind": "enforcement_decision",
                "trust": "plugin_observed",
                "payload": {
                    "policy_mode": "advisory",
                    "enforcement_action": action,
                    "category": category,
                    "reason": reason,
                },
            }
        )

    def _tool_observed_event(
        self,
        tool_name: str,
        args: dict[str, Any],
        result: Any,
    ) -> dict[str, Any] | None:
        payload = self._tool_observed_payload(tool_name, args, result)
        if not payload:
            return None
        return {
            "event_kind": "tool_observed",
            "trust": "plugin_observed",
            "payload": payload,
        }

    @staticmethod
    def _tool_observed_payload(
        tool_name: str,
        args: dict[str, Any],
        result: Any,
    ) -> dict[str, Any] | None:
        if tool_name == "read_file":
            path = str(args.get("path") or "")
            if not path:
                return None
            payload: dict[str, Any] = {"tool_name": tool_name, "path": path}
            for key in ("offset", "limit"):
                value = args.get(key)
                if isinstance(value, int):
                    payload[key] = value
            return payload

        if tool_name == "search_files":
            payload = {"tool_name": tool_name}
            path = str(args.get("path") or "")
            pattern = str(args.get("pattern") or "")
            if path:
                payload["path"] = path
            if pattern:
                payload["pattern_hash"] = hash_text(pattern)
            total_count = _result_total_count(result)
            if total_count is not None:
                payload["total_count"] = total_count
            return payload

        if tool_name == "execute_code":
            code = str(args.get("code") or "")
            payload = {
                "tool_name": tool_name,
                "probe_kind": "isolated_code",
                "code_hash": hash_text(code),
                "code_length": len(code),
            }
            return payload

        return None

    def _task_key(self) -> tuple[str, str]:
        if self.identity is None:
            return "", ""
        return self.identity.task_id, self.identity.run_id

    def _reset_guidance_state_if_task_changed(self) -> None:
        key = self._task_key()
        if key == self._guidance_task_key:
            return
        self._guidance_task_key = key
        self._reset_guidance_state(clear_protocol=True)

    def _reset_guidance_state(self, *, clear_protocol: bool = False) -> None:
        self._bootstrap_guidance_injected = False
        self._context_search_seen = False
        self._grounding_state = _GroundingState.NEEDS_GROUNDING
        self._bootstrap_source_exploration_reserved = False
        self._exploration_without_context_count = 0
        self._exploration_guidance_injected = False
        self._terminal_failure_count = 0
        self._terminal_failure_guidance_injected = False
        self._code_probe_count = 0
        self._code_probe_guidance_injected = False
        self._pending_guidance_text = ""
        self._pending_completion_projection_text = ""
        self._active_context_directive = None
        self._active_probe_budget_directive = None
        self._active_next_tool_directive = None
        self._next_tool_deviation_count = 0
        self._next_tool_visible_delivery_count = 0
        self._next_tool_failed = False
        self._context_read_counts = {}
        self._pending_tool_result_replacement = ""
        self._last_successful_terminal_probe = {}
        self._probe_budget_counts = {
            "search_files": 0,
            "read_file": 0,
            "terminal_or_execute_code": 0,
        }
        self._empty_process_list_count = 0
        self._full_diff_stdout_count = 0
        if clear_protocol:
            self.latest_protocol_text = ""
            self._last_injected_protocol_text = ""
            self.latest_diff_hash = ""
            self.recovery_open = False
            self._active_patch_authorization = None
            self._edit_patch_authorization = None
            self._latest_diff_patch_authorization = None
            self._next_compile_reason = "context_refresh"

    @staticmethod
    def _workspace_revalidation_card() -> str:
        return (
            "FormSy workspace revalidation required\n"
            "- Do not claim this task is already completed from session history alone.\n"
            "- First check the current working tree, e.g. git status --short or equivalent.\n"
            "- Then call context_search to compare memory/verified recipes with current source before finalizing."
        )

    def _mark_context_retrieval_seen(self) -> None:
        self._context_search_seen = True
        self._grounding_state = _GroundingState.GROUNDED
        self._pending_guidance_text = ""
        self._active_context_directive = None
        self._active_next_tool_directive = None
        self._next_tool_deviation_count = 0
        self._next_tool_visible_delivery_count = 0
        self._next_tool_failed = False

    def _observe_skill_uptake(
        self, tool_name: str, args: dict[str, Any], result: Any
    ) -> None:
        if tool_name != "skill_view":
            return
        requested = str(
            args.get("name") or args.get("skill") or args.get("skill_name") or ""
        ).strip()
        if requested != self._skill_name:
            return
        self._mark_skill_body_loaded(
            visibility="skill_view_loaded",
            result_len=len(str(result or "")),
        )

    def _with_formsy_context_skill_capsule(
        self, context: str, *, session_id: str = ""
    ) -> str:
        capsule = self._formsy_context_skill_capsule()
        self._mark_skill_body_loaded(
            visibility="plugin_projected",
            result_len=len(capsule),
            session_id=session_id,
        )
        return f"{capsule}\n\n{context}"

    @staticmethod
    def _formsy_context_skill_capsule() -> str:
        return (
            "FormSy Context skill capsule\n"
            "- Use context_search before broad source exploration, patching, or final submission.\n"
            "- After context_search returns a relevant target, use context_read or a same-target local read fallback.\n"
            "- Patch accepted targets first and keep diff review compact.\n"
            "- Call Completion Verifier before final submission and stop after ACCEPT_DONE."
        )

    def _mark_skill_body_loaded(
        self,
        *,
        visibility: str,
        result_len: int,
        session_id: str = "",
    ) -> None:
        if self._skill_body_loaded and self._skill_visibility == visibility:
            return
        self._skill_visibility = visibility
        self._skill_body_loaded = True
        logger.info(
            "event=skill_uptake_observed skill_name=%s skill_visibility=%s "
            "skill_body_loaded=%s result_len=%s session_id=%s task_id=%s run_id=%s",
            self._skill_name,
            self._skill_visibility,
            self._skill_body_loaded,
            result_len,
            session_id or self.identity.session_id,
            self.identity.task_id,
            self.identity.run_id,
        )

    def _skill_is_installed(self) -> bool:
        candidates = [
            Path.home()
            / ".hermes"
            / "skills"
            / "software-development"
            / self._skill_name
            / "SKILL.md",
            Path.home() / ".hermes" / "skills" / self._skill_name / "SKILL.md",
            Path.cwd()
            / "skills"
            / "software-development"
            / self._skill_name
            / "SKILL.md",
            Path.cwd() / "skills" / self._skill_name / "SKILL.md",
        ]
        return any(path.exists() for path in candidates)

    def _observe_guidance_signal(
        self, tool_name: str, args: dict[str, Any], result: Any
    ) -> None:
        if tool_name == "context_search":
            query = str(args.get("query") or "").strip()
            if query:
                self._latest_grounding_query_text = query
            self._mark_context_retrieval_seen()
            return
        if tool_name == "context_read":
            self._maybe_satisfy_next_tool_directive(args, result)
            self._record_context_read_repeat(args, result)
            return
        if tool_name == "read_file":
            self._maybe_satisfy_context_read_directive_from_same_target_read(
                args, result
            )
            self._maybe_satisfy_failed_next_tool_from_same_target_read(args, result)
        self._maybe_emit_pending_next_action_reminder(tool_name, args)
        if self._context_search_seen:
            if (
                tool_name == "execute_code"
                and not self.latest_diff_hash
                and not self._code_probe_guidance_injected
            ):
                self._code_probe_count += 1
                if self._code_probe_count >= 5:
                    self._code_probe_guidance_injected = True
                    self._pending_guidance_text = _CODE_PROBE_LOOP_GUIDANCE
            return

        self._maybe_emit_grounding_advisory(tool_name, args)

        if self._is_bootstrap_source_exploration_tool(tool_name, args):
            self._exploration_without_context_count += 1
            if (
                self._exploration_without_context_count >= 3
                and not self._exploration_guidance_injected
                and self._grounding_state == _GroundingState.NEEDS_GROUNDING
            ):
                self._exploration_guidance_injected = True
                self._pending_guidance_text = _EXPLORATION_GUIDANCE

        if tool_name == "terminal":
            event = classify_terminal_result(args, result)
            if event and event.get("event_kind") == "failure":
                self._terminal_failure_count += 1
                if (
                    self._terminal_failure_count >= 3
                    and not self._terminal_failure_guidance_injected
                ):
                    self._terminal_failure_guidance_injected = True
                    self._pending_guidance_text = _TERMINAL_FAILURE_GUIDANCE

    def _bootstrap_context_search_block_message(
        self,
        tool_name: str,
        args: dict[str, Any],
    ) -> str | None:
        if self._context_search_seen or tool_name == "context_search":
            return None
        if tool_name == "context_read":
            return _BOOTSTRAP_CONTEXT_READ_BLOCK
        if is_final_submit(tool_name, args):
            return None
        if is_edit_surface(tool_name, args):
            return _BOOTSTRAP_CONTEXT_SEARCH_BLOCK
        if self._is_bootstrap_source_exploration_tool(tool_name, args):
            if (
                self._bootstrap_source_exploration_reserved
                or self._exploration_without_context_count >= 1
            ):
                return _BOOTSTRAP_CONTEXT_SEARCH_BLOCK
            self._bootstrap_source_exploration_reserved = True
        return None

    def _is_bootstrap_source_exploration_tool(
        self,
        tool_name: str,
        args: dict[str, Any],
    ) -> bool:
        return tool_name == "execute_code" or self._is_broad_source_exploration_tool(
            tool_name, args
        )

    def _maybe_emit_grounding_advisory(
        self,
        tool_name: str,
        args: dict[str, Any],
    ) -> None:
        if self._grounding_state != _GroundingState.NEEDS_GROUNDING:
            return
        if not (
            self._is_bootstrap_source_exploration_tool(tool_name, args)
            or is_edit_surface(tool_name, args)
        ):
            return
        self._grounding_state = _GroundingState.GROUNDING_ADVISED
        query = self._grounding_query_hint(tool_name, args)
        self._materialize_grounding_next_action(query)
        self._next_tool_visible_delivery_count = 1
        self._pending_guidance_text = self._grounding_action_card(query)

    def _materialize_grounding_next_action(self, query: str) -> dict[str, Any]:
        directive = {
            "action_id": "grounding.seed.1",
            "tool": "context_search",
            "args": {"query": query},
            "reason": "Retrieve memory, ranked evidence, verifier contracts, and next-step guidance.",
            "enforcement": "suggested",
        }
        self._active_next_tool_directive = directive
        self._next_tool_deviation_count = 0
        self._next_tool_visible_delivery_count = 0
        self._next_tool_failed = False
        return directive

    @staticmethod
    def _recommended_next_action_card(directive: dict[str, Any]) -> str:
        tool = (
            str(directive.get("tool") or "context_search").strip() or "context_search"
        )
        directive_args = directive.get("args")
        query = ""
        if isinstance(directive_args, dict):
            query = str(directive_args.get("query") or "").strip()
        reason = str(directive.get("reason") or "").strip()
        action_id = str(directive.get("action_id") or f"{tool}.next").strip()
        if tool == "context_search" and query:
            call = f'context_search({{"query": "{_json_string_value(query)}"}})'
        else:
            call = tool
        lines = [
            "FormSy recommended next action (advisory)",
            f"Action ID: {action_id}",
            f"Call: {call}",
        ]
        if reason:
            lines.append(f"Why: {reason}")
        lines.append(
            "Policy: advisory only; continuing is allowed; Completion Gate verifies final submit."
        )
        return "\n".join(lines)

    def _log_pre_llm_projection_delivered(
        self,
        *,
        action_id: str,
        context: str,
        delivery_count: int,
        session_id: str,
    ) -> None:
        logger.info(
            "event=pre_llm_projection_delivered surface=pre_llm action_id=%s "
            "session_id=%s task_id=%s run_id=%s delivery_count=%s context_len=%s "
            "context_hash=%s",
            action_id or "-",
            session_id or self.identity.session_id,
            self.identity.task_id,
            self.identity.run_id,
            delivery_count,
            len(context),
            hash_text(context),
        )

    def _log_advisory_uptake_missed(
        self,
        *,
        directive: dict[str, Any],
        actual_tool: str,
        deviation_count: int,
        delivery_count: int,
    ) -> None:
        expected_tool = str(directive.get("tool") or "").strip() or "-"
        action_id = str(directive.get("action_id") or "").strip() or "-"
        logger.info(
            "event=advisory_uptake_missed action_id=%s expected_tool=%s "
            "actual_tool=%s session_id=%s task_id=%s run_id=%s "
            "deviation_count=%s delivery_count=%s",
            action_id,
            expected_tool,
            actual_tool or "-",
            self.identity.session_id,
            self.identity.task_id,
            self.identity.run_id,
            deviation_count,
            delivery_count,
        )

    def _log_advisory_uptake_satisfied_via_fallback(
        self,
        *,
        directive: dict[str, Any],
        actual_tool: str,
        path: str,
    ) -> None:
        expected_tool = str(directive.get("tool") or "").strip() or "-"
        action_id = str(directive.get("action_id") or "").strip() or "-"
        logger.info(
            "event=advisory_uptake_satisfied_via_fallback action_id=%s "
            "expected_tool=%s actual_tool=%s path=%s session_id=%s task_id=%s run_id=%s",
            action_id,
            expected_tool,
            actual_tool or "-",
            path or "-",
            self.identity.session_id,
            self.identity.task_id,
            self.identity.run_id,
        )

    @staticmethod
    def _grounding_action_card(query: str) -> str:
        return (
            "FormSy grounding action card\n"
            "State: needs_grounding\n"
            f'Recommended next tool call: context_search({{"query": "{query}"}})\n'
            "Why: retrieve memory, ranked evidence, verifier contracts, and next-step guidance.\n"
            "Policy: advisory only; continuing is allowed; final completion is still verified by FormSy Completion Gate.\n"
            "If this query no longer matches the current task, use the current PR description as the query."
        )

    def _grounding_query_hint(self, tool_name: str, args: dict[str, Any]) -> str:
        task_query = self._compact_task_query(self._latest_user_task_text)
        if task_query:
            return _json_string_value(task_query[:320])
        previous_query = self._compact_task_query(self._latest_grounding_query_text)
        if previous_query:
            return _json_string_value(previous_query[:320])
        query = (
            self._tool_query_hint(tool_name, args)
            or "current task key symbols and accepted edit target"
        )
        return _json_string_value(query[:320])

    @staticmethod
    def _compact_task_query(task_text: str) -> str:
        text = str(task_text or "").strip()
        if not text:
            return ""
        title_match = re.search(r"(?im)^#\s*title\s*$\s*([^\n]+)", text)
        if title_match:
            return title_match.group(1).strip()
        cleaned = re.sub(r"<[^>]+>", " ", text)
        cleaned = " ".join(cleaned.split())
        return cleaned[:180]

    @classmethod
    def _is_auxiliary_task_text(cls, task_text: Any) -> bool:
        return cls._is_meta_maintenance_task_text(
            task_text
        ) or cls._is_validation_only_task_text(
            task_text
        ) or cls._is_execution_policy_only_text(task_text)

    @staticmethod
    def _is_execution_policy_only_text(task_text: Any) -> bool:
        text = " ".join(str(task_text or "").lower().split())
        if not text:
            return False
        policy_markers = (
            "do not stop to ask design questions",
            "make the best implementation decision",
            "patch the code",
            "add focused unit tests",
            "run the tests",
            "only then report uncertainty",
        )
        domain_markers = (
            "expected behavior",
            "actual behavior",
            "traceback",
            "error:",
            "exception",
            "<task_description>",
            "<pr_description>",
        )
        marker_count = sum(1 for marker in policy_markers if marker in text)
        if marker_count < 3:
            return False
        return not any(marker in text for marker in domain_markers)

    @classmethod
    def _promotable_task_text(cls, query: Any, instruction: Any) -> str:
        for value in (instruction, query):
            text = str(value or "").strip()
            if not text:
                continue
            if cls._is_auxiliary_task_text(text):
                continue
            lowered = " ".join(text.lower().split())
            if lowered.startswith("/"):
                continue
            if len(text) < 24:
                continue
            if any(
                marker in lowered
                for marker in (
                    "expected behavior",
                    "actual behavior",
                    "should ",
                    "fix ",
                    "bug",
                    "error",
                    "exception",
                    "regression",
                    "handle ",
                    "respecting ",
                )
            ):
                return text
        return ""

    @staticmethod
    def _is_meta_maintenance_task_text(task_text: Any) -> bool:
        text = " ".join(str(task_text or "").lower().split())
        if not text:
            return False
        return (
            "review the conversation above" in text
            and "skill" in text
            and "update" in text
        )

    @staticmethod
    def _is_validation_only_task_text(task_text: Any) -> bool:
        text = " ".join(str(task_text or "").lower().split())
        if not text:
            return False
        validation_terms = (
            "test",
            "tests",
            "pytest",
            "unit test",
            "unit tests",
            "validation",
            "validate",
        )
        report_terms = ("report", "summarize", "result", "results")
        modification_terms = (
            "do not modify",
            "do not edit",
            "no files were created or modified",
            "only run",
        )
        repair_terms = (
            "fix",
            "implement",
            "patch",
            "change the code",
            "modify the code",
        )
        if any(term in text for term in repair_terms):
            return False
        if not any(term in text for term in validation_terms):
            return False
        if any(term in text for term in modification_terms):
            return True
        return text.startswith(("run ", "rerun ", "execute ")) and any(
            term in text for term in report_terms
        )

    @staticmethod
    def _tool_query_hint(tool_name: str, args: dict[str, Any]) -> str:
        path = _repo_relative_source_path(str(args.get("path") or ""))
        pattern = str(args.get("pattern") or "").strip()
        command = str(args.get("command") or args.get("cmd") or "").strip()
        if path and pattern:
            return f"{path} {pattern}"
        if path:
            return path
        if pattern:
            return pattern
        if tool_name == "execute_code":
            return "runtime invariant from recent execute_code probe"
        if command:
            return _command_query_hint(command)
        return ""

    def _capture_guidance_packet(self, tool_name: str, result: Any) -> None:
        if tool_name != "context_search":
            return
        parsed = _result_dict(result)
        packet = parsed.get("guidance_packet") if isinstance(parsed, dict) else None
        if isinstance(packet, dict) and packet.get("mode") == "degraded_recovery":
            self._active_probe_budget_directive = packet
            directive = packet.get("next_tool_directive") or packet.get(
                "required_next_tool"
            )
            if isinstance(directive, dict):
                captured = dict(directive)
                tool = str(captured.get("tool") or "").strip() or "context_search"
                captured["action_id"] = str(
                    captured.get("action_id") or f"{tool}.next"
                ).strip()
                captured.setdefault("enforcement", "suggested")
                self._active_next_tool_directive = captured
                self._next_tool_visible_delivery_count = 1
                self._pending_guidance_text = self._next_tool_directive_text(captured)
            else:
                self._active_next_tool_directive = None
                self._next_tool_visible_delivery_count = 0
            self._next_tool_deviation_count = 0
            self._next_tool_failed = False
            self._probe_budget_counts = {
                "search_files": 0,
                "read_file": 0,
                "terminal_or_execute_code": 0,
            }
            self._full_diff_stdout_count = 0
            return
        self._active_probe_budget_directive = None
        self._active_next_tool_directive = None
        self._next_tool_deviation_count = 0
        self._next_tool_visible_delivery_count = 0
        self._next_tool_failed = False

    def _maybe_emit_pending_next_action_reminder(
        self,
        tool_name: str,
        args: dict[str, Any],
    ) -> None:
        directive = self._active_next_tool_directive
        if not directive:
            return
        if self._pending_guidance_text:
            return
        if str(directive.get("enforcement") or "suggested") == "lifecycle_required":
            return
        if self._next_tool_matches_directive(tool_name, args, directive):
            self._active_next_tool_directive = None
            self._next_tool_deviation_count = 0
            self._next_tool_visible_delivery_count = 0
            self._next_tool_failed = False
            return
        if not self._is_pending_next_action_effective_deviation(tool_name, args):
            return
        self._next_tool_deviation_count += 1
        if self._next_tool_deviation_count == 1:
            self._log_advisory_uptake_missed(
                directive=directive,
                actual_tool=tool_name,
                deviation_count=self._next_tool_deviation_count,
                delivery_count=self._next_tool_visible_delivery_count,
            )
        if (
            self._next_tool_deviation_count == 1
            and self._next_tool_visible_delivery_count < 2
        ):
            self._pending_guidance_text = self._pending_next_action_reminder_text(
                directive
            )
            self._next_tool_visible_delivery_count += 1
        self._active_next_tool_directive = None
        self._next_tool_failed = False

    def _next_tool_matches_directive(
        self,
        tool_name: str,
        args: dict[str, Any],
        directive: dict[str, Any],
    ) -> bool:
        required_tool = str(directive.get("tool") or "").strip()
        directive_args = directive.get("args")
        required_path = ""
        if isinstance(directive_args, dict):
            required_path = _normalize_path(str(directive_args.get("path") or ""))
        if required_tool == "context_search":
            return tool_name == "context_search"
        if required_tool == "context_read":
            if tool_name == "context_read":
                requested_path = _normalize_path(str(args.get("path") or ""))
                return _paths_equivalent(required_path, requested_path)
            return False
        if required_tool in {"compact_diff", "compact_diff_review"}:
            command = str(args.get("command") or args.get("cmd") or "")
            return tool_name == "terminal" and self._is_compact_diff_review_command(
                command
            )
        return False

    @staticmethod
    def _is_pending_next_action_effective_deviation(
        tool_name: str, args: dict[str, Any]
    ) -> bool:
        if tool_name == "read_file":
            path = str(args.get("path") or "")
            return bool(path and not path.endswith(".md"))
        if tool_name in {"search_files", "execute_code"}:
            return True
        return is_edit_surface(tool_name, args)

    @staticmethod
    def _pending_next_action_reminder_text(directive: dict[str, Any]) -> str:
        tool = str(directive.get("tool") or "").strip() or "context_search"
        directive_args = directive.get("args")
        query = ""
        path = ""
        if isinstance(directive_args, dict):
            query = str(directive_args.get("query") or "").strip()
            path = _normalize_path(str(directive_args.get("path") or ""))
        reason = str(directive.get("reason") or "").strip()
        action_id = str(directive.get("action_id") or f"{tool}.next").strip()
        if tool == "context_search" and query:
            call = f'context_search({{"query": "{_json_string_value(query)}"}})'
        elif tool == "context_read" and path:
            call = f"context_read path={path}"
        elif tool in {"compact_diff", "compact_diff_review"}:
            call = "git diff --stat or git diff -- <accepted-edit-target>"
        else:
            call = tool
        lines = [
            "FormSy next action still pending",
            f"Action ID: {action_id}",
            f"Recommended next tool call: {call}",
        ]
        if reason:
            lines.append(
                f"Why now: prior guidance was not followed before another source/edit action. {reason}"
            )
        else:
            lines.append(
                "Why now: prior guidance was not followed before another source/edit action."
            )
        lines.append(
            "Policy: advisory only; continuing is allowed. If you continue without this, "
            "Completion Gate will verify the final patch against FormSy contracts."
        )
        return "\n".join(lines)

    def _maybe_satisfy_next_tool_directive(
        self, args: dict[str, Any], result: Any
    ) -> None:
        directive = self._active_next_tool_directive
        if not directive or directive.get("tool") != "context_read":
            return
        expected_args = directive.get("args")
        expected_path = ""
        if isinstance(expected_args, dict):
            expected_path = _normalize_path(str(expected_args.get("path") or ""))
        actual_path = _normalize_path(str(args.get("path") or ""))
        if _paths_equivalent(expected_path, actual_path) and _result_succeeded(result):
            self._active_next_tool_directive = None
            self._next_tool_deviation_count = 0
            self._next_tool_visible_delivery_count = 0
            self._next_tool_failed = False
            self._pending_guidance_text = ""
        elif _paths_equivalent(expected_path, actual_path):
            self._next_tool_failed = True

    def _maybe_satisfy_context_read_directive_from_same_target_read(
        self,
        args: dict[str, Any],
        result: Any,
    ) -> None:
        directive = self._active_next_tool_directive
        if not directive or directive.get("tool") != "context_read":
            return
        directive_args = directive.get("args")
        expected_path = ""
        if isinstance(directive_args, dict):
            expected_path = _normalize_path(str(directive_args.get("path") or ""))
        actual_path = _normalize_path(str(args.get("path") or ""))
        if _paths_equivalent(expected_path, actual_path) and _result_has_content(
            result
        ):
            self._log_advisory_uptake_satisfied_via_fallback(
                directive=directive,
                actual_tool="read_file",
                path=expected_path or actual_path,
            )
            self._active_next_tool_directive = None
            self._next_tool_deviation_count = 0
            self._next_tool_visible_delivery_count = 0
            self._next_tool_failed = False
            self._pending_guidance_text = ""

    def _maybe_satisfy_failed_next_tool_from_same_target_read(
        self,
        args: dict[str, Any],
        result: Any,
    ) -> None:
        if not self._next_tool_failed:
            return
        directive = self._active_next_tool_directive
        if not directive:
            return
        directive_args = directive.get("args")
        expected_path = ""
        if isinstance(directive_args, dict):
            expected_path = _normalize_path(str(directive_args.get("path") or ""))
        actual_path = _normalize_path(str(args.get("path") or ""))
        if _paths_equivalent(expected_path, actual_path) and _result_has_content(
            result
        ):
            self._log_advisory_uptake_satisfied_via_fallback(
                directive=directive,
                actual_tool="read_file",
                path=expected_path or actual_path,
            )
            self._active_next_tool_directive = None
            self._next_tool_deviation_count = 0
            self._next_tool_visible_delivery_count = 0
            self._next_tool_failed = False

    def _next_tool_directive_block_message(
        self,
        tool_name: str,
        args: dict[str, Any],
    ) -> str | None:
        directive = self._active_next_tool_directive
        if not directive:
            return None
        if str(directive.get("enforcement") or "suggested") != "lifecycle_required":
            return None
        if self._next_tool_failed and tool_name == "read_file":
            directive_args = directive.get("args")
            required_path = ""
            if isinstance(directive_args, dict):
                required_path = _normalize_path(str(directive_args.get("path") or ""))
            requested_path = _normalize_path(str(args.get("path") or ""))
            if _paths_equivalent(required_path, requested_path):
                return None
        if self._next_tool_failed and directive.get("tool") == "context_read":
            if tool_name == "context_read":
                return self._failed_context_read_directive_text(directive)
            if tool_name in {
                "terminal",
                "search_files",
                "patch",
                "write_file",
                "execute_code",
            }:
                return self._failed_context_read_directive_text(directive)
        required_tool = str(directive.get("tool") or "").strip()
        directive_args = directive.get("args")
        required_path = ""
        if isinstance(directive_args, dict):
            required_path = _normalize_path(str(directive_args.get("path") or ""))
        if tool_name == required_tool:
            if required_tool != "context_read":
                return None
            requested_path = _normalize_path(str(args.get("path") or ""))
            if _paths_equivalent(required_path, requested_path):
                return None
            self._next_tool_deviation_count += 1
            if self._next_tool_deviation_count == 1:
                self._pending_guidance_text = self._next_tool_directive_text(directive)
                return None
            return self._next_tool_directive_text(directive)
        if tool_name in {"terminal", "search_files", "read_file", "execute_code"}:
            return self._next_tool_directive_text(directive)
        self._next_tool_deviation_count += 1
        if self._next_tool_deviation_count == 1:
            self._pending_guidance_text = self._next_tool_directive_text(directive)
            return None
        return self._next_tool_directive_text(directive)

    @staticmethod
    def _next_tool_directive_text(directive: dict[str, Any]) -> str:
        tool = str(directive.get("tool") or "").strip() or "context_read"
        directive_args = directive.get("args")
        path = ""
        if isinstance(directive_args, dict):
            path = _normalize_path(str(directive_args.get("path") or ""))
        reason = str(directive.get("reason") or "").strip()
        prefix = (
            "NEXT REQUIRED TOOL"
            if str(directive.get("enforcement") or "suggested") == "lifecycle_required"
            else "NEXT SUGGESTED TOOL"
        )
        if tool == "context_read" and path:
            message = f"{prefix}: context_read path={path}"
        else:
            message = f"{prefix}: {tool}"
        if reason:
            message = f"{message}\nReason: {reason}"
        return message

    def _record_context_read_repeat(self, args: dict[str, Any], result: Any) -> None:
        if not _result_succeeded(result):
            return
        key = self._context_read_key(args, result)
        if not key:
            return
        count = self._context_read_counts.get(key, 0) + 1
        self._context_read_counts[key] = count
        if count >= 5:
            path, line_range = self._context_read_path_and_range(args, result)
            self._pending_tool_result_replacement = json.dumps(
                {
                    "ok": True,
                    "fused": True,
                    "path": path,
                    "range": line_range,
                    "content": "",
                    "context_meta": {
                        "read_key": key,
                        "source": "fuse_advisory",
                        "working_tree_alignment": "not_applicable",
                    },
                    "advisory": [
                        "This exact range was already returned multiple times.",
                        "Do not call context_read for the same range again.",
                        "Next useful action: patch the accepted target, run context_search with a missing question, or read the current workspace file once.",
                    ],
                },
                ensure_ascii=False,
            )

    @staticmethod
    def _context_read_key(args: dict[str, Any], result: Any) -> str:
        parsed = _result_dict(result)
        meta = parsed.get("context_meta") if isinstance(parsed, dict) else None
        if isinstance(meta, dict):
            read_key = str(meta.get("read_key") or "").strip()
            if read_key:
                return read_key
        path, line_range = ConstraintKeeperCoordinator._context_read_path_and_range(
            args, result
        )
        if not path:
            return ""
        return f"{path}:{line_range[0]}-{line_range[1]}"

    @staticmethod
    def _context_read_path_and_range(
        args: dict[str, Any], result: Any
    ) -> tuple[str, list[int]]:
        parsed = _result_dict(result)
        path = _normalize_path(str(parsed.get("path") or args.get("path") or ""))
        start = _positive_int(args.get("start_line")) or 1
        end = _positive_int(args.get("end_line"))
        if end is None:
            lines = parsed.get("lines") if isinstance(parsed, dict) else None
            if isinstance(lines, list) and len(lines) >= 2:
                start = _positive_int(lines[0]) or start
                end = _positive_int(lines[1])
        if end is None:
            total = parsed.get("total_lines") if isinstance(parsed, dict) else None
            end = _positive_int(total) or start
        return path, [start, end]

    def _failed_context_read_directive_text(self, directive: dict[str, Any]) -> str:
        path = self._next_tool_path_hint(directive)
        if path:
            return (
                "NEXT REQUIRED TOOL fallback: context_read failed for the hinted target. "
                f"Use read_file path={path} to ground the same file, or rerun context_search "
                "if the path is still unavailable."
            )
        return (
            "NEXT REQUIRED TOOL fallback: context_read failed for the hinted target. "
            "Use read_file on the same target path, or rerun context_search if the path is unavailable."
        )

    def _next_tool_path_hint(self, directive: dict[str, Any]) -> str:
        primary = self._primary_diff_target()
        if primary and primary != "<accepted-edit-target>":
            return primary
        directive_args = directive.get("args")
        if not isinstance(directive_args, dict):
            return ""
        return _normalize_path(str(directive_args.get("path") or ""))

    def _record_probe_budget_event(self, tool_name: str, args: dict[str, Any]) -> None:
        if not self._active_probe_budget_directive:
            return
        if tool_name == "search_files":
            self._probe_budget_counts["search_files"] += 1
        elif tool_name == "read_file":
            path = str(args.get("path") or "")
            if path and not path.endswith(".md"):
                self._probe_budget_counts["read_file"] += 1
        elif tool_name == "execute_code":
            self._probe_budget_counts["terminal_or_execute_code"] += 1
        elif tool_name == "terminal":
            command = str(args.get("command") or args.get("cmd") or "")
            if self._is_full_diff_stdout_command(command):
                self._full_diff_stdout_count += 1
            if not self._is_terminal_validation_or_bookkeeping_command(command):
                self._probe_budget_counts["terminal_or_execute_code"] += 1

    def _record_successful_terminal_probe(
        self,
        tool_name: str,
        args: dict[str, Any],
        result: Any,
    ) -> None:
        if tool_name != "terminal":
            return
        command = str(args.get("command") or args.get("cmd") or "").strip()
        if not command or self._is_terminal_validation_or_bookkeeping_command(command):
            return
        parsed = _result_dict(result)
        if parsed:
            exit_code = parsed.get("exit_code")
            if exit_code not in (0, None):
                self._last_successful_terminal_probe = {}
                return
            if parsed.get("error"):
                self._last_successful_terminal_probe = {}
                return
            output = str(parsed.get("output") or parsed.get("stdout") or "")
        else:
            output = str(result or "")
            if not output.strip():
                return
        key = self._terminal_probe_repeat_key(command, output)
        previous = self._last_successful_terminal_probe
        count = int(previous.get("count") or 0) + 1 if previous.get("key") == key else 1
        self._last_successful_terminal_probe = {
            "key": key,
            "command": command,
            "output_hash": hash_text(output),
            "count": count,
        }

    def _record_empty_process_list_probe(
        self,
        tool_name: str,
        args: dict[str, Any],
        result: Any,
    ) -> None:
        if not self._is_process_list_tool(tool_name, args):
            return
        if self._process_list_result_is_empty(result):
            self._empty_process_list_count += 1
        else:
            self._empty_process_list_count = 0

    def _repeated_empty_process_list_block_message(
        self,
        tool_name: str,
        args: dict[str, Any],
    ) -> str | None:
        if not self._is_process_list_tool(tool_name, args):
            return None
        if self._empty_process_list_count < 3:
            return None
        self._append_policy_event(
            action="blocked",
            reason="process tool attempted repeated empty process list polling",
            category="repeated_empty_process_list",
        )
        return "\n".join(
            [
                "FormSy blocked a repeated empty process list probe.",
                "Repeated empty process list polling does not add repair evidence.",
                (
                    "Stop polling background processes; run focused validation, "
                    "review the current diff, or call formsy_verify_completion "
                    "after a real patch/validation state change."
                ),
            ]
        )

    @staticmethod
    def _is_process_list_tool(tool_name: str, args: dict[str, Any]) -> bool:
        if tool_name != "process":
            return False
        action = str(
            args.get("action")
            or args.get("operation")
            or args.get("cmd")
            or args.get("command")
            or ""
        ).strip().lower()
        return action in {"", "list", "ls", "status"}

    @staticmethod
    def _process_list_result_is_empty(result: Any) -> bool:
        parsed = _result_dict(result)
        if isinstance(parsed.get("processes"), list):
            return len(parsed.get("processes") or []) == 0
        if isinstance(parsed.get("items"), list):
            return len(parsed.get("items") or []) == 0
        if isinstance(parsed.get("data"), list):
            return len(parsed.get("data") or []) == 0
        if isinstance(result, list):
            return len(result) == 0
        text = str(result or "").strip()
        return text in {"", "[]", "{}"}

    @staticmethod
    def _terminal_probe_repeat_key(command: str, output: str) -> str:
        normalized_command = " ".join(str(command or "").split())
        return json.dumps(
            {
                "command": normalized_command,
                "output_hash": hash_text(str(output or "")),
            },
            sort_keys=True,
        )

    def _repeated_terminal_probe_block_message(
        self,
        tool_name: str,
        args: dict[str, Any],
    ) -> str | None:
        if tool_name != "terminal":
            return None
        command = str(args.get("command") or args.get("cmd") or "").strip()
        if not command or self._is_terminal_validation_or_bookkeeping_command(command):
            return None
        previous = self._last_successful_terminal_probe
        if previous.get("command") != command or int(previous.get("count") or 0) < 2:
            return None
        self._append_policy_event(
            action="blocked",
            reason=f"terminal attempted repeated identical probe: {command}",
            category="repeated_identical_terminal_probe",
        )
        return "\n".join(
            [
                "FormSy blocked a repeated identical terminal probe.",
                "Repeated identical terminal probe detected.",
                f"Command: {command}",
                "Do not run the same probe again.",
                (
                    "Summarize the observed invariant and switch strategy: patch the "
                    "accepted target, run a different targeted read, or ask Completion "
                    "Verifier only after a real patch/validation state change."
                ),
            ]
        )

    def _probe_budget_block_message(
        self, tool_name: str, args: dict[str, Any]
    ) -> str | None:
        directive = self._active_probe_budget_directive
        if not directive:
            return None
        if tool_name == "terminal":
            command = str(args.get("command") or args.get("cmd") or "")
            if (
                self._is_full_diff_stdout_command(command)
                and self._full_diff_stdout_count >= 1
            ):
                return self._compact_diff_guidance_text()
        return None

    def _candidate_test_write_block_message(
        self,
        tool_name: str,
        args: dict[str, Any],
    ) -> str | None:
        if not self._latest_candidate_test_paths:
            return None
        paths = self._candidate_test_write_paths_from_tool(tool_name, args)
        if tool_name == "terminal":
            paths.extend(self._terminal_candidate_test_write_mentions(args))
        if not paths:
            return None
        path = ""
        for candidate_path in paths:
            if not any(
                _paths_equivalent(candidate_path, candidate)
                for candidate in self._latest_candidate_test_paths
            ):
                continue
            if any(
                _paths_equivalent(candidate_path, target)
                for target in self._latest_accepted_targets
            ):
                continue
            if any(
                _paths_equivalent(candidate_path, collateral)
                for collateral in self._latest_validation_collateral_paths
            ):
                continue
            path = candidate_path
            break
        if not path:
            return None
        accepted = ", ".join(self._latest_accepted_targets) or "<none>"
        self._append_policy_event(
            action="blocked",
            reason=(
                f"{tool_name} attempted to reconstruct candidate test outside "
                f"accepted targets: {path}"
            ),
            category="candidate_test_write_outside_accepted_targets",
        )
        return "\n".join(
            [
                "FormSy blocked writing a candidate test outside accepted targets.",
                "Candidate tests are validation obligations, not edit permission.",
                f"Candidate test path: {path}",
                f"Accepted targets: {accepted}",
                (
                    "Allowed validation collateral: "
                    + (
                        ", ".join(self._latest_validation_collateral_paths)
                        or "<none>"
                    )
                ),
                (
                    "Do not reconstruct missing candidate tests from compiled context, "
                    "memory, bytecode caches, git history, or copied snippets unless "
                    "FormSy explicitly lists that test path as an accepted edit target."
                ),
            ]
        )

    @staticmethod
    def _candidate_test_write_paths_from_tool(
        tool_name: str,
        args: dict[str, Any],
    ) -> list[str]:
        if tool_name == "write_file":
            path = _repo_relative_source_path(
                str(
                    args.get("path")
                    or args.get("file_path")
                    or args.get("filename")
                    or ""
                )
            )
            return [path] if path else []
        if tool_name != "terminal":
            return []
        command = str(args.get("command") or args.get("cmd") or "")
        if not command:
            return []
        paths: list[str] = []
        patterns = (
            r"(?:^|[\s;&|])(?:cat|printf|echo)?[^\n;&|]*?>+\s*['\"]?"
            r"((?:\.?/)?(?:lib|src|test|tests|plugins|docs)/[^'\"\s<>]+)",
            r"(?:^|[\s;&|])tee(?:\s+-a)?\s+['\"]?"
            r"((?:\.?/)?(?:lib|src|test|tests|plugins|docs)/[^'\"\s<>]+)",
            r"\bopen\(\s*['\"]([^'\"]+)['\"]\s*,\s*['\"](?:w|a|x)",
            r"\bPath\(\s*['\"]([^'\"]+)['\"]\s*\)\.write_text\(",
        )
        for pattern in patterns:
            for match in re.finditer(pattern, command):
                path = _repo_relative_source_path(match.group(1))
                if path and path not in paths:
                    paths.append(path)
        return paths

    def _terminal_candidate_test_write_mentions(
        self, args: dict[str, Any]
    ) -> list[str]:
        command = str(args.get("command") or args.get("cmd") or "")
        if not command or not self._terminal_command_has_write_intent(command):
            return []
        paths: list[str] = []
        normalized_command = command.replace("\\", "/")
        for candidate in self._latest_candidate_test_paths:
            candidate_path = _repo_relative_source_path(candidate)
            if not candidate_path:
                continue
            candidate_dir = str(Path(candidate_path).parent).replace("\\", "/")
            candidate_name = Path(candidate_path).name
            if candidate_path in normalized_command or (
                candidate_dir in normalized_command
                and candidate_name in normalized_command
            ):
                paths.append(candidate_path)
        return paths

    @staticmethod
    def _terminal_command_has_write_intent(command: str) -> bool:
        text = str(command or "")
        if not text.strip():
            return False
        return bool(
            re.search(r"(?:^|[\s;&|])(?:cat|printf|echo)[^\n;&|]*?>+", text)
            or re.search(r"(?:^|[\s;&|])tee(?:\s+-a)?\s+", text)
            or re.search(
                r"\bopen\s*\([^)]*(?:,\s*|mode\s*=\s*)['\"][^'\"]*[wax+][^'\"]*['\"]",
                text,
            )
            or re.search(r"\.\s*(write|write_text|write_bytes|writelines)\s*\(", text)
        )

    def _execute_code_read_write_bridge_block_message(
        self,
        tool_name: str,
        args: dict[str, Any],
    ) -> str | None:
        if tool_name != "execute_code":
            return None
        if not self._execute_code_uses_hermes_read_write_bridge(
            str(args.get("code") or "")
        ):
            return None
        self._append_policy_event(
            action="blocked",
            reason="execute_code attempted to bridge hermes_tools.read_file content into hermes_tools.write_file",
            category="execute_code_read_file_write_file_bridge",
        )
        return _EXECUTE_CODE_READ_WRITE_BRIDGE_BLOCK

    def _execute_code_direct_source_write_block_message(
        self,
        tool_name: str,
        args: dict[str, Any],
    ) -> str | None:
        if tool_name != "execute_code":
            return None
        if not self._execute_code_uses_direct_file_write(str(args.get("code") or "")):
            return None
        self._append_policy_event(
            action="blocked",
            reason="execute_code attempted direct filesystem writes; source edits must use patch/write_file",
            category="execute_code_direct_source_write",
        )
        return _EXECUTE_CODE_DIRECT_SOURCE_WRITE_BLOCK

    @staticmethod
    def _execute_code_uses_hermes_read_write_bridge(code: str) -> bool:
        text = str(code or "")
        if "hermes_tools" not in text:
            return False
        uses_read_file = bool(
            re.search(r"\bhermes_tools\.read_file\s*\(", text)
            or re.search(r"\bread_file\s*\(", text)
        )
        uses_write_file = bool(
            re.search(r"\bhermes_tools\.write_file\s*\(", text)
            or re.search(r"\bwrite_file\s*\(", text)
        )
        return uses_read_file and uses_write_file

    @staticmethod
    def _execute_code_uses_direct_file_write(code: str) -> bool:
        text = str(code or "")
        if not text.strip():
            return False
        has_write_call = bool(
            re.search(
                r"\bopen\s*\([^)]*(?:,\s*|mode\s*=\s*)['\"][^'\"]*[wax+][^'\"]*['\"]",
                text,
            )
            or re.search(r"\.\s*(write_text|write_bytes|writelines)\s*\(", text)
        )
        if not has_write_call:
            return False
        for literal in re.findall(r"['\"]([^'\"]+)['\"]", text):
            if _looks_like_repo_source_path_literal(literal):
                return True
        return False

    def _reset_probe_budget_counts(self) -> None:
        self._probe_budget_counts = {
            "search_files": 0,
            "read_file": 0,
            "terminal_or_execute_code": 0,
        }
        self._empty_process_list_count = 0
        self._full_diff_stdout_count = 0

    @staticmethod
    def _is_terminal_validation_or_bookkeeping_command(command: str) -> bool:
        normalized = " ".join(str(command or "").lower().split())
        if not normalized:
            return False
        effective = ConstraintKeeperCoordinator._effective_policy_command(normalized)
        if is_validation_command(normalized) or "complete_task" in normalized:
            return True
        if ConstraintKeeperCoordinator._is_compact_diff_review_command(effective):
            return True
        if ConstraintKeeperCoordinator._is_patch_file_bookkeeping_command(effective):
            return True
        return "py_compile" in normalized or "compileall" in normalized

    @staticmethod
    def _effective_policy_command(command: str) -> str:
        normalized = " ".join(str(command or "").lower().split())
        while True:
            match = re.match(r"^(?:cd|pushd)\s+[^;&|]+&&\s*(.+)$", normalized)
            if not match:
                return normalized
            normalized = match.group(1).strip()

    @staticmethod
    def _is_compact_diff_review_command(command: str) -> bool:
        normalized = ConstraintKeeperCoordinator._effective_policy_command(command)
        if not re.search(r"\bgit\s+diff\b", normalized):
            return False
        if re.search(
            r"\bgit\s+diff\b.*(--stat|--shortstat|--numstat|--name-only|--name-status|--check)",
            normalized,
        ):
            return True
        if " -- " in normalized:
            return True
        if ">" in normalized:
            return True
        return bool(re.search(r"\|\s*(head|tail|wc|sed)\b", normalized))

    @staticmethod
    def _is_full_diff_stdout_command(command: str) -> bool:
        normalized = ConstraintKeeperCoordinator._effective_policy_command(command)
        if not re.search(r"\bgit\s+diff\b", normalized):
            return False
        return not ConstraintKeeperCoordinator._is_compact_diff_review_command(
            normalized
        )

    @staticmethod
    def _is_patch_file_bookkeeping_command(command: str) -> bool:
        normalized = ConstraintKeeperCoordinator._effective_policy_command(command)
        if re.fullmatch(r"wc\s+-l\s+patch\.txt", normalized):
            return True
        if re.fullmatch(r"(head|tail)(?:\s+-(?:n\s+)?\d+)?\s+patch\.txt", normalized):
            return True
        if re.fullmatch(r"ls\s+(-l\s+)?patch\.txt", normalized):
            return True
        if re.fullmatch(r"test\s+-s\s+patch\.txt", normalized):
            return True
        return bool(
            re.fullmatch(
                r"grep\s+(?:-[a-z]+\s+)*['\"]?[^'\"]*(?:diff --git|\^\[\+-\]\{3\})[^'\"]*['\"]?\s+patch\.txt(?:\s*\|\s*sort\s+-u)?",
                normalized,
            )
        )

    def _compact_diff_guidance_text(self) -> str:
        target = self._primary_diff_target()
        return (
            "FormSy compact diff policy: avoid repeated full git diff output. "
            f"Use git diff --stat, git diff -- {target}, or git diff --check. "
            "If preparing final output, redirect the full diff to patch.txt and submit once."
        )

    def _primary_diff_target(self) -> str:
        directive = self._active_probe_budget_directive or {}
        for key in ("accepted_edit_targets", "likely_edit_files", "target_candidates"):
            values = directive.get(key)
            if isinstance(values, list):
                for value in values:
                    path = _normalize_path(str(value or ""))
                    if path:
                        return path
        return "<accepted-edit-target>"

    def _probe_budget_limit(self, key: str, default: int) -> int:
        directive = self._active_probe_budget_directive or {}
        budget = directive.get("probe_budget")
        if not isinstance(budget, dict):
            return default
        value = budget.get(key)
        return value if isinstance(value, int) and value > 0 else default

    def _capture_server_directive(self, response: Any) -> None:
        if not isinstance(response, dict):
            return
        protocol = response.get("protocol") or response.get("protocol_bundle")
        protocol_dict = protocol if isinstance(protocol, dict) else {}
        decision = _protocol_scalar(
            response.get("gate_decision")
            or response.get("decision")
            or protocol_dict.get("gate_decision")
            or protocol_dict.get("decision")
            or protocol_dict.get("state")
        )
        state = _protocol_scalar(protocol_dict.get("state"))
        if decision not in {"NEED_CONTEXT", "REQUERY_CODE_GRAPH"} and state not in {
            "NEED_CONTEXT",
            "REQUERY_CODE_GRAPH",
        }:
            return
        actions = _string_list(protocol_dict.get("required_next_actions"))
        queries = _string_list(protocol_dict.get("suggested_queries"))
        if not any("context_search" in action for action in actions):
            return
        self._active_context_directive = {
            "decision": decision or state,
            "summary": str(protocol_dict.get("summary") or "").strip(),
            "required_next_actions": actions,
            "suggested_queries": queries,
        }

    def _context_directive_block_message(
        self, tool_name: str, args: dict[str, Any]
    ) -> str | None:
        directive = self._active_context_directive
        if not directive or not self._is_broad_source_exploration_tool(tool_name, args):
            return None
        summary = str(
            directive.get("summary") or "Server requested fresh context guidance."
        ).strip()
        actions = _string_list(directive.get("required_next_actions"))
        queries = _string_list(directive.get("suggested_queries"))
        lines = [
            "FormSy Constraint Keeper requires context refresh before more broad source exploration.",
            f"Summary: {summary}",
        ]
        if actions:
            lines.append("Required next action:")
            lines.extend(f"- {action}" for action in actions[:2])
        if queries:
            lines.append("Suggested context_search query:")
            lines.append(f"- {queries[0]}")
        return "\n".join(lines)

    @staticmethod
    def _is_broad_source_exploration_tool(tool_name: str, args: dict[str, Any]) -> bool:
        if tool_name == "search_files":
            return True
        if tool_name == "read_file":
            path = str(args.get("path") or "")
            return bool(path and not path.endswith(".md"))
        if tool_name != "terminal":
            return False
        command = " ".join(
            str(args.get("command") or args.get("cmd") or "").lower().split()
        )
        return bool(re.search(r"\b(rg|grep|find|ack|ag|cat|sed)\b", command))

    @staticmethod
    def _server_event(
        event: dict[str, Any], *, identity: FormSyIdentity
    ) -> dict[str, Any]:
        return {
            **event,
            "task_id": identity.task_id,
            "run_id": identity.run_id,
        }

    def _maybe_recover_from_failure(self, event: dict[str, Any]) -> None:
        if event.get("event_kind") != "failure":
            return
        payload = event.get("payload")
        if not isinstance(payload, dict):
            return
        command = str(payload.get("command") or "")
        if not is_validation_command(command):
            return
        fingerprint = str(payload.get("fingerprint") or "")
        if not fingerprint:
            return
        self._failure_counts[fingerprint] = self._failure_counts.get(fingerprint, 0) + 1
        if (
            self._failure_counts[fingerprint] < 2
            or fingerprint in self._recovered_fingerprints
        ):
            return
        self._recovered_fingerprints.add(fingerprint)
        reason = self._recovery_reason(payload)
        self.recover(reason=reason)

    def _set_protocol_text(
        self, protocol_text: str, *, recovery_open: bool | None = None
    ) -> None:
        if protocol_text and protocol_text != self.latest_protocol_text:
            self.latest_protocol_text = protocol_text
        if recovery_open is not None:
            self.recovery_open = recovery_open
        elif protocol_text:
            lowered = protocol_text.lower()
            if "recovery_open" in lowered or "recovery is still open" in lowered:
                self.recovery_open = True

    def _mark_task_closed(self) -> None:
        self._task_closed = True
        self._grounding_state = _GroundingState.CLOSED
        self._pending_guidance_text = ""
        self._active_context_directive = None
        self._active_probe_budget_directive = None
        self._active_next_tool_directive = None
        self._next_tool_deviation_count = 0
        self._next_tool_visible_delivery_count = 0
        self._next_tool_failed = False
        self._context_read_counts = {}
        self._pending_tool_result_replacement = ""
        # Hermes runs post_tool_call before transform_tool_result. Keep pending
        # completion projection available until transform_tool_result injects it.
        self.latest_protocol_text = ""
        self._last_injected_protocol_text = ""
        self.recovery_open = False

    def _mark_completion_accepted_for_revalidation(self) -> None:
        self._task_closed = True
        self._completion_verified = True
        self._grounding_state = _GroundingState.CLOSED
        self._completion_revalidation_pending = True
        self._active_context_directive = None
        self._active_probe_budget_directive = None
        self._active_next_tool_directive = None
        self._next_tool_deviation_count = 0
        self._next_tool_visible_delivery_count = 0
        self._next_tool_failed = False
        self.recovery_open = False

    @staticmethod
    def _assistant_response_claims_completion_accept(text: str) -> bool:
        lowered = str(text or "").lower()
        mentions_verifier = (
            "completion verifier" in lowered
            or "finish gate" in lowered
            or "formsy_verify_completion" in lowered
        )
        return mentions_verifier and "accept_done" in lowered

    @staticmethod
    def _assistant_response_claims_task_completion(text: str) -> bool:
        lowered = " ".join(str(text or "").lower().split())
        if not lowered:
            return False
        completion_markers = (
            "done",
            "implemented",
            "fixed",
            "completed",
            "ready",
            "verified",
            "tests pass",
            "test passed",
            "all tests pass",
        )
        return any(marker in lowered for marker in completion_markers)

    def _has_unverified_current_diff(self) -> bool:
        if not self.latest_diff_hash:
            self._append_fresh_diff_if_changed()
        return bool(self.latest_diff_hash and not self._completion_verified)

    @staticmethod
    def _recovery_reason(payload: dict[str, Any]) -> str:
        output = str(payload.get("truncated_output") or "")
        meaningful = ""
        for line in reversed(output.splitlines()):
            if line.strip():
                meaningful = line.strip()
                break
        command = str(payload.get("command") or "")
        if meaningful:
            return f"Repeated validation failure for `{command}`: {meaningful}"
        return f"Repeated validation failure for `{command}`"

    @staticmethod
    def _protocol_text(response: Any) -> str:
        if not isinstance(response, dict):
            return ""
        for key in ("protocol_text", "agent_text"):
            value = response.get(key)
            if isinstance(value, str):
                return value
        protocol = response.get("protocol") or response.get("protocol_bundle")
        if isinstance(protocol, dict):
            for key in ("protocol_text", "agent_text", "text"):
                value = protocol.get(key)
                if isinstance(value, str):
                    return value
            return ConstraintKeeperCoordinator._render_protocol_bundle(protocol)
        return ""

    @staticmethod
    def _render_protocol_bundle(protocol: dict[str, Any]) -> str:
        state = _protocol_scalar(protocol.get("state"))
        decision = _protocol_scalar(
            protocol.get("gate_decision") or protocol.get("decision")
        )
        summary = str(protocol.get("summary") or "").strip()
        lines = ["## FormSy Constraint Protocol"]
        if state:
            lines.append(f"- State: {state}")
        if decision:
            lines.append(f"- Decision: {decision}")
        if summary:
            lines.append(f"- Summary: {summary}")
        blocking = _string_list(protocol.get("blocking_conditions"))
        if blocking:
            lines.append("- Blocking conditions:")
            lines.extend(f"  - {item}" for item in blocking)
        actions = _string_list(protocol.get("required_next_actions"))
        if actions:
            lines.append("- Required next actions:")
            lines.extend(f"  - {item}" for item in actions)
        queries = _string_list(protocol.get("suggested_queries"))
        if queries:
            lines.append("- Suggested context_search queries:")
            lines.extend(f"  - {item}" for item in queries)
        return "\n".join(lines) if len(lines) > 1 else ""

    @classmethod
    def _accepted_completion_projection_text(cls, result: Any) -> str:
        audit_text = cls._completion_audit_projection_text(result)
        if audit_text:
            return audit_text
        decision = cls._completion_decision(result) or "MISSING_VERIFIER_DECISION"
        summary = cls._completion_summary(result)
        lines = [
            "## FormSy Completion Verifier",
            f"- Decision: {decision}",
        ]
        if summary:
            lines.append(f"- Summary: {summary}")
        return "\n".join(lines)

    @staticmethod
    def _completion_audit_projection_text(result: Any) -> str:
        if not isinstance(result, dict):
            return ""
        audit = result.get("completion_audit")
        if not isinstance(audit, dict):
            return ""
        evidence = audit.get("evidence")
        if not isinstance(evidence, dict):
            evidence = {}
        lines = ["## FormSy Completion Verifier"]
        audit_status = _protocol_scalar(audit.get("audit_status"))
        gate_decision = _protocol_scalar(audit.get("gate_decision"))
        latest_diff_hash = _protocol_scalar(evidence.get("latest_diff_hash"))
        patch_check_id = _protocol_scalar(evidence.get("patch_check_event_id"))
        validation_id = _protocol_scalar(evidence.get("validation_event_id"))
        local_guard = _protocol_scalar(evidence.get("local_patch_semantic_guard"))
        preferred_next = _protocol_scalar(evidence.get("preferred_next_step"))
        repeat_count = _protocol_scalar(evidence.get("repeat_count"))
        failed_validation_commands = _string_list(
            evidence.get("failed_validation_commands")
        )
        outside_targets = _string_list(evidence.get("outside_accepted_targets"))
        accepted_targets = _string_list(evidence.get("accepted_targets"))
        memory_allowed = audit.get("memory_write_allowed")
        memory_quality = _protocol_scalar(audit.get("memory_write_quality"))
        block_reason = _protocol_scalar(audit.get("memory_write_block_reason"))
        if audit_status:
            lines.append(f"- Audit status: {audit_status}")
        if gate_decision:
            lines.append(f"- Gate decision: {gate_decision}")
        if latest_diff_hash:
            lines.append(f"- Latest diff: {latest_diff_hash}")
        if patch_check_id:
            lines.append(f"- Patch check: {patch_check_id}")
        if validation_id:
            lines.append(f"- Validation: {validation_id}")
        if local_guard:
            lines.append(f"- Local guard: {local_guard}")
        if preferred_next:
            lines.append(f"- Preferred next step: {preferred_next}")
        if repeat_count:
            lines.append(f"- Repeat count: {repeat_count}")
        if failed_validation_commands:
            lines.append("- Failed validation commands:")
            lines.extend(f"  - `{command}`" for command in failed_validation_commands)
        if outside_targets:
            lines.append(f"- Outside accepted targets: {', '.join(outside_targets)}")
        if accepted_targets:
            lines.append(f"- Accepted targets: {', '.join(accepted_targets)}")
        if isinstance(memory_allowed, bool):
            lines.append(f"- Memory write allowed: {str(memory_allowed).lower()}")
        if memory_quality:
            lines.append(f"- Memory write quality: {memory_quality}")
        if block_reason:
            lines.append(f"- Memory write block reason: {block_reason}")
        return "\n".join(lines) if len(lines) > 1 else ""

    @staticmethod
    def _completion_unavailable_projection_text(exc: Exception) -> str:
        return "\n".join(
            [
                "## FormSy Completion Verifier",
                "- Decision: completion_verification_unavailable",
                "- Policy: final submit allowed by adapter policy; do not write successful implementation memory.",
                f"- Reason: verify_completion unavailable: {exc.__class__.__name__}: {exc}",
            ]
        )

    @staticmethod
    def _completion_decision(result: Any) -> str:
        if not isinstance(result, dict):
            return ""
        for key in ("gate_decision", "decision"):
            value = result.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        protocol = result.get("protocol") or result.get("protocol_bundle")
        if isinstance(protocol, dict):
            for key in ("gate_decision", "decision", "state"):
                value = protocol.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
        return ""

    @staticmethod
    def _completion_summary(result: Any) -> str:
        if not isinstance(result, dict):
            return ""
        protocol = result.get("protocol") or result.get("protocol_bundle")
        if isinstance(protocol, dict):
            summary = str(protocol.get("summary") or "").strip()
            if summary:
                return summary
        for key in ("summary", "message", "reason"):
            value = result.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return ""

    @staticmethod
    def _is_accepted(result: Any) -> bool:
        if not isinstance(result, dict):
            return False
        if result.get("accepted") is True or result.get("allowed") is True:
            return True
        decision = str(
            result.get("gate_decision") or result.get("decision") or ""
        ).lower()
        return decision in {
            "accepted",
            "accept",
            "allowed",
            "allow",
            "pass",
            "passed",
            "accept_done",
            "accept_done_with_override",
        }

    @classmethod
    def _has_deterministic_hard_violation(cls, result: Any) -> bool:
        if not isinstance(result, dict):
            return False
        risk = result.get("risk")
        if isinstance(risk, dict):
            try:
                confidence = float(risk.get("confidence") or 0)
            except (TypeError, ValueError):
                confidence = 0.0
            if risk.get("hard_violation") is True and confidence >= 0.95:
                return True
        if result.get("hard_violation") is True:
            return True
        hard_violations = result.get("hard_violations")
        if isinstance(hard_violations, list) and hard_violations:
            return True
        for text in cls._result_text_fragments(result):
            lowered = text.lower()
            if any(
                marker in lowered
                for marker in (
                    "explicit forbidden path",
                    "forbidden path violation",
                    "forbidden file",
                    "hard patch violation",
                    "patch_check hard violation",
                    "restricted path violation",
                    "destructive command",
                    "rm -rf",
                    "git reset --hard",
                    "git clean -fd",
                    "git clean -xdf",
                    "writes .git",
                    "write .git",
                )
            ):
                return True
        return False

    @classmethod
    def _legacy_rejection_category(cls, result: Any) -> str:
        text = " ".join(cls._result_text_fragments(result)).lower()
        if any(
            marker in text for marker in ("no diff", "missing diff", "diff evidence")
        ):
            return "missing_diff_evidence"
        if any(
            marker in text
            for marker in ("no passing test", "missing test", "test_result")
        ):
            return "missing_validation_evidence"
        if any(marker in text for marker in ("patch check", "patch_check")):
            return "missing_patch_check"
        if any(marker in text for marker in ("contract", "contracts")):
            return "missing_contracts"
        return "unclassified_rejection"

    @classmethod
    def _result_text_fragments(cls, result: Any) -> list[str]:
        if not isinstance(result, dict):
            return []
        fragments: list[str] = []
        for key in ("message", "reason", "gate_decision", "decision"):
            value = result.get(key)
            if isinstance(value, str) and value.strip():
                fragments.append(value.strip())
        for key in ("blocking_conditions", "hard_violations"):
            fragments.extend(_string_list(result.get(key)))
        protocol = result.get("protocol") or result.get("protocol_bundle")
        if isinstance(protocol, dict):
            for key in ("summary", "message", "reason", "gate_decision", "decision"):
                value = protocol.get(key)
                if isinstance(value, str) and value.strip():
                    fragments.append(value.strip())
            fragments.extend(_string_list(protocol.get("blocking_conditions")))
            fragments.extend(_string_list(protocol.get("required_next_actions")))
        risk = result.get("risk")
        if isinstance(risk, dict):
            for key in ("reason", "decision", "severity"):
                value = risk.get(key)
                if isinstance(value, str) and value.strip():
                    fragments.append(value.strip())
            fragments.extend(_string_list(risk.get("missing_evidence")))
            fragments.extend(_string_list(risk.get("recommended_next_actions")))
        return fragments

    @classmethod
    def _rejection_message(cls, result: Any) -> str:
        if not isinstance(result, dict):
            return "FormSy Constraint Keeper rejected final submit."
        lines: list[str] = []
        for key in ("message", "reason", "gate_decision", "decision"):
            value = result.get(key)
            if isinstance(value, str) and value.strip():
                lines.append(value.strip())
                break
        protocol = result.get("protocol")
        if isinstance(protocol, dict):
            summary = str(protocol.get("summary") or "").strip()
            if summary and summary not in lines:
                lines.append(summary)
            blocking = _string_list(protocol.get("blocking_conditions"))
            if blocking:
                lines.append("Blocking conditions:")
                lines.extend(f"- {item}" for item in blocking)
            actions = _string_list(protocol.get("required_next_actions"))
            if actions:
                lines.append("Required next actions:")
                lines.extend(f"- {item}" for item in actions)
        if cls._is_semantic_recovery_case(result):
            lines.append(cls._semantic_recovery_guidance_text())
        return (
            "\n".join(lines)
            if lines
            else "FormSy Constraint Keeper rejected final submit."
        )

    @classmethod
    def _is_semantic_recovery_case(cls, result: Any) -> bool:
        text = " ".join(cls._result_text_fragments(result)).lower()
        return (
            "semanticcontract violation" in text
            or "semantic contract violation" in text
        )

    @staticmethod
    def _semantic_recovery_guidance_text() -> str:
        return (
            "FormSy semantic recovery guidance\n"
            "Patch current diff in place; do not reset or recreate the target file.\n"
            "Avoid: git checkout/reset of accepted target, rebuilding the whole patch, helper patch scripts.\n"
            "Do next: inspect the listed semantic violations, patch only the accepted target, "
            "rerun compact validation."
        )

    @staticmethod
    def _run_async(value: Any) -> Any:
        if not inspect.isawaitable(value):
            return value
        try:
            from model_tools import _run_async as run_from_model_tools

            return run_from_model_tools(value)
        except ImportError:
            return asyncio.run(value)


def _result_total_count(result: Any) -> int | None:
    parsed = _result_dict(result)
    if isinstance(parsed, dict):
        value = parsed.get("total_count")
        return value if isinstance(value, int) else None
    return None


def _result_dict(result: Any) -> dict[str, Any]:
    if isinstance(result, dict):
        return result
    if not isinstance(result, str):
        return {}
    try:
        parsed = json.loads(result)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _result_succeeded(result: Any) -> bool:
    parsed = _result_dict(result)
    if parsed:
        if parsed.get("success") is False:
            return False
        return parsed.get("ok") is not False and not parsed.get("error")
    return bool(str(result or "").strip())


def _result_has_content(result: Any) -> bool:
    parsed = _result_dict(result)
    if parsed:
        if parsed.get("ok") is False:
            return False
        return any(parsed.get(key) for key in ("content", "text", "source", "path"))
    return bool(str(result or "").strip())


def _now_ms() -> int:
    return int(time.time() * 1000)


def _positive_int(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _normalize_path(value: str) -> str:
    text = str(value or "").strip().replace("\\", "/")
    if text.startswith("./"):
        text = text[2:]
    return text.strip("/")


def _repo_relative_source_path(value: str) -> str:
    text = _normalize_path(value)
    if not text:
        return ""
    match = re.search(r"(?:^|/)((?:lib|src|test|tests|plugins|docs)/[^'\"\s]+)", text)
    if match:
        return match.group(1).rstrip(",:;")
    if text.startswith(("/", "Users/")):
        return ""
    return text


def _looks_like_repo_source_path_literal(value: str) -> bool:
    text = _normalize_path(value)
    return bool(
        re.search(r"(?:^|/)(?:lib|src|test|tests|plugins|docs)/[^'\"\s]+", text)
    )


def _command_query_hint(command: str) -> str:
    paths: list[str] = []
    for raw_path in re.findall(
        r"(?<![A-Za-z0-9_./-])((?:lib|src|test|tests|plugins|docs)/[^'\"\s]+)", command
    ):
        path = _repo_relative_source_path(raw_path)
        if path and path not in paths:
            paths.append(path)

    symbols: list[str] = []
    for symbol in re.findall(r"\b[A-Z][A-Za-z0-9_]{2,}\b", command):
        if symbol not in symbols:
            symbols.append(symbol)

    return " ".join([*symbols[:4], *paths[:2]])


def _normalize_command_for_match(command: str) -> str:
    return re.sub(r"\s+", " ", str(command or "").strip())


def _pytest_selector_from_command(command: str) -> str:
    selectors = _pytest_selectors_from_command(command)
    return selectors[0] if selectors else ""


def _pytest_selectors_from_command(command: str) -> list[str]:
    tokens, start_index = _pytest_command_tokens(command)
    if start_index is None:
        return []

    selectors: list[str] = []
    skip_next = False
    for token in tokens[start_index:]:
        if skip_next:
            skip_next = False
            continue
        if token == "--deselect":
            skip_next = True
            continue
        if token.startswith("--deselect="):
            continue
        if token.startswith("-") or "=" in token:
            continue
        if ("::" in token or token.endswith(".py")) and token not in selectors:
            selectors.append(token)
    return selectors


def _pytest_deselected_selectors_from_command(command: str) -> list[str]:
    tokens, start_index = _pytest_command_tokens(command)
    if start_index is None:
        return []

    selectors: list[str] = []
    iterator = iter(tokens[start_index:])
    for token in iterator:
        selector = ""
        if token == "--deselect":
            selector = next(iterator, "")
        elif token.startswith("--deselect="):
            selector = token.split("=", 1)[1]
        if selector and selector not in selectors:
            selectors.append(selector)
    return selectors


def _pytest_command_tokens(command: str) -> tuple[list[str], int | None]:
    try:
        tokens = shlex.split(_normalize_command_for_match(command))
    except ValueError:
        tokens = _normalize_command_for_match(command).split(" ")
    while tokens and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", tokens[0]):
        tokens.pop(0)
    if tokens and tokens[0] == "env":
        tokens.pop(0)
        while tokens and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", tokens[0]):
            tokens.pop(0)

    start_index: int | None = None
    for index, token in enumerate(tokens):
        basename = token.rsplit("/", 1)[-1]
        if basename == "pytest":
            start_index = index + 1
            break
        if (
            ConstraintKeeperCoordinator._is_python_executable_token(token)
            and tokens[index + 1 : index + 3] == ["-m", "pytest"]
        ):
            start_index = index + 3
            break
    return tokens, start_index


def _pytest_selector_covers_failed_node(selector: str, failed_node: str) -> bool:
    selector_path, selector_sep, selector_node = str(selector or "").partition("::")
    failed_path, failed_sep, failed_name = str(failed_node or "").partition("::")
    selector_path = _repo_relative_source_path(selector_path)
    failed_path = _repo_relative_source_path(failed_path)
    if (
        not selector_path
        or not failed_path
        or not _paths_equivalent(selector_path, failed_path)
    ):
        return False
    if not selector_sep:
        return False
    if not failed_sep:
        return False
    return selector_node == failed_name


def _paths_equivalent(left: str, right: str) -> bool:
    lhs = _normalize_path(left)
    rhs = _normalize_path(right)
    if not lhs or not rhs:
        return False
    return lhs == rhs or lhs.endswith(f"/{rhs}") or rhs.endswith(f"/{lhs}")


def _protocol_scalar(value: Any) -> str:
    if hasattr(value, "value"):
        value = value.value
    return str(value or "").strip()


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _json_string_value(value: str) -> str:
    return json.dumps(str(value or ""), ensure_ascii=False)[1:-1]
