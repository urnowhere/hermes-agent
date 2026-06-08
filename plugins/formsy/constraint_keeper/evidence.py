"""Evidence classification helpers for FormSy Constraint Keeper."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

FINAL_SUBMIT_MARKER = "COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"

_VALIDATION_RE = re.compile(
    r"\b("
    r"pytest|python(?:\d+(?:\.\d+)?)?\s+-m\s+pytest|"
    r"python(?:\d+(?:\.\d+)?)?\s+-m\s+(?:py_compile|compileall)|"
    r"python(?:\d+(?:\.\d+)?)?\s+tests/runtests\.py|tox|nox|unittest|"
    r"npm\s+test|npm\s+run\s+test|pnpm\s+test|pnpm\s+run\s+test|yarn\s+test|"
    r"go\s+test|cargo\s+test|mvn\s+test|gradle\s+test|make\s+test"
    r")\b",
    re.IGNORECASE,
)
_KNOWN_EDIT_TOOLS = {"write_file", "patch", "edit_file", "apply_patch"}
_TERMINAL_MUTATION_RE = re.compile(
    r"("
    r"\bsed\s+-i\b|"
    r"\bcat\s+>|"
    r"\btee\s+|"
    r"\bmv\s+|"
    r"\bcp\s+|"
    r"\brm\s+|"
    r"\bgit\s+(checkout|restore|apply)\b|"
    r"open\([^)]*,\s*['\"]w|"
    r"write_text\(|"
    r"\.write\("
    r")",
    re.IGNORECASE,
)
_READ_ONLY_TERMINAL_RE = re.compile(
    r"^\s*(git\s+(diff|status|show)|cat\s+|ls\b|pwd\b|grep\b|rg\b|find\b)",
    re.IGNORECASE,
)


def is_final_submit(tool_name: str, args: dict[str, Any] | None) -> bool:
    if tool_name != "terminal":
        return False
    command = _command_from_args(args)
    return FINAL_SUBMIT_MARKER in command


def is_validation_command(command: str, contract_commands: tuple[str, ...] | list[str] = ()) -> bool:
    normalized = _normalize_command(command)
    if not normalized:
        return False
    for contract_command in contract_commands:
        if _normalize_command(contract_command) and _normalize_command(contract_command) in normalized:
            return True
    return bool(_VALIDATION_RE.search(normalized))


def is_edit_surface(tool_name: str, args: dict[str, Any] | None) -> bool:
    if tool_name in _KNOWN_EDIT_TOOLS:
        return True
    if tool_name != "terminal":
        return False
    command = _command_from_args(args)
    if not command or _READ_ONLY_TERMINAL_RE.search(command):
        return False
    return bool(_TERMINAL_MUTATION_RE.search(command))


def classify_terminal_result(
    args: dict[str, Any] | None,
    result: Any,
    *,
    contract_commands: tuple[str, ...] | list[str] = (),
    output_limit: int = 8192,
) -> dict[str, Any] | None:
    command = _command_from_args(args)
    if not command:
        return None
    exit_code = _exit_code(args, result)
    output = _result_text(result)
    output_info = truncate_with_hash(output, limit=output_limit)
    payload = {
        "command": command,
        "exit_code": exit_code,
        "output_hash": output_info["hash"],
        "truncated_output": output_info["text"],
        "output_truncated": output_info["truncated"],
    }

    if exit_code == 0 and is_validation_command(command, contract_commands):
        return {
            "event_kind": "test_result",
            "trust": "plugin_observed",
            "payload": {**payload, "passed": True},
        }
    if exit_code not in (None, 0):
        return {
            "event_kind": "failure",
            "trust": "plugin_observed",
            "payload": {
                **payload,
                "passed": False,
                "fingerprint": failure_fingerprint(command, exit_code, output),
            },
        }
    return None


def truncate_with_hash(text: str, *, limit: int = 8192) -> dict[str, Any]:
    value = str(text or "")
    truncated = len(value) > limit
    return {
        "text": value[:limit] if truncated else value,
        "hash": hash_text(value),
        "truncated": truncated,
    }


def changed_files_from_diff(diff_text: str) -> list[str]:
    files: list[str] = []
    seen: set[str] = set()
    for line in str(diff_text or "").splitlines():
        if not line.startswith("diff --git "):
            continue
        parts = line.split()
        if len(parts) < 4:
            continue
        path = parts[3]
        if path.startswith("b/"):
            path = path[2:]
        if path and path not in seen:
            seen.add(path)
            files.append(path)
    return files


def hash_text(text: str) -> str:
    return "sha256:" + hashlib.sha256(str(text or "").encode("utf-8")).hexdigest()


def failure_fingerprint(command: str, exit_code: int | None, output: str) -> str:
    significant = ""
    for line in reversed(str(output or "").splitlines()):
        stripped = line.strip()
        if stripped:
            significant = _normalize_volatile(stripped)
            break
    seed = json.dumps(
        {
            "command": _normalize_command(command),
            "exit_code": exit_code,
            "line": significant,
        },
        sort_keys=True,
    )
    return hash_text(seed)


def _command_from_args(args: dict[str, Any] | None) -> str:
    if not isinstance(args, dict):
        return ""
    return str(args.get("command") or args.get("cmd") or "").strip()


def _exit_code(args: dict[str, Any] | None, result: Any) -> int | None:
    for value in _candidate_exit_codes(args, result):
        if isinstance(value, bool):
            continue
        if isinstance(value, int):
            return value
        if isinstance(value, str) and value.strip().lstrip("-").isdigit():
            return int(value.strip())
    return None


def _candidate_exit_codes(args: dict[str, Any] | None, result: Any) -> list[Any]:
    values: list[Any] = []
    if isinstance(args, dict):
        values.extend([args.get("exit_code"), args.get("returncode")])
    if isinstance(result, dict):
        values.extend([result.get("exit_code"), result.get("returncode")])
    elif isinstance(result, str):
        try:
            parsed = json.loads(result)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, dict):
            values.extend([parsed.get("exit_code"), parsed.get("returncode")])
    return values


def _result_text(result: Any) -> str:
    if isinstance(result, str):
        try:
            parsed = json.loads(result)
        except json.JSONDecodeError:
            return result
        if isinstance(parsed, dict):
            for key in ("output", "stdout", "stderr", "text"):
                value = parsed.get(key)
                if isinstance(value, str) and value:
                    return value
        return result
    if isinstance(result, dict):
        for key in ("output", "stdout", "stderr", "text"):
            value = result.get(key)
            if isinstance(value, str) and value:
                return value
        return json.dumps(result, ensure_ascii=False, sort_keys=True)
    return str(result or "")


def _normalize_command(command: str) -> str:
    return re.sub(r"\s+", " ", str(command or "").strip())


def _normalize_volatile(text: str) -> str:
    text = re.sub(r"0x[0-9a-fA-F]+", "0x<addr>", text)
    text = re.sub(r"\b\d+\.\d+s\b", "<duration>", text)
    text = re.sub(r"/var/folders/[^\s]+", "<tmp-path>", text)
    return text
