"""Codex CLI worker routing for Hermes Kanban dispatch."""

from __future__ import annotations

import sys
from subprocess import CompletedProcess
from pathlib import Path

import pytest

from hermes_cli import kanban_db as kb


@pytest.fixture
def kanban_home(tmp_path, monkeypatch):
    """Isolated HERMES_HOME with an empty kanban DB."""
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    kb.init_db()
    return home


@pytest.fixture
def captured_popen(monkeypatch):
    captured: dict[str, object] = {}

    class FakeProc:
        pid = 98765

    def fake_popen(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["kwargs"] = kwargs
        return FakeProc()

    monkeypatch.setattr("subprocess.Popen", fake_popen)
    return captured


def _ready_task(conn, *, assignee: str) -> kb.Task:
    task_id = kb.create_task(conn, title=f"{assignee} task", assignee=assignee)
    task = kb.get_task(conn, task_id)
    assert task is not None
    assert task.status == "ready"
    return task


@pytest.mark.parametrize("assignee", ["codex", "codex-cli", "codex-worker", "openai-codex"])
def test_codex_assignees_route_to_codex_worker(
    kanban_home, captured_popen, assignee
):
    with kb.connect() as conn:
        task = _ready_task(conn, assignee=assignee)
        workspace = kb.resolve_workspace(task)

    pid = kb._default_spawn(task, str(workspace), board="default")

    assert pid == 98765
    cmd = captured_popen["cmd"]
    assert cmd[:3] == [sys.executable, "-m", "hermes_cli.codex_worker"]
    assert "--task-id" in cmd
    assert cmd[cmd.index("--task-id") + 1] == task.id
    assert "--workspace" in cmd
    assert cmd[cmd.index("--workspace") + 1] == str(workspace)
    assert "--board" in cmd
    assert cmd[cmd.index("--board") + 1] == "default"

    env = captured_popen["kwargs"]["env"]
    assert env["HERMES_PROFILE"] == "codex-worker"
    assert env["HERMES_KANBAN_TASK"] == task.id
    assert env["HERMES_KANBAN_WORKSPACE"] == str(workspace)
    assert env["HERMES_KANBAN_DB"] == str(kb.kanban_db_path(board="default"))
    assert env["HERMES_KANBAN_WORKSPACES_ROOT"] == str(
        kb.workspaces_root(board="default")
    )
    assert env["HERMES_KANBAN_BOARD"] == "default"


def test_non_codex_assignee_still_routes_to_hermes_profile(
    kanban_home, captured_popen
):
    with kb.connect() as conn:
        task = _ready_task(conn, assignee="engineer")
        workspace = kb.resolve_workspace(task)

    pid = kb._default_spawn(task, str(workspace), board="default")

    assert pid == 98765
    cmd = captured_popen["cmd"]
    assert cmd[:3] == ["hermes", "-p", "engineer"]
    assert "chat" in cmd
    assert cmd[-2:] == ["-q", f"work kanban task {task.id}"]
    env = captured_popen["kwargs"]["env"]
    assert env["HERMES_PROFILE"] == "engineer"
    assert env["HERMES_KANBAN_TASK"] == task.id


def test_codex_worker_spawn_env_strips_parent_secrets(
    kanban_home, captured_popen, monkeypatch
):
    monkeypatch.setenv("OPENAI_API_KEY", "parent-openai-secret")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "parent-anthropic-secret")
    monkeypatch.setenv("GITHUB_TOKEN", "parent-github-secret")
    monkeypatch.setenv("HERMES_API_KEY", "parent-hermes-secret")

    with kb.connect() as conn:
        task = _ready_task(conn, assignee="codex-worker")
        workspace = kb.resolve_workspace(task)

    pid = kb._default_spawn(task, str(workspace), board="default")

    assert pid == 98765
    env = captured_popen["kwargs"]["env"]
    assert env["HERMES_PROFILE"] == "codex-worker"
    assert env["HERMES_KANBAN_TASK"] == task.id
    assert "OPENAI_API_KEY" not in env
    assert "ANTHROPIC_API_KEY" not in env
    assert "GITHUB_TOKEN" not in env
    assert "HERMES_API_KEY" not in env


def test_codex_subprocess_env_strips_parent_secrets(
    kanban_home, monkeypatch, tmp_path
):
    from hermes_cli import codex_worker

    monkeypatch.setenv("OPENAI_API_KEY", "parent-openai-secret")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "parent-anthropic-secret")
    monkeypatch.setenv("GITHUB_TOKEN", "parent-github-secret")
    monkeypatch.setenv("HERMES_API_KEY", "parent-hermes-secret")

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    with kb.connect() as conn:
        task_id = kb.create_task(
            conn,
            title="implement feature",
            assignee="codex-worker",
            workspace_kind="dir",
            workspace_path=str(workspace),
        )
        assert kb.claim_task(conn, task_id, claimer="test") is not None

    seen_env = None

    def fake_run(cmd, **kwargs):
        nonlocal seen_env
        if cmd[:2] == ["git", "status"]:
            return CompletedProcess(cmd, 0, stdout="", stderr="")
        if cmd[:2] == ["git", "diff"]:
            return CompletedProcess(cmd, 0, stdout="", stderr="")
        assert cmd[0] == "codex"
        seen_env = kwargs["env"]
        return CompletedProcess(cmd, 0, stdout="codex stdout\n", stderr="")

    monkeypatch.setattr(codex_worker.shutil, "which", lambda name: "/usr/bin/codex")
    monkeypatch.setattr(codex_worker.subprocess, "run", fake_run)

    rc = codex_worker.main([
        "--task-id", task_id,
        "--workspace", str(workspace),
        "--board", "default",
    ])

    assert rc == 0
    assert seen_env is not None
    assert "PATH" in seen_env
    assert "OPENAI_API_KEY" not in seen_env
    assert "ANTHROPIC_API_KEY" not in seen_env
    assert "GITHUB_TOKEN" not in seen_env
    assert "HERMES_API_KEY" not in seen_env


