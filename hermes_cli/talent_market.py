"""OMC Dynamic Talent Market for Hermes agents.

Provides task-to-talent matching, talent profile management, and dispatcher
integration for the Hermes kanban system.

Design:
  * Talent profiles are derived from two sources:
    1. Historical task runs in the kanban DB (success rates, completion times)
    2. Profile skill directories on disk (discovered skills)
  * Matching scores combine skill overlap, historical performance, load,
    and recency.
  * The dispatcher can auto-assign unassigned tasks via talent matching.
"""

from __future__ import annotations

import json
import math
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from hermes_cli import kanban_db as kb
from hermes_cli.profiles import list_profiles, normalize_profile_name


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Weightings for the composite match score (sum to 1.0 conceptually,
# though we normalise by the max possible so scores stay in [0,1]).
_WEIGHT_SKILL_OVERLAP = 0.35
_WEIGHT_SUCCESS_RATE = 0.25
_WEIGHT_SPEED = 0.15
_WEIGHT_LOAD = 0.15
_WEIGHT_RECENCY = 0.10

# Penalty per currently-running task (diminishing returns).
_LOAD_PENALTY_PER_TASK = 0.12

# Maximum completion time we consider "fast" (seconds). Tasks faster than
# this get the full speed bonus; slower ones get a linear decay.
_FAST_COMPLETION_SECONDS = 600  # 10 minutes

# Recency half-life: after this many seconds without activity, the recency
# score decays to 0.5.
_RECENCY_HALF_LIFE_SECONDS = 7 * 24 * 3600  # 7 days


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass
class TalentProfile:
    """Aggregated performance + skill snapshot for a single profile."""

    profile: str
    skills: set[str] = field(default_factory=set)
    success_rate: float = 0.0          # 0.0–1.0
    avg_completion_time: Optional[float] = None  # seconds
    total_tasks: int = 0
    completed_tasks: int = 0
    failed_tasks: int = 0
    running_tasks: int = 0
    last_active: Optional[int] = None  # unix timestamp
    created_at: Optional[int] = None   # unix timestamp

    def to_dict(self) -> dict[str, Any]:
        return {
            "profile": self.profile,
            "skills": sorted(self.skills),
            "success_rate": round(self.success_rate, 4),
            "avg_completion_time": (
                round(self.avg_completion_time, 1)
                if self.avg_completion_time is not None else None
            ),
            "total_tasks": self.total_tasks,
            "completed_tasks": self.completed_tasks,
            "failed_tasks": self.failed_tasks,
            "running_tasks": self.running_tasks,
            "last_active": self.last_active,
            "created_at": self.created_at,
        }


@dataclass
class MatchResult:
    """Result of matching a task against a talent profile."""

    profile: str
    score: float                       # 0.0–1.0 composite
    skill_score: float = 0.0
    success_rate_score: float = 0.0
    speed_score: float = 0.0
    load_score: float = 0.0
    recency_score: float = 0.0
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "profile": self.profile,
            "score": round(self.score, 4),
            "skill_score": round(self.skill_score, 4),
            "success_rate_score": round(self.success_rate_score, 4),
            "speed_score": round(self.speed_score, 4),
            "load_score": round(self.load_score, 4),
            "recency_score": round(self.recency_score, 4),
            "reason": self.reason,
        }


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

_TALENT_SCHEMA = """
CREATE TABLE IF NOT EXISTS talent_profiles (
    profile                TEXT PRIMARY KEY,
    skills                 TEXT,            -- JSON array of skill names
    success_rate           REAL DEFAULT 0.0,
    avg_completion_time    REAL,            -- seconds
    total_tasks            INTEGER DEFAULT 0,
    completed_tasks        INTEGER DEFAULT 0,
    failed_tasks           INTEGER DEFAULT 0,
    running_tasks          INTEGER DEFAULT 0,
    last_active            INTEGER,         -- unix timestamp
    created_at             INTEGER NOT NULL,
    updated_at             INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS talent_skill_stats (
    profile                TEXT NOT NULL,
    skill                  TEXT NOT NULL,
    proficiency_score      REAL DEFAULT 0.0, -- 0.0-1.0
    tasks_completed        INTEGER DEFAULT 0,
    avg_completion_time    REAL,
    PRIMARY KEY (profile, skill)
);

CREATE INDEX IF NOT EXISTS idx_talent_skill_profile ON talent_skill_stats(profile);
CREATE INDEX IF NOT EXISTS idx_talent_skill_skill   ON talent_skill_stats(skill);
"""


