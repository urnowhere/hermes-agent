"""CLI for the OMC Dynamic Talent Market — ``hermes kanban talent …`` subcommands.

Attaches under the existing ``kanban`` parser via the ``talent`` action.
All DB work is delegated to ``talent_market``.  This module adds:

  * Argparse subcommand construction.
  * Output formatting (plain text + ``--json``).
  * A thin wrapper that imports ``talent_market`` lazily so the kanban
    CLI still starts quickly when the talent feature isn't in use.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Optional


# ---------------------------------------------------------------------------
# Lazy import so startup is fast when talent commands aren't used.
# talent_market.py is a sibling of this file in the hermes_cli package.
# ---------------------------------------------------------------------------
def _tm():
    """Lazy import of talent_market to avoid heavy import on CLI boot."""
    import importlib
    from hermes_cli import talent_market
    return talent_market


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

def _fmt_duration(seconds: Optional[float]) -> str:
    if seconds is None or seconds < 0:
        return "—"
    if seconds < 60:
        return f"{int(seconds)}s"
    if seconds < 3600:
        return f"{int(seconds / 60)}m"
    return f"{seconds / 3600:.1f}h"


def _fmt_profile_line(p: dict[str, Any]) -> str:
    skills = p.get("skills", [])
    skill_str = ",".join(skills[:5]) + ("…" if len(skills) > 5 else "")
    return (
        f"  {p['profile']:20s}  "
        f"success={p['success_rate']:.0%}  "
        f"tasks={p['completed_tasks']}/{p['total_tasks']}  "
        f"running={p['running_tasks']}  "
        f"avg={_fmt_duration(p.get('avg_completion_time'))}  "
        f"[{skill_str}]"
    )


def _fmt_match_line(m: dict[str, Any]) -> str:
    return (
        f"  {m['profile']:20s}  score={m['score']:.3f}  "
        f"(skill={m['skill_score']:.2f}  "
        f"success={m['success_rate_score']:.2f}  "
        f"speed={m['speed_score']:.2f}  "
        f"load={m['load_score']:.2f}  "
        f"recency={m['recency_score']:.2f})"
        f"\n    → {m['reason']}"
    )


# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------

def _cmd_refresh(args: argparse.Namespace) -> int:
    from hermes_cli import kanban_db as kb
    tm = _tm()
    with kb.connect(board=getattr(args, "board", None)) as conn:
        profiles = tm.refresh_talent_profiles(
            conn,
            profiles=args.profiles or None,
            include_disk_skills=True,
        )
    if args.json:
        print(json.dumps([p.to_dict() for p in profiles], indent=2))
        return 0
    print(f"Refreshed {len(profiles)} talent profile(s):")
    for p in profiles:
        print(_fmt_profile_line(p.to_dict()))
    return 0


def _cmd_list(args: argparse.Namespace) -> int:
    from hermes_cli import kanban_db as kb
    tm = _tm()
    with kb.connect(board=getattr(args, "board", None)) as conn:
        board = tm.talent_leaderboard(conn)
    if args.json:
        print(json.dumps(board, indent=2))
        return 0
    print(f"{'Profile':20s}  {'Success':>7s}  {'Tasks':>10s}  {'Running':>7s}  {'Avg':>8s}  Skills")
    print("-" * 90)
    for p in board:
        skills = ",".join(p.get("skills", [])[:5])
        total = p.get("total_tasks", 0)
        completed = p.get("completed_tasks", 0)
        print(
            f"{p['profile']:20s}  "
            f"{p.get('success_rate', 0.0):>7.0%}  "
            f"{completed}/{total:>5d}  "
            f"{p.get('running_tasks', 0):>7d}  "
            f"{_fmt_duration(p.get('avg_completion_time')):>8s}  "
            f"{skills}"
        )
    return 0


def _cmd_match(args: argparse.Namespace) -> int:
    from hermes_cli import kanban_db as kb
    tm = _tm()
    with kb.connect(board=getattr(args, "board", None)) as conn:
        task = kb.get_task(conn, args.task_id)
        if task is None:
            print(f"ERROR: task {args.task_id} not found", file=sys.stderr)
            return 1
        matches = tm.match_task_to_profiles(conn, task)
    if args.json:
        print(json.dumps([m.to_dict() for m in matches], indent=2))
        return 0
    print(f"Task: {task.id} — {task.title}")
    print(f"Skills extracted: {', '.join(sorted(tm._extract_task_skills(task))) or '(none)'}")
    print(f"\nTop {args.top_k or len(matches)} match(es):")
    for m in (matches[:args.top_k] if args.top_k else matches):
        print(_fmt_match_line(m.to_dict()))
    return 0


def _cmd_assign(args: argparse.Namespace) -> int:
    from hermes_cli import kanban_db as kb
    tm = _tm()
    with kb.connect(board=getattr(args, "board", None)) as conn:
        match = tm.auto_assign_task(
            conn,
            args.task_id,
            min_score=args.min_score,
            dry_run=args.dry_run,
        )
    if match is None:
        print("No suitable candidate found (or task already assigned).")
        return 1
    action = "would assign" if args.dry_run else "assigned"
    print(
        f"{action} {args.task_id} -> {match.profile}  "
        f"(score={match.score:.3f}, reason: {match.reason})"
    )
    return 0


def _cmd_inspect(args: argparse.Namespace) -> int:
    """Show full talent profile for a single profile + per-skill stats."""
    from hermes_cli import kanban_db as kb
    tm = _tm()
    profile = args.profile
    with kb.connect(board=getattr(args, "board", None)) as conn:
        tm.init_talent_schema(conn)
        row = conn.execute(
            "SELECT * FROM talent_profiles WHERE profile = ?",
            (profile,),
        ).fetchone()
        skill_rows = conn.execute(
            "SELECT skill, proficiency_score, tasks_completed, avg_completion_time "
            "FROM talent_skill_stats WHERE profile = ? ORDER BY proficiency_score DESC",
            (profile,),
        ).fetchall()

    if row is None:
        print(f"No talent data for profile '{profile}'. Run `hermes kanban talent refresh`.")
        return 1

    out = dict(row)
    out["skills_detail"] = [
        {
            "skill": r["skill"],
            "proficiency": r["proficiency_score"],
            "tasks": r["tasks_completed"],
            "avg_time": r["avg_completion_time"],
        }
        for r in skill_rows
    ]

    if args.json:
        print(json.dumps(out, indent=2, default=str))
        return 0

    print(f"Talent Profile: {profile}")
    print(f"  Total tasks:    {out.get('total_tasks', 0)}")
    print(f"  Completed:      {out.get('completed_tasks', 0)}")
    print(f"  Failed:         {out.get('failed_tasks', 0)}")
    print(f"  Running:        {out.get('running_tasks', 0)}")
    print(f"  Success rate:   {out.get('success_rate', 0.0):.0%}")
    print(f"  Avg time:       {_fmt_duration(out.get('avg_completion_time'))}")
    try:
        skills = json.loads(out.get("skills", "[]") or "[]")
    except Exception:
        skills = []
    print(f"  Skills (disk):  {', '.join(skills) or '(none)'}")
    if out["skills_detail"]:
        print("\n  Per-skill proficiency:")
        for s in out["skills_detail"]:
            print(
                f"    {s['skill']:20s}  prof={s['proficiency']:.2f}  "
                f"tasks={s['tasks']}  avg={_fmt_duration(s['avg_time'])}"
            )
    return 0


# ---------------------------------------------------------------------------
# Parser attachment
# ---------------------------------------------------------------------------

def attach_talent_parser(
    parent_parser: argparse.ArgumentParser,
) -> None:
    """Attach ``talent`` subcommands under the given ``kanban`` parser.

    Called from ``kanban.py``'s ``build_parser`` after the main kanban
    subparsers are created.
    """
    # We need the subparsers action from the parent parser.
    # The kanban parser already has a ``subparsers dest="kanban_action"``.
    # We'll reach into its _subparsers to add our own.
    subparsers = None
    for action in parent_parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            subparsers = action
            break

    if subparsers is None:
        # Should never happen — kanban.py always creates subparsers.
        return

    talent_parser = subparsers.add_parser(
        "talent",
        help="Dynamic talent market — match tasks to the best profiles",
        description=(
            "The talent market aggregates historical task-run data and "
            "profile skills to score which profile is best suited for a "
            "given task. Commands below let you refresh the data, inspect "
            "leaderboards, test matches, and trigger auto-assignment."
        ),
    )
    talent_sub = talent_parser.add_subparsers(dest="talent_action")

    # --- refresh ---
    p_refresh = talent_sub.add_parser(
        "refresh",
        help="Recompute talent profiles from kanban history + disk skills",
    )
    p_refresh.add_argument(
        "profiles", nargs="*",
        help="Specific profiles to refresh (default: all)",
    )
    p_refresh.add_argument("--json", action="store_true")
    p_refresh.set_defaults(_handler=_cmd_refresh)

    # --- list ---
    p_list = talent_sub.add_parser(
        "list", aliases=["ls"],
        help="Show the talent leaderboard (ranked profiles)",
    )
    p_list.add_argument("--json", action="store_true")
    p_list.set_defaults(_handler=_cmd_list)

    # --- match ---
    p_match = talent_sub.add_parser(
        "match",
        help="Score all profiles against a single task",
    )
    p_match.add_argument("task_id", help="Task id to match")
    p_match.add_argument(
        "--top-k", type=int, default=5,
        help="Only show the top K matches (default: 5)",
    )
    p_match.add_argument("--json", action="store_true")
    p_match.set_defaults(_handler=_cmd_match)

    # --- assign ---
    p_assign = talent_sub.add_parser(
        "assign",
        help="Auto-assign a task using the talent market",
    )
    p_assign.add_argument("task_id", help="Task id to auto-assign")
    p_assign.add_argument(
        "--min-score", type=float, default=0.05,
        help="Minimum match score to assign (default: 0.05)",
    )
    p_assign.add_argument(
        "--dry-run", action="store_true",
        help="Show the match without mutating the task",
    )
    p_assign.set_defaults(_handler=_cmd_assign)

    # --- inspect ---
    p_inspect = talent_sub.add_parser(
        "inspect",
        help="Deep-dive into one profile's talent data",
    )
    p_inspect.add_argument("profile", help="Profile name")
    p_inspect.add_argument("--json", action="store_true")
    p_inspect.set_defaults(_handler=_cmd_inspect)


def dispatch_talent_command(args: argparse.Namespace) -> int:
    """Router called from ``kanban_command`` when ``kanban_action == 'talent'``.

    Returns the handler's exit code.
    """
    handler = getattr(args, "_handler", None)
    if handler is None:
        print("Usage: hermes kanban talent <command> …", file=sys.stderr)
        return 2
    return handler(args)
