#!/usr/bin/env python3
"""Hermes latency/soak criteria runner.

This runner is intentionally local and deterministic by default. It checks the
artifacts that should exist after the latency/governance work and emits JSON
criteria suitable for CI or manual comparison before/after a live soak.
"""
from __future__ import annotations

import argparse
import importlib
import json
import sqlite3
import subprocess
import sys
import time
from pathlib import Path


DEFAULT_REPO = Path(__file__).resolve().parents[1]
DEFAULT_HOME = Path("~/.hermes").expanduser()


def time_import(module: str) -> dict:
    start = time.perf_counter()
    importlib.import_module(module)
    return {"module": module, "ms": (time.perf_counter() - start) * 1000.0}


def state_db_stats(home: Path) -> dict:
    db = home / "state.db"
    if not db.exists():
        return {"exists": False}
    result = {"exists": True, "bytes": db.stat().st_size}
    try:
        with sqlite3.connect(str(db)) as conn:
            result["sessions"] = conn.execute("select count(*) from sessions").fetchone()[0]
            result["messages"] = conn.execute("select count(*) from messages").fetchone()[0]
    except Exception as exc:
        result["error"] = str(exc)
    return result


def run_pytest(repo: Path, tests: list[str]) -> dict:
    cmd = [sys.executable, "-m", "pytest", "-o", "addopts=", *tests, "-q"]
    start = time.perf_counter()
    proc = subprocess.run(cmd, cwd=repo, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    return {
        "cmd": cmd,
        "exit_code": proc.returncode,
        "ms": (time.perf_counter() - start) * 1000.0,
        "output_tail": "\n".join(proc.stdout.splitlines()[-40:]),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default=str(DEFAULT_REPO))
    parser.add_argument("--home", default=str(DEFAULT_HOME))
    parser.add_argument("--run-tests", action="store_true")
    args = parser.parse_args(argv)

    repo = Path(args.repo).expanduser().resolve(strict=False)
    if str(repo) not in sys.path:
        sys.path.insert(0, str(repo))
    home = Path(args.home).expanduser().resolve(strict=False)
    result = {
        "criteria": {
            "governance_source_modified": "must be false by git diff/status",
            "focused_tests": "must pass",
            "state_archive_before_prune": "session_db_maintenance.py --apply must verify before optimize/prune",
            "latency_logs": "expect latency.phase lines for system prompt, hooks, external memory, exact governance retrieval",
            "side_task_bounds": "non-essential auxiliary tasks must have timeout caps via existing timeout path",
        },
        "imports": [
            time_import("agent.governance_context"),
            time_import("agent.side_task_policy"),
        ],
        "state_db": state_db_stats(home),
    }
    if args.run_tests:
        result["tests"] = run_pytest(
            repo,
            [
                "tests/test_governance_context.py",
                "tests/test_side_task_policy.py",
            ],
        )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if not result.get("tests") or result["tests"]["exit_code"] == 0 else result["tests"]["exit_code"]


if __name__ == "__main__":
    raise SystemExit(main())
