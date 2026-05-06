"""Tests for the bounded Kanban MCP facade."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from hermes_cli import kanban_db as kb
from hermes_cli.kanban_mcp import KanbanMCPFacade


@pytest.fixture
def kanban_home(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.delenv("HERMES_KANBAN_HOME", raising=False)
    monkeypatch.delenv("HERMES_KANBAN_DB", raising=False)
    monkeypatch.delenv("HERMES_KANBAN_BOARD", raising=False)
    monkeypatch.delenv("HERMES_KANBAN_WORKSPACES_ROOT", raising=False)
    monkeypatch.delenv("HERMES_KANBAN_MCP_WRITE_MODE", raising=False)
    monkeypatch.delenv("HERMES_KANBAN_MCP_ALLOWED_BOARDS", raising=False)
    monkeypatch.delenv("HERMES_KANBAN_ALLOWED_BOARDS", raising=False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    kb.init_db()
    return home


def test_boards_list_reports_existing_boards(kanban_home):
    kb.create_board("alpha", name="Alpha Board")

    result = KanbanMCPFacade().boards_list()

    assert result["count"] == 2
    assert [b["slug"] for b in result["boards"]] == ["default", "alpha"]
    assert result["boards"][1]["name"] == "Alpha Board"


def test_create_comment_link_and_assign_preserve_audit_events(kanban_home):
    facade = KanbanMCPFacade()
    parent = facade.task_create(
        board="default",
        title="parent",
        assignee="builder",
        created_by="mcp-test",
    )["task"]["id"]
    child = facade.task_create(
        board="default",
        title="child",
        assignee="researcher",
        created_by="mcp-test",
    )["task"]["id"]

    assert facade.task_comment(
        board="default", task_id=parent, body="visible note", author="external-llm"
    )["ok"] is True
    assert facade.task_link(board="default", parent_id=parent, child_id=child)["ok"] is True
    assert facade.task_assign(board="default", task_id=child, assignee="reviewer")["ok"] is True

    shown = facade.task_show(board="default", task_id=child)
    assert shown["task"]["assignee"] == "reviewer"
    assert shown["parents"] == [parent]
    assert any(e["kind"] == "linked" for e in shown["events"])
    parent_shown = facade.task_show(board="default", task_id=parent)
    assert parent_shown["comments"][0]["body"] == "visible note"
    assert any(e["kind"] == "commented" for e in parent_shown["events"])


def test_board_isolation_requires_explicit_board_and_does_not_cross_read(kanban_home):
    kb.create_board("alpha")
    with kb.connect(board="default") as conn:
        default_tid = kb.create_task(conn, title="default task", tenant="tenant-a")
    with kb.connect(board="alpha") as conn:
        alpha_tid = kb.create_task(conn, title="alpha task", tenant="tenant-a")

    facade = KanbanMCPFacade()

    assert [t["id"] for t in facade.tasks_list(board="default")["tasks"]] == [default_tid]
    assert [t["id"] for t in facade.tasks_list(board="alpha")["tasks"]] == [alpha_tid]
    assert facade.task_show(board="default", task_id=alpha_tid)["error"] == "task_not_found"

    with pytest.raises(ValueError, match="board is required"):
        facade.tasks_list(board="")


def test_board_allowlist_filters_and_denies_unlisted_boards(kanban_home, monkeypatch):
    kb.create_board("alpha")
    kb.create_board("beta")
    with kb.connect(board="alpha") as conn:
        alpha_tid = kb.create_task(conn, title="alpha task")
    with kb.connect(board="beta") as conn:
        kb.create_task(conn, title="beta task")

    monkeypatch.setenv("HERMES_KANBAN_MCP_ALLOWED_BOARDS", "alpha")
    facade = KanbanMCPFacade()

    listed = facade.boards_list()
    assert [b["slug"] for b in listed["boards"]] == ["alpha"]
    assert [t["id"] for t in facade.tasks_list(board="alpha")["tasks"]] == [alpha_tid]
    denied = facade.tasks_list(board="beta")
    assert denied["error"] == "board_not_allowed"
    assert denied["allowed_boards"] == ["alpha"]


def test_invalid_board_returns_structured_error(kanban_home):
    result = KanbanMCPFacade().tasks_list(board="missing-board")

    assert result["error"] == "board_not_found"
    assert result["board"] == "missing-board"


def test_readonly_mode_denies_safe_mutations(kanban_home, monkeypatch):
    monkeypatch.setenv("HERMES_KANBAN_MCP_WRITE_MODE", "readonly")
    result = KanbanMCPFacade().task_create(board="default", title="nope")

    assert result["error"] == "write_disabled"
    assert result["write_mode"] == "readonly"
    with kb.connect() as conn:
        assert kb.list_tasks(conn) == []


def test_dangerous_operations_are_gated_until_operator_mode(kanban_home, monkeypatch):
    facade = KanbanMCPFacade()
    task_id = facade.task_create(board="default", title="finish me")["task"]["id"]

    denied = facade.task_complete(board="default", task_id=task_id, result="done")
    assert denied["error"] == "operator_mode_required"
    with kb.connect() as conn:
        assert kb.get_task(conn, task_id).status == "ready"

    monkeypatch.setenv("HERMES_KANBAN_MCP_WRITE_MODE", "operator")
    allowed = facade.task_complete(board="default", task_id=task_id, result="done")
    assert allowed["ok"] is True
    assert facade.task_show(board="default", task_id=task_id)["task"]["status"] == "done"


def test_dispatch_dry_run_is_safe_but_real_dispatch_requires_operator(kanban_home, monkeypatch):
    monkeypatch.setattr("hermes_cli.profiles.profile_exists", lambda name: True)
    facade = KanbanMCPFacade()
    task_id = facade.task_create(board="default", title="spawnable", assignee="builder")["task"]["id"]

    dry = facade.dispatch_dry_run(board="default")
    assert dry["dry_run"] is True
    assert dry["spawned"][0]["task_id"] == task_id

    real = facade.dispatch(board="default")
    assert real["error"] == "operator_mode_required"

    monkeypatch.setenv("HERMES_KANBAN_MCP_WRITE_MODE", "operator")
    # Use a stubbed spawn function so the test never launches Hermes workers.
    real = facade.dispatch(board="default", _spawn_fn=lambda task, workspace, **kw: 12345)
    assert real["dry_run"] is False
    assert real["spawned"][0]["task_id"] == task_id
