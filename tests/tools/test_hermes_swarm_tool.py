"""Tests for the in-process hermes_swarm tool (no Telegram, no setup)."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest


def _import_tool():
    """Trigger registration + return the underlying handler for direct testing."""
    from tools import hermes_swarm_tool

    return hermes_swarm_tool


def test_rejects_empty_objective():
    mod = _import_tool()
    raw = mod.hermes_swarm(objective="", subtasks=[{"goal": "a"}, {"goal": "b"}])
    out = json.loads(raw)
    assert out.get("error")
    assert out.get("code") == "bad_request"


def test_rejects_empty_subtasks():
    mod = _import_tool()
    raw = mod.hermes_swarm(objective="X", subtasks=[])
    out = json.loads(raw)
    assert out.get("error")
    assert out.get("code") == "bad_request"


def test_rejects_single_subtask_anti_serialization():
    """A 'fan-out' of one is just delegate_task with extra steps."""
    mod = _import_tool()
    raw = mod.hermes_swarm(objective="X", subtasks=[{"goal": "lonely"}])
    out = json.loads(raw)
    assert out.get("error")
    assert out.get("code") == "anti_serialization"


def test_rejects_too_many_subtasks():
    mod = _import_tool()
    subtasks = [{"goal": f"task-{i}"} for i in range(20)]
    raw = mod.hermes_swarm(objective="X", subtasks=subtasks)
    out = json.loads(raw)
    assert out.get("error")
    assert out.get("code") == "too_many_subtasks"


def test_rejects_subtask_missing_goal():
    mod = _import_tool()
    raw = mod.hermes_swarm(
        objective="X",
        subtasks=[{"goal": "ok"}, {"persona": "no goal"}],
    )
    out = json.loads(raw)
    assert out.get("error")
    assert out.get("code") == "bad_request"


def test_fans_out_via_delegate_fn():
    mod = _import_tool()
    delegate = MagicMock(side_effect=lambda **kw: f"answered: {kw['goal']}")
    raw = mod.hermes_swarm(
        objective="Map the impact of new EU regs",
        subtasks=[
            {"goal": "legal angle", "persona": "regulatory analyst"},
            {"goal": "market angle", "persona": "market sizing"},
            {"goal": "tech angle", "persona": "engineering review"},
        ],
        delegate_fn=delegate,
    )
    out = json.loads(raw)
    assert out["success"] is True
    assert delegate.call_count == 3
    goals = sorted(r["goal"] for r in out["results"])
    assert goals == ["legal angle", "market angle", "tech angle"]
    assert all(r["response"].startswith("answered:") for r in out["results"])


def test_returns_critical_path_metric():
    """Critical path = max worker duration (Kimi PARL optimisation target)."""
    mod = _import_tool()
    durations_seen = []

    def slow_delegate(**kw):
        # Spread durations so max is distinct and < total.
        import time

        time.sleep(0.05)
        durations_seen.append(kw["goal"])
        return "ok"

    raw = mod.hermes_swarm(
        objective="X",
        subtasks=[{"goal": "a"}, {"goal": "b"}, {"goal": "c"}],
        delegate_fn=slow_delegate,
        max_parallel=3,
    )
    out = json.loads(raw)
    metrics = out["metrics"]
    assert metrics["workers"] == 3
    assert metrics["failures"] == 0
    assert metrics["critical_path_seconds"] > 0
    # Total serial time >= critical path; speedup should be > 1 with 3 parallel workers.
    assert metrics["total_serial_seconds"] >= metrics["critical_path_seconds"]
    assert metrics["parallel_speedup"] >= 1.0


def test_per_subtask_failure_isolated():
    mod = _import_tool()

    def flaky(**kw):
        if kw["goal"] == "boom":
            raise RuntimeError("kaboom")
        return "ok"

    raw = mod.hermes_swarm(
        objective="X",
        subtasks=[{"goal": "ok"}, {"goal": "boom"}, {"goal": "fine"}],
        delegate_fn=flaky,
    )
    out = json.loads(raw)
    failures = [r for r in out["results"] if r.get("error")]
    successes = [r for r in out["results"] if not r.get("error")]
    assert len(failures) == 1
    assert "kaboom" in failures[0]["error"]
    assert len(successes) == 2
    assert out["metrics"]["failures"] == 1


def test_passes_persona_into_context():
    mod = _import_tool()
    captured = []

    def cap_delegate(**kw):
        captured.append(kw)
        return "ok"

    mod.hermes_swarm(
        objective="Research X",
        subtasks=[{"goal": "g", "persona": "skeptical fact-checker"}],
        delegate_fn=cap_delegate,
    )
    # rejected for single subtask — captured stays empty
    assert captured == []

    mod.hermes_swarm(
        objective="Research X",
        subtasks=[
            {"goal": "g1", "persona": "skeptical fact-checker"},
            {"goal": "g2", "persona": "market analyst"},
        ],
        delegate_fn=cap_delegate,
    )
    assert len(captured) == 2
    by_goal = {c["goal"]: c for c in captured}
    assert "skeptical fact-checker" in by_goal["g1"]["context"]
    assert "Research X" in by_goal["g1"]["context"]
    assert by_goal["g1"]["role"] == "leaf"  # workers can't recurse


def test_tool_registers_under_delegate_toolset():
    mod = _import_tool()
    from tools.registry import registry

    entry = registry.get_entry("hermes_swarm")
    assert entry is not None
    assert entry.toolset == "delegate"


def test_summary_text_is_present():
    mod = _import_tool()
    delegate = MagicMock(side_effect=lambda **kw: f"finding for {kw['goal']}")
    raw = mod.hermes_swarm(
        objective="X",
        subtasks=[{"goal": "a"}, {"goal": "b"}],
        delegate_fn=delegate,
    )
    out = json.loads(raw)
    assert "summary" in out
    assert "Workers: 2" in out["summary"]
    assert "ok: 2" in out["summary"]
