"""Formsy context engine implementation."""

import json
import logging
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse

from hermes_constants import get_hermes_home
from agent.context_engine import ContextEngine
from plugins.formsy import RuntimeClient
from .config import EngineConfigManager, EngineConfig
from .client import EngineClient

logger = logging.getLogger("formsy.context_engine")


@dataclass
class RetrievalTrace:
    """Per-run retrieval state used for gating and trace output."""

    state: str = "not_started"
    seed_calls: int = 0
    retry_calls: int = 0
    grounded_calls: int = 0
    legacy_calls: int = 0
    exploration_closed: bool = False
    accepted_targets: list[str] = field(default_factory=list)
    test_plan_files: list[str] = field(default_factory=list)
    retrieval_budget: int = 0
    blocked_tool_reason: str = ""
    contradiction_retry_used: bool = False
    contradiction_legacy_used: bool = False
    context_artifact_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "seed_calls": self.seed_calls,
            "retry_calls": self.retry_calls,
            "grounded_calls": self.grounded_calls,
            "legacy_calls": self.legacy_calls,
            "exploration_closed": self.exploration_closed,
            "accepted_targets": list(self.accepted_targets),
            "test_plan_files": list(self.test_plan_files),
            "retrieval_budget": self.retrieval_budget,
            "blocked_tool_reason": self.blocked_tool_reason,
            "contradiction_retry_used": self.contradiction_retry_used,
            "contradiction_legacy_used": self.contradiction_legacy_used,
            "context_artifact_ids": list(self.context_artifact_ids),
        }