def init_talent_schema(conn: sqlite3.Connection) -> None:
    """Create talent-market tables inside the kanban DB (idempotent)."""
    conn.executescript(_TALENT_SCHEMA)


# ---------------------------------------------------------------------------
# Skill extraction
# ---------------------------------------------------------------------------

def _extract_task_skills(task: kb.Task) -> set[str]:
    """Derive a set of skill-like tokens from a task.

    Sources (in priority order):
      1. Explicit ``task.skills`` list (force-loaded skills on the task).
      2. Keyword tokens from title + body (simple heuristic).
    """
    skills: set[str] = set()
    if task.skills:
        skills.update(str(s).lower().strip() for s in task.skills if s)

    text = f"{task.title or ''} {task.body or ''}".lower()
    # Simple keyword extraction: look for known skill-like terms.
    # In a production system this could use an LLM or embedding model;
    # here we use a fast heuristic that catches common skill names.
    known_keywords = {
        "python", "rust", "swift", "javascript", "typescript", "go", "c++",
        "docker", "kubernetes", "terraform", "ansible", "aws", "gcp", "azure",
        "react", "vue", "angular", "svelte", "nextjs", "nuxt",
        "ml", "ai", "llm", "rl", "training", "inference", "benchmark",
        "frontend", "backend", "devops", "security", "testing",
        "physics", "cfd", "fea", "modal", "buckling", "composite",
        "web", "browser", "cli", "gateway", "telegram", "discord", "slack",
        "ios", "macos", "android", "linux", "windows",
        "sql", "postgres", "mysql", "sqlite", "redis", "mongo",
        "git", "github", "ci", "cd", "review", "refactor", "debug",
        "docs", "design", "ux", "product", "marketing", "gtm",
        "data", "analytics", "visualisation", "dashboard",
        "math", "algorithm", "simulation", "solver", "mesh",
    }
    for kw in known_keywords:
        if kw in text:
            skills.add(kw)
    return skills


def _profile_skills_from_disk(profile: str) -> set[str]:
    """Read the skills installed in a profile's ``skills/`` directory."""
    from hermes_cli.profiles import get_profile_dir
    skills_dir = get_profile_dir(profile) / "skills"
    if not skills_dir.is_dir():
        return set()
    found: set[str] = set()
    for md in skills_dir.rglob("SKILL.md"):
        if "/.hub/" in str(md) or "/.git/" in str(md):
            continue
        found.add(md.parent.name.lower())
    return found


# ---------------------------------------------------------------------------
# Profile sync / refresh
# ---------------------------------------------------------------------------

