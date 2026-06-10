"""FormSy memory provider implementation for Hermes."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse

from agent.memory_provider import MemoryProvider
from plugins.formsy import RuntimeClient
from plugins.formsy.models import (
    ArtifactRef,
    ArtifactType,
    CodingSummary,
    MemorySearchRequest,
    SessionEndRequest,
    SyncMode,
)

from .client import MemoryClient
from .config import ConfigManager, MemoryConfig

logger = logging.getLogger("formsy.memory.provider")


class FormSyMemoryProvider(MemoryProvider):
    """FormSy Runtime-backed memory provider for Hermes."""

    def __init__(self) -> None:
        self._config: Optional[MemoryConfig] = None
        self._runtime_client: Optional[RuntimeClient] = None
        self._memory_client: Optional[MemoryClient] = None
        self._hermes_home: Optional[Path] = None
        self._identity_snapshot: Any = None
        self._session_id: str = ""
        self._turn_counter: int = 0
        self._turn_id: str = ""
        self._platform: str = ""
        self._query_budget: int = 1200
        self._search_top_k: int = 5
        self._memory_artifact_ids: list[str] = []
        self._memory_query_hints: list[str] = []
        self._memory_test_hints: list[str] = []
        self._memory_status: str = ""
        self._memory_freshness: str = ""
        self._memory_block: str = ""
        self._verified_solution_recipes: list[dict[str, Any]] = []
        self._completion_gate_decision: str = ""
        self._completion_audit: dict[str, Any] = {}
        self._session_end_sent: set[str] = set()
        self._context_artifact_ids: list[str] = []
        self._terminal_calls: list[dict] = []
        self._pending_sync_queue: list[dict] = []
        self._pending_sync_queue_max: int = 100
        self._async_loop: Any = None
        self._async_thread: threading.Thread | None = None
        self._async_lock = threading.Lock()

    @property
    def name(self) -> str:
        return "formsy_memory"

    def is_available(self) -> bool:
        """Provider is available when config/env is sufficient; no network calls."""
        try:
            from hermes_constants import get_hermes_home

            cfg = ConfigManager(get_hermes_home()).load_config()
        except Exception:
            cfg = MemoryConfig()

        api_key_env = cfg.api_key_env or "FORMSY_API_KEY"
        has_key = bool(
            cfg.api_key
            or os.environ.get(api_key_env)
            or os.environ.get("FORMSY_API_KEY")
        )

        return bool(cfg.base_url and has_key)

    def initialize(self, session_id: str, **kwargs) -> None:
        from hermes_constants import get_hermes_home

        self._session_id = str(session_id or "").strip()
        self._platform = str(kwargs.get("platform") or "").strip()
        self._hermes_home = Path(kwargs.get("hermes_home") or get_hermes_home())
        self._identity_snapshot = kwargs.get("runtime_identity_snapshot")

        cfg = ConfigManager(self._hermes_home).load_config()
        self._config = cfg
        self._query_budget = max(1, int(getattr(cfg, "query_budget", 1200) or 1200))
        self._search_top_k = max(1, int(getattr(cfg, "search_top_k", 5) or 5))

        self._runtime_client = RuntimeClient(
            base_url=cfg.base_url,
            api_key_env=cfg.api_key_env,
            api_key=cfg.api_key,
            timeout_s=cfg.timeout_s,
            max_retries=cfg.max_retries,
        )
        self._run_async(self._runtime_client.__aenter__())
        self._memory_client = MemoryClient(self._runtime_client)

    def on_turn_start(self, turn_number: int, message: str, **kwargs) -> None:
        self._turn_counter = max(0, int(turn_number or 0))
        self._turn_id = f"{self._session_id}:turn:{self._turn_counter}"
        if kwargs.get("runtime_identity_snapshot") is not None:
            self._identity_snapshot = kwargs.get("runtime_identity_snapshot")
        self._reset_turn_memory_trace()

    def prefetch(self, query: str, *, session_id: str = "") -> str:
        if not self._memory_client or not self._config:
            return ""
        active_session_id = str(session_id or self._session_id or "").strip() or "unknown"
        self._session_id = active_session_id
        if not self._turn_id:
            self._turn_id = f"{active_session_id}:turn:{max(self._turn_counter, 1)}"

        response = self._run_async(
            self._memory_client.prefetch(
                workspace_id=self._current_workspace_id(),
                session_id=active_session_id,
                turn_id=self._turn_id,
                query=str(query or "").strip(),
                identity=self._current_runtime_identity(),
                budget={"max_tokens": self._query_budget},
            )
        )
        if response is None:
            return self._prefetch_from_local_store(query)
        self._record_prefetch_response(response)
        local_block = self._prefetch_from_local_store(query)
        if response.memory_block:
            merged = self._merge_memory_blocks(local_block, response.memory_block)
            self._memory_block = merged
            return merged
        return local_block

    def sync_turn(self, user_content: str, assistant_content: str, *, session_id: str = "", **kwargs) -> None:
        if not self._memory_client or not self._config:
            return
        if self._is_skill_review_turn(user_content):
            logger.debug("Skipping FormSy memory sync for Hermes skill-review turn")
            return
        active_session_id = str(session_id or self._session_id or "").strip() or "unknown"
        turn_id = self._turn_id or f"{active_session_id}:turn:{max(self._turn_counter, 1)}"
        messages = [
            {"role": "user", "content": str(user_content or "")},
            {"role": "assistant", "content": str(assistant_content or "")},
        ]
        coding_summary = self._build_coding_summary(kwargs)
        artifacts = self._build_artifact_refs(kwargs.get("context_artifacts"))
        event = {
            "workspace_id": self._current_workspace_id(),
            "session_id": active_session_id,
            "turn_id": turn_id,
            "messages": messages,
            "identity": self._current_runtime_identity(),
            "coding_summary": coding_summary,
            "artifacts": artifacts or None,
        }
        self._flush_pending_sync_queue()
        self._dispatch_sync_event(event)
        self._append_local_memory_event(event)
        self._clear_completion_verifier_trace()

    def on_session_end(self, messages: list[dict[str, Any]]) -> None:
        if not self._runtime_client or not self._config:
            return
        session_id = self._session_id or "unknown"
        if session_id in self._session_end_sent:
            return
        summary_hint = self._build_summary_hint(messages)
        request = SessionEndRequest(
            workspace_id=self._current_workspace_id(),
            session_id=session_id,
            identity=self._current_runtime_identity(),
            summary_hint=summary_hint,
        )
        self._run_async(self._runtime_client.session_end(request))
        self._session_end_sent.add(session_id)

    def on_session_switch(
        self,
        new_session_id: str,
        *,
        parent_session_id: str = "",
        reset: bool = False,
        **kwargs,
    ) -> None:
        self._session_id = str(new_session_id or "").strip()
        if kwargs.get("runtime_identity_snapshot") is not None:
            self._identity_snapshot = kwargs.get("runtime_identity_snapshot")
        if reset:
            self._turn_counter = 0
            self._turn_id = ""
            self._reset_turn_memory_trace()
        elif self._turn_counter:
            self._turn_id = f"{self._session_id}:turn:{self._turn_counter}"

    def get_context_hints(self) -> dict[str, Any]:
        """Return memory hints for FormSy context_search metadata."""
        hints: dict[str, Any] = {}
        if self._memory_artifact_ids:
            hints["memory_artifact_ids"] = list(self._memory_artifact_ids)
        if self._memory_query_hints:
            hints["memory_query_hints"] = list(self._memory_query_hints)
        if self._memory_test_hints:
            hints["memory_test_hints"] = list(self._memory_test_hints)
        if self._memory_status:
            hints["memory_status"] = self._memory_status
        if self._memory_freshness:
            hints["memory_freshness"] = self._memory_freshness
        if self._memory_block:
            hints["memory_block"] = self._memory_block
        if self._verified_solution_recipes:
            hints["verified_solution_recipes"] = list(self._verified_solution_recipes)
        return hints

    def record_context_artifacts(self, artifact_ids: list[str]) -> None:
        """Accumulate context artifact IDs returned from context_search/context_read."""
        for artifact_id in artifact_ids:
            artifact_id = str(artifact_id or "").strip()
            if artifact_id and artifact_id not in self._context_artifact_ids:
                self._context_artifact_ids.append(artifact_id)

    def record_terminal_call(self, command: str, result: str) -> None:
        """Record a terminal tool call for coding summary construction."""
        self._terminal_calls.append({
            "command": str(command or "").strip(),
            "result": str(result or "").strip(),
        })

    def record_completion_verifier_result(self, payload: Any) -> None:
        """Record accepted Completion Verifier evidence for the next memory sync."""
        parsed = self._parse_json_payload(payload)
        if not isinstance(parsed, dict):
            return

        decision = self._completion_gate_decision_from_payload(parsed)
        if not decision:
            return

        audit = parsed.get("completion_audit")
        if isinstance(audit, dict):
            completion_audit = dict(audit)
            completion_audit.setdefault("audit_status", "verified")
            completion_audit.setdefault("gate_decision", decision)
            completion_audit.setdefault("memory_write_allowed", True)
            completion_audit.setdefault("memory_write_quality", "medium")
        else:
            completion_audit = {
                "audit_status": "verified",
                "gate_decision": decision,
                "memory_write_allowed": True,
                "memory_write_quality": "medium",
            }

        self._completion_gate_decision = decision
        self._completion_audit = completion_audit

    def get_tool_schemas(self) -> list[dict[str, Any]]:
        if self._config is not None and not self._config.enable_memory_tools:
            return []
        return [
            {
                "name": "cc_memory_search",
                "description": (
                    "Search FormSy memory for prior-session preferences, repo lessons, "
                    "and similar task history. Use as historical hints only."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "Search query.",
                        },
                        "top_k": {
                            "type": "integer",
                            "description": "Maximum number of results to return.",
                            "default": self._search_top_k,
                            "minimum": 1,
                            "maximum": 20,
                        },
                    },
                    "required": ["query"],
                },
            }
        ]

    def handle_tool_call(self, tool_name: str, args: dict[str, Any], **kwargs) -> str:
        if tool_name != "cc_memory_search":
            return json.dumps({"ok": False, "error": f"Unknown tool: {tool_name}"})
        if not self._runtime_client or not self._config:
            return json.dumps({"ok": False, "error": "FormSy memory client not initialized"})

        query = str(args.get("query") or "").strip()
        if not query:
            return json.dumps({"ok": False, "error": "query is required"})

        top_k = self._coerce_positive_int(args.get("top_k"), self._search_top_k)
        identity = self._current_runtime_identity()
        repo_id = str(identity.get("repo_id") or "").strip()
        revision = str(identity.get("revision") or "latest")
        request = MemorySearchRequest(
            workspace_id=self._current_workspace_id(),
            session_id=self._session_id or "unknown",
            turn_id=self._turn_id or None,
            identity=identity,
            query=query,
            top_k=top_k,
        )

        result = self._run_async(
            self._runtime_client._request(  # uses the existing runtime client surface for this endpoint
                "POST",
                "/v1/runtime/memory/search",
                data=request.model_dump(mode="json"),
                session_id=self._session_id or "unknown",
            )
        )
        if result is None:
            return json.dumps({"ok": False, "error": "FormSy memory search failed", "degraded": True})

        payload = {
            "ok": True,
            "query": query,
            "repo_id": repo_id,
            "revision": revision,
            "top_k": top_k,
            "result": result,
        }
        return json.dumps(payload, ensure_ascii=False)

    def shutdown(self) -> None:
        self._flush_pending_sync_queue()
        if self._runtime_client is not None:
            self._run_async(self._runtime_client.__aexit__(None, None, None))
        self._runtime_client = None
        self._memory_client = None
        self._stop_async_loop()

    def _reset_turn_memory_trace(self) -> None:
        self._memory_artifact_ids = []
        self._memory_query_hints = []
        self._memory_test_hints = []
        self._memory_status = ""
        self._memory_freshness = ""
        self._memory_block = ""
        self._verified_solution_recipes = []
        self._clear_completion_verifier_trace()
        self._context_artifact_ids = []
        self._terminal_calls = []

    def _clear_completion_verifier_trace(self) -> None:
        self._completion_gate_decision = ""
        self._completion_audit = {}

    def _dispatch_sync_event(self, event: dict) -> None:
        """Send one sync event; enqueue it on failure for later retry."""
        try:
            self._run_async(
                self._memory_client.sync_turn(
                    workspace_id=event["workspace_id"],
                    session_id=event["session_id"],
                    turn_id=event["turn_id"],
                    messages=event["messages"],
                    identity=event["identity"],
                    sync_mode=SyncMode.ASYNC_BEST_EFFORT,
                    coding_summary=event.get("coding_summary"),
                    artifacts=event.get("artifacts"),
                )
            )
        except Exception as exc:
            logger.warning("sync_turn failed, queuing for retry: %s", exc)
            self._enqueue_pending_sync(event)

    def _enqueue_pending_sync(self, event: dict) -> None:
        """Add event to the pending queue, evicting the oldest if over limit."""
        if len(self._pending_sync_queue) >= self._pending_sync_queue_max:
            dropped = self._pending_sync_queue.pop(0)
            logger.warning(
                "Pending sync queue full (%d); dropped oldest event turn_id=%s",
                self._pending_sync_queue_max,
                dropped.get("turn_id", "?"),
            )
        self._pending_sync_queue.append(event)

    def _flush_pending_sync_queue(self) -> None:
        """Retry all queued pending sync events; remove successfully sent ones."""
        if not self._pending_sync_queue or not self._memory_client:
            return
        remaining: list[dict] = []
        for event in self._pending_sync_queue:
            try:
                self._run_async(
                    self._memory_client.sync_turn(
                        workspace_id=event["workspace_id"],
                        session_id=event["session_id"],
                        turn_id=event["turn_id"],
                        messages=event["messages"],
                        identity=event["identity"],
                        sync_mode=SyncMode.ASYNC_BEST_EFFORT,
                        coding_summary=event.get("coding_summary"),
                        artifacts=event.get("artifacts"),
                    )
                )
                logger.debug("Flushed pending sync event turn_id=%s", event.get("turn_id", "?"))
            except Exception as exc:
                logger.debug("Pending sync event still failing, keeping: %s", exc)
                remaining.append(event)
        self._pending_sync_queue = remaining

    def _record_prefetch_response(self, response: Any) -> None:
        advisory = getattr(response, "advisory", None)
        if not isinstance(advisory, dict):
            advisory = {}

        artifacts = getattr(response, "artifacts", None)
        self._memory_artifact_ids = self._extract_artifact_ids(artifacts)
        memory_block = str(getattr(response, "memory_block", "") or "").strip()
        self._memory_block = memory_block
        retrieved_facts = getattr(response, "retrieved_facts", None)
        self._memory_query_hints = self._coerce_string_list(
            advisory.get("query_hints")
            or self._nested_get(advisory, "bundle", "query_hints")
        )
        self._memory_test_hints = self._coerce_string_list(
            advisory.get("test_hints")
            or self._nested_get(advisory, "bundle", "test_hints")
        )
        self._memory_status = str(advisory.get("status") or "").strip()
        if not self._memory_status and (memory_block or self._memory_artifact_ids or retrieved_facts):
            self._memory_status = "hit"
        self._memory_freshness = str(advisory.get("freshness") or "").strip()

    def _append_local_memory_event(self, event: dict[str, Any]) -> None:
        """Persist a compact local copy so new Hermes clients can recall it."""
        if self._hermes_home is None:
            return
        try:
            coding_summary = event.get("coding_summary")
            if hasattr(coding_summary, "model_dump"):
                coding_summary = coding_summary.model_dump(mode="json", exclude_none=True)
            artifacts = event.get("artifacts") or []
            artifact_ids = [
                str(getattr(artifact, "artifact_id", "") or "").strip()
                for artifact in artifacts
                if str(getattr(artifact, "artifact_id", "") or "").strip()
            ]
            record = {
                "created_at": time.time(),
                "workspace_id": event.get("workspace_id"),
                "session_id": event.get("session_id"),
                "turn_id": event.get("turn_id"),
                "identity": event.get("identity") or {},
                "messages": event.get("messages") or [],
                "coding_summary": coding_summary,
                "artifact_ids": artifact_ids,
            }
            path = self._local_memory_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")
            self._trim_local_memory_store(max_records=500)
        except Exception:
            logger.debug("local FormSy memory append failed", exc_info=True)

    def _prefetch_from_local_store(self, query: str) -> str:
        matches = self._local_memory_matches(query)
        if not matches:
            return ""

        self._memory_status = "hit"
        self._memory_freshness = "local"
        self._memory_artifact_ids = self._dedupe_string_list(
            [artifact_id for record, _score in matches for artifact_id in record.get("artifact_ids", [])]
        )
        self._memory_query_hints = self._dedupe_string_list(
            path
            for record, _score in matches
            for path in self._local_memory_query_hint_paths(record)
        )
        self._memory_test_hints = self._dedupe_string_list(
            command
            for record, _score in matches
            for command in self._local_memory_test_commands(record)
        )

        lines = ["## Relevant Memory", "", "### Prior Runs"]
        for record, score in matches[:3]:
            summary = record.get("coding_summary") or {}
            identity = record.get("identity") or {}
            parts = [
                f"session={record.get('session_id')}",
                f"score={score:.2f}",
            ]
            repo_id = identity.get("repo_id")
            if repo_id:
                parts.append(f"repo={repo_id}")
            lines.append(f"- [{'|'.join(parts)}] {self._local_memory_summary(summary, record)}")
        digest_lines: list[str] = []
        verified_recipes: list[dict[str, Any]] = []
        for record, _score in matches[:3]:
            summary = record.get("coding_summary") or {}
            if self._coding_summary_has_accepted_completion(summary):
                recipe = self._local_memory_verified_solution_recipe(summary, record)
                if recipe:
                    verified_recipes.append(recipe)
                digest_lines.extend(self._local_memory_solution_digest(summary))
        verified_recipes = self._dedupe_recipe_list(verified_recipes)
        if verified_recipes:
            lines.extend(["", "### Verified Solution Recipe"])
            lines.append(
                "Use this as a verified starting recipe for similar tasks; verify current source before patching."
            )
            lines.append("```json")
            lines.append(json.dumps(verified_recipes[0], ensure_ascii=False, indent=2, sort_keys=True))
            lines.append("```")
        digest_lines = self._dedupe_string_list(digest_lines)
        if digest_lines:
            lines.extend(["", "### Solution Digest"])
            lines.extend(f"- {line}" for line in digest_lines[:8])
        lines.append("- Treat memory as historical hints; verify current code if the working tree may have changed.")
        block = "\n".join(lines)
        self._memory_block = block
        self._verified_solution_recipes = verified_recipes[:3]
        return block

    def _local_memory_matches(self, query: str) -> list[tuple[dict[str, Any], float]]:
        if self._hermes_home is None:
            return []
        path = self._local_memory_path()
        if not path.exists():
            return []
        query_terms = self._terms(query)
        current_identity = self._current_runtime_identity()
        current_repo = str(current_identity.get("repo_id") or "").strip()
        current_workspace = self._current_workspace_id()
        records: list[tuple[dict[str, Any], float]] = []
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except Exception:
            return []

        for line in lines[-500:]:
            try:
                record = json.loads(line)
            except Exception:
                continue
            summary = record.get("coding_summary")
            if not isinstance(summary, dict) or not summary:
                continue
            if not self._record_has_coherent_coding_summary(record):
                continue
            if str(record.get("workspace_id") or "") != current_workspace:
                continue
            identity = record.get("identity") or {}
            repo_id = str(identity.get("repo_id") or "").strip()
            if current_repo and repo_id and current_repo != repo_id:
                continue
            text = self._local_memory_search_text(record)
            terms = self._terms(text)
            overlap = len(query_terms & terms)
            if query_terms and overlap < (2 if len(query_terms) >= 3 else 1):
                continue
            score = min(0.7, overlap * 0.08)
            if current_repo and repo_id == current_repo:
                score += 0.25
            if (record.get("coding_summary") or {}).get("patch_summary"):
                score += 0.1
            if not query_terms and text:
                score += 0.1
            if score <= 0:
                continue
            records.append((record, min(score, 1.0)))
        records.sort(key=lambda item: (item[1], float(item[0].get("created_at") or 0)), reverse=True)
        return records[:5]

    @staticmethod
    def _local_memory_search_text(record: dict[str, Any]) -> str:
        summary = record.get("coding_summary") or {}
        messages = record.get("messages") or []
        parts: list[str] = []
        for key in (
            "task_type",
            "problem_summary",
            "root_cause",
            "patch_summary",
            "test_result",
            "context_query",
        ):
            value = summary.get(key)
            if value:
                parts.append(str(value))
        for key in (
            "accepted_targets",
            "changed_files",
            "changed_symbols",
            "failure_lessons",
        ):
            value = summary.get(key)
            if isinstance(value, list):
                parts.extend(str(item) for item in value)
        for command in summary.get("tests_run") or []:
            if FormSyMemoryProvider._is_test_or_verification_command(str(command)):
                parts.append(str(command))
        parts.extend(str(msg.get("content") or "") for msg in messages if isinstance(msg, dict))
        return "\n".join(parts)

    @staticmethod
    def _local_memory_summary(summary: dict[str, Any], record: dict[str, Any]) -> str:
        chunks: list[str] = []
        changed = [
            path
            for path in (FormSyMemoryProvider._normalize_repo_path(str(item or "")) for item in summary.get("changed_files") or [])
            if FormSyMemoryProvider._is_repo_hint_path(path)
        ]
        if changed:
            chunks.append("changed " + ", ".join(str(path) for path in changed[:3]))
        patch = str(summary.get("patch_summary") or "").strip()
        if patch:
            patch_paths = sorted(FormSyMemoryProvider._extract_patch_paths(patch))
            if patch_paths:
                chunks.append("patch touched " + ", ".join(patch_paths[:3]))
            else:
                chunks.append("patch recorded")
        tests = summary.get("tests_run") or []
        tests = [command for command in tests if FormSyMemoryProvider._is_test_or_verification_command(str(command))]
        if tests:
            chunks.append("tests " + "; ".join(str(command) for command in tests[:3]))
        if not chunks:
            messages = record.get("messages") or []
            chunks.extend(str(msg.get("content") or "").strip()[:300] for msg in messages if isinstance(msg, dict))
        return " | ".join(chunk for chunk in chunks if chunk)[:1500]

    @staticmethod
    def _local_memory_query_hint_paths(record: dict[str, Any]) -> list[str]:
        summary = record.get("coding_summary") or {}
        changed = summary.get("changed_files") or []
        if changed:
            return [
                path
                for path in (FormSyMemoryProvider._normalize_repo_path(str(item or "")) for item in changed)
                if FormSyMemoryProvider._is_repo_hint_path(path)
            ]
        accepted = summary.get("accepted_targets") or []
        return [
            path
            for path in (FormSyMemoryProvider._normalize_repo_path(str(item or "")) for item in accepted)
            if FormSyMemoryProvider._is_repo_hint_path(path)
        ]

    @staticmethod
    def _local_memory_test_commands(record: dict[str, Any]) -> list[str]:
        summary = record.get("coding_summary") or {}
        commands = summary.get("tests_run") or []
        return [
            str(command)
            for command in commands
            if FormSyMemoryProvider._is_test_or_verification_command(str(command))
        ]

    def _local_memory_path(self) -> Path:
        base = self._hermes_home or Path.home() / ".hermes"
        return base / "formsy-memory-local.jsonl"

    def _trim_local_memory_store(self, *, max_records: int) -> None:
        path = self._local_memory_path()
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
            if len(lines) <= max_records:
                return
            path.write_text("\n".join(lines[-max_records:]) + "\n", encoding="utf-8")
        except Exception:
            logger.debug("local FormSy memory trim failed", exc_info=True)

    def _build_coding_summary(self, kwargs: dict[str, Any]) -> Optional[CodingSummary]:
        """Build a CodingSummary from per-turn accumulated state and caller-supplied kwargs."""
        accepted_targets: list[str] = self._coerce_string_list(
            kwargs.get("accepted_targets") or []
        )
        changed_files: list[str] = self._coerce_string_list(
            kwargs.get("changed_files") or self._extract_changed_files_from_terminal()
        )
        accepted_targets = self._repo_hint_paths(accepted_targets)
        changed_files = self._repo_hint_paths(changed_files)
        changed_symbols: list[str] = self._coerce_string_list(
            kwargs.get("changed_symbols") or []
        )
        tests_run: list[str] = self._coerce_string_list(
            kwargs.get("tests_run")
            or [
                call["command"]
                for call in self._terminal_calls
                if self._is_test_or_verification_command(str(call.get("command") or ""))
            ]
        )
        retrieval_state = str(kwargs.get("retrieval_state") or "").strip() or None
        task_type = str(kwargs.get("task_type") or "").strip() or None
        problem_summary = str(kwargs.get("problem_summary") or "").strip() or None
        root_cause = str(kwargs.get("root_cause") or "").strip() or None
        patch_summary = (
            str(kwargs.get("patch_summary") or "").strip()
            or self._extract_patch_summary_from_terminal()
            or None
        )
        if not self._coding_summary_paths_coherent(
            accepted_targets,
            changed_files,
            patch_summary,
        ):
            logger.info(
                "Dropping incoherent FormSy coding summary: changed files do not overlap accepted targets"
            )
            return None
        test_result = str(kwargs.get("test_result") or "").strip() or None
        failure_lessons: list[str] = self._coerce_string_list(kwargs.get("failure_lessons") or [])
        context_query = str(kwargs.get("context_query") or "").strip() or None
        completion_gate_decision = (
            str(kwargs.get("completion_gate_decision") or "").strip()
            or self._completion_gate_decision
            or None
        )
        completion_audit = (
            kwargs.get("completion_audit")
            if isinstance(kwargs.get("completion_audit"), dict)
            else self._completion_audit or None
        )
        workspace_fingerprint = (
            str(kwargs.get("workspace_fingerprint") or "").strip() or None
        )
        confidence = kwargs.get("confidence")
        if confidence is not None:
            try:
                confidence = float(confidence)
                confidence = max(0.0, min(1.0, confidence))
            except (TypeError, ValueError):
                confidence = None

        has_data = any([
            accepted_targets, changed_files, changed_symbols, tests_run, retrieval_state,
            task_type, problem_summary, root_cause, patch_summary, test_result,
            failure_lessons, context_query, completion_gate_decision,
            completion_audit, workspace_fingerprint, confidence is not None,
        ])
        if not has_data:
            return None

        return CodingSummary(
            task_type=task_type,
            problem_summary=problem_summary,
            accepted_targets=accepted_targets,
            changed_files=changed_files,
            changed_symbols=changed_symbols,
            root_cause=root_cause,
            patch_summary=patch_summary,
            tests_run=tests_run,
            test_result=test_result,
            failure_lessons=failure_lessons,
            context_query=context_query,
            retrieval_state=retrieval_state,
            confidence=confidence,
            completion_gate_decision=completion_gate_decision,
            completion_audit=completion_audit,
            workspace_fingerprint=workspace_fingerprint,
        )

    def _build_artifact_refs(self, artifact_ids: Any = None) -> list[ArtifactRef]:
        """Build ArtifactRef objects from accumulated context artifact IDs."""
        workspace_id = self._current_workspace_id()
        refs: list[ArtifactRef] = []
        ids = self._coerce_string_list(artifact_ids) + list(self._context_artifact_ids)
        seen: set[str] = set()
        for artifact_id in ids:
            if artifact_id in seen:
                continue
            seen.add(artifact_id)
            refs.append(ArtifactRef(
                artifact_id=artifact_id,
                artifact_type=ArtifactType.CODE_CONTEXT,
                workspace_id=workspace_id,
            ))
        return refs

    def get_config_schema(self) -> list[dict[str, Any]]:
        return [
            {"key": "base_url", "description": "FormSy Runtime base URL", "default": "https://api.formsy.ai"},
            {"key": "api_key_env", "description": "Environment variable for the FormSy API key", "default": "FORMSY_API_KEY"},
            {"key": "workspace_id", "description": "FormSy workspace identifier", "default": "ws_default"},
            {"key": "tenant_id", "description": "Optional tenant identifier"},
            {"key": "timeout_s", "description": "Request timeout in seconds", "default": "30"},
            {"key": "max_retries", "description": "Maximum retries", "default": "3"},
            {"key": "enable_memory_tools", "description": "Expose memory search tools", "default": "true", "choices": ["true", "false"]},
            {"key": "query_budget", "description": "Prefetch token budget", "default": "1200"},
            {"key": "search_top_k", "description": "Default top_k for cc_memory_search", "default": "5"},
        ]

    def save_config(self, values: dict[str, Any], hermes_home: str) -> None:
        config_path = ConfigManager(Path(hermes_home)).config_file
        existing: dict[str, Any] = {}
        if config_path.exists():
            try:
                existing = json.loads(config_path.read_text())
            except Exception:
                existing = {}
        existing.update(values or {})
        config_path.write_text(json.dumps(existing, indent=2))

    def _run_async(self, coro):
        try:
            loop = self._ensure_async_loop()
            future = asyncio.run_coroutine_threadsafe(coro, loop)
            return future.result(timeout=300)
        except Exception:
            logger.exception("FormSy memory async call failed")
            return None

    def _ensure_async_loop(self):
        with self._async_lock:
            if self._async_loop is not None and self._async_thread and self._async_thread.is_alive():
                return self._async_loop

            ready = threading.Event()
            loop_holder: dict[str, Any] = {}

            def _run_loop() -> None:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                loop_holder["loop"] = loop
                ready.set()
                loop.run_forever()
                pending = asyncio.all_tasks(loop)
                for task in pending:
                    task.cancel()
                if pending:
                    loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
                loop.close()

            thread = threading.Thread(target=_run_loop, name="formsy-memory-async", daemon=True)
            thread.start()
            ready.wait(timeout=5)
            self._async_loop = loop_holder["loop"]
            self._async_thread = thread
            return self._async_loop

    def _stop_async_loop(self) -> None:
        with self._async_lock:
            loop = self._async_loop
            thread = self._async_thread
            self._async_loop = None
            self._async_thread = None
        if loop is not None and thread is not None and thread.is_alive():
            loop.call_soon_threadsafe(loop.stop)
            thread.join(timeout=5)

    def _current_workspace_id(self) -> str:
        if self._identity_snapshot is not None:
            workspace_id = str(getattr(self._identity_snapshot, "workspace_id", "") or "").strip()
            if workspace_id:
                return workspace_id
        return str(getattr(self._config, "workspace_id", "") or "ws_default")

    def _current_runtime_identity(self) -> dict[str, Any]:
        if self._identity_snapshot is not None:
            identity_fn = getattr(self._identity_snapshot, "to_runtime_identity", None)
            if callable(identity_fn):
                return dict(identity_fn())

        repo_id = ""
        revision = ""
        branch = self._git_output(["git", "rev-parse", "--abbrev-ref", "HEAD"])
        remote_url = self._git_output(["git", "remote", "get-url", "origin"])
        if remote_url:
            repo_id = self._repo_id_from_git_url(remote_url)
        revision = self._git_output(["git", "rev-parse", "HEAD"])
        if not repo_id and self._config:
            repo_id = str(getattr(self._config, "repo_id", "") or "").strip()
        if not revision and self._config:
            revision = str(getattr(self._config, "revision", "") or "").strip()
        return {
            key: value
            for key, value in {
                "repo_id": repo_id or None,
                "branch": branch or None,
                "revision": revision or "latest",
            }.items()
            if value is not None
        }

    @staticmethod
    def _repo_id_from_git_url(remote_url: str) -> str:
        value = remote_url.strip()
        if not value:
            return ""
        if "://" in value:
            parsed = urlparse(value)
            path = parsed.path
        elif ":" in value and not value.startswith("/"):
            path = value.split(":", 1)[1]
        else:
            path = value
        path = path.strip("/")
        if path.endswith(".git"):
            path = path[:-4]
        parts = [part for part in path.split("/") if part]
        if len(parts) < 2:
            return ""
        return f"{parts[-2]}__{parts[-1]}"

    @staticmethod
    def _git_output(cmd: list[str]) -> str:
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                check=False,
                text=True,
                timeout=2,
            )
        except Exception:
            return ""
        if result.returncode != 0:
            return ""
        return (result.stdout or "").strip()

    @staticmethod
    def _coerce_positive_int(value: Any, default: int) -> int:
        try:
            number = int(value)
        except (TypeError, ValueError):
            number = default
        return max(1, number)

    @staticmethod
    def _coerce_string_list(value: Any) -> list[str]:
        if isinstance(value, str):
            text = value.strip()
            return [text] if text else []
        if not isinstance(value, list):
            return []
        items: list[str] = []
        for item in value:
            text = str(item or "").strip()
            if text:
                items.append(text)
        return items

    @staticmethod
    def _dedupe_string_list(values: Any) -> list[str]:
        seen: set[str] = set()
        result: list[str] = []
        for value in values or []:
            text = str(value or "").strip()
            if not text or text in seen:
                continue
            seen.add(text)
            result.append(text)
        return result

    @staticmethod
    def _terms(text: str) -> set[str]:
        stopwords = {
            "command",
            "commands",
            "context",
            "context_search",
            "directory",
            "file",
            "files",
            "filename",
            "filenames",
            "modify",
            "post",
            "process",
            "source",
            "task",
            "test",
            "tests",
            "tool",
            "tools",
            "workflow",
        }
        terms = set()
        for raw in re.findall(r"[A-Za-z0-9_./:-]+", text or ""):
            if len(raw) > 2 and raw.lower() not in stopwords:
                terms.add(raw.lower())
            for part in re.split(r"[^A-Za-z0-9]+", raw):
                if len(part) > 2 and part.lower() not in stopwords:
                    terms.add(part.lower())
                for camel_part in re.findall(r"[A-Z]?[a-z]+|[A-Z]+(?=[A-Z]|$)|[0-9]+", part):
                    if len(camel_part) > 2 and camel_part.lower() not in stopwords:
                        terms.add(camel_part.lower())
        return terms

    @staticmethod
    def _extract_artifact_ids(artifacts: Any) -> list[str]:
        if not isinstance(artifacts, list):
            return []
        ids: list[str] = []
        for artifact in artifacts:
            artifact_id = ""
            if isinstance(artifact, dict):
                artifact_id = str(artifact.get("artifact_id") or "").strip()
            else:
                artifact_id = str(getattr(artifact, "artifact_id", "") or "").strip()
            if artifact_id:
                ids.append(artifact_id)
        return ids

    @staticmethod
    def _nested_get(data: dict[str, Any], *keys: str) -> Any:
        current: Any = data
        for key in keys:
            if not isinstance(current, dict):
                return None
            current = current.get(key)
        return current

    @staticmethod
    def _parse_json_payload(payload: Any) -> dict[str, Any] | None:
        if isinstance(payload, dict):
            return payload
        text = str(payload or "").strip()
        if not text:
            return None
        json_text = text.split("\n\n## FormSy Constraint Protocol", 1)[0].strip()
        try:
            parsed = json.loads(json_text)
        except (json.JSONDecodeError, TypeError):
            return None
        return parsed if isinstance(parsed, dict) else None

    @staticmethod
    def _completion_gate_decision_from_payload(payload: dict[str, Any]) -> str:
        accepted = {"ACCEPT_DONE", "ACCEPT_DONE_WITH_OVERRIDE"}
        audit = payload.get("completion_audit")
        verifier = payload.get("verifier")
        protocol = payload.get("protocol")
        candidates = [
            payload.get("decision"),
            audit.get("gate_decision") if isinstance(audit, dict) else None,
            verifier.get("gate_decision") if isinstance(verifier, dict) else None,
            protocol.get("gate_decision") if isinstance(protocol, dict) else None,
        ]
        for candidate in candidates:
            decision = str(candidate or "").strip().upper()
            if decision in accepted:
                return decision
        if isinstance(audit, dict):
            audit_status = str(audit.get("audit_status") or "").strip()
            if audit_status == "verified":
                return "ACCEPT_DONE"
        return ""

    def _extract_changed_files_from_terminal(self) -> list[str]:
        """Mine recorded terminal calls for files mentioned in git diff output."""
        files: list[str] = []
        seen: set[str] = set()
        for call in self._terminal_calls:
            result = call.get("result") or ""
            if not result:
                continue
            for match in re.finditer(r'^(?:\+\+\+|---)\s+(?:a/|b/)?([\w./\-]+)', result, re.MULTILINE):
                path = self._normalize_repo_path(match.group(1).strip())
                if not self._is_repo_hint_path(path):
                    continue
                if path and path not in seen:
                    seen.add(path)
                    files.append(path)
        return files

    def _extract_patch_summary_from_terminal(self) -> str:
        """Return the first git diff output found in recorded terminal calls."""
        for call in self._terminal_calls:
            result = call.get("result") or ""
            if result.startswith("diff --git") or "\ndiff --git" in result:
                idx = result.find("diff --git")
                return result[idx:idx + 2000].strip()
        return ""

    @staticmethod
    def _is_skill_review_turn(user_content: str) -> bool:
        text = str(user_content or "")
        return (
            "Review the conversation above and update the skill library" in text
            or "Target shape of the library: CLASS-LEVEL skills" in text
        )

    @staticmethod
    def _is_test_or_verification_command(command: str) -> bool:
        lowered = command.strip().lower()
        if not lowered:
            return False
        if "complete_task_and_submit_final_output" in lowered:
            return False
        if lowered.startswith(("cat ", "git diff", "patch ")):
            return False
        if "runtests.py" in lowered:
            return True
        if lowered.startswith(("pytest ", "python -m pytest ", "python3 -m pytest ")):
            return True
        if re.search(r"\bpython3?\s+-m\s+py_compile\s+[\w./@+\-]+\.py\b", lowered):
            return True
        if re.match(r"^(?:python|python3)\s+reproduce(?:[_-].*)?\.py(?:\s|$)", lowered):
            return True
        if " manage.py test" in lowered or lowered.startswith(("python manage.py test", "python3 manage.py test")):
            return True
        return False

    @classmethod
    def _record_has_coherent_coding_summary(cls, record: dict[str, Any]) -> bool:
        summary = record.get("coding_summary") or {}
        if not isinstance(summary, dict):
            return True
        return cls._coding_summary_paths_coherent(
            summary.get("accepted_targets") or [],
            summary.get("changed_files") or [],
            summary.get("patch_summary") or "",
        )

    @staticmethod
    def _coding_summary_has_accepted_completion(summary: dict[str, Any]) -> bool:
        if not isinstance(summary, dict):
            return False
        audit = summary.get("completion_audit")
        if isinstance(audit, dict):
            audit_status = str(audit.get("audit_status") or "").strip()
            decision = str(audit.get("gate_decision") or "").strip().upper()
            if (
                audit_status == "verified"
                and decision in {"ACCEPT_DONE", "ACCEPT_DONE_WITH_OVERRIDE"}
                and audit.get("memory_write_allowed") is True
            ):
                return True
            return False
        decision = str(summary.get("completion_gate_decision") or "").strip().upper()
        if decision not in {"ACCEPT_DONE", "ACCEPT_DONE_WITH_OVERRIDE"}:
            return False
        return bool(str(summary.get("workspace_fingerprint") or "").strip())

    @classmethod
    def _coding_summary_paths_coherent(
        cls,
        accepted_targets: list[str],
        changed_files: list[str],
        patch_summary: str | None,
    ) -> bool:
        """Detect stale patch.txt summaries attached to the wrong retrieval target."""
        accepted = cls._normalize_repo_paths(accepted_targets)
        touched = cls._normalize_repo_paths(changed_files)
        touched.update(cls._extract_patch_paths(patch_summary or ""))
        if not accepted or not touched:
            return True
        return bool(accepted & touched)

    @classmethod
    def _normalize_repo_paths(cls, values: Any) -> set[str]:
        if isinstance(values, str):
            values = [values]
        if not isinstance(values, list):
            return set()
        paths: set[str] = set()
        for value in values:
            text = cls._normalize_repo_path(str(value or ""))
            if text:
                paths.add(text)
        return paths

    @staticmethod
    def _normalize_repo_path(path: str) -> str:
        text = path.strip().replace("\\", "/")
        while text.startswith("./"):
            text = text[2:]
        if text.startswith("a/") or text.startswith("b/"):
            text = text[2:]
        text = text.lstrip("/")
        source_markers = (
            "lib/",
            "src/",
            "packages/",
            "pkg/",
            "app/",
            "apps/",
            "tests/",
            "test/",
            "django/",
        )
        marker_positions = [
            (idx, marker)
            for marker in source_markers
            for idx in [text.find(marker)]
            if idx > 0
        ]
        if marker_positions:
            idx, _marker = min(marker_positions, key=lambda item: item[0])
            text = text[idx:]
        return text

    @classmethod
    def _repo_hint_paths(cls, values: Any) -> list[str]:
        if isinstance(values, str):
            values = [values]
        if not isinstance(values, list):
            return []
        paths: list[str] = []
        seen: set[str] = set()
        for value in values:
            path = cls._normalize_repo_path(str(value or ""))
            if not cls._is_repo_hint_path(path) or path in seen:
                continue
            seen.add(path)
            paths.append(path)
        return paths

    @staticmethod
    def _is_repo_hint_path(path: str) -> bool:
        text = path.strip().replace("\\", "/")
        if not text or text == "/dev/null" or any(ch.isspace() for ch in text):
            return False
        if text in {"else", "then", "fi", "done"}:
            return False
        if text.startswith(("-", "+")) or ".." in text.split("/"):
            return False
        filename = text.rsplit("/", 1)[-1]
        if "." not in filename:
            return False
        return bool(re.fullmatch(r"[A-Za-z0-9_./@+\-]+", text))

    @classmethod
    def _extract_patch_paths(cls, patch_summary: str) -> set[str]:
        paths: set[str] = set()
        for match in re.finditer(r"^diff --git\s+a/(\S+)\s+b/(\S+)", patch_summary or "", re.MULTILINE):
            paths.add(cls._normalize_repo_path(match.group(1)))
            paths.add(cls._normalize_repo_path(match.group(2)))
        for match in re.finditer(r"^(?:---|\+\+\+)\s+(?:a/|b/)?(\S+)", patch_summary or "", re.MULTILINE):
            path = cls._normalize_repo_path(match.group(1))
            if cls._is_repo_hint_path(path):
                paths.add(path)
        return {path for path in paths if cls._is_repo_hint_path(path)}

    @classmethod
    def _local_memory_solution_digest(cls, summary: dict[str, Any]) -> list[str]:
        changed = cls._repo_hint_paths(summary.get("changed_files") or [])
        patch_summary = str(summary.get("patch_summary") or "")
        patch_paths = sorted(cls._extract_patch_paths(patch_summary))
        touched = cls._dedupe_string_list(changed + patch_paths)
        lines: list[str] = []
        if touched:
            lines.append("Prior patch touched: " + ", ".join(touched[:3]))
        added_symbols = cls._extract_added_symbols_from_patch(patch_summary)
        if added_symbols:
            lines.append("Prior patch added/changed symbols: " + ", ".join(added_symbols[:6]))
        implementation_digest = cls._extract_implementation_digest_from_patch(patch_summary)
        if implementation_digest:
            lines.append(implementation_digest)
        tests = [
            str(command)
            for command in summary.get("tests_run") or []
            if cls._is_test_or_verification_command(str(command))
        ]
        if tests:
            lines.append("Prior tests recorded: " + "; ".join(tests[:3]))
        elif touched or added_symbols:
            lines.append("No test command was recorded for the prior patch.")
        return lines

    @classmethod
    def _local_memory_verified_solution_recipe(
        cls,
        summary: dict[str, Any],
        record: dict[str, Any],
    ) -> dict[str, Any] | None:
        if not cls._coding_summary_has_accepted_completion(summary):
            return None

        patch_summary = str(summary.get("patch_summary") or "")
        accepted = cls._repo_hint_paths(summary.get("accepted_targets") or [])
        changed = cls._repo_hint_paths(summary.get("changed_files") or [])
        patch_paths = sorted(cls._extract_patch_paths(patch_summary))
        primary_files = cls._dedupe_string_list(accepted + changed + patch_paths)
        if not primary_files:
            return None

        implementation_digest = cls._extract_implementation_digest_from_patch(patch_summary)
        patch_plan: list[str] = []
        if implementation_digest:
            patch_plan.extend(
                part.strip()
                for part in implementation_digest.removeprefix("Prior implementation pattern: ").split(";")
                if part.strip()
            )
        added_symbols = cls._extract_added_symbols_from_patch(patch_summary)
        if added_symbols:
            patch_plan.append("preserve or recreate symbols: " + ", ".join(added_symbols[:8]))
        if not patch_plan:
            patch_plan.append("reuse the prior accepted patch shape on the listed primary edit files")
        patch_plan = cls._dedupe_string_list(patch_plan)

        validation_commands = [
            str(command)
            for command in summary.get("tests_run") or []
            if cls._is_test_or_verification_command(str(command))
        ]
        avoid = cls._coerce_string_list(summary.get("failure_lessons") or [])
        if not avoid:
            avoid = ["Do not blindly replay stale diffs; verify current source first."]

        audit = summary.get("completion_audit") if isinstance(summary.get("completion_audit"), dict) else {}
        evidence = audit.get("evidence") if isinstance(audit.get("evidence"), dict) else {}
        gate_decision = (
            str(audit.get("gate_decision") or "").strip()
            or str(summary.get("completion_gate_decision") or "").strip()
        )
        verification: dict[str, Any] = {
            "gate_decision": gate_decision,
            "audit_status": str(audit.get("audit_status") or "").strip() or None,
            "memory_write_quality": str(audit.get("memory_write_quality") or "").strip() or None,
            "validation_after_latest_diff": evidence.get("validation_after_latest_diff"),
            "latest_diff_hash": evidence.get("latest_diff_hash"),
        }
        verification = {
            key: value
            for key, value in verification.items()
            if value not in (None, "", [])
        }

        recipe: dict[str, Any] = {
            "schema": "formsy.verified_solution_recipe.v1",
            "source_session_id": str(record.get("session_id") or "").strip(),
            "source_turn_id": str(record.get("turn_id") or "").strip(),
            "primary_edit_files": primary_files[:5],
            "patch_plan": patch_plan[:8],
            "validation_commands": cls._dedupe_string_list(validation_commands)[:5],
            "avoid": cls._dedupe_string_list(avoid)[:5],
            "verification": verification,
            "reuse_instruction": (
                "Start from this verified recipe for the same or highly similar task; "
                "verify current source before patching and only re-derive if the source contradicts it."
            ),
        }
        problem_summary = str(summary.get("problem_summary") or "").strip()
        if problem_summary:
            recipe["problem_summary"] = problem_summary[:500]
        return {key: value for key, value in recipe.items() if value not in ("", [], {})}

    @staticmethod
    def _dedupe_recipe_list(recipes: list[dict[str, Any]]) -> list[dict[str, Any]]:
        seen: set[str] = set()
        result: list[dict[str, Any]] = []
        for recipe in recipes:
            if not isinstance(recipe, dict) or not recipe:
                continue
            fingerprint = json.dumps(recipe, sort_keys=True, ensure_ascii=False)
            if fingerprint in seen:
                continue
            seen.add(fingerprint)
            result.append(recipe)
        return result

    @staticmethod
    def _extract_added_symbols_from_patch(patch_summary: str) -> list[str]:
        symbols: list[str] = []
        seen: set[str] = set()
        for match in re.finditer(r"^\+\s*(?:class|def)\s+([A-Za-z_][A-Za-z0-9_]*)", patch_summary or "", re.MULTILINE):
            symbol = match.group(1)
            if symbol not in seen:
                seen.add(symbol)
                symbols.append(symbol)
        return symbols

    @staticmethod
    def _extract_implementation_digest_from_patch(patch_summary: str) -> str:
        text = str(patch_summary or "")
        if not text:
            return ""
        notes: list[str] = []
        if "PlayIteratorRunState" in text and "PlayIteratorFailedState" in text:
            run_kind = "IntEnum" if "PlayIteratorRunState(enum.IntEnum)" in text else "public type"
            failed_kind = "IntFlag" if "PlayIteratorFailedState(enum.IntFlag)" in text else "public type"
            notes.append(
                f"define PlayIteratorRunState({run_kind}) and PlayIteratorFailedState({failed_kind})"
            )
        if re.search(r"PlayIterator\.(?:RunState|FailedState)\s*=", text):
            notes.append("expose namespaced state types on PlayIterator")
        if "_DeprecatedStateAttribute" in text or "DeprecationWarning" in text:
            notes.append("keep legacy ITERATING_*/FAILED_* compatibility through descriptor aliases")
        if ("HostState" in text or "run_state" in text) and ("__str__" in text or "_state_name" in text):
            notes.append("render HostState state names readably")
        if not notes:
            return ""
        return "Prior implementation pattern: " + "; ".join(notes)

    @staticmethod
    def _merge_memory_blocks(local_block: str, server_block: str) -> str:
        local = str(local_block or "").strip()
        server = str(server_block or "").strip()
        if not local:
            return server
        if not server:
            return local
        if local in server:
            return server
        if server in local:
            return local
        return f"{local}\n\n## Runtime Memory\n{server}"

    def _build_summary_hint(self, messages: list[dict[str, Any]]) -> str:
        parts: list[str] = []
        patch_diff = self._extract_patch_summary_from_terminal()
        if patch_diff:
            parts.append(f"Patch applied:\n{patch_diff[:1500]}")
        assistant_messages = [
            str(msg.get("content") or "").strip()
            for msg in messages
            if isinstance(msg, dict) and msg.get("role") == "assistant"
            and str(msg.get("content") or "").strip()
        ]
        if assistant_messages:
            parts.append(assistant_messages[-1][:1500])
        elif messages:
            user_messages = [
                str(msg.get("content") or "").strip()
                for msg in messages
                if isinstance(msg, dict) and msg.get("role") == "user"
                and str(msg.get("content") or "").strip()
            ]
            if user_messages:
                parts.append(user_messages[-1][:1500])
        return "\n\n".join(parts)[:3000]