class FormsyContextEngine(ContextEngine):
    """Formsy context engine for Hermes."""

    def __init__(self):
        self._config: Optional[EngineConfig] = None
        self._runtime_client: Optional[RuntimeClient] = None
        self._engine_client: Optional[EngineClient] = None
        self._session_id: Optional[str] = None
        self._turn_counter: int = 0
        self._context: dict[str, Any] = {}
        self._identity_snapshot: Any = None
        self._memory_manager: Any = None
        self._retrieval_trace = RetrievalTrace()
        self._retrieval_state: str = "not_started"
        self._symbolic_failures: int = 0
        self._legacy_attempted: bool = False
        self._grounded_symbols: list[str] = []
        self._grounded_files: list[str] = []
        self._last_suggested_queries: list[str] = []
        self._symbolic_retry_count: int = 0
        self._grounded_search_count: int = 0
        self._legacy_search_count: int = 0
        self._requirement_analysis: Any = None
        self._template_family: Any = None
        self._retrieval_targets: Any = None
        self._test_plan: Any = None
        self._symbolic_prompt_present: bool = False
        self._symbolic_prompt_sections: list[str] = []
        self._symbolic_prompt_missing: bool = False
        self._constraints_present: bool = False
        self._constraints_quality: str = "missing"
        self._bundle_must_edit: list[str] = []
        self._bundle_primary_files: list[str] = []
        self._direct_match_files: list[str] = []
        self._preferred_edit_targets: list[str] = []
        self._target_changed_after_grounding: bool = False
        self._target_conflict: bool = False
        self._last_retrieval_decision: dict[str, Any] = {}
        self._last_gate_failure: dict[str, Any] = {}
        self._grounded_search_required: bool = False
        self._test_plan_commands: list[str] = []
        self._memory_compiled_identity: tuple[str, str] | None = None
        self._memory_compile_revision: str = ""

        self.last_prompt_tokens = 0
        self.last_completion_tokens = 0
        self.last_total_tokens = 0
        self.threshold_tokens = 0
        self.context_length = 0
        self.compression_count = 0

    @property
    def name(self) -> str:
        return "formsy"

    async def _initialize_runtime(self, config: dict, hermes_home: Path) -> None:
        """Initialize Formsy runtime resources."""
        logger.info("Initializing formsy context engine")

        config_manager = EngineConfigManager(hermes_home)
        self._config = config_manager.load_config(config)

        self._runtime_client = RuntimeClient(
            base_url=self._config.base_url,
            memory_search_endpoint=self._config.memory_search_endpoint,
            api_key_env=self._config.api_key_env,
            api_key=self._config.api_key,
            timeout_s=self._config.timeout_s,
            max_retries=self._config.max_retries,
        )

        await self._runtime_client.__aenter__()
        self._engine_client = EngineClient(self._runtime_client)

        logger.info("Engine initialized successfully")

    def update_from_response(self, usage: dict[str, Any]) -> None:
        """Update tracked token usage from an API response."""
        self._turn_counter += 1
        self.last_prompt_tokens = int(usage.get("prompt_tokens") or 0)
        self.last_completion_tokens = int(usage.get("completion_tokens") or 0)
        self.last_total_tokens = int(usage.get("total_tokens") or 0)

    def should_compress(self, prompt_tokens: int = None) -> bool:
        """Return True when token usage exceeds the configured threshold."""
        tokens = self.last_prompt_tokens if prompt_tokens is None else prompt_tokens
        return bool(self.threshold_tokens and tokens >= self.threshold_tokens)

    def compress(
        self,
        messages: list[dict],
        current_tokens: int = None,
        focus_topic: Optional[str] = None,
    ) -> list[dict]:
        """Return messages unchanged; Formsy memory is accessed through tools."""
        if current_tokens is not None:
            self.last_prompt_tokens = current_tokens

        return messages

    def on_session_start(self, session_id: str, **kwargs) -> None:
        """Initialize runtime state when a Hermes session starts."""
        self._session_id = session_id
        self._context = dict(kwargs)
        self._context["session_id"] = session_id
        self._identity_snapshot = kwargs.get("runtime_identity_snapshot")
        self._memory_manager = kwargs.get("memory_manager")

        if self._engine_client:
            return

        hermes_home = Path(kwargs.get("hermes_home") or get_hermes_home())
        config = kwargs.get("config") if isinstance(kwargs.get("config"), dict) else {}
        self._run_async(self._initialize_runtime(config, hermes_home))

    def update_model(
        self,
        model: str,
        context_length: int,
        base_url: str = "",
        api_key: str = "",
        provider: str = "",
    ) -> None:
        """Update context-window metadata used by Hermes preflight checks."""
        super().update_model(model, context_length, base_url, api_key, provider)
        self._context.update({
            "model": model,
            "base_url": base_url,
            "provider": provider,
        })

    def on_session_end(self, session_id: str, messages: list[dict[str, Any]]) -> None:
        """Flush the session episode to the memory store, then close the HTTP client."""
        if not self._runtime_client:
            return
        self._flush_session_for_task_boundary(messages=messages)
        self._run_async(self._runtime_client.__aexit__(None, None, None))
        self._runtime_client = None
        self._engine_client = None

    def on_session_reset(self) -> None:
        """Reset per-session counters and cached context."""
        super().on_session_reset()
        self._turn_counter = 0
        self._session_id = None
        self._context = {}
        self._reset_retrieval_state()

    # Task-injection markers that indicate a fresh SWE-bench / batch task is
    # starting inside the same Hermes session.  When any of these appear in a
    # new user message the retrieval state machine must be reset so the new
    # task starts from "not_started" rather than inheriting grounding from the
    # previous task.
    _TASK_INJECTION_MARKERS = (
        "<pr_description>",
        "<instructions>",
        "<task_description>",
        "COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT",
    )

    def on_user_turn(self, user_message: str) -> None:
        """Reset retrieval state when a new task is injected into the session.

        In batch / SWE-bench runs the runner injects a new task as a user
        message into the same long-running Hermes session.  Without a reset
        the retrieval gate would still be in the grounded/exploration_closed
        state from the previous task, blocking context_search entirely.

        Before resetting, we flush the current session to the memory store so
        the episode from the just-completed task is available for the next run.
        """
        if not user_message:
            return
        # Only reset when the message looks like a fresh task injection.
        # Regular conversational follow-ups should not reset grounding.
        msg_lower = user_message[:2000].lower()
        if any(marker.lower() in msg_lower for marker in self._TASK_INJECTION_MARKERS):
            logger.debug("on_user_turn: task injection detected, flushing session then resetting retrieval state")
            self._flush_session_for_task_boundary()
            self._reset_retrieval_state()

    def _flush_session_for_task_boundary(self, messages: list[dict[str, Any]] | None = None) -> None:
        """Best-effort: call session_end on the runtime client so the current
        task's episode is written to the memory store before the next task starts.

        This makes memory hits available on repeated runs of the same case
        within a single long-running Hermes session (e.g. SWE-bench batch runs).
        Failures are swallowed — this is advisory only.
        """
        if not self._runtime_client or not self._session_id:
            return
        try:
            from plugins.formsy.models import SessionEndRequest
            config = self._config
            workspace_id = getattr(config, "workspace_id", "") if config else ""
            summary_hint = self._build_session_summary_hint(messages or [])
            request = SessionEndRequest(
                workspace_id=workspace_id or "",
                session_id=self._session_id,
                identity=self._current_runtime_identity() or None,
                summary_hint=summary_hint or None,
            )
            self._run_async(self._runtime_client.session_end(request))
            logger.debug("_flush_session_for_task_boundary: session_end flushed for session %s", self._session_id)
        except Exception as exc:
            logger.debug("_flush_session_for_task_boundary: session_end flush failed (non-fatal): %s", exc)

    @staticmethod
    def _build_session_summary_hint(messages: list[dict[str, Any]]) -> str:
        """Build a brief summary hint from the conversation for the memory store."""
        if not messages:
            return ""
        assistant_texts = [
            str(msg.get("content") or "").strip()
            for msg in messages
            if isinstance(msg, dict) and msg.get("role") == "assistant"
            and str(msg.get("content") or "").strip()
        ]
        if assistant_texts:
            return assistant_texts[-1][:2000]
        user_texts = [
            str(msg.get("content") or "").strip()
            for msg in messages
            if isinstance(msg, dict) and msg.get("role") == "user"
            and str(msg.get("content") or "").strip()
        ]
        if user_texts:
            return user_texts[-1][:2000]
        return ""

    def _try_memory_prefetch_fallback(
        self, *, query: str, repo_id: str, session_id: str
    ) -> str | None:
        """Attempt a memory-only prefetch when source compile has failed.

        Queries the in-memory store for episodes/facts from previous runs without
        requiring compiled source. Returns a JSON string on success (memory hit),
        or None if the prefetch fails or returns no useful content.
        """
        if not self._runtime_client:
            return None
        try:
            from plugins.formsy.models import MemoryPrefetchRequest
            config = self._config
            workspace_id = getattr(config, "workspace_id", "") if config else ""
            turn_id = f"prefetch-{session_id}-{self._turn_counter}"
            request = MemoryPrefetchRequest(
                workspace_id=workspace_id or "ws_default",
                session_id=session_id,
                turn_id=turn_id,
                query=query,
            )
            response = self._run_async(self._runtime_client.memory_prefetch(request))
        except Exception as exc:
            logger.debug("memory_prefetch fallback failed (non-fatal): %s", exc)
            return None

        if response is None:
            return None

        memory_block = getattr(response, "memory_block", "") or ""
        retrieved_facts = getattr(response, "retrieved_facts", None) or []
        retrieved_count = getattr(response, "retrieved_count", 0) or 0

        # Only treat as a hit if there's actual content
        if not memory_block.strip() and not retrieved_facts:
            logger.debug("memory_prefetch fallback: no useful content returned")
            return None

        logger.debug(
            "memory_prefetch fallback: hit with %d facts, %d chars of memory_block",
            retrieved_count,
            len(memory_block),
        )
        self._retrieval_state = "grounded"
        self._sync_trace_state(state=self._retrieval_state)
        return json.dumps({
            "ok": True,
            "query": query,
            "repo_id": repo_id,
            "source": "memory_prefetch_fallback",
            "memory_block": memory_block,
            "retrieved_count": retrieved_count,
            "retrieved_facts": [
                (f.model_dump() if hasattr(f, "model_dump") else dict(f))
                for f in retrieved_facts
            ],
            "coverage": "memory_only",
            "note": (
                "Source compile was unavailable; results are from the memory store only "
                "(episodes and facts from previous runs). Use these to guide your approach "
                "without re-running the full exploration flow."
            ),
        })

    def get_tool_schemas(self) -> list[dict[str, Any]]:
        """Expose Formsy memory/context search to the agent."""
        return [
            {
                "name": "context_search",
                "description": (
                    "Search Formsy's compiled code memory/context for information "
                    "relevant to a natural-language query. Use context_search proactively "
                    "and repeatedly to understand the codebase faster. Prefer several "
                    "targeted queries, such as symbols, file paths, PR behavior, call flow, "
                    "and edge cases, over one broad query. Treat this tool as mandatory for "
                    "retrieval: after a seed search, continue only if matches is non-empty "
                    "and coverage is not poor, or rerun with grounded/legacy metadata. When "
                    "context_search returns a candidate file or span, use context_read next "
                    "instead of shell grep/find or direct file reads. The memory compile step "
                    "has already completed before the task starts, so this tool is ready to "
                    "use immediately. Repository identity is derived from the current git "
                    "remote URL and commit; do not provide repo_id or revision."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "Natural-language query describing the code, behavior, or fact to find.",
                        },
                        "budget": {
                            "type": "integer",
                            "description": "Context token budget for the Formsy query.",
                            "default": 4000,
                            "minimum": 1,
                        },
                        "limit": {
                            "type": "integer",
                            "description": "Deprecated. Kept for compatibility; Formsy query uses budget instead.",
                            "default": 5,
                            "minimum": 1,
                            "maximum": 20,
                        },
                        "metadata": {
                            "type": "object",
                            "description": "Optional server-side query metadata used to control retrieval mode and grounding phase.",
                            "properties": {
                                "retrieval_mode": {
                                    "type": "string",
                                    "enum": ["symbolic", "legacy"],
                                    "description": "Select the retrieval strategy used by the server-side query API.",
                                },
                                "grounding_phase": {
                                    "type": "string",
                                    "enum": ["seed", "grounded", "fallback"],
                                    "description": "Indicate whether this query is part of seed grounding or grounded verification.",
                                },
                                "response_format": {
                                    "type": "string",
                                    "enum": ["bundle", "legacy"],
                                    "description": "Choose the response envelope expected from the query API.",
                                },
                                "trace_id": {
                                    "type": "string",
                                    "description": "Optional trace identifier for correlating related query calls.",
                                },
                                "case_id": {
                                    "type": "string",
                                    "description": "Optional case identifier for E2E runs.",
                                },
                                "grounded_symbols": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                    "description": "Optional grounded evidence symbols returned or confirmed by prior inspection.",
                                },
                                "grounded_files": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                    "description": "Optional grounded evidence file paths returned or confirmed by prior inspection.",
                                },
                                "retrieval_feedback": {
                                    "type": "string",
                                    "description": "Optional feedback about retrieval quality or contradictions to carry into the next query.",
                                },
                            },
                        },
                    },
                    "required": ["query"],
                },
            },
            {
                "name": "context_read",
                "description": (
                    "Read exact source context from Formsy's compiled repository memory. "
                    "Use context_read after context_search returns a relevant file path or "
                    "line range. This is the preferred way to inspect source code for "
                    "SWE-bench tasks when direct file-content reads are discouraged."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "Repository-relative file path to read.",
                        },
                        "start_line": {
                            "type": "integer",
                            "description": "Optional 1-indexed first source line to read.",
                            "minimum": 1,
                        },
                        "end_line": {
                            "type": "integer",
                            "description": "Optional inclusive 1-indexed last source line to read.",
                            "minimum": 1,
                        },
                    },
                    "required": ["path"],
                },
            },
        ]

    def handle_tool_call(self, name: str, args: dict[str, Any], **kwargs) -> str:
        """Handle Formsy context-engine tool calls."""
        if name == "context_read":
            return self._handle_memory_read(args)
        if name != "context_search":
            return super().handle_tool_call(name, args, **kwargs)

        query = str(args.get("query") or "").strip()
        if not query:
            return json.dumps({"ok": False, "error": "context_search requires a non-empty query"})

        if not self._engine_client:
            return json.dumps({"ok": False, "query": query, "error": "Formsy engine client is not initialized"})

        session_id = self._session_id or self._context.get("session_id") or "unknown"
        repo_id, revision = self._resolve_repository_identity()
        identity = self._current_runtime_identity()
        if not repo_id:
            return json.dumps({
                "ok": False,
                "query": query,
                "error": "context_search could not infer repo_id from the current git remote.",
            })
        if not self._ensure_memory_compiled(repo_id=repo_id, revision=revision, query=query, session_id=session_id):
            # Source compile failed — attempt a memory-only prefetch before falling back to
            # degraded_recovery. This surfaces episodes/facts from previous runs without
            # requiring compiled source, enabling a memory hit on repeated tasks.
            memory_hit = self._try_memory_prefetch_fallback(
                query=query, repo_id=repo_id, session_id=session_id
            )
            if memory_hit is not None:
                return memory_hit
            self._retrieval_state = "degraded_recovery"
            self._sync_trace_state(state=self._retrieval_state)
            return json.dumps({
                "ok": False,
                "query": query,
                "repo_id": repo_id,
                "revision": revision,
                "error": "Formsy memory compile failed before context_search",
                "recovery_mode": "degraded_recovery",
                "preferred_next_step": "bounded_shell_inspection",
                "allowed_tools": ["terminal", "read_file", "search_files"],
                "retrieval_feedback": (
                    "Memory compile failed. Falling back to bounded shell inspection. "
                    "Use terminal/read_file/search_files to locate relevant files directly."
                ),
            })
        revision = self._memory_compile_revision or revision
        budget = self._coerce_positive_int(args.get("budget"), self._config.query_budget if self._config else 4000)
        self._retrieval_trace.retrieval_budget = budget
        metadata = self._build_query_metadata(args, repo_id=repo_id, session_id=session_id)
        self._merge_memory_hints(metadata)
        phase = str(metadata.get("grounding_phase") or "").strip().lower()
        if phase == "grounded":
            if self._grounded_files and not metadata.get("grounded_files"):
                metadata["grounded_files"] = list(self._grounded_files)
            if self._grounded_symbols and not metadata.get("grounded_symbols"):
                metadata["grounded_symbols"] = list(self._grounded_symbols)
            for key, value in (
                ("requirement_analysis", self._requirement_analysis),
                ("template_family", self._template_family),
                ("retrieval_targets", self._retrieval_targets),
                ("test_plan", self._test_plan),
            ):
                if value is not None and not metadata.get(key):
                    metadata[key] = value
        result = self._run_async(
            self._engine_client.memory_search(
                repo_id=repo_id,
                session_id=session_id,
                query=query,
                revision=revision,
                budget=budget,
                metadata=metadata,
                **({"identity": identity} if identity else {}),
            )
        )
        if result is None:
            return json.dumps({"ok": False, "query": query, "error": "Formsy context search failed"})

        payload: dict[str, Any] = {
            "ok": True,
            "query": query,
            "extra_context": self._extract_extra_context(result),
            "retrieval_budget": budget,
        }
        for key in (
            "symbolic_prompt",
            "matches",
            "suggested_queries",
            "coverage",
            "missing_context",
            "diagnostics",
            "test_plan",
            "requirement_analysis",
            "template_family",
            "retrieval_targets",
            "bundle",
            "context_package",
            "grounded_symbols",
            "grounded_files",
            "retrieval_feedback",
            "retrieval_state",
            "preferred_next_step",
            "accepted_targets",
            "exploration_closed",
            "blocked_tool_reason",
        ):
            if key in result:
                payload[key] = result[key]
        coverage = str(payload.get("coverage") or "").strip().lower()
        matches = payload.get("matches")
        payload["direct_match_files"] = self._extract_match_files(matches)
        payload["bundle_primary_files"] = self._extract_bundle_primary_files(result.get("bundle"))
        payload["bundle_must_edit"] = self._extract_bundle_must_edit(result.get("bundle"))
        self._collect_context_artifact_ids(result.get("artifacts"))
        bundle = result.get("bundle")
        if isinstance(bundle, dict):
            bundle_id = str(bundle.get("bundle_id") or "").strip()
            if bundle_id:
                self._collect_context_artifact_ids([bundle_id])
        self._record_context_search_result(
            query=query,
            metadata=metadata,
            coverage=coverage,
            matches=matches,
            payload=payload,
        )
        return json.dumps(payload)

    def _handle_memory_read(self, args: dict[str, Any]) -> str:
        path = str(args.get("path") or "").strip()
        if not path:
            return json.dumps({"ok": False, "error": "context_read requires a non-empty path"})
        if not self._is_context_read_allowed(path):
            return json.dumps({
                "ok": False,
                "path": path,
                "error": (
                    "context_read is limited to accepted targets or test-plan files "
                    "after a grounded target has been accepted."
                ),
            })

        if not self._engine_client:
            return json.dumps({"ok": False, "path": path, "error": "Formsy engine client is not initialized"})

        session_id = self._session_id or self._context.get("session_id") or "unknown"
        repo_id, revision = self._resolve_repository_identity()
        identity = self._current_runtime_identity()
        if not repo_id:
            return json.dumps({
                "ok": False,
                "path": path,
                "error": "context_read could not infer repo_id from the current git remote.",
            })
        start_line = self._optional_positive_int(args.get("start_line"))
        end_line = self._optional_positive_int(args.get("end_line"))
        result = self._run_async(
            self._engine_client.memory_read(
                repo_id=repo_id,
                session_id=session_id,
                path=path,
                revision=revision,
                start_line=start_line,
                end_line=end_line,
                **({"identity": identity} if identity else {}),
            )
        )
        if result is None:
            return json.dumps({"ok": False, "path": path, "error": "Formsy context read failed"})

        self._collect_context_artifact_ids(result.get("artifacts") if isinstance(result, dict) else None)
        self._record_context_read(path, result)
        return self._format_memory_read_result(path, result)

    @staticmethod
    def _format_memory_read_result(requested_path: str, result: Any) -> str:
        if not isinstance(result, dict):
            return json.dumps({"ok": True, "path": requested_path, "result": result})

        path = str(result.get("path") or requested_path)
        content = str(result.get("content") or "")
        start_line = result.get("start_line")
        end_line = result.get("end_line")
        total_lines = result.get("total_lines")
        truncated = bool(result.get("truncated", False))

        line_label = "unknown"
        if start_line is not None and end_line is not None:
            line_label = f"{start_line}-{end_line}"
        elif start_line is not None:
            line_label = f"{start_line}+"

        metadata = [
            "ok: true",
            f"path: {path}",
            f"lines: {line_label}",
        ]
        if total_lines is not None:
            metadata.append(f"total_lines: {total_lines}")
        if truncated:
            metadata.append("truncated: true")
        metadata.append("")
        metadata.append("```python")
        metadata.append(content)
        metadata.append("```")
        return "\n".join(metadata)

    def _run_async(self, coro):
        """Run Formsy async API calls from the synchronous ContextEngine API."""
        try:
            from model_tools import _run_async
            return _run_async(coro)
        except Exception:
            logger.exception("Formsy async call failed")
            return None

    @staticmethod
    def _coerce_positive_int(value: Any, default: int) -> int:
        try:
            number = int(value)
        except (TypeError, ValueError):
            number = default
        return max(1, number)

    @staticmethod
    def _optional_positive_int(value: Any) -> int | None:
        if value is None:
            return None
        try:
            number = int(value)
        except (TypeError, ValueError):
            return None
        return max(1, number)

    def _resolve_repository_identity(self) -> tuple[str, str]:
        if self._identity_snapshot is not None:
            repo_id = str(getattr(self._identity_snapshot, "repo_id", "") or "").strip()
            revision = str(getattr(self._identity_snapshot, "revision", "") or "").strip()
            if repo_id:
                return repo_id, revision or "latest"
        repo_id = ""
        revision = ""
        remote_url = self._git_output(["git", "remote", "get-url", "origin"])
        if remote_url:
            repo_id = self._repo_id_from_git_url(remote_url)
        revision = self._git_output(["git", "rev-parse", "HEAD"])
        if not repo_id and self._config:
            repo_id = str(self._config.repo_id or "").strip()
        if not revision and self._config:
            revision = str(self._config.revision or "").strip()
        if self._identity_snapshot is not None:
            if repo_id:
                self._identity_snapshot.repo_id = repo_id
                if hasattr(self._identity_snapshot, "set_source"):
                    self._identity_snapshot.set_source("repo_id", "context_engine_fallback")
                if hasattr(self._identity_snapshot, "clear_limited"):
                    self._identity_snapshot.clear_limited("missing_repo_id")
            if revision:
                self._identity_snapshot.revision = revision
                if hasattr(self._identity_snapshot, "set_source"):
                    self._identity_snapshot.set_source("revision", "context_engine_fallback")
                if hasattr(self._identity_snapshot, "clear_limited"):
                    self._identity_snapshot.clear_limited("revision_unknown")
        return repo_id, revision or "latest"

    def _current_runtime_identity(self) -> dict[str, Any]:
        if self._identity_snapshot is not None:
            identity_fn = getattr(self._identity_snapshot, "to_runtime_identity", None)
            if callable(identity_fn):
                return dict(identity_fn())
        return {}

    def _ensure_memory_compiled(self, *, repo_id: str, revision: str, query: str, session_id: str) -> bool:
        identity_key = (repo_id, revision)
        if self._memory_compiled_identity == identity_key:
            return True
        if not self._engine_client or not hasattr(self._engine_client, "compile_repo"):
            return True

        files = self._collect_memory_source_files(Path.cwd())
        result = self._run_async(
            self._engine_client.compile_repo(
                repo_id=repo_id,
                files=files,
                revision=revision,
                metadata={
                    "instance_id": repo_id,
                    "query": query,
                    "source_file_count": len(files),
                },
                session_id=session_id,
            )
        )
        if result is None:
            return False

        self._memory_compiled_identity = identity_key
        self._memory_compile_revision = str(
            result.get("revision") if isinstance(result, dict) else ""
            or revision
        )
        return True

    @staticmethod
    def _collect_memory_source_files(root: Path) -> list[dict[str, Any]]:
        allowed_suffixes = {
            ".py",
            ".js",
            ".jsx",
            ".ts",
            ".tsx",
            ".java",
            ".go",
            ".rs",
            ".c",
            ".cc",
            ".cpp",
            ".h",
            ".hpp",
            ".md",
            ".toml",
            ".yaml",
            ".yml",
            ".json",
        }
        excluded_parts = {
            ".git",
            ".venv",
            "venv",
            "node_modules",
            "__pycache__",
            ".pytest_cache",
            ".mypy_cache",
            "dist",
            "build",
            "runs",
            # Low-value directories for context search — skip to keep payload small
            "docs",
            "migrations",
            "locale",
            "fixtures",
        }
        # Hard caps to prevent server overload on large repos.
        # Non-test source files are collected first (higher priority), then test
        # files are appended if budget remains.
        MAX_COMPILE_FILES = 500
        MAX_COMPILE_BYTES = 4 * 1024 * 1024  # 4 MB

        def _is_test(rel: str) -> bool:
            return (
                rel.startswith("tests/")
                or rel.startswith("test/")
                or "/tests/" in f"/{rel}/"
                or rel.endswith("_test.py")
                or rel.endswith(".test.js")
                or rel.endswith(".test.ts")
            )

        source_files: list[dict[str, Any]] = []
        test_files: list[dict[str, Any]] = []
        try:
            paths = list(root.rglob("*"))
        except Exception:
            return []
        for path in paths:
            try:
                if not path.is_file() or path.suffix.lower() not in allowed_suffixes:
                    continue
                if any(part in excluded_parts for part in path.relative_to(root).parts):
                    continue
                rel = path.relative_to(root).as_posix()
                content = path.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue
            entry = {
                "path": rel,
                "content": content,
                "language": path.suffix.lower().lstrip(".") or "text",
                "is_test": _is_test(rel),
            }
            if entry["is_test"]:
                test_files.append(entry)
            else:
                source_files.append(entry)

        # Fill quota with source files first, then test files.
        files: list[dict[str, Any]] = []
        total_bytes = 0
        for entry in source_files:
            if len(files) >= MAX_COMPILE_FILES or total_bytes >= MAX_COMPILE_BYTES:
                break
            files.append(entry)
            total_bytes += len(entry["content"])
        for entry in test_files:
            if len(files) >= MAX_COMPILE_FILES or total_bytes >= MAX_COMPILE_BYTES:
                break
            files.append(entry)
            total_bytes += len(entry["content"])
        return files

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
    def _repo_id_from_git_url(remote_url: str) -> str:
        value = remote_url.strip()
        if not value:
            return ""

        path = ""
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
        owner, repo = parts[-2], parts[-1]
        if not owner or not repo:
            return ""
        return f"{owner}__{repo}"

    @staticmethod
    def _extract_extra_context(result: Any) -> str:
        if not isinstance(result, dict):
            return ""
        extra = result.get("extra_context")
        if isinstance(extra, str):
            return extra
        memory_block = result.get("memory_block")
        if isinstance(memory_block, str):
            return memory_block
        return ""

    @staticmethod
    def _build_query_metadata(
        args: dict[str, Any],
        *,
        repo_id: str = "",
        session_id: str = "",
    ) -> dict[str, Any]:
        metadata: dict[str, Any] = {}
        for key in (
            "retrieval_mode",
            "grounding_phase",
            "response_format",
            "trace_id",
            "case_id",
            "grounded_symbols",
            "grounded_files",
            "retrieval_feedback",
        ):
            value = args.get(key)
            if value is not None and value != "":
                metadata[key] = value
        supplied = args.get("metadata")
        if isinstance(supplied, dict):
            metadata.update(supplied)
        metadata.setdefault("retrieval_mode", "symbolic")
        metadata.setdefault("grounding_phase", "seed")
        metadata.setdefault("response_format", "bundle")
        if repo_id:
            metadata.setdefault("case_id", repo_id)
        if session_id and session_id != "unknown":
            metadata.setdefault("trace_id", session_id)
        return metadata

    def _merge_memory_hints(self, metadata: dict[str, Any]) -> None:
        manager = self._memory_manager or self._context.get("memory_manager")
        if manager is None or not hasattr(manager, "providers"):
            return
        for provider in manager.providers:
            if not hasattr(provider, "get_context_hints"):
                continue
            try:
                hints = provider.get_context_hints()
            except Exception:
                logger.debug("memory provider get_context_hints failed", exc_info=True)
                continue
            if not isinstance(hints, dict):
                continue
            for key in (
                "memory_artifact_ids",
                "memory_query_hints",
                "memory_test_hints",
                "memory_status",
                "memory_freshness",
            ):
                value = hints.get(key)
                if value in (None, "", []):
                    continue
                if isinstance(value, list):
                    existing = metadata.get(key)
                    if isinstance(existing, list):
                        metadata[key] = self._dedupe_string_list(existing + value)
                    else:
                        metadata[key] = self._dedupe_string_list(value)
                else:
                    metadata.setdefault(key, value)

    @staticmethod
    def _dedupe_string_list(values: list[Any]) -> list[str]:
        seen: set[str] = set()
        result: list[str] = []
        for value in values:
            text = str(value or "").strip()
            if not text or text in seen:
                continue
            seen.add(text)
            result.append(text)
        return result

    def _reset_retrieval_state(self) -> None:
        self._retrieval_trace = RetrievalTrace()
        self._retrieval_state = "not_started"
        self._symbolic_failures = 0
        self._symbolic_retry_count = 0
        self._grounded_search_count = 0
        self._legacy_search_count = 0
        self._legacy_attempted = False
        self._grounded_symbols = []
        self._grounded_files = []
        self._last_suggested_queries = []
        self._requirement_analysis = None
        self._template_family = None
        self._retrieval_targets = None
        self._test_plan = None
        self._symbolic_prompt_present = False
        self._symbolic_prompt_sections = []
        self._symbolic_prompt_missing = False
        self._constraints_present = False
        self._constraints_quality = "missing"
        self._bundle_must_edit = []
        self._bundle_primary_files = []
        self._direct_match_files = []
        self._preferred_edit_targets = []
        self._target_changed_after_grounding = False
        self._target_conflict = False
        self._last_retrieval_decision = {}
        self._last_gate_failure = {}
        self._grounded_search_required = False
        self._test_plan_commands = []
        # NOTE: _memory_compiled_identity and _memory_compile_revision are intentionally
        # NOT reset here. The compiled repo remains valid on the server for the same
        # (repo_id, revision) pair across task boundaries within the same session.

    @staticmethod
    def _has_useful_matches(matches: Any) -> bool:
        return isinstance(matches, list) and len(matches) > 0

    @staticmethod
    def _coerce_string_list(value: Any) -> list[str]:
        if isinstance(value, str):
            text = value.strip()
            return [text] if text else []
        if not isinstance(value, list):
            return []
        items: list[str] = []
        for item in value:
            text = str(item).strip()
            if text and text not in items:
                items.append(text)
        return items

    def _record_context_search_result(
        self,
        *,
        query: str,
        metadata: dict[str, Any],
        coverage: str,
        matches: Any,
        payload: dict[str, Any],
    ) -> None:
        mode = str(metadata.get("retrieval_mode") or "symbolic").strip().lower()
        phase = str(metadata.get("grounding_phase") or "seed").strip().lower()
        server_state = str(payload.get("retrieval_state") or "").strip().lower()
        server_preferred_next = str(payload.get("preferred_next_step") or "").strip()
        server_accepted_targets = self._coerce_string_list(payload.get("accepted_targets"))
        server_exploration_closed = bool(payload.get("exploration_closed"))
        server_blocked_reason = str(payload.get("blocked_tool_reason") or "").strip()
        suggested_queries = payload.get("suggested_queries")
        if isinstance(suggested_queries, list):
            self._last_suggested_queries = [str(query) for query in suggested_queries if str(query).strip()]
        else:
            suggested_queries = []
        self._symbolic_prompt_present = bool(str(payload.get("symbolic_prompt") or "").strip())
        if self._symbolic_prompt_present:
            self._symbolic_prompt_sections = self._extract_symbolic_prompt_sections(
                str(payload.get("symbolic_prompt") or "")
            )
            self._symbolic_prompt_missing = False
        else:
            self._symbolic_prompt_sections = []
            self._symbolic_prompt_missing = mode == "symbolic"
        self._constraints_present = "Constraints" in self._symbolic_prompt_sections
        self._constraints_quality = self._classify_constraints_quality(str(payload.get("symbolic_prompt") or ""))
        useful = coverage != "poor" and self._has_useful_matches(matches)
        legacy_useful = coverage != "poor" and (self._has_useful_matches(matches) or bool(payload.get("extra_context")))
        grounded_symbols = (
            self._coerce_string_list(payload.get("grounded_symbols"))
            or self._coerce_string_list(metadata.get("grounded_symbols"))
        )
        grounded_files = (
            self._coerce_string_list(payload.get("grounded_files"))
            or self._coerce_string_list(metadata.get("grounded_files"))
        )
        has_grounded_evidence = bool(
            grounded_symbols
            or grounded_files
            or server_accepted_targets
            or self._grounded_symbols
            or self._grounded_files
            or self._direct_match_files
            or self._bundle_primary_files
            or self._bundle_must_edit
        )
        previous_targets = list(self._preferred_edit_targets or self._grounded_files or [])
        test_plan_files = self._extract_test_plan_files(payload.get("test_plan") or self._test_plan)
        if test_plan_files:
            self._retrieval_trace.test_plan_files = list(test_plan_files)
        self._direct_match_files = self._extract_match_files(matches)
        self._bundle_must_edit = self._extract_bundle_must_edit(payload.get("bundle"))
        self._bundle_primary_files = self._extract_bundle_primary_files(payload.get("bundle"))
        candidate_targets = (
            server_accepted_targets
            if server_exploration_closed and server_accepted_targets
            else grounded_files
            or list(self._grounded_files)
            or list(self._direct_match_files)
            or list(self._bundle_primary_files)
            or (
                [str(target) for target in payload.get("retrieval_targets") if str(target).strip()]
                if isinstance(payload.get("retrieval_targets"), list) else (
                    [str(target) for target in self._retrieval_targets if str(target).strip()]
                    if isinstance(self._retrieval_targets, list) else []
                )
            )
            or list(self._bundle_must_edit)
        )
        stronger_target_evidence = bool(
            server_accepted_targets
            or grounded_files
            or self._grounded_files
            or self._direct_match_files
            or self._bundle_primary_files
        )
        candidate_target_conflict = bool(
            self._bundle_must_edit
            and candidate_targets
            and not stronger_target_evidence
            and set(self._bundle_must_edit) != set(candidate_targets)
        )
        candidate_target_changed = (
            phase == "grounded"
            and bool(previous_targets)
            and set(previous_targets) != set(candidate_targets)
        )
        if server_exploration_closed and server_accepted_targets:
            candidate_target_conflict = False
            candidate_target_changed = False
        contradiction_found = (
            candidate_target_conflict
            or candidate_target_changed
            or self._has_contradiction(payload, metadata)
        )
        grounded_useful = bool(
            coverage != "poor"
            and has_grounded_evidence
            and candidate_targets
        )
        compile_missing = self._is_compile_missing(payload)
        is_first_symbolic_seed = mode != "legacy" and phase != "grounded" and self._retrieval_trace.seed_calls == 0

        if mode == "legacy":
            self._legacy_search_count += 1
            self._legacy_attempted = True
            self._retrieval_trace.legacy_calls += 1
            if legacy_useful:
                self._retrieval_state = "legacy_fallback"
                preferred_next = "direct_inspection"
                if not self._retrieval_trace.accepted_targets:
                    self._set_accepted_targets(
                        candidate_targets,
                        test_plan_files=test_plan_files,
                    )
            else:
                self._retrieval_state = "degraded_recovery"
                preferred_next = "bounded_shell_inspection"
        elif phase == "grounded":
            self._grounded_search_count += 1
            self._retrieval_trace.grounded_calls += 1
            if grounded_useful and not contradiction_found:
                self._grounded_symbols = grounded_symbols or list(self._grounded_symbols)
                self._grounded_files = grounded_files or list(self._grounded_files)
                self._retrieval_state = "grounded"
                preferred_next = "edit"
                self._set_accepted_targets(
                    candidate_targets,
                    test_plan_files=test_plan_files,
                )
                self._retrieval_trace.contradiction_retry_used = False
                self._retrieval_trace.contradiction_legacy_used = False
            elif contradiction_found and not self._retrieval_trace.contradiction_retry_used:
                self._retrieval_state = "grounded_retry"
                self._retrieval_trace.contradiction_retry_used = True
                preferred_next = "context_search"
            elif contradiction_found and not self._retrieval_trace.contradiction_legacy_used:
                self._retrieval_state = "legacy_fallback"
                self._retrieval_trace.contradiction_legacy_used = True
                preferred_next = "direct_inspection"
            elif self._grounded_files or self._grounded_symbols:
                self._retrieval_state = "degraded_recovery"
                preferred_next = "bounded_shell_inspection"
            else:
                self._retrieval_state = "grounded_retry"
                preferred_next = "context_search"
        elif useful:
            if is_first_symbolic_seed:
                self._retrieval_trace.seed_calls += 1
            else:
                self._retrieval_trace.retry_calls += 1
            self._symbolic_retry_count += 1
            self._retrieval_state = "inspect_candidates"
            preferred_next = "context_read"
        elif compile_missing:
            self._symbolic_failures += 1
            self._symbolic_retry_count += 1
            if is_first_symbolic_seed:
                self._retrieval_trace.seed_calls += 1
            else:
                self._retrieval_trace.retry_calls += 1
            self._retrieval_state = "degraded_recovery"
            preferred_next = "bounded_shell_inspection"
        else:
            self._symbolic_failures += 1
            self._symbolic_retry_count += 1
            if is_first_symbolic_seed:
                self._retrieval_trace.seed_calls += 1
            else:
                self._retrieval_trace.retry_calls += 1
            self._retrieval_state = "retry"
            preferred_next = "context_search"

        for key in ("requirement_analysis", "template_family", "retrieval_targets", "test_plan"):
            value = payload.get(key)
            if value is not None:
                setattr(self, f"_{key}", value)
        self._test_plan_commands = self._extract_test_plan_commands(self._test_plan)
        self._preferred_edit_targets = self._select_preferred_edit_targets()
        self._target_conflict = candidate_target_conflict
        self._target_changed_after_grounding = candidate_target_changed
        if server_exploration_closed and server_accepted_targets:
            self._set_accepted_targets(server_accepted_targets, test_plan_files=test_plan_files)
            self._grounded_files = list(server_accepted_targets)
            if server_state:
                self._retrieval_state = server_state
            if server_preferred_next:
                preferred_next = server_preferred_next
            elif self._retrieval_state == "grounded":
                preferred_next = "edit"
            if server_blocked_reason:
                self._retrieval_trace.blocked_tool_reason = server_blocked_reason
            self._preferred_edit_targets = self._select_preferred_edit_targets()
        self._sync_trace_state(state=self._retrieval_state)

        next_retrieval = self._next_retrieval_hint(metadata=metadata, payload=payload)
        payload["retrieval_state"] = self._retrieval_state
        payload["preferred_next_step"] = preferred_next
        payload["retrieval_budget"] = self._retrieval_trace.retrieval_budget
        payload["accepted_targets"] = list(self._retrieval_trace.accepted_targets)
        payload["exploration_closed"] = self._retrieval_trace.exploration_closed
        if next_retrieval:
            payload["next_retrieval"] = next_retrieval
        payload["retrieval_decision"] = {
            "query": query,
            "retrieval_mode": mode,
            "grounding_phase": phase,
            "retrieval_budget": self._retrieval_trace.retrieval_budget,
            "coverage": coverage or None,
            "matches_count": len(matches) if isinstance(matches, list) else 0,
            "symbolic_failures": self._symbolic_failures,
            "symbolic_retry_count": self._symbolic_retry_count,
            "grounded_search_count": self._grounded_search_count,
            "legacy_search_count": self._legacy_search_count,
            "seed_calls": self._retrieval_trace.seed_calls,
            "retry_calls": self._retrieval_trace.retry_calls,
            "grounded_calls": self._retrieval_trace.grounded_calls,
            "legacy_calls": self._retrieval_trace.legacy_calls,
            "legacy_attempted": self._legacy_attempted,
            "symbolic_prompt_present": self._symbolic_prompt_present,
            "symbolic_prompt_sections": list(self._symbolic_prompt_sections),
            "constraints_present": self._constraints_present,
            "constraints_quality": self._constraints_quality,
            "direct_match_files": list(self._direct_match_files),
            "bundle_primary_files": list(self._bundle_primary_files),
            "bundle_must_edit": list(self._bundle_must_edit),
            "preferred_edit_targets": list(self._preferred_edit_targets),
            "accepted_targets": list(self._retrieval_trace.accepted_targets),
            "exploration_closed": self._retrieval_trace.exploration_closed,
            "blocked_tool_reason": self._retrieval_trace.blocked_tool_reason,
            "target_conflict": self._target_conflict,
            "target_changed_after_grounding": self._target_changed_after_grounding,
            "contradiction_found": contradiction_found,
            "decision": self._retrieval_state,
            "reason": self._retrieval_reason(payload),
        }
        if next_retrieval:
            payload["retrieval_decision"]["next_retrieval"] = next_retrieval
        self._last_retrieval_decision = dict(payload["retrieval_decision"])

    def _next_retrieval_hint(self, *, metadata: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any] | None:
        if self._retrieval_state == "retry":
            if self._retrieval_trace.exploration_closed:
                if self._retrieval_trace.contradiction_retry_used and not self._retrieval_trace.contradiction_legacy_used:
                    return {
                        "retrieval_mode": "legacy",
                        "grounding_phase": "fallback",
                        "response_format": "bundle",
                        "retrieval_feedback": "Contradiction found after grounded acceptance; fall back to legacy retrieval once.",
                    }
                if self._retrieval_trace.contradiction_retry_used and self._retrieval_trace.contradiction_legacy_used:
                    return {
                        "recovery_mode": "degraded_recovery",
                        "preferred_next_step": "bounded_shell_inspection",
                        "allowed_tools": ["terminal", "read_file"],
                    }
                return None
            if self._symbolic_failures >= 2:
                return {
                    "retrieval_mode": "legacy",
                    "grounding_phase": "fallback",
                    "response_format": "bundle",
                    "retrieval_feedback": "Symbolic seed searches returned no matches or poor coverage.",
                }
            suggested_queries = payload.get("suggested_queries")
            if isinstance(suggested_queries, list) and suggested_queries:
                query = next((str(item) for item in suggested_queries if str(item).strip()), None)
                if query and "compile the repository before querying" not in query.lower():
                    return {
                        "query": query,
                        "retrieval_mode": "symbolic",
                        "grounding_phase": "seed",
                        "response_format": "bundle",
                    }
            return {
                "retrieval_mode": "symbolic",
                "grounding_phase": "seed",
                "response_format": "bundle",
            }
        if self._retrieval_state == "context_read":
            return {
                "query": "confirm grounded source details",
                "retrieval_mode": "symbolic",
                "grounding_phase": "grounded",
                "response_format": "bundle",
                "grounded_symbols": list(self._grounded_symbols),
                "grounded_files": list(self._grounded_files),
                "requirement_analysis": self._requirement_analysis,
                "template_family": self._template_family,
                "retrieval_targets": self._retrieval_targets,
                "test_plan": self._test_plan,
            }
        if self._retrieval_state == "grounded_retry":
            return {
                "retrieval_mode": str(metadata.get("retrieval_mode") or "symbolic"),
                "grounding_phase": str(metadata.get("grounding_phase") or "seed"),
                "response_format": "bundle",
                "requirement_analysis": self._requirement_analysis,
                "template_family": self._template_family,
                "retrieval_targets": self._retrieval_targets,
                "test_plan": self._test_plan,
                "retrieval_feedback": self._retrieval_reason(payload),
            }
        if self._retrieval_state == "degraded_recovery":
            return {
                "recovery_mode": "degraded_recovery",
                "preferred_next_step": "bounded_shell_inspection",
                "allowed_tools": ["terminal", "read_file", "search_files"],
                "retrieval_feedback": (
                    "Symbolic and legacy retrieval returned weak context. "
                    "Bounded shell inspection is allowed, but editing remains low-confidence "
                    "until a file or symbol is grounded."
                ),
            }
        return None

    @staticmethod
    def _retrieval_reason(payload: dict[str, Any]) -> str:
        missing = payload.get("missing_context")
        if isinstance(missing, list) and missing:
            return str(missing[0])
        if payload.get("coverage") == "poor":
            return "coverage is poor"
        matches = payload.get("matches")
        if not matches:
            return "no matches returned"
        return "retrieval result accepted"

    @staticmethod
    def _is_compile_missing(payload: dict[str, Any]) -> bool:
        missing = payload.get("missing_context")
        if isinstance(missing, list):
            text = " ".join(str(item) for item in missing).lower()
            if "compiled repository not found" in text:
                return True
        return False

    def _record_context_read(self, requested_path: str, result: Any) -> None:
        if isinstance(result, dict):
            path = str(result.get("path") or requested_path)
        else:
            path = requested_path
        norm = path.lstrip("./") if path.startswith("./") else path
        if path and path not in self._grounded_files:
            self._grounded_files.append(path)
        # If the agent read a test file, track it in test_plan_files regardless
        # of how grounding happened (server test_plan may be absent in degraded_recovery).
        if norm.startswith("tests/") or norm.startswith("test_"):
            if path not in self._retrieval_trace.test_plan_files:
                self._retrieval_trace.test_plan_files.append(path)
        if self._retrieval_state == "degraded_recovery":
            self._retrieval_state = "grounded"
            self._set_accepted_targets([path])
            self._grounded_search_required = False
        else:
            self._retrieval_state = "context_read"
            self._grounded_search_required = True
        self._sync_trace_state(state=self._retrieval_state)
        self._last_retrieval_decision = {
            "decision": self._retrieval_state,
            "grounded_files": list(self._grounded_files),
            "accepted_targets": list(self._retrieval_trace.accepted_targets),
            "exploration_closed": self._retrieval_trace.exploration_closed,
            "retrieval_budget": self._retrieval_trace.retrieval_budget,
        }
        if self._retrieval_state == "context_read":
            self._last_retrieval_decision["next_state"] = "grounded_search"
            self._last_retrieval_decision["next_retrieval"] = {
                "query": "confirm grounded source details",
                "retrieval_mode": "symbolic",
                "grounding_phase": "grounded",
                "response_format": "bundle",
                "grounded_symbols": list(self._grounded_symbols),
                "grounded_files": list(self._grounded_files),
                "requirement_analysis": self._requirement_analysis,
                "template_family": self._template_family,
                "retrieval_targets": self._retrieval_targets,
                "test_plan": self._test_plan,
            }
        else:
            self._last_retrieval_decision["next_state"] = "edit_or_test"

    @staticmethod
    def _extract_symbolic_prompt_sections(symbolic_prompt: str) -> list[str]:
        sections = []
        for label in ("Formal Semantics:", "Constraints:", "Retrieval Strategy:", "Retrieved Facts:"):
            if label in symbolic_prompt:
                sections.append(label[:-1])
        return sections

    @staticmethod
    def _classify_constraints_quality(symbolic_prompt: str) -> str:
        if "Constraints:" not in symbolic_prompt:
            return "missing"
        after = symbolic_prompt.split("Constraints:", 1)[1]
        for next_label in ("Retrieval Strategy:", "Retrieved Facts:"):
            if next_label in after:
                after = after.split(next_label, 1)[0]
                break
        text = after.strip()
        return "thin" if len(text) < 20 else "present"

    @staticmethod
    def _extract_bundle_must_edit(bundle: Any) -> list[str]:
        if not isinstance(bundle, dict):
            return []
        candidates = bundle.get("must_edit")
        if candidates is None:
            candidates = bundle.get("must_edit_files")
        if candidates is None and isinstance(bundle.get("edit_targets"), dict):
            candidates = bundle["edit_targets"].get("must_edit")
        if isinstance(candidates, str):
            return [candidates]
        if isinstance(candidates, list):
            paths = []
            for item in candidates:
                if isinstance(item, str):
                    paths.append(item)
                elif isinstance(item, dict):
                    path = item.get("path") or item.get("file")
                    if path:
                        paths.append(str(path))
            return paths
        return []

    @staticmethod
    def _extract_match_files(matches: Any) -> list[str]:
        if not isinstance(matches, list):
            return []
        paths = []
        for match in matches:
            if isinstance(match, str):
                path = match
            elif isinstance(match, dict):
                path = match.get("path") or match.get("file") or match.get("filepath")
            else:
                path = None
            if path:
                path = str(path)
                if path not in paths:
                    paths.append(path)
        return paths

    @staticmethod
    def _extract_bundle_primary_files(bundle: Any) -> list[str]:
        if not isinstance(bundle, dict):
            return []
        candidates = bundle.get("primary_files")
        if candidates is None:
            candidates = bundle.get("primary")
        if candidates is None and isinstance(bundle.get("files"), dict):
            candidates = bundle["files"].get("primary")
        if isinstance(candidates, str):
            return [candidates]
        if isinstance(candidates, list):
            paths = []
            for item in candidates:
                if isinstance(item, str):
                    path = item
                elif isinstance(item, dict):
                    path = item.get("path") or item.get("file")
                else:
                    path = None
                if path:
                    path = str(path)
                    if path not in paths:
                        paths.append(path)
            return paths
        return []

    @staticmethod
    def _extract_test_plan_files(test_plan: Any) -> list[str]:
        if not isinstance(test_plan, dict):
            return []
        candidates: list[Any] = []
        for key in ("files", "file_paths", "paths", "read_files", "targets", "target_files"):
            value = test_plan.get(key)
            if value is not None:
                candidates.append(value)
        paths: list[str] = []
        for candidate in candidates:
            if isinstance(candidate, str):
                if candidate not in paths:
                    paths.append(candidate)
            elif isinstance(candidate, list):
                for item in candidate:
                    if isinstance(item, str) and item not in paths:
                        paths.append(item)
                    elif isinstance(item, dict):
                        path = item.get("path") or item.get("file") or item.get("filepath")
                        if path:
                            path = str(path)
                            if path not in paths:
                                paths.append(path)
            elif isinstance(candidate, dict):
                path = candidate.get("path") or candidate.get("file") or candidate.get("filepath")
                if path:
                    path = str(path)
                    if path not in paths:
                        paths.append(path)
        return paths

    def _collect_context_artifact_ids(self, artifacts: Any) -> None:
        if not isinstance(artifacts, list):
            return
        for artifact in artifacts:
            if isinstance(artifact, dict):
                artifact_id = str(artifact.get("artifact_id") or "").strip()
            elif isinstance(artifact, str):
                artifact_id = artifact.strip()
            else:
                continue
            if artifact_id and artifact_id not in self._retrieval_trace.context_artifact_ids:
                self._retrieval_trace.context_artifact_ids.append(artifact_id)

    @staticmethod
    def _extract_test_plan_commands(test_plan: Any) -> list[str]:
        if not isinstance(test_plan, dict):
            return []
        commands: list[str] = []
        direct = test_plan.get("commands")
        if isinstance(direct, list):
            for item in direct:
                if isinstance(item, str) and item.strip() and item not in commands:
                    commands.append(item)
        phases = test_plan.get("phases")
        if isinstance(phases, list):
            for phase in phases:
                if not isinstance(phase, dict):
                    continue
                phase_commands = phase.get("commands")
                if isinstance(phase_commands, list):
                    for item in phase_commands:
                        if isinstance(item, str) and item.strip() and item not in commands:
                            commands.append(item)
        return commands

    def _sync_trace_state(self, *, state: Optional[str] = None) -> None:
        if state is not None:
            self._retrieval_state = state
            self._retrieval_trace.state = state
        self._retrieval_trace.exploration_closed = bool(self._retrieval_trace.accepted_targets)

    def _set_accepted_targets(self, targets: list[str], *, test_plan_files: list[str] | None = None) -> None:
        seen: list[str] = []
        for target in targets:
            target = str(target).strip()
            if target and target not in seen:
                seen.append(target)
        self._retrieval_trace.accepted_targets = seen
        if test_plan_files is not None:
            seen_test: list[str] = []
            for path in test_plan_files:
                path = str(path).strip()
                if path and path not in seen_test:
                    seen_test.append(path)
            self._retrieval_trace.test_plan_files = seen_test
        self._retrieval_trace.exploration_closed = bool(self._retrieval_trace.accepted_targets)

    def _is_context_read_allowed(self, path: str) -> bool:
        if not self._retrieval_trace.exploration_closed and not self._retrieval_trace.accepted_targets:
            return True
        # Normalize ./foo/bar → foo/bar so prefix checks work regardless of how
        # the agent prefixes the path.
        norm = path.lstrip("./") if path.startswith("./") else path
        accepted = {p.lstrip("./") if p.startswith("./") else p for p in self._retrieval_trace.accepted_targets}
        test_plan_files = {p.lstrip("./") if p.startswith("./") else p for p in self._retrieval_trace.test_plan_files}
        if norm in accepted or path in accepted or norm in test_plan_files or path in test_plan_files:
            return True
        if norm.startswith("tests/") and self._retrieval_state in {"grounded", "context_read"}:
            return True
        return False

    def _is_context_read_phase_terminal_allowed(self, command: str) -> bool:
        text = " ".join(str(command or "").split()).lower()
        if not text:
            return False
        if self._is_broad_discovery_command(text):
            return False
        if any(marker in text for marker in ("cat >", "<<", "tee ", ".write(", "open(")):
            return False
        if text.startswith("python tests/runtests.py") or " python tests/runtests.py" in text:
            return True
        allowed_snippets = (
            "pytest ",
            "git diff",
            "git status",
            "cat ",
            "sed -n",
        )
        return any(snippet in text for snippet in allowed_snippets)

    def _matches_test_plan_command(self, command: str) -> bool:
        text = " ".join(str(command or "").split()).strip().lower()
        if not text:
            return False
        for planned in self._test_plan_commands:
            planned_text = " ".join(str(planned).split()).strip().lower()
            if planned_text and (text == planned_text or text.startswith(planned_text)):
                return True
        return False

    def _has_contradiction(self, payload: dict[str, Any], metadata: dict[str, Any]) -> bool:
        if self._target_conflict or self._target_changed_after_grounding:
            return True
        feedback = " ".join(
            str(value)
            for value in (
                payload.get("retrieval_feedback"),
                metadata.get("retrieval_feedback"),
                payload.get("missing_context"),
                payload.get("coverage"),
            )
            if value is not None
        ).lower()
        if "contradict" in feedback or "conflict" in feedback or "inconsisten" in feedback:
            return True
        diagnostics = payload.get("diagnostics")
        if isinstance(diagnostics, dict):
            diag_blob = json.dumps(diagnostics, ensure_ascii=False).lower()
            if "contradict" in diag_blob or "conflict" in diag_blob or "inconsisten" in diag_blob:
                return True
        return False

    @staticmethod
    def _is_broad_discovery_command(command: str) -> bool:
        text = " ".join(str(command or "").split()).lower()
        if not text:
            return False
        patterns = (
            "grep ",
            " find ",
            " fd ",
            " rg ",
            "git grep",
            "ack ",
            "ag ",
        )
        if any(pattern in f" {text} " for pattern in patterns):
            return True
        return text.startswith("find ") or text.startswith("grep ") or text.startswith("rg ")

    def _select_preferred_edit_targets(self) -> list[str]:
        if self._grounded_files:
            return list(self._grounded_files)
        if self._direct_match_files:
            return list(self._direct_match_files)
        if self._bundle_primary_files:
            return list(self._bundle_primary_files)
        if isinstance(self._retrieval_targets, list):
            return [str(target) for target in self._retrieval_targets if str(target).strip()]
        return list(self._bundle_must_edit)

    def get_retrieval_status(self) -> dict[str, Any]:
        return {
            "retrieval_state": self._retrieval_state,
            "retrieval_status": self._retrieval_status(),
            "coding_status": "unverified",
            "retrieval_trace": self._retrieval_trace.to_dict(),
            "retrieval_budget": self._retrieval_trace.retrieval_budget,
            "symbolic_failures": self._symbolic_failures,
            "seed_calls": self._retrieval_trace.seed_calls,
            "retry_calls": self._retrieval_trace.retry_calls,
            "grounded_calls": self._retrieval_trace.grounded_calls,
            "legacy_calls": self._retrieval_trace.legacy_calls,
            "legacy_attempted": self._legacy_attempted,
            "grounded_symbols": list(self._grounded_symbols),
            "grounded_files": list(self._grounded_files),
            "accepted_targets": list(self._retrieval_trace.accepted_targets),
            "exploration_closed": self._retrieval_trace.exploration_closed,
            "blocked_tool_reason": self._retrieval_trace.blocked_tool_reason,
            "test_plan_files": list(self._retrieval_trace.test_plan_files),
            "context_artifact_ids": list(self._retrieval_trace.context_artifact_ids),
            "suggested_queries": list(self._last_suggested_queries),
            "requirement_analysis": self._requirement_analysis,
            "template_family": self._template_family,
            "retrieval_targets": self._retrieval_targets,
            "test_plan": self._test_plan,
            "symbolic_prompt_present": self._symbolic_prompt_present,
            "symbolic_prompt_sections": list(self._symbolic_prompt_sections),
            "symbolic_prompt_missing": self._symbolic_prompt_missing,
            "constraints_present": self._constraints_present,
            "constraints_quality": self._constraints_quality,
            "direct_match_files": list(self._direct_match_files),
            "bundle_primary_files": list(self._bundle_primary_files),
            "bundle_must_edit": list(self._bundle_must_edit),
            "preferred_edit_targets": list(self._preferred_edit_targets),
            "target_conflict": self._target_conflict,
            "target_changed_after_grounding": self._target_changed_after_grounding,
            "last_decision": dict(self._last_retrieval_decision),
            "last_gate_failure": dict(self._last_gate_failure),
        }

    def _retrieval_status(self) -> str:
        if self._retrieval_state in {"grounded", "context_read", "inspect_candidates"}:
            return "good"
        if self._retrieval_state == "legacy_fallback":
            return "legacy_fallback"
        if self._retrieval_state == "degraded_recovery":
            return "failed"
        return "weak"

    def _gate_block(self, tool_name: str, message: str) -> str:
        self._last_gate_failure = {
            "tool_name": tool_name,
            "retrieval_state": self._retrieval_state,
            "retrieval_status": self._retrieval_status(),
            "message": message,
        }
        self._retrieval_trace.blocked_tool_reason = message
        logger.info(
            "Retrieval gate blocked tool=%s state=%s status=%s",
            tool_name,
            self._retrieval_state,
            self._retrieval_status(),
        )
        return message

    def get_tool_block_message(self, tool_name: str, args: dict[str, Any] | None = None) -> str | None:
        """Return a block message when a non-retrieval tool would bypass grounding."""
        if tool_name == "context_search":
            args = args or {}
            if self._grounded_search_required:
                metadata = args.get("metadata") if isinstance(args.get("metadata"), dict) else {}
                mode = str(metadata.get("retrieval_mode") or args.get("retrieval_mode") or "").strip().lower()
                phase = str(metadata.get("grounding_phase") or args.get("grounding_phase") or "").strip().lower()
                if phase == "grounded" and mode in {"", "symbolic"}:
                    return None
                return self._gate_block(tool_name, (
                    "Retrieval gate active: context_read requires exactly one grounded "
                    "context_search next, with metadata.grounding_phase='grounded'."
                ))
            if self._retrieval_trace.exploration_closed:
                return self._gate_block(tool_name, (
                    "Retrieval gate active: accepted targets close exploration; additional "
                    "context_search calls are blocked. Continue with accepted-target reads, "
                    "editing, or tests."
                ))
            return None

        args = args or {}
        if tool_name == "context_read":
            requested_path = str(args.get("path") or "").strip()
            if self._is_context_read_allowed(requested_path):
                return None
            return self._gate_block(tool_name, (
                "Retrieval gate active: context_read is limited to accepted targets or test-plan files "
                "after a grounded target has been accepted."
            ))

        read_or_discovery_tools = {
            "terminal",
            "read_file",
            "search_files",
        }
        edit_or_execution_tools = {
            "write_file",
            "patch",
            "execute_code",
        }
        gated_tools = read_or_discovery_tools | edit_or_execution_tools
        if tool_name not in gated_tools:
            return None

        if tool_name == "read_file":
            requested_path = str(args.get("path") or "").strip()
            if self._is_context_read_allowed(requested_path):
                return None
            return self._gate_block(tool_name, (
                "Retrieval gate active: read_file is limited to accepted targets or test-plan files "
                "after a grounded target has been accepted."
            ))
        if tool_name == "search_files" and self._retrieval_trace.exploration_closed:
            return self._gate_block(tool_name, (
                "Retrieval gate active: accepted targets close exploration; search_files is blocked "
                "to prevent alternative-file searches."
            ))
        if tool_name == "terminal" and self._retrieval_trace.exploration_closed:
            command = str(args.get("command") or args.get("cmd") or "")
            if self._test_plan_commands and self._matches_test_plan_command(command):
                return None
            if self._is_broad_discovery_command(command):
                return self._gate_block(tool_name, (
                    "Retrieval gate active: accepted targets plus server test plan are available; "
                    "broad grep/find/search commands are blocked."
                ))

        if self._retrieval_state == "not_started":
            return self._gate_block(tool_name, (
                "Retrieval gate active: call context_search first with "
                "metadata.retrieval_mode='symbolic' and metadata.grounding_phase='seed'."
            ))
        if self._retrieval_state in {"retry", "grounded_retry"}:
            if self._retrieval_state == "retry" and self._symbolic_failures >= 2:
                retry_message = (
                    "Fallback context_search is required with metadata.retrieval_mode='legacy' "
                    "and metadata.grounding_phase='fallback'."
                )
            elif self._last_suggested_queries:
                retry_message = (
                    "Retry context_search using one of suggested_queries before shell/file exploration."
                )
            else:
                retry_message = (
                    "Retry context_search with a narrower query before shell/file exploration."
                )
            return self._gate_block(tool_name, (
                "Retrieval gate active: the last context_search result was weak. "
                f"{retry_message}"
            ))
        if self._retrieval_state == "inspect_candidates":
            return self._gate_block(tool_name, (
                "Retrieval gate active: use context_read on a candidate file/span before "
                "shell/file exploration or editing."
            ))
        if self._retrieval_state == "context_read":
            if tool_name in {"read_file", "context_read"}:
                return None
            if tool_name == "terminal":
                command = str(args.get("command") or args.get("cmd") or "")
                if self._is_context_read_phase_terminal_allowed(command):
                    return None
            return self._gate_block(tool_name, (
                "Retrieval gate active: context_read requires exactly one grounded context_search "
                "before repro, broad terminal exploration, or editing. Only narrow target "
                "inspection or targeted test commands are allowed in this phase."
            ))
        if self._retrieval_state == "degraded_recovery":
            if tool_name in read_or_discovery_tools:
                return None
            return self._gate_block(tool_name, (
                "Retrieval gate active: symbolic and legacy retrieval both failed. "
                "Bounded shell inspection is allowed in degraded_recovery, but editing "
                "requires grounded evidence from a relevant file or symbol."
            ))
        if self._retrieval_state in {"grounded", "legacy_fallback"}:
            self._grounded_search_required = False
            return None
        return None

    def observe_tool_result(self, tool_name: str, args: dict[str, Any], result: str) -> None:
        """Update engine state based on a completed tool call.

        When the agent uses read_file in degraded_recovery and gets a successful
        result, treat the read path as grounded evidence so that subsequent
        patch/write_file calls on that file are unblocked.

        When the agent reads a tests/ file in any grounded state, add it to
        test_plan_files so the engine can surface the correct test runner command.
        """
        if tool_name != "read_file":
            return
        path = str(args.get("path") or "").strip()
        if not path:
            return
        try:
            parsed = json.loads(result)
            if isinstance(parsed, dict) and parsed.get("error"):
                return
            if isinstance(parsed, dict) and parsed.get("dedup"):
                # File was unchanged since last read — still counts as grounded.
                pass
        except (json.JSONDecodeError, TypeError):
            pass

        norm = path.lstrip("./") if path.startswith("./") else path

        if self._retrieval_state == "degraded_recovery":
            self._record_context_read(path, {"path": path})
        elif self._retrieval_state in {"grounded", "context_read", "legacy_fallback"}:
            # Track test files read by the agent so test_plan_files is populated
            # even when grounding came from degraded_recovery (no server test_plan).
            if norm.startswith("tests/") or norm.startswith("test_"):
                if path not in self._retrieval_trace.test_plan_files:
                    self._retrieval_trace.test_plan_files.append(path)
