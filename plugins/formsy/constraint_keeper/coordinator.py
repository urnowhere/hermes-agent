"""Synchronous Hermes-facing coordinator for FormSy Constraint Keeper."""

from __future__ import annotations

import asyncio
import json
import inspect
import logging
import re
import time
import uuid
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
        self._task_closed = False
        self._active_context_directive: dict[str, Any] | None = None
        self._active_probe_budget_directive: dict[str, Any] | None = None
        self._active_next_tool_directive: dict[str, Any] | None = None
        self._next_tool_deviation_count = 0
        self._next_tool_visible_delivery_count = 0
        self._next_tool_failed = False
        self._context_read_counts: dict[str, int] = {}
        self._pending_tool_result_replacement = ""
        self._probe_budget_counts: dict[str, int] = {
            "search_files": 0,
            "read_file": 0,
            "terminal_or_execute_code": 0,
        }
        self._full_diff_stdout_count = 0
        self._latest_user_task_text = ""
        self._latest_grounding_query_text = ""
        self._latest_diff_payload: dict[str, Any] = {}
        self._skill_name = "formsy-context"
        self._skill_visibility = "unknown"
        self._skill_body_loaded = False

    def on_session_start(self, session_id: str = "", **_: Any) -> None:
        self._session_id = session_id or self._session_id

    def on_user_turn(self, user_message: str = "", session_id: str = "", **_: Any) -> None:
        meta_maintenance_turn = self._is_meta_maintenance_task_text(user_message)
        if (
            isinstance(user_message, str)
            and user_message.strip()
            and not meta_maintenance_turn
        ):
            self._latest_user_task_text = user_message
        self.ensure_identity(
            session_id=session_id,
            user_message=None if meta_maintenance_turn else user_message,
        )
        if self._task_closed:
            self._task_closed = False
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
                workspace_id=workspace_id or (self.identity.workspace_id if self.identity else ""),
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

        self._capture_server_directive(response)
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
        if tool_name == "formsy_verify_completion" and self._is_accepted(_result_dict(result)):
            self._mark_completion_accepted_for_revalidation()
        self._record_probe_budget_event(tool_name, args or {})
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
                if event.get("event_kind") == "failure":
                    event.setdefault("payload", {})["diff_context_hash"] = self.latest_diff_hash
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
        if self.latest_protocol_text and self.latest_protocol_text != self._last_injected_protocol_text:
            self._last_injected_protocol_text = self.latest_protocol_text
            suffix_additions.append(self.latest_protocol_text)
        if self._pending_completion_projection_text:
            suffix_additions.append(self._pending_completion_projection_text)
            self._pending_completion_projection_text = ""
        if prefix_additions or suffix_additions:
            transformed = result
            if prefix_additions:
                transformed = "\n\n---\n\n".join(prefix_additions) + f"\n\n---\n\n{transformed}"
            if suffix_additions:
                transformed = f"{transformed}\n\n" + "\n\n".join(suffix_additions)
            return transformed
        return None

    def pre_llm_call_context(self, *, session_id: str = "", task_id: str = "") -> dict[str, str] | None:
        self.ensure_identity(session_id=session_id, task_id=task_id)
        if self._task_closed:
            self._task_closed = False
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
                    action_card = f"{self._workspace_revalidation_card()}\n\n{action_card}"
                    self._completion_revalidation_pending = False
                context = self._with_formsy_context_skill_capsule(action_card, session_id=session_id)
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
            direct_source_write_message = self._execute_code_direct_source_write_block_message(tool_name, args or {})
            if direct_source_write_message:
                return direct_source_write_message
            read_write_bridge_message = self._execute_code_read_write_bridge_block_message(tool_name, args or {})
            if read_write_bridge_message:
                return read_write_bridge_message
            probe_budget_message = self._probe_budget_block_message(tool_name, args or {})
            if probe_budget_message:
                return probe_budget_message
        if not self.fail_closed_on_submit or not final_submit:
            return None
        try:
            result = self.verify_completion(session_id=session_id, task_id=task_id)
        except Exception as exc:
            self._pending_completion_projection_text = self._completion_unavailable_projection_text(exc)
            self._append_policy_event(
                action="allowed_with_warning",
                reason=f"verify_completion unavailable: {exc.__class__.__name__}: {exc}",
                category="server_unavailable",
            )
            return None
        if self._is_accepted(result):
            projection_text = self._accepted_completion_projection_text(result)
            if projection_text:
                self._pending_completion_projection_text = projection_text
            self._append_policy_event(
                action="allowed",
                reason=self._completion_summary(result) or self._completion_decision(result) or "Completion accepted.",
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

    def verify_completion(self, *, session_id: str = "", task_id: str = "") -> dict[str, Any]:
        identity = self.ensure_identity(session_id=session_id, task_id=task_id)
        self.ensure_task_started(session_id=identity.session_id)
        self.flush_pending()
        diff_payload = self._append_fresh_diff_if_changed()
        self._append_event({
            "event_kind": "done_claim",
            "trust": "agent_claimed",
            "payload": {"claimed_at_ms": _now_ms()},
        })
        self.flush_pending()
        return self._run_async(
            self.client.verify_completion(
                {
                    "task_id": identity.task_id,
                    "run_id": identity.run_id,
                    "completion_bootstrap": self._completion_bootstrap_payload(
                        diff_payload
                    ),
                },
                session_id=identity.session_id,
            )
        )

    def recover(self, *, reason: str = "", session_id: str = "", task_id: str = "") -> dict[str, Any]:
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
            self.client.status(identity.task_id, identity.run_id, session_id=identity.session_id)
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
        diff_text = self.diff_provider() or ""
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
        if post_patch_sources:
            payload["post_patch_sources"] = post_patch_sources
        if source_snapshot_hashes:
            payload["source_snapshot_hashes"] = source_snapshot_hashes
        self._latest_diff_payload = payload
        if diff_hash == self.latest_diff_hash:
            return payload
        self.latest_diff_hash = diff_hash
        self._append_event({
            "event_kind": "diff_observed",
            "trust": "plugin_observed",
            "payload": payload,
        })
        return payload

    def _completion_bootstrap_payload(
        self, diff_payload: dict[str, Any] | None
    ) -> dict[str, Any]:
        payload = diff_payload or self._latest_diff_payload or {}
        instruction = self._latest_user_task_text.strip()
        freshness = "current_run" if instruction else "unknown"
        post_patch_sources = payload.get("post_patch_sources")
        source_snapshot_hashes = payload.get("source_snapshot_hashes")
        return {
            "instruction": instruction,
            "instruction_freshness": freshness,
            "unified_diff": str(payload.get("unified_diff") or ""),
            "changed_files": list(payload.get("changed_files") or []),
            "post_patch_sources": post_patch_sources if isinstance(post_patch_sources, dict) else {},
            "diff_hash": str(payload.get("diff_hash") or ""),
            "source_snapshot_hashes": (
                source_snapshot_hashes
                if isinstance(source_snapshot_hashes, dict)
                else {}
            ),
        }

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
        self.spool.append(task_id=identity.task_id, run_id=identity.run_id, event=enriched)
        return enriched

    def _append_policy_event(self, *, action: str, reason: str, category: str) -> dict[str, Any]:
        return self._append_event({
            "event_kind": "enforcement_decision",
            "trust": "plugin_observed",
            "payload": {
                "policy_mode": "advisory",
                "enforcement_action": action,
                "category": category,
                "reason": reason,
            },
        })

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
        self._probe_budget_counts = {
            "search_files": 0,
            "read_file": 0,
            "terminal_or_execute_code": 0,
        }
        self._full_diff_stdout_count = 0
        if clear_protocol:
            self.latest_protocol_text = ""
            self._last_injected_protocol_text = ""
            self.latest_diff_hash = ""
            self.recovery_open = False

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

    def _observe_skill_uptake(self, tool_name: str, args: dict[str, Any], result: Any) -> None:
        if tool_name != "skill_view":
            return
        requested = str(
            args.get("name")
            or args.get("skill")
            or args.get("skill_name")
            or ""
        ).strip()
        if requested != self._skill_name:
            return
        self._mark_skill_body_loaded(
            visibility="skill_view_loaded",
            result_len=len(str(result or "")),
        )

    def _with_formsy_context_skill_capsule(self, context: str, *, session_id: str = "") -> str:
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
            Path.home() / ".hermes" / "skills" / "software-development" / self._skill_name / "SKILL.md",
            Path.home() / ".hermes" / "skills" / self._skill_name / "SKILL.md",
            Path.cwd() / "skills" / "software-development" / self._skill_name / "SKILL.md",
            Path.cwd() / "skills" / self._skill_name / "SKILL.md",
        ]
        return any(path.exists() for path in candidates)

    def _observe_guidance_signal(self, tool_name: str, args: dict[str, Any], result: Any) -> None:
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
            self._maybe_satisfy_context_read_directive_from_same_target_read(args, result)
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
            if self._bootstrap_source_exploration_reserved or self._exploration_without_context_count >= 1:
                return _BOOTSTRAP_CONTEXT_SEARCH_BLOCK
            self._bootstrap_source_exploration_reserved = True
        return None

    def _is_bootstrap_source_exploration_tool(
        self,
        tool_name: str,
        args: dict[str, Any],
    ) -> bool:
        return tool_name == "execute_code" or self._is_broad_source_exploration_tool(tool_name, args)

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
        tool = str(directive.get("tool") or "context_search").strip() or "context_search"
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
        query = self._tool_query_hint(tool_name, args) or "current task key symbols and accepted edit target"
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
            directive = packet.get("next_tool_directive") or packet.get("required_next_tool")
            if isinstance(directive, dict):
                captured = dict(directive)
                tool = str(captured.get("tool") or "").strip() or "context_search"
                captured["action_id"] = str(captured.get("action_id") or f"{tool}.next").strip()
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
        if self._next_tool_deviation_count == 1 and self._next_tool_visible_delivery_count < 2:
            self._pending_guidance_text = self._pending_next_action_reminder_text(directive)
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
            return tool_name == "terminal" and self._is_compact_diff_review_command(command)
        return False

    @staticmethod
    def _is_pending_next_action_effective_deviation(tool_name: str, args: dict[str, Any]) -> bool:
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
            lines.append(f"Why now: prior guidance was not followed before another source/edit action. {reason}")
        else:
            lines.append("Why now: prior guidance was not followed before another source/edit action.")
        lines.append(
            "Policy: advisory only; continuing is allowed. If you continue without this, "
            "Completion Gate will verify the final patch against FormSy contracts."
        )
        return "\n".join(lines)

    def _maybe_satisfy_next_tool_directive(self, args: dict[str, Any], result: Any) -> None:
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
        if _paths_equivalent(expected_path, actual_path) and _result_has_content(result):
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
        if _paths_equivalent(expected_path, actual_path) and _result_has_content(result):
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
            if tool_name in {"terminal", "search_files", "patch", "write_file", "execute_code"}:
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
        path, line_range = ConstraintKeeperCoordinator._context_read_path_and_range(args, result)
        if not path:
            return ""
        return f"{path}:{line_range[0]}-{line_range[1]}"

    @staticmethod
    def _context_read_path_and_range(args: dict[str, Any], result: Any) -> tuple[str, list[int]]:
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

    def _probe_budget_block_message(self, tool_name: str, args: dict[str, Any]) -> str | None:
        directive = self._active_probe_budget_directive
        if not directive:
            return None
        if tool_name == "terminal":
            command = str(args.get("command") or args.get("cmd") or "")
            if self._is_full_diff_stdout_command(command) and self._full_diff_stdout_count >= 1:
                return self._compact_diff_guidance_text()
        return None

    def _execute_code_read_write_bridge_block_message(
        self,
        tool_name: str,
        args: dict[str, Any],
    ) -> str | None:
        if tool_name != "execute_code":
            return None
        if not self._execute_code_uses_hermes_read_write_bridge(str(args.get("code") or "")):
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
        if re.search(
            r"\bopen\s*\([^)]*(?:,\s*|mode\s*=\s*)['\"][^'\"]*[wax+][^'\"]*['\"]",
            text,
        ):
            return True
        return bool(
            re.search(r"\.\s*(write_text|write_bytes|writelines)\s*\(", text)
        )

    def _reset_probe_budget_counts(self) -> None:
        self._probe_budget_counts = {
            "search_files": 0,
            "read_file": 0,
            "terminal_or_execute_code": 0,
        }
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
        if re.search(r"\bgit\s+diff\b.*(--stat|--shortstat|--numstat|--name-only|--name-status|--check)", normalized):
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
        return not ConstraintKeeperCoordinator._is_compact_diff_review_command(normalized)

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

    def _context_directive_block_message(self, tool_name: str, args: dict[str, Any]) -> str | None:
        directive = self._active_context_directive
        if not directive or not self._is_broad_source_exploration_tool(tool_name, args):
            return None
        summary = str(directive.get("summary") or "Server requested fresh context guidance.").strip()
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
        command = " ".join(str(args.get("command") or args.get("cmd") or "").lower().split())
        return bool(re.search(r"\b(rg|grep|find|ack|ag|cat|sed)\b", command))

    @staticmethod
    def _server_event(event: dict[str, Any], *, identity: FormSyIdentity) -> dict[str, Any]:
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
        if self._failure_counts[fingerprint] < 2 or fingerprint in self._recovered_fingerprints:
            return
        self._recovered_fingerprints.add(fingerprint)
        reason = self._recovery_reason(payload)
        self.recover(reason=reason)

    def _set_protocol_text(self, protocol_text: str, *, recovery_open: bool | None = None) -> None:
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
        decision = _protocol_scalar(protocol.get("gate_decision") or protocol.get("decision"))
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
        decision = cls._completion_decision(result) or "ACCEPT_DONE"
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
        if isinstance(memory_allowed, bool):
            lines.append(
                f"- Memory write allowed: {str(memory_allowed).lower()}"
            )
        if memory_quality:
            lines.append(f"- Memory write quality: {memory_quality}")
        if block_reason:
            lines.append(f"- Memory write block reason: {block_reason}")
        return "\n".join(lines) if len(lines) > 1 else ""

    @staticmethod
    def _completion_unavailable_projection_text(exc: Exception) -> str:
        return "\n".join([
            "## FormSy Completion Verifier",
            "- Decision: completion_verification_unavailable",
            "- Policy: final submit allowed by adapter policy; do not write successful implementation memory.",
            f"- Reason: verify_completion unavailable: {exc.__class__.__name__}: {exc}",
        ])

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
        decision = str(result.get("gate_decision") or result.get("decision") or "").lower()
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
        if any(marker in text for marker in ("no diff", "missing diff", "diff evidence")):
            return "missing_diff_evidence"
        if any(marker in text for marker in ("no passing test", "missing test", "test_result")):
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
        return "\n".join(lines) if lines else "FormSy Constraint Keeper rejected final submit."

    @classmethod
    def _is_semantic_recovery_case(cls, result: Any) -> bool:
        text = " ".join(cls._result_text_fragments(result)).lower()
        return "semanticcontract violation" in text or "semantic contract violation" in text

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


def _command_query_hint(command: str) -> str:
    paths: list[str] = []
    for raw_path in re.findall(r"(?<![A-Za-z0-9_./-])((?:lib|src|test|tests|plugins|docs)/[^'\"\s]+)", command):
        path = _repo_relative_source_path(raw_path)
        if path and path not in paths:
            paths.append(path)

    symbols: list[str] = []
    for symbol in re.findall(r"\b[A-Z][A-Za-z0-9_]{2,}\b", command):
        if symbol not in symbols:
            symbols.append(symbol)

    return " ".join([*symbols[:4], *paths[:2]])


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