def refresh_talent_profiles(
    conn: sqlite3.Connection,
    *,
    profiles: Optional[list[str]] = None,
    include_disk_skills: bool = True,
) -> list[TalentProfile]:
    """Recompute talent profiles from kanban run history + disk skills.

    Returns the updated profiles. When ``profiles`` is None, every profile
    that appears in ``task_runs`` or on disk is refreshed.
    """
    init_talent_schema(conn)
    now = int(time.time())

    # 1. Gather candidate profiles from runs + disk.
    cursor = conn.execute(
        "SELECT DISTINCT profile FROM task_runs WHERE profile IS NOT NULL"
    )
    from_runs = {r["profile"] for r in cursor if r["profile"]}

    if profiles is not None:
        target = set(normalize_profile_name(p) for p in profiles)
    else:
        target = from_runs.copy()
        if include_disk_skills:
            for pi in list_profiles():
                target.add(pi.name)

    results: list[TalentProfile] = []

    for profile in sorted(target):
        # Aggregate runs for this profile.
        runs = conn.execute(
            """
            SELECT
                outcome,
                started_at,
                ended_at,
                task_id
            FROM task_runs
            WHERE profile = ?
            ORDER BY started_at DESC
            """,
            (profile,),
        ).fetchall()

        completed = 0
        failed = 0
        total = len(runs)
        completion_times: list[float] = []
        task_ids: set[str] = set()
        last_active: Optional[int] = None

        for run in runs:
            task_ids.add(run["task_id"])
            if run["outcome"] == "completed":
                completed += 1
                if run["started_at"] and run["ended_at"]:
                    dt = run["ended_at"] - run["started_at"]
                    if dt > 0:
                        completion_times.append(float(dt))
            elif run["outcome"] in ("crashed", "timed_out", "spawn_failed", "gave_up"):
                failed += 1
            if run["ended_at"]:
                if last_active is None or run["ended_at"] > last_active:
                    last_active = run["ended_at"]

        # Running tasks right now.
        running_row = conn.execute(
            "SELECT COUNT(*) AS n FROM tasks WHERE assignee = ? AND status = 'running'",
            (profile,),
        ).fetchone()
        running = int(running_row["n"]) if running_row else 0

        success_rate = completed / total if total > 0 else 0.0
        avg_time = (
            sum(completion_times) / len(completion_times)
            if completion_times else None
        )

        # Skills: union of disk skills + explicitly recorded skills.
        skills: set[str] = set()
        if include_disk_skills:
            skills.update(_profile_skills_from_disk(profile))
        # Also pull skills from the DB if they exist.
        db_skills_row = conn.execute(
            "SELECT skills FROM talent_profiles WHERE profile = ?",
            (profile,),
        ).fetchone()
        if db_skills_row and db_skills_row["skills"]:
            try:
                skills.update(json.loads(db_skills_row["skills"]))
            except Exception:
                pass

        # Upsert the profile row.
        conn.execute(
            """
            INSERT INTO talent_profiles (
                profile, skills, success_rate, avg_completion_time,
                total_tasks, completed_tasks, failed_tasks, running_tasks,
                last_active, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(profile) DO UPDATE SET
                skills = excluded.skills,
                success_rate = excluded.success_rate,
                avg_completion_time = excluded.avg_completion_time,
                total_tasks = excluded.total_tasks,
                completed_tasks = excluded.completed_tasks,
                failed_tasks = excluded.failed_tasks,
                running_tasks = excluded.running_tasks,
                last_active = excluded.last_active,
                updated_at = excluded.updated_at
            """,
            (
                profile,
                json.dumps(sorted(skills)),
                success_rate,
                avg_time,
                total,
                completed,
                failed,
                running,
                last_active,
                now,
                now,
            ),
        )

        # Rebuild per-skill stats from runs where the task had skills.
        conn.execute(
            "DELETE FROM talent_skill_stats WHERE profile = ?",
            (profile,),
        )
        skill_agg: dict[str, dict[str, Any]] = {}
        for task_id in task_ids:
            trow = conn.execute(
                "SELECT skills FROM tasks WHERE id = ?", (task_id,)
            ).fetchone()
            if not trow or not trow["skills"]:
                continue
            try:
                task_skills = json.loads(trow["skills"])
            except Exception:
                continue
            # Find the run for this task/profile.
            run_row = conn.execute(
                """
                SELECT outcome, started_at, ended_at
                FROM task_runs
                WHERE task_id = ? AND profile = ?
                ORDER BY started_at DESC LIMIT 1
                """,
                (task_id, profile),
            ).fetchone()
            if not run_row:
                continue
            for sk in task_skills:
                sk = str(sk).lower().strip()
                if not sk:
                    continue
                if sk not in skill_agg:
                    skill_agg[sk] = {
                        "completed": 0, "total": 0, "times": []
                    }
                skill_agg[sk]["total"] += 1
                if run_row["outcome"] == "completed":
                    skill_agg[sk]["completed"] += 1
                    if run_row["started_at"] and run_row["ended_at"]:
                        dt = run_row["ended_at"] - run_row["started_at"]
                        if dt > 0:
                            skill_agg[sk]["times"].append(float(dt))

        for sk, agg in skill_agg.items():
            prof = agg["completed"] / agg["total"] if agg["total"] > 0 else 0.0
            avg_sk_time = (
                sum(agg["times"]) / len(agg["times"]) if agg["times"] else None
            )
            conn.execute(
                """
                INSERT INTO talent_skill_stats
                    (profile, skill, proficiency_score, tasks_completed, avg_completion_time)
                VALUES (?, ?, ?, ?, ?)
                """,
                (profile, sk, prof, agg["completed"], avg_sk_time),
            )

        tp = TalentProfile(
            profile=profile,
            skills=skills,
            success_rate=success_rate,
            avg_completion_time=avg_time,
            total_tasks=total,
            completed_tasks=completed,
            failed_tasks=failed,
            running_tasks=running,
            last_active=last_active,
            created_at=now,
        )
        results.append(tp)

    return results


# ---------------------------------------------------------------------------
# Matching
# ---------------------------------------------------------------------------