def test_codex_worker_success_blocks_for_review_with_log_and_metadata(
    kanban_home, monkeypatch, tmp_path
):
    from hermes_cli import codex_worker

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    with kb.connect() as conn:
        task_id = kb.create_task(
            conn,
            title="implement feature",
            body="Add the feature using tests first.",
            assignee="codex-worker",
            workspace_kind="dir",
            workspace_path=str(workspace),
        )
        assert kb.claim_task(conn, task_id, claimer="test") is not None

    calls = []

    def fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        if cmd[:2] == ["git", "status"]:
            return CompletedProcess(cmd, 0, stdout=" M app.py\n", stderr="")
        if cmd[:2] == ["git", "diff"]:
            return CompletedProcess(cmd, 0, stdout="diff --git a/app.py b/app.py\n", stderr="")
        assert cmd[0] == "codex"
        assert "exec" in cmd
        return CompletedProcess(cmd, 0, stdout="codex stdout\n", stderr="codex stderr\n")

    monkeypatch.setattr(codex_worker.shutil, "which", lambda name: "/usr/bin/codex")
    monkeypatch.setattr(codex_worker.subprocess, "run", fake_run)

    rc = codex_worker.main([
        "--task-id", task_id,
        "--workspace", str(workspace),
        "--board", "default",
    ])

    assert rc == 0
    codex_cmd = calls[0][0]
    assert codex_cmd[0] == "codex"
    assert "exec" in codex_cmd
    assert codex_cmd.index("--ask-for-approval") < codex_cmd.index("exec")
    assert "--cd" in codex_cmd
    assert codex_cmd[codex_cmd.index("--cd") + 1] == str(workspace)
    assert "--sandbox" in codex_cmd
    assert codex_cmd[codex_cmd.index("--sandbox") + 1] == "workspace-write"
    assert "--ask-for-approval" in codex_cmd
    assert codex_cmd[codex_cmd.index("--ask-for-approval") + 1] == "never"
    assert not any("dangerously-bypass" in token for token in codex_cmd)

    log_text = kb.worker_log_path(task_id, board="default").read_text(encoding="utf-8")
    assert "codex stdout" in log_text
    assert "codex stderr" in log_text

    with kb.connect() as conn:
        task = kb.get_task(conn, task_id)
        run = kb.latest_run(conn, task_id)
    assert task.status == "blocked"
    assert run.outcome == "blocked"
    assert run.summary == "Codex completed; Hermes review required"
    assert run.metadata["codex"]["exit_code"] == 0
    assert run.metadata["git"]["status"] == " M app.py\n"
    assert "diff --git" in run.metadata["git"]["diff_summary"]


def test_codex_worker_failure_blocks_with_exit_code_and_output_tail(
    kanban_home, monkeypatch, tmp_path
):
    from hermes_cli import codex_worker

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    with kb.connect() as conn:
        task_id = kb.create_task(
            conn,
            title="broken feature",
            assignee="openai-codex",
            workspace_kind="dir",
            workspace_path=str(workspace),
        )
        assert kb.claim_task(conn, task_id, claimer="test") is not None

    def fake_run(cmd, **kwargs):
        if cmd[:2] == ["git", "status"]:
            return CompletedProcess(cmd, 0, stdout="", stderr="")
        if cmd[:2] == ["git", "diff"]:
            return CompletedProcess(cmd, 0, stdout="", stderr="")
        assert cmd[0] == "codex"
        assert "exec" in cmd
        return CompletedProcess(
            cmd,
            7,
            stdout="before failure\n",
            stderr="codex auth failed\n",
        )

    monkeypatch.setattr(codex_worker.shutil, "which", lambda name: "/usr/bin/codex")
    monkeypatch.setattr(codex_worker.subprocess, "run", fake_run)

    rc = codex_worker.main([
        "--task-id", task_id,
        "--workspace", str(workspace),
        "--board", "default",
    ])

    assert rc == 7
    with kb.connect() as conn:
        task = kb.get_task(conn, task_id)
        run = kb.latest_run(conn, task_id)
    assert task.status == "blocked"
    assert run.summary == "Codex failed"
    assert run.metadata["codex"]["exit_code"] == 7
    assert "codex auth failed" in run.error
    assert "before failure" in kb.worker_log_path(task_id, board="default").read_text(
        encoding="utf-8"
    )
