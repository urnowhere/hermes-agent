"""FormSy observability plugin for Hermes.

This plugin observes Hermes lifecycle hooks and submits compact task-level
metrics to a local FormSy server. It deliberately avoids prompt text, source
content, diffs, and shell output.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import socket
import threading
import time
import urllib.request
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from plugins.formsy.identity import derive_formsy_identity

logger = logging.getLogger(__name__)

_TEST_COMMAND_RE = re.compile(
    r"\b(pytest|tox|nox|unittest|npm\s+test|npm\s+run\s+test|pnpm\s+test|pnpm\s+run\s+test|"
    r"yarn\s+test|go\s+test|cargo\s+test|mvn\s+test|gradle\s+test|make\s+test)\b",
    re.IGNORECASE,
)
_SECRET_ASSIGNMENT_RE = re.compile(
    r"(?i)\b([A-Z0-9_]*(?:API[_-]?KEY|TOKEN|SECRET|PASSWORD|PASS|CREDENTIAL)[A-Z0-9_]*)\s*=\s*([^\s;&|]+)"
)
_BEARER_RE = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+")
_LONG_TOKEN_RE = re.compile(r"\b[A-Za-z0-9._~+/=-]{32,}\b")

_CONTEXT_SEARCH_TOOLS = {
    "cc_memory_search",
    "formsy_memory_search",
    "memory_search",
    "session_search",
    "search_files",
}
_CONTEXT_READ_TOOLS = {
    "cc_memory_read",
    "formsy_memory_read",
    "memory_read",
    "read_file",
}
_FILE_EDIT_TOOLS = {"write_file", "patch", "edit_file"}


def _now_ms() -> int:
    return int(time.time() * 1000)


def _hash_text(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _today_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _truthy_env(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _string(value: Any, default: str = "") -> str:
    return value if isinstance(value, str) and value else default


def _get_hermes_home() -> Path:
    try:
        from hermes_constants import get_hermes_home

        return get_hermes_home()
    except Exception:
        return Path(os.getenv("HERMES_HOME", "~/.hermes")).expanduser()


def _workspace_id() -> str:
    return (
        os.getenv("FORMSY_WORKSPACE_ID")
        or os.getenv("HERMES_WORKSPACE_ID")
        or "local"
    )


def _repo_id() -> str:
    explicit = os.getenv("FORMSY_REPO_ID")
    if explicit:
        return explicit
    cwd = os.getenv("TERMINAL_CWD") or os.getcwd()
    return Path(cwd).resolve().name


def _revision() -> str:
    return os.getenv("FORMSY_REVISION") or ""


def _formsy_config() -> dict[str, Any]:
    config_path = _get_hermes_home() / "config.yaml"
    if not config_path.exists():
        return {}
    try:
        import yaml

        payload = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}
    if isinstance(payload.get("formsy"), dict):
        return payload["formsy"]
    return {}


def _submit_url() -> str:
    cfg = _formsy_config()
    base = (
        os.getenv("FORMSY_OBSERVABILITY_URL")
        or os.getenv("FORMSY_BASE_URL")
        or _string(cfg.get("base_url"))
        or "http://127.0.0.1:8000"
    ).rstrip("/")
    endpoint = os.getenv("FORMSY_OBSERVABILITY_TASK_REPORT_ENDPOINT", "/v1/observations/task_reports")
    if not endpoint.startswith("/"):
        endpoint = "/" + endpoint
    return base + endpoint


def _api_key() -> str:
    cfg = _formsy_config()
    api_key_env = _string(cfg.get("api_key_env"), "FORMSY_API_KEY")
    return (
        os.getenv("FORMSY_OBSERVABILITY_API_KEY")
        or _string(cfg.get("api_key"))
        or os.getenv(api_key_env)
        or os.getenv("FORMSY_API_KEY")
        or ""
    )


def _spool_root() -> Path:
    configured = os.getenv("FORMSY_OBSERVABILITY_SPOOL_DIR")
    if configured:
        return Path(configured).expanduser()
    return _get_hermes_home() / "formsy-observability"


@dataclass
class TaskCounters:
    turn_count: int = 0
    model_turn_count: int = 0
    context_search_count: int = 0
    context_read_count: int = 0
    shell_fallback_count: int = 0
    test_command_count: int = 0
    file_edit_count: int = 0
    server_request_count: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0


@dataclass
class TaskState:
    session_id: str
    run_id: str
    task_id: str
    case_id: str = ""
    case_id_source: str = ""
    started_at_ms: int = field(default_factory=_now_ms)
    ended_at_ms: int | None = None
    model: str = ""
    platform: str = ""
    source_instance_id: str = field(default_factory=lambda: f"{socket.gethostname()}-{os.getpid()}")
    first_task_label: str = ""
    first_test_command_summary: str | None = None
    first_test_command_kind: str | None = None
    edited_file_hashes: set[str] = field(default_factory=set)
    used_observation_ids: set[str] = field(default_factory=set)
    last_grounded_accepted_target_hashes: set[str] = field(default_factory=set)
    counters: TaskCounters = field(default_factory=TaskCounters)
    last_flush_ms: int = 0


class FormSyObservationReporter:
    def __init__(self) -> None:
        self.enabled = _truthy_env("FORMSY_OBSERVABILITY_ENABLED", True)
        self.submit_url = _submit_url()
        self.timeout_s = float(os.getenv("FORMSY_OBSERVABILITY_TIMEOUT", "2.0"))
        self.flush_interval_ms = int(float(os.getenv("FORMSY_OBSERVABILITY_PARTIAL_INTERVAL_S", "300")) * 1000)
        self.agent_version = os.getenv("HERMES_VERSION", "")
        self._states: dict[str, TaskState] = {}
        self._lock = threading.Lock()

    def on_session_start(self, session_id: str = "", model: str = "", platform: str = "", **_: Any) -> None:
        if not self.enabled:
            return
        with self._lock:
            self._ensure_state(session_id=session_id, model=model, platform=platform)

    def pre_llm_call(
        self,
        session_id: str = "",
        user_message: Any = None,
        is_first_turn: bool = False,
        model: str = "",
        platform: str = "",
        **_: Any,
    ) -> None:
        if not self.enabled:
            return
        with self._lock:
            state = self._ensure_state(
                session_id=session_id,
                user_message=user_message,
                model=model,
                platform=platform,
            )
            state.counters.turn_count += 1
            if is_first_turn and not state.first_task_label:
                state.first_task_label = self._task_label(user_message)

    def post_api_request(self, session_id: str = "", model: str = "", usage: Any = None, **_: Any) -> None:
        if not self.enabled:
            return
        with self._lock:
            state = self._ensure_state(session_id=session_id, model=model)
            state.counters.model_turn_count += 1
            state.counters.server_request_count += 1
            if isinstance(usage, dict):
                state.counters.input_tokens += self._int_value(
                    usage,
                    "input_tokens",
                    "prompt_tokens",
                    "total_input_tokens",
                )
                state.counters.output_tokens += self._int_value(
                    usage,
                    "output_tokens",
                    "completion_tokens",
                    "total_output_tokens",
                )

    def pre_tool_call(
        self,
        tool_name: str = "",
        args: dict[str, Any] | None = None,
        task_id: str = "",
        session_id: str = "",
        **_: Any,
    ) -> None:
        if not self.enabled:
            return
        args = args if isinstance(args, dict) else {}
        with self._lock:
            state = self._ensure_state(session_id=session_id, task_id=task_id)
            self._observe_tool_start(state, tool_name, args)

    def post_tool_call(
        self,
        tool_name: str = "",
        args: dict[str, Any] | None = None,
        result: Any = None,
        task_id: str = "",
        session_id: str = "",
        **_: Any,
    ) -> None:
        if not self.enabled:
            return
        args = args if isinstance(args, dict) else {}
        with self._lock:
            state = self._ensure_state(session_id=session_id, task_id=task_id)
            self._observe_tool_result(state, tool_name, args, result)

    def post_llm_call(self, session_id: str = "", model: str = "", platform: str = "", **_: Any) -> None:
        if not self.enabled:
            return
        report: dict[str, Any] | None = None
        with self._lock:
            state = self._ensure_state(session_id=session_id, model=model, platform=platform)
            if self.flush_interval_ms > 0 and _now_ms() - state.last_flush_ms >= self.flush_interval_ms:
                state.last_flush_ms = _now_ms()
                report = self._build_report(state, report_phase="partial", status="running")
        if report:
            self._submit_async(report)

    def on_session_end(
        self,
        session_id: str = "",
        completed: bool = True,
        interrupted: bool = False,
        model: str = "",
        platform: str = "",
        **_: Any,
    ) -> None:
        if not self.enabled:
            return
        report: dict[str, Any] | None = None
        with self._lock:
            state = self._ensure_state(session_id=session_id, model=model, platform=platform)
            status = "running" if completed and not interrupted else "failed"
            report = self._build_report(state, report_phase="partial", status=status)
        self._submit_async(report)

    def on_session_finalize(self, session_id: str = "", platform: str = "", **_: Any) -> None:
        self._finalize_session(session_id=session_id, platform=platform, status="completed")

    def on_session_reset(self, session_id: str = "", platform: str = "", **_: Any) -> None:
        self._finalize_session(session_id=session_id, platform=platform, status="completed")

    def _finalize_session(self, *, session_id: str = "", platform: str = "", status: str) -> None:
        if not self.enabled:
            return
        report: dict[str, Any] | None = None
        with self._lock:
            state = self._states.get(session_id or "default")
            if state is None:
                return
            if platform:
                state.platform = platform
            report = self._build_report(state, report_phase="final", status=status)
            self._states.pop(state.session_id, None)
        self._submit_async(report)

    def _ensure_state(
        self,
        *,
        session_id: str = "",
        task_id: str = "",
        user_message: Any = None,
        model: str = "",
        platform: str = "",
    ) -> TaskState:
        sid = session_id or "default"
        identity = derive_formsy_identity(
            session_id=sid,
            task_id=task_id,
            user_message=user_message,
            workspace_id=_workspace_id(),
            repo_id=_repo_id(),
            revision=_revision(),
        )
        state = self._states.get(sid)
        if state is None:
            state = TaskState(
                session_id=sid,
                run_id=identity.run_id,
                task_id=identity.task_id,
                case_id=identity.case_id,
                case_id_source=identity.case_id_source,
                model=model or "",
                platform=platform or "",
            )
            self._states[sid] = state
        if task_id and task_id != state.task_id:
            state.task_id = task_id
            if not state.case_id or state.case_id_source in {"", "fallback_hash"}:
                state.case_id = task_id
                state.case_id_source = "task_id_param"
        if (
            user_message is not None
            and state.counters.turn_count == 0
            and state.case_id_source in {"", "fallback_hash"}
            and identity.case_id_source == "fallback_hash"
        ):
            state.task_id = identity.task_id
            state.case_id = identity.case_id
            state.case_id_source = identity.case_id_source
            state.run_id = identity.run_id
        if identity.case_id_source in {"case_id_param", "case_id_env", "parsed_case_id"}:
            state.task_id = identity.task_id
            state.case_id = identity.case_id
            state.case_id_source = identity.case_id_source
            state.run_id = identity.run_id
        if model:
            state.model = model
        if platform:
            state.platform = platform
        return state

    def _observe_tool_start(self, state: TaskState, tool_name: str, args: dict[str, Any]) -> None:
        if tool_name in _CONTEXT_SEARCH_TOOLS or "search" in tool_name:
            state.counters.context_search_count += 1
        if tool_name in _CONTEXT_READ_TOOLS or tool_name.endswith("_read"):
            state.counters.context_read_count += 1
        if tool_name in _FILE_EDIT_TOOLS:
            state.counters.file_edit_count += 1
            path_hash = self._path_hash_from_args(args)
            if path_hash:
                state.edited_file_hashes.add(path_hash)
        if tool_name == "terminal":
            command = _string(args.get("command"))
            if command:
                state.counters.shell_fallback_count += 1
            if self._is_test_command(command):
                state.counters.test_command_count += 1
                if not state.first_test_command_summary:
                    state.first_test_command_summary = self._text_summary(command, max_chars=160)
                    state.first_test_command_kind = self._test_command_kind(command)

    def _observe_tool_result(self, state: TaskState, tool_name: str, args: dict[str, Any], result: Any) -> None:
        if tool_name in _CONTEXT_SEARCH_TOOLS or "search" in tool_name:
            self._collect_server_correlation(state, result)
        if tool_name in _CONTEXT_READ_TOOLS or tool_name.endswith("_read"):
            self._collect_server_correlation(state, result)
        if tool_name in _FILE_EDIT_TOOLS:
            path_hash = self._path_hash_from_args(args)
            if path_hash:
                state.edited_file_hashes.add(path_hash)

    def _build_report(self, state: TaskState, *, report_phase: str, status: str) -> dict[str, Any]:
        state.ended_at_ms = _now_ms()
        report_id = f"rpt_hermes_{uuid.uuid4().hex}"
        return {
            "schema_version": "observation.v1",
            "report_type": "agent.task_report",
            "report_id": report_id,
            "run_id": state.run_id,
            "session_id": state.session_id,
            "task_id": state.task_id,
            "started_at_ms": state.started_at_ms,
            "ended_at_ms": state.ended_at_ms,
            "source": {
                "kind": "agent",
                "name": "hermes",
                "instance_id": state.source_instance_id,
            },
            "workspace": {
                "workspace_id": _workspace_id(),
                "repo_id": _repo_id(),
                "revision": _revision(),
            },
            "task": {
                "task_kind": "coding",
                "case_id": self._report_case_id(state),
                "status": status,
                "report_phase": report_phase,
            },
            "counters": {
                "turn_count": state.counters.turn_count,
                "model_turn_count": state.counters.model_turn_count,
                "context_search_count": state.counters.context_search_count,
                "context_read_count": state.counters.context_read_count,
                "shell_fallback_count": state.counters.shell_fallback_count,
                "test_command_count": state.counters.test_command_count,
                "file_edit_count": state.counters.file_edit_count,
                "input_tokens": state.counters.input_tokens,
                "output_tokens": state.counters.output_tokens,
                "model_provider": state.model or "unknown",
                "cost_usd": state.counters.cost_usd,
            },
            "observed_behavior": {
                "first_test_command_summary": state.first_test_command_summary,
                "first_test_command_kind": state.first_test_command_kind,
                "edited_file_hashes": sorted(state.edited_file_hashes),
                "edited_file_count": len(state.edited_file_hashes) or state.counters.file_edit_count,
            },
            "server_correlation": {
                "used_observation_ids": sorted(state.used_observation_ids),
                "last_grounded_accepted_target_hashes": sorted(state.last_grounded_accepted_target_hashes),
                "server_request_count": state.counters.server_request_count,
            },
            "privacy": {
                "redaction": "metrics_and_redacted_summaries",
                "contains_prompt": False,
                "contains_source": False,
                "contains_diff": False,
                "contains_shell_output": False,
            },
        }

    @staticmethod
    def _report_case_id(state: TaskState) -> str:
        if state.case_id_source in {"case_id_param", "case_id_env", "parsed_case_id"}:
            return state.case_id or state.task_id
        return state.first_task_label or state.case_id or state.task_id

    def _submit_async(self, report: dict[str, Any]) -> None:
        thread = threading.Thread(
            target=self._submit_or_spool,
            args=(report,),
            daemon=True,
            name="formsy-observability-submit",
        )
        thread.start()

    def _submit_or_spool(self, report: dict[str, Any]) -> None:
        body = {
            "client": {
                "agent_name": "hermes",
                "agent_version": self.agent_version,
                "instance_id": report.get("source", {}).get("instance_id"),
                "capabilities": ["task_report", "metrics_and_redacted_summaries", "hermes_plugin_hooks"],
            },
            "reports": [report],
        }
        try:
            payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
            headers = {"Content-Type": "application/json"}
            api_key = _api_key()
            if api_key:
                headers["Authorization"] = f"Bearer {api_key}"
            request = urllib.request.Request(
                self.submit_url,
                data=payload,
                headers=headers,
                method="POST",
            )
            with urllib.request.urlopen(request, timeout=self.timeout_s) as response:
                if response.status >= 400:
                    raise RuntimeError(f"FormSy observability returned HTTP {response.status}")
        except Exception as exc:
            logger.warning("FormSy observability submit failed; spooling report: %s", exc)
            self._spool(report)

    def _spool(self, report: dict[str, Any]) -> None:
        try:
            directory = _spool_root() / "task-reports" / _today_utc()
            directory.mkdir(parents=True, exist_ok=True)
            path = directory / f"task-reports-{_today_utc()}.jsonl"
            with path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(report, ensure_ascii=False) + "\n")
            self._trim_spool(_spool_root(), max_bytes=int(os.getenv("FORMSY_OBSERVABILITY_SPOOL_MAX_BYTES", "20971520")))
        except Exception:
            logger.warning("FormSy observability spool write failed", exc_info=True)

    @staticmethod
    def _trim_spool(root: Path, *, max_bytes: int) -> None:
        if max_bytes <= 0 or not root.exists():
            return
        files = sorted(root.glob("task-reports/*/*.jsonl"), key=lambda p: p.stat().st_mtime)
        total = sum(path.stat().st_size for path in files)
        while files and total > max_bytes:
            victim = files.pop(0)
            size = victim.stat().st_size
            victim.unlink(missing_ok=True)
            total -= size

    @staticmethod
    def _task_label(user_message: Any) -> str:
        if isinstance(user_message, str):
            return FormSyObservationReporter._text_summary(user_message, max_chars=96)
        return ""

    @staticmethod
    def _text_summary(value: str, *, max_chars: int) -> str:
        text = str(value or "").strip()
        if not text:
            return ""
        text = _SECRET_ASSIGNMENT_RE.sub(lambda match: f"{match.group(1)}=<redacted>", text)
        text = _BEARER_RE.sub("Bearer <redacted>", text)
        text = _LONG_TOKEN_RE.sub("<redacted>", text)
        text = re.sub(r"\s+", " ", text).strip()
        if len(text) <= max_chars:
            return text
        return text[: max_chars - 3].rstrip() + "..."

    @staticmethod
    def _path_hash_from_args(args: dict[str, Any]) -> str:
        path = args.get("path") or args.get("file_path") or args.get("target_file")
        if isinstance(path, str) and path:
            return _hash_text(path)
        return ""

    @staticmethod
    def _is_test_command(command: str) -> bool:
        return bool(command and _TEST_COMMAND_RE.search(command))

    @staticmethod
    def _test_command_kind(command: str) -> str:
        command = command.lower()
        if "pytest" in command or "unittest" in command or "tox" in command or "nox" in command:
            return "python"
        if "npm" in command or "pnpm" in command or "yarn" in command:
            return "javascript"
        if "go test" in command:
            return "go"
        if "cargo test" in command:
            return "rust"
        if "mvn test" in command or "gradle test" in command:
            return "jvm"
        return "test"

    @staticmethod
    def _int_value(payload: dict[str, Any], *keys: str) -> int:
        for key in keys:
            value = payload.get(key)
            if isinstance(value, int):
                return max(value, 0)
            if isinstance(value, float):
                return max(int(value), 0)
        return 0

    def _collect_server_correlation(self, state: TaskState, result: Any) -> None:
        payload = self._json_result(result)
        if not isinstance(payload, dict):
            return
        for key in ("observation_id", "request_id", "trace_id"):
            value = payload.get(key)
            if isinstance(value, str) and value:
                state.used_observation_ids.add(value)
        for key in ("accepted_targets", "target_paths", "files"):
            values = payload.get(key)
            if isinstance(values, list):
                for value in values:
                    if isinstance(value, str) and value:
                        state.last_grounded_accepted_target_hashes.add(_hash_text(value))
        metadata = payload.get("metadata")
        if isinstance(metadata, dict):
            self._collect_server_correlation(state, metadata)

    @staticmethod
    def _json_result(result: Any) -> Any:
        if isinstance(result, (dict, list)):
            return result
        if isinstance(result, str):
            try:
                return json.loads(result)
            except json.JSONDecodeError:
                return None
        return None


_reporter = FormSyObservationReporter()


def register(ctx: Any) -> None:
    ctx.register_hook("on_session_start", _reporter.on_session_start)
    ctx.register_hook("pre_llm_call", _reporter.pre_llm_call)
    ctx.register_hook("post_api_request", _reporter.post_api_request)
    ctx.register_hook("pre_tool_call", _reporter.pre_tool_call)
    ctx.register_hook("post_tool_call", _reporter.post_tool_call)
    ctx.register_hook("post_llm_call", _reporter.post_llm_call)
    ctx.register_hook("on_session_end", _reporter.on_session_end)
    ctx.register_hook("on_session_finalize", _reporter.on_session_finalize)
    ctx.register_hook("on_session_reset", _reporter.on_session_reset)
