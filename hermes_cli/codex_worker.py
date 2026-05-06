"""Hermes-owned runner for Codex CLI Kanban workers."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Mapping, Sequence

from hermes_cli import kanban_db as kb


SUCCESS_REASON = "Codex completed; Hermes review required"
FAILURE_REASON = "Codex failed"
OUTPUT_TAIL_CHARS = 4000
DIFF_SUMMARY_CHARS = 12000
_SECRET_ENV_MARKERS = (
    "TOKEN",
    "SECRET",
    "PASSWORD",
    "PASSWD",
    "API_KEY",
    "ACCESS_KEY",
    "PRIVATE_KEY",
    "CREDENTIAL",
    "OAUTH",
)


def _looks_like_secret_env(name: str) -> bool:
    upper = name.upper()
    return any(marker in upper for marker in _SECRET_ENV_MARKERS)


def _codex_subprocess_env(source: Mapping[str, str] | None = None) -> dict[str, str]:
    """Return environment safe to hand to Codex CLI.

    Codex CLI auth is intentionally separate from Hermes/provider tokens.
    Preserve normal process settings such as PATH/HOME/proxies, but strip
    secret-looking variables so parent Hermes credentials are not exposed to
    the external coding tool.
    """
    env_source = os.environ if source is None else source
    return {k: v for k, v in env_source.items() if not _looks_like_secret_env(k)}


def _tail(text: str, limit: int = OUTPUT_TAIL_CHARS) -> str:
    if len(text) <= limit:
        return text
    return text[-limit:]


def _append_log(task_id: str, board: str | None, text: str) -> None:
    if not text:
        return
    log_path = kb.worker_log_path(task_id, board=board)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(log_path, "a", encoding="utf-8", errors="replace") as f:
        f.write(text)
        if not text.endswith("\n"):
            f.write("\n")


def _build_prompt(context: str, workspace: Path) -> str:
    return (
        "You are the OpenAI Codex CLI running as a Hermes Kanban worker.\n\n"
        "Hermes Kanban is the single source of truth for task state, logs, "
        "workspace, and lifecycle. Work only inside this workspace:\n"
        f"{workspace}\n\n"
        "Use strict TDD for code changes: add or update failing tests first, "
        "run them to verify RED, implement the minimal production change, then "
        "rerun targeted tests to verify GREEN. Do not use dangerous bypass "
        "flags. Do not edit files outside the workspace. Do not mark the "
        "Kanban task done; Hermes will block it for review after you exit.\n\n"
        "Kanban task context follows.\n\n"
        f"{context}"
    )


def _run_git(args: list[str], workspace: Path) -> str:
    result = subprocess.run(
        args,
        cwd=str(workspace),
        text=True,
        capture_output=True,
    )
    return (result.stdout or "") + (result.stderr or "")


def _git_summary(workspace: Path) -> dict[str, str]:
    return {
        "status": _run_git(["git", "status", "--short"], workspace),
        "diff_summary": _tail(
            _run_git(["git", "diff", "--stat", "--patch"], workspace),
            DIFF_SUMMARY_CHARS,
        ),
    }


def _codex_command(workspace: Path, prompt: str) -> list[str]:
    return [
        "codex",
        "--cd", str(workspace),
        "--sandbox", "workspace-write",
        "--ask-for-approval", "never",
        "exec",
        prompt,
    ]


def run_task(task_id: str, workspace: Path, *, board: str | None = None) -> int:
    workspace = workspace.expanduser().resolve()
    if not workspace.is_dir():
        raise ValueError(f"workspace is not a directory: {workspace}")

    if shutil.which("codex") is None:
        output = "`codex` executable not found on PATH."
        _append_log(task_id, board, output)
        with kb.connect(board=board) as conn:
            kb.block_task(
                conn,
                task_id,
                reason=FAILURE_REASON,
                error=output,
                metadata={"codex": {"exit_code": 127, "output_tail": output}},
            )
        return 127

    with kb.connect(board=board) as conn:
        context = kb.build_worker_context(conn, task_id)

    prompt = _build_prompt(context, workspace)
    cmd = _codex_command(workspace, prompt)
    result = subprocess.run(
        cmd,
        cwd=str(workspace),
        text=True,
        capture_output=True,
        env=_codex_subprocess_env(),
    )
    output = (result.stdout or "") + (result.stderr or "")
    _append_log(task_id, board, output)

    git = _git_summary(workspace)
    metadata = {
        "codex": {
            "exit_code": int(result.returncode),
            "output_tail": _tail(output),
        },
        "git": git,
    }

    with kb.connect(board=board) as conn:
        if result.returncode == 0:
            kb.block_task(
                conn,
                task_id,
                reason=SUCCESS_REASON,
                metadata=metadata,
            )
        else:
            kb.block_task(
                conn,
                task_id,
                reason=FAILURE_REASON,
                error=_tail(output),
                metadata=metadata,
            )
    return int(result.returncode)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run a Codex CLI Kanban worker")
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--board", default=None)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    return run_task(args.task_id, Path(args.workspace), board=args.board)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
