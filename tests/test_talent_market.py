"""Tests for the OMC Dynamic Talent Market.

Covers:
  * Schema creation (idempotent)
  * Profile refresh from synthetic run data
  * Skill extraction from task title/body/skills
  * Matching score computation
  * Auto-assignment (dry-run and real)
  * Dispatcher integration wrapper
"""

from __future__ import annotations

import json
import sqlite3
import time
from typing import Any

import pytest

from hermes_cli import kanban_db as kb
from hermes_cli import talent_market as tm


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def in_memory_db():
    """Fresh in-memory kanban DB with the full schema (production isolation)."""
    conn = sqlite3.connect(":memory:", isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.executescript(kb.SCHEMA_SQL)
    kb._migrate_add_optional_columns(conn)
    tm.init_talent_schema(conn)
    yield conn
    conn.close()


def _task(**kwargs):
    """Build a kanban_db.Task with sensible defaults for tests."""
    defaults = dict(
        id="t", title="", body="", assignee=None, status="todo",
        priority=0, created_by=None, created_at=0, started_at=None,
        completed_at=None, workspace_kind="scratch", workspace_path=None,
        claim_lock=None, claim_expires=None, tenant=None, skills=None,
    )
    defaults.update(kwargs)
    return kb.Task(**defaults)


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

def test_talent_schema_idempotent(in_memory_db):
    """Calling init_talent_schema twice must not error."""
    tm.init_talent_schema(in_memory_db)
    cur = in_memory_db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='talent_profiles'"
    )
    assert cur.fetchone() is not None


# ---------------------------------------------------------------------------
# Profile refresh
# ---------------------------------------------------------------------------

def _seed_task_and_runs(conn: sqlite3.Connection, task_id: str, profile: str, outcome: str, skills=None):
    """Helper: insert a task + one run."""
    now = int(time.time())
    _insert_task(
        conn,
        id=task_id,
        title=f"Task {task_id}",
        body="",
        assignee=profile,
        status="done",
        created_at=now - 3600,
        skills=skills,
    )
    # Create a run manually.
    started = now - 1800
    ended = now - 1200 if outcome == "completed" else None
    conn.execute(
        """
        INSERT INTO task_runs (task_id, profile, status, started_at, ended_at, outcome)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (task_id, profile, "done" if outcome == "completed" else outcome, started, ended, outcome),
    )


def _insert_task(conn, *, id, title, body=None, assignee=None, status="todo", created_at=None, skills=None):
    now = created_at if created_at is not None else int(time.time())
    conn.execute(
        """
        INSERT INTO tasks (id, title, body, assignee, status, priority,
                           created_by, created_at, workspace_kind, workspace_path,
                           tenant, idempotency_key, max_runtime_seconds, skills)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            id, title, body, assignee, status, 0,
            None, now, "scratch", None,
            None, None, None,
            json.dumps(skills) if skills is not None else None,
        ),
    )


def test_refresh_profiles_from_runs(in_memory_db):
    db = in_memory_db
    _seed_task_and_runs(db, "t1", "alice", "completed")
    _seed_task_and_runs(db, "t2", "alice", "completed")
    _seed_task_and_runs(db, "t3", "alice", "crashed")
    _seed_task_and_runs(db, "t4", "bob", "completed")

    profiles = tm.refresh_talent_profiles(db, profiles=["alice", "bob"])
    by_name = {p.profile: p for p in profiles}

    assert "alice" in by_name
    assert "bob" in by_name
    assert by_name["alice"].total_tasks == 3
    assert by_name["alice"].completed_tasks == 2
    assert by_name["alice"].failed_tasks == 1
    # Success rate = 2/3
    assert abs(by_name["alice"].success_rate - 2 / 3) < 0.01


def test_refresh_preserves_existing_skills(in_memory_db):
    db = in_memory_db
    # Seed DB with pre-existing skills.
    now = int(time.time())
    db.execute(
        """
        INSERT INTO talent_profiles (profile, skills, created_at, updated_at)
        VALUES (?, ?, ?, ?)
        """,
        ("alice", json.dumps(["python", "rust"]), now, now),
    )
    _seed_task_and_runs(db, "t1", "alice", "completed")

    tm.refresh_talent_profiles(db, profiles=["alice"])
    row = db.execute("SELECT skills FROM talent_profiles WHERE profile='alice'").fetchone()
    skills = json.loads(row["skills"])
    # The refresh should still contain the old skills even if disk scan
    # finds nothing (in-memory DB has no real profile dir).
    assert "python" in skills


