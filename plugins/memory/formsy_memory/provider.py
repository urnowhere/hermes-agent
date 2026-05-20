"""FormSy memory provider implementation for Hermes."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import subprocess
import threading
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
            or os.environ.get("FORMALCC_API_KEY")
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
            return ""
        self._record_prefetch_response(response)
        return response.memory_block or ""

    def sync_turn(self, user_content: str, assistant_content: str, *, session_id: str = "", **kwargs) -> None:
        if not self._memory_client or not self._config:
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
        self._context_artifact_ids = []
        self._terminal_calls = []

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

    def _build_coding_summary(self, kwargs: dict[str, Any]) -> Optional[CodingSummary]:
        """Build a CodingSummary from per-turn accumulated state and caller-supplied kwargs."""
        accepted_targets: list[str] = self._coerce_string_list(
            kwargs.get("accepted_targets") or []
        )
        changed_files: list[str] = self._coerce_string_list(
            kwargs.get("changed_files") or self._extract_changed_files_from_terminal()
        )
        changed_symbols: list[str] = self._coerce_string_list(
            kwargs.get("changed_symbols") or []
        )
        tests_run: list[str] = self._coerce_string_list(
            kwargs.get("tests_run")
            or [call["command"] for call in self._terminal_calls if call.get("command")]
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
        test_result = str(kwargs.get("test_result") or "").strip() or None
        failure_lessons: list[str] = self._coerce_string_list(kwargs.get("failure_lessons") or [])
        context_query = str(kwargs.get("context_query") or "").strip() or None
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
            failure_lessons, context_query, confidence is not None,
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

    def _extract_changed_files_from_terminal(self) -> list[str]:
        """Mine recorded terminal calls for files mentioned in git diff output."""
        files: list[str] = []
        seen: set[str] = set()
        for call in self._terminal_calls:
            result = call.get("result") or ""
            if not result:
                continue
            for match in re.finditer(r'^(?:\+\+\+|---)\s+(?:a/|b/)?([\w./\-]+)', result, re.MULTILINE):
                path = match.group(1).strip()
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