def match_task_to_profiles(
    conn: sqlite3.Connection,
    task: kb.Task,
    *,
    candidates: Optional[list[str]] = None,
) -> list[MatchResult]:
    """Score every candidate profile against *task* and return descending list.

    When ``candidates`` is None, every profile with a talent record is
    considered. If the talent table is empty, falls back to all profiles
    known on disk + all current assignees on the board.
    """
    init_talent_schema(conn)
    task_skills = _extract_task_skills(task)

    # Ensure profiles are materialised.
    cursor = conn.execute("SELECT profile FROM talent_profiles")
    db_profiles = {r["profile"] for r in cursor}
    if not db_profiles:
        refresh_talent_profiles(conn)
        cursor = conn.execute("SELECT profile FROM talent_profiles")
        db_profiles = {r["profile"] for r in cursor}

    if candidates is not None:
        target = [normalize_profile_name(p) for p in candidates]
    else:
        target = sorted(db_profiles)

    now = int(time.time())
    results: list[MatchResult] = []

    for profile in target:
        row = conn.execute(
            """
            SELECT skills, success_rate, avg_completion_time,
                   total_tasks, completed_tasks, failed_tasks, running_tasks, last_active
            FROM talent_profiles
            WHERE profile = ?
            """,
            (profile,),
        ).fetchone()

        if row is None:
            # No talent record yet — build a minimal result from disk.
            disk_skills = _profile_skills_from_disk(profile)
            skill_overlap = disk_skills & task_skills
            skill_score = (
                len(skill_overlap) / max(len(task_skills), 1)
                if task_skills else 0.0
            )
            # Slight penalty for unknown profiles so they don't outrank
            # proven performers when skills match equally.
            score = skill_score * 0.7
            results.append(MatchResult(
                profile=profile,
                score=round(score, 4),
                skill_score=round(skill_score, 4),
                success_rate_score=0.0,
                speed_score=0.0,
                load_score=0.5,
                recency_score=0.0,
                reason="no historical data — matched on installed skills only",
            ))
            continue

        # Parse skills.
        try:
            profile_skills = set(json.loads(row["skills"] or "[]"))
        except Exception:
            profile_skills = set()

        skill_overlap = profile_skills & task_skills
        if task_skills:
            skill_score = len(skill_overlap) / len(task_skills)
        else:
            # Task has no explicit skills — fall back to "does the profile
            # have ANY skills at all" as a weak discriminator.
            skill_score = 0.5 if profile_skills else 0.0

        success_rate = float(row["success_rate"] or 0.0)
        avg_time = row["avg_completion_time"]
        running = int(row["running_tasks"] or 0)
        last_active = row["last_active"]
        total_tasks = int(row["total_tasks"] or 0)

        # Success-rate score: squashed so 90% → ~0.99, 50% → ~0.76.
        success_rate_score = math.tanh(success_rate * 2.5)

        # Speed score: faster is better, capped at _FAST_COMPLETION_SECONDS.
        if avg_time is not None and avg_time > 0:
            speed_score = max(0.0, 1.0 - (avg_time / _FAST_COMPLETION_SECONDS))
        else:
            # No data → neutral (0.5) so it doesn't penalise new profiles.
            speed_score = 0.5

        # Load score: penalise currently-busy profiles, but not linearly
        # (a profile with 1 task is fine; 5 is heavily penalised).
        load_score = max(0.0, 1.0 - (_LOAD_PENALTY_PER_TASK * running))

        # Recency score: exponential decay since last activity.
        if last_active:
            age = max(0, now - int(last_active))
            recency_score = math.exp(-age / _RECENCY_HALF_LIFE_SECONDS)
        else:
            recency_score = 0.0

        # Composite score — weighted sum, normalised by the weight total
        # so it stays roughly in [0, 1].
        total_weight = (
            _WEIGHT_SKILL_OVERLAP + _WEIGHT_SUCCESS_RATE +
            _WEIGHT_SPEED + _WEIGHT_LOAD + _WEIGHT_RECENCY
        )
        score = (
            _WEIGHT_SKILL_OVERLAP * skill_score +
            _WEIGHT_SUCCESS_RATE * success_rate_score +
            _WEIGHT_SPEED * speed_score +
            _WEIGHT_LOAD * load_score +
            _WEIGHT_RECENCY * recency_score
        ) / total_weight

        # Boost profiles that have proven themselves (>=5 tasks completed).
        if total_tasks >= 5 and success_rate >= 0.7:
            score = min(1.0, score * 1.1)

        # Mild penalty for profiles with very poor success rate.
        if total_tasks >= 3 and success_rate < 0.3:
            score *= 0.7

        reasons: list[str] = []
        if skill_overlap:
            reasons.append(f"skill overlap: {', '.join(sorted(skill_overlap))}")
        if total_tasks:
            reasons.append(
                f"history: {total_tasks} tasks, {success_rate:.0%} success"
            )
        if running:
            reasons.append(f"load: {running} running")
        if not reasons:
            reasons.append("fallback match")

        results.append(MatchResult(
            profile=profile,
            score=round(score, 4),
            skill_score=round(skill_score, 4),
            success_rate_score=round(success_rate_score, 4),
            speed_score=round(speed_score, 4),
            load_score=round(load_score, 4),
            recency_score=round(recency_score, 4),
            reason="; ".join(reasons),
        ))

    results.sort(key=lambda r: r.score, reverse=True)
    return results


