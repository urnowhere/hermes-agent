"""Canonical FormSy task/run identity helpers for Hermes integrations."""

from __future__ import annotations

import hashlib
import os
import re
import time
from dataclasses import dataclass
from typing import Any

_SWEBENCH_CASE_RE = re.compile(r"\b([A-Za-z0-9_.-]+__[A-Za-z0-9_.-]+-\d+)\b")
_SAFE_ID_RE = re.compile(r"[^A-Za-z0-9_.:-]+")


@dataclass(frozen=True)
class FormSyIdentity:
    task_id: str
    run_id: str
    session_id: str
    case_id: str
    workspace_id: str
    repo_id: str
    revision: str
    case_id_source: str = ""

    def to_task_ref(self) -> dict[str, str]:
        return {
            "task_id": self.task_id,
            "run_id": self.run_id,
            "session_id": self.session_id,
            "case_id": self.case_id,
        }

    def to_workspace_ref(self) -> dict[str, str]:
        return {
            "workspace_id": self.workspace_id,
            "repo_id": self.repo_id,
            "revision": self.revision,
        }

    def to_runtime_identity(self) -> dict[str, str]:
        return {
            **self.to_task_ref(),
            **self.to_workspace_ref(),
        }


def derive_formsy_identity(
    *,
    session_id: str = "",
    task_id: str = "",
    run_id: str = "",
    case_id: str = "",
    user_message: Any = None,
    workspace_id: str = "",
    repo_id: str = "",
    revision: str = "",
    run_counter: int = 0,
) -> FormSyIdentity:
    """Derive stable logical task identity and unique run identity.

    The task id must be stable across repeated attempts of the same coding
    task. The run id must identify this specific attempt.
    """

    sid = _clean_id(session_id) or "default"
    repo = _clean_id(repo_id) or "local"
    workspace = _clean_id(workspace_id) or "local"
    rev = str(revision or "")

    resolved_case_id, case_source = _resolve_case_id(
        explicit_case_id=case_id,
        user_message=user_message,
    )
    resolved_task_id, task_source = _resolve_task_id(
        explicit_task_id=task_id,
        case_id=resolved_case_id,
        user_message=user_message,
        repo_id=repo,
        session_id=sid,
    )
    resolved_run_id = _resolve_run_id(
        explicit_run_id=run_id,
        session_id=sid,
        run_counter=run_counter,
    )

    if not resolved_case_id:
        resolved_case_id = resolved_task_id
        case_source = task_source

    return FormSyIdentity(
        task_id=resolved_task_id,
        run_id=resolved_run_id,
        session_id=sid,
        case_id=resolved_case_id,
        workspace_id=workspace,
        repo_id=repo,
        revision=rev,
        case_id_source=case_source,
    )


def _resolve_task_id(
    *,
    explicit_task_id: str,
    case_id: str,
    user_message: Any,
    repo_id: str,
    session_id: str,
) -> tuple[str, str]:
    value = _clean_id(explicit_task_id)
    if value:
        return value, "task_id_param"
    value = _clean_id(os.getenv("FORMSY_TASK_ID", ""))
    if value:
        return value, "task_id_env"
    if case_id:
        return case_id, "case_id"
    message = _message_text(user_message)
    seed = message or session_id
    digest = _short_hash(f"{repo_id}\n{seed}")
    return _truncate_id(f"{repo_id}-{digest}", limit=80), "fallback_hash"


def _resolve_case_id(*, explicit_case_id: str, user_message: Any) -> tuple[str, str]:
    value = _clean_id(explicit_case_id)
    if value:
        return value, "case_id_param"
    value = _clean_id(os.getenv("FORMSY_CASE_ID", ""))
    if value:
        return value, "case_id_env"
    match = _SWEBENCH_CASE_RE.search(_message_text(user_message))
    if match:
        return _clean_id(match.group(1)), "parsed_case_id"
    return "", ""


def _resolve_run_id(*, explicit_run_id: str, session_id: str, run_counter: int) -> str:
    value = _clean_id(explicit_run_id)
    if value:
        return value
    value = _clean_id(os.getenv("FORMSY_RUN_ID", ""))
    if value:
        return value
    now_ms = int(time.time() * 1000)
    counter = max(int(run_counter or 0), 0)
    return f"run_{now_ms}_{_short_hash(session_id)[:8]}_{counter}"


def _message_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        content = value.get("content")
        return content if isinstance(content, str) else ""
    return ""


def _clean_id(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    return _SAFE_ID_RE.sub("-", text).strip("-")


def _truncate_id(value: str, *, limit: int) -> str:
    if len(value) <= limit:
        return value
    digest = _short_hash(value)
    return f"{value[: limit - len(digest) - 1].rstrip('-')}-{digest}"


def _short_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]