# ---------------------------------------------------------------------------
# Skill extraction
# ---------------------------------------------------------------------------

def test_extract_task_skills_from_explicit_list():
    task = _task(id="x", skills=["python", "docker"])
    assert tm._extract_task_skills(task) == {"python", "docker"}


def test_extract_task_skills_from_body():
    task = _task(
        id="x", title="Fix React bug", body="Update the kubernetes deployment",
        skills=None,
    )
    skills = tm._extract_task_skills(task)
    assert "react" in skills
    assert "kubernetes" in skills


# ---------------------------------------------------------------------------
# Matching
# ---------------------------------------------------------------------------

def _setup_match_scenario(db: sqlite3.Connection):
    """Alice: python expert, fast, high success.  Bob: rust expert, slower."""
    now = int(time.time())
    for profile, skills, success, avg_time in [
        ("alice", ["python", "docker"], 0.9, 120),
        ("bob", ["rust", "docker"], 0.6, 600),
    ]:
        db.execute(
            """
            INSERT INTO talent_profiles
                (profile, skills, success_rate, avg_completion_time,
                 total_tasks, completed_tasks, failed_tasks, running_tasks,
                 last_active, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (profile, json.dumps(skills), success, avg_time,
             10, int(10 * success), 10 - int(10 * success), 0,
             now, now, now),
        )


def test_match_prefers_skill_overlap(in_memory_db):
    db = in_memory_db
    _setup_match_scenario(db)
    task = _task(
        id="t", title="Python script", body="Write a python script",
        status="ready", skills=None,
    )
    matches = tm.match_task_to_profiles(db, task)
    assert matches[0].profile == "alice"
    assert matches[0].skill_score > matches[1].skill_score


def test_match_penalises_running_load(in_memory_db):
    db = in_memory_db
    _setup_match_scenario(db)
    # Mark alice as busy.
    db.execute(
        "UPDATE talent_profiles SET running_tasks = 3 WHERE profile = 'alice'"
    )
    task = _task(
        id="t", title="Python script", body="",
        status="ready", skills=None,
    )
    matches = tm.match_task_to_profiles(db, task)
    # With 3 running tasks alice's load score is 1 - 3*0.12 = 0.64,
    # which may flip the ranking depending on exact weights.
    alice = next(m for m in matches if m.profile == "alice")
    assert alice.load_score < 1.0


# ---------------------------------------------------------------------------
# Auto-assignment
# ---------------------------------------------------------------------------

def test_auto_assign_dry_run(in_memory_db):
    db = in_memory_db
    _setup_match_scenario(db)
    _insert_task(
        db, id="t_auto", title="Python script", body="",
        assignee=None, status="ready", created_at=int(time.time()),
    )
    match = tm.auto_assign_task(db, "t_auto", dry_run=True)
    assert match is not None
    assert match.profile == "alice"
    # Task must still be unassigned.
    t = kb.get_task(db, "t_auto")
    assert t.assignee is None


def test_auto_assign_real(in_memory_db):
    db = in_memory_db
    _setup_match_scenario(db)
    _insert_task(
        db, id="t_auto", title="Python script", body="",
        assignee=None, status="ready", created_at=int(time.time()),
    )
    match = tm.auto_assign_task(db, "t_auto", dry_run=False)
    assert match is not None
    t = kb.get_task(db, "t_auto")
    assert t.assignee == "alice"


def test_auto_assign_respects_min_score(in_memory_db):
    db = in_memory_db
    _setup_match_scenario(db)
    _insert_task(
        db, id="t_auto", title="Obscure work", body="completely unknown domain",
        assignee=None, status="ready", created_at=int(time.time()),
    )
    match = tm.auto_assign_task(db, "t_auto", min_score=0.99, dry_run=False)
    assert match is None


# ---------------------------------------------------------------------------
# Dispatcher integration
# ---------------------------------------------------------------------------

def test_dispatch_once_with_talent_auto_assigns(in_memory_db):
    db = in_memory_db
    _setup_match_scenario(db)
    _insert_task(
        db, id="t_disp", title="Python script", body="",
        assignee=None, status="ready", created_at=int(time.time()),
    )
    # Dry-run so we don't try to spawn a real subprocess.
    res = tm.dispatch_once_with_talent(db, dry_run=True, auto_assign=True)
    t = kb.get_task(db, "t_disp")
    assert t.assignee == "alice"
    # In dry-run the task is still "ready" (claim requires non-dry run).
    assert t.status == "ready"