# ---------------------------------------------------------------------------
# Dispatcher integration
# ---------------------------------------------------------------------------

def auto_assign_task(
    conn: sqlite3.Connection,
    task_id: str,
    *,
    top_k: int = 1,
    min_score: float = 0.05,
    dry_run: bool = False,
) -> Optional[MatchResult]:
    """Find the best profile for *task_id* and assign it.

    Returns the :class:`MatchResult` on success, ``None`` if no suitable
    candidate exceeds ``min_score``.
    """
    task = kb.get_task(conn, task_id)
    if task is None:
        return None
    if task.assignee:
        # Already assigned — optionally verify it's optimal, but for now
        # respect the existing assignment.
        return None

    matches = match_task_to_profiles(conn, task)
    if not matches:
        return None

    best = matches[0]
    if best.score < min_score:
        return None

    if dry_run:
        return best

    conn.execute(
        "UPDATE tasks SET assignee = ? WHERE id = ? AND assignee IS NULL",
        (best.profile, task_id),
    )
    kb._append_event(conn, task_id, "auto_assigned", {
        "profile": best.profile,
        "score": best.score,
        "reason": best.reason,
    })
    return best


def dispatch_once_with_talent(
    conn: sqlite3.Connection,
    *,
    spawn_fn=None,
    ttl_seconds: int = kb.DEFAULT_CLAIM_TTL_SECONDS,
    dry_run: bool = False,
    max_spawn: Optional[int] = None,
    failure_limit: int = kb.DEFAULT_SPAWN_FAILURE_LIMIT,
    board: Optional[str] = None,
    auto_assign: bool = True,
    min_match_score: float = 0.05,
) -> kb.DispatchResult:
    """Wrapper around ``kanban_db.dispatch_once`` that auto-assigns unassigned
    ready tasks via the talent market before spawning.

    All arguments mirror ``dispatch_once``; ``auto_assign`` and
    ``min_match_score`` are talent-market specific.
    """
    if auto_assign:
        init_talent_schema(conn)
        # Find all ready, unassigned tasks and try to match them.
        rows = conn.execute(
            "SELECT id FROM tasks WHERE status = 'ready' AND assignee IS NULL"
        ).fetchall()
        assigned_count = 0
        for row in rows:
            match = auto_assign_task(
                conn, row["id"],
                min_score=min_match_score,
                dry_run=False,
            )
            if match:
                assigned_count += 1
        if assigned_count:
            # Emit a single synthetic event on the board (not tied to a
            # specific task) so the dashboard can show "talent market
            # auto-assigned N tasks".
            kb._append_event(
                conn, "__board__", "talent_auto_assigned",
                {"count": assigned_count, "timestamp": int(time.time())},
            )

    # Fall through to the standard dispatch pass.
    return kb.dispatch_once(
        conn,
        spawn_fn=spawn_fn,
        ttl_seconds=ttl_seconds,
        dry_run=dry_run,
        max_spawn=max_spawn,
        failure_limit=failure_limit,
        board=board,
    )


# ---------------------------------------------------------------------------
# Stats / leaderboard
# ---------------------------------------------------------------------------

def talent_leaderboard(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Return a ranked list of talent profiles for the dashboard."""
    init_talent_schema(conn)
    rows = conn.execute(
        """
        SELECT
            profile,
            skills,
            success_rate,
            avg_completion_time,
            total_tasks,
            completed_tasks,
            failed_tasks,
            running_tasks,
            last_active
        FROM talent_profiles
        ORDER BY completed_tasks DESC, success_rate DESC
        """
    ).fetchall()

    now = int(time.time())
    out: list[dict[str, Any]] = []
    for row in rows:
        try:
            skills = json.loads(row["skills"] or "[]")
        except Exception:
            skills = []
        out.append({
            "profile": row["profile"],
            "skills": skills,
            "success_rate": row["success_rate"],
            "avg_completion_time": row["avg_completion_time"],
            "total_tasks": row["total_tasks"],
            "completed_tasks": row["completed_tasks"],
            "failed_tasks": row["failed_tasks"],
            "running_tasks": row["running_tasks"],
            "last_active": row["last_active"],
            "idle_seconds": (
                now - int(row["last_active"]) if row["last_active"] else None
            ),
        })
    return out
