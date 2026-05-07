"""Tests for the telegram_orchestrate_swarm approval gate.

Mirrors Hermes' existing dangerous-command approval pattern:
  • Default: tool refuses to run, returns ``approval_required`` with a plan.
  • Bypass A: caller passes ``user_approved=True`` (after explicit consent).
  • Bypass B: every subtask pins a ``bot_username`` (by-name request).
  • Operator override: ``telegram_fleet.auto_approve: true`` in config.
"""

from __future__ import annotations

import json
import os
from typing import List
from unittest.mock import MagicMock, patch

import pytest

from gateway.telegram_fleet.api import ManagedBotInfo
from gateway.telegram_fleet import coordinator as coord_module
from gateway.telegram_fleet.coordinator import (
    FleetApprovalRequired,
    FleetCoordinator,
    reset_coordinator,
)
from gateway.telegram_fleet.guardrails import reset_rate_limits


@pytest.fixture
def hermes_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.delenv("TELEGRAM_FLEET_AUTO_APPROVE", raising=False)
    return tmp_path


@pytest.fixture(autouse=True)
def _isolate():
    reset_coordinator()
    reset_rate_limits()
    yield
    reset_coordinator()
    reset_rate_limits()


def _stub_api():
    api = MagicMock()
    api.get_me.return_value = {"id": 1, "username": "TestMgr", "can_manage_bots": True}
    api.send_message_as.return_value = {"message_id": 9}
    return api


def _seed(coord: FleetCoordinator, names: List[str]) -> None:
    for n in names:
        coord.spawn_bot(n, persona=f"persona for {n}")
        coord.absorb_managed_bot(
            ManagedBotInfo(token=f"x:tok_{n}", bot_id=hash(n) & 0xFFFF, bot_username=n)
        )


# ── coordinator-level: raises with structured plan ─────────────────


def test_orchestrate_blocks_without_approval(hermes_home):
    coord = FleetCoordinator(
        manager_token="12345:ABC",
        api_client=_stub_api(),
        delegate_fn=MagicMock(side_effect=lambda **kw: "ok"),
    )
    _seed(coord, ["alpha_bot", "beta_bot"])
    with pytest.raises(FleetApprovalRequired) as exc:
        coord.orchestrate_swarm(
            objective="Research X",
            subtasks=[{"goal": "angle 1"}, {"goal": "angle 2"}],
        )
    plan = exc.value.to_plan_dict()
    assert plan["objective"] == "Research X"
    assert {w["bot_username"] for w in plan["workers"]} == {"alpha_bot", "beta_bot"}


def test_orchestrate_runs_with_user_approved(hermes_home):
    delegate = MagicMock(side_effect=lambda **kw: "ok")
    coord = FleetCoordinator(
        manager_token="12345:ABC",
        api_client=_stub_api(),
        delegate_fn=delegate,
    )
    _seed(coord, ["alpha_bot", "beta_bot"])
    result = coord.orchestrate_swarm(
        objective="Research X",
        subtasks=[{"goal": "angle 1"}, {"goal": "angle 2"}],
        user_approved=True,
    )
    assert delegate.call_count == 2
    assert result["metrics"]["workers"] == 2


def test_orchestrate_skips_approval_when_all_bots_pinned(hermes_home):
    """By-name request: user already named who they want → no prompt."""
    delegate = MagicMock(side_effect=lambda **kw: "ok")
    coord = FleetCoordinator(
        manager_token="12345:ABC",
        api_client=_stub_api(),
        delegate_fn=delegate,
    )
    _seed(coord, ["alpha_bot", "beta_bot"])
    result = coord.orchestrate_swarm(
        objective="Research X",
        subtasks=[
            {"goal": "angle 1", "bot_username": "alpha_bot"},
            {"goal": "angle 2", "bot_username": "@beta_bot"},
        ],
    )
    assert delegate.call_count == 2
    assert result["metrics"]["workers"] == 2


def test_orchestrate_blocks_partial_pinning(hermes_home):
    """If only SOME subtasks pin a bot, the user hasn't named the whole plan."""
    coord = FleetCoordinator(
        manager_token="12345:ABC",
        api_client=_stub_api(),
        delegate_fn=MagicMock(),
    )
    _seed(coord, ["alpha_bot", "beta_bot"])
    with pytest.raises(FleetApprovalRequired):
        coord.orchestrate_swarm(
            objective="X",
            subtasks=[
                {"goal": "a", "bot_username": "alpha_bot"},
                {"goal": "b"},  # not pinned
            ],
        )


def test_config_auto_approve_disables_gate(hermes_home, monkeypatch):
    """Operator opt-out via env var bypasses the approval prompt."""
    monkeypatch.setenv("TELEGRAM_FLEET_AUTO_APPROVE", "1")
    delegate = MagicMock(side_effect=lambda **kw: "ok")
    coord = FleetCoordinator(
        manager_token="12345:ABC",
        api_client=_stub_api(),
        delegate_fn=delegate,
    )
    _seed(coord, ["alpha_bot"])
    result = coord.orchestrate_swarm(
        objective="X",
        subtasks=[{"goal": "a"}, {"goal": "b"}],
    )
    assert result["metrics"]["workers"] == 2


def test_config_yaml_auto_approve_disables_gate(hermes_home):
    """Operator opt-out via config.yaml also bypasses."""
    (hermes_home / "config.yaml").write_text(
        "telegram_fleet:\n  auto_approve: true\n"
    )
    delegate = MagicMock(side_effect=lambda **kw: "ok")
    coord = FleetCoordinator(
        manager_token="12345:ABC",
        api_client=_stub_api(),
        delegate_fn=delegate,
    )
    _seed(coord, ["alpha_bot"])
    result = coord.orchestrate_swarm(
        objective="X",
        subtasks=[{"goal": "a"}, {"goal": "b"}],
    )
    assert result["metrics"]["workers"] == 2


# ── tool-level: returns structured approval_required JSON ──────────


def test_tool_returns_approval_required_json(hermes_home, monkeypatch):
    """The tool wraps the exception into a Hermes-style approval response."""
    monkeypatch.setenv("TELEGRAM_FLEET_MANAGER_TOKEN", "12345:ABC")
    coord = FleetCoordinator(
        manager_token="12345:ABC",
        api_client=_stub_api(),
        delegate_fn=MagicMock(),
    )
    monkeypatch.setattr(coord_module, "_singleton", coord)
    _seed(coord, ["alpha_bot", "beta_bot"])

    from tools.telegram_fleet_tool import telegram_orchestrate_swarm

    raw = telegram_orchestrate_swarm(
        objective="X",
        subtasks=[{"goal": "a"}, {"goal": "b"}],
    )
    out = json.loads(raw)
    assert out["status"] == "approval_required"
    assert out["code"] == "approval_required"
    assert "plan" in out
    assert len(out["plan"]["workers"]) == 2
    assert "instruction" in out
    assert "user_approved=true" in out["instruction"]


def test_tool_runs_on_re_call_with_approval(hermes_home, monkeypatch):
    monkeypatch.setenv("TELEGRAM_FLEET_MANAGER_TOKEN", "12345:ABC")
    delegate = MagicMock(side_effect=lambda **kw: "ok")
    coord = FleetCoordinator(
        manager_token="12345:ABC",
        api_client=_stub_api(),
        delegate_fn=delegate,
    )
    monkeypatch.setattr(coord_module, "_singleton", coord)
    _seed(coord, ["alpha_bot", "beta_bot"])

    from tools.telegram_fleet_tool import telegram_orchestrate_swarm

    raw = telegram_orchestrate_swarm(
        objective="X",
        subtasks=[{"goal": "a"}, {"goal": "b"}],
        user_approved=True,
    )
    out = json.loads(raw)
    assert out.get("success") is True
    assert delegate.call_count == 2


def test_tool_runs_when_all_bots_pinned(hermes_home, monkeypatch):
    monkeypatch.setenv("TELEGRAM_FLEET_MANAGER_TOKEN", "12345:ABC")
    delegate = MagicMock(side_effect=lambda **kw: "ok")
    coord = FleetCoordinator(
        manager_token="12345:ABC",
        api_client=_stub_api(),
        delegate_fn=delegate,
    )
    monkeypatch.setattr(coord_module, "_singleton", coord)
    _seed(coord, ["alpha_bot", "beta_bot"])

    from tools.telegram_fleet_tool import telegram_orchestrate_swarm

    raw = telegram_orchestrate_swarm(
        objective="X",
        subtasks=[
            {"goal": "a", "bot_username": "alpha_bot"},
            {"goal": "b", "bot_username": "beta_bot"},
        ],
    )
    out = json.loads(raw)
    assert out.get("success") is True
    assert delegate.call_count == 2


# ── audit + token-redaction guarantees ───────────────────────────────


def test_approval_response_does_not_leak_tokens(hermes_home, monkeypatch):
    """The structured plan must NEVER include bot tokens — only usernames."""
    monkeypatch.setenv("TELEGRAM_FLEET_MANAGER_TOKEN", "12345:ABC")
    coord = FleetCoordinator(
        manager_token="12345:ABC",
        api_client=_stub_api(),
        delegate_fn=MagicMock(),
    )
    monkeypatch.setattr(coord_module, "_singleton", coord)
    _seed(coord, ["alpha_bot", "beta_bot"])
    # Each child has a sentinel token in its binding internally.
    from tools.telegram_fleet_tool import telegram_orchestrate_swarm

    raw = telegram_orchestrate_swarm(
        objective="X",
        subtasks=[{"goal": "a"}, {"goal": "b"}],
    )
    # Both raw and parsed must not contain the secret token substring.
    assert "x:tok_alpha_bot" not in raw
    assert "x:tok_beta_bot" not in raw
    assert ":tok_" not in raw
    payload = json.loads(raw)
    assert payload["status"] == "approval_required"
    for worker in payload["plan"]["workers"]:
        assert "token" not in worker
        assert "bot_token" not in worker


def test_approval_required_writes_audit_event(hermes_home):
    """Operator visibility: a denied/pending swarm leaves an audit footprint."""
    from gateway.telegram_fleet.audit import get_audit_path, read_recent_events

    coord = FleetCoordinator(
        manager_token="12345:ABC",
        api_client=_stub_api(),
        delegate_fn=MagicMock(),
    )
    _seed(coord, ["alpha_bot"])
    with pytest.raises(FleetApprovalRequired):
        coord.orchestrate_swarm(
            objective="research X across angles",
            subtasks=[{"goal": "a"}, {"goal": "b"}],
        )
    events = read_recent_events()
    actions = [e["action"] for e in events]
    assert "swarm_approval_required" in actions
    # And no swarm_started should have been written for this run.
    matched = [e for e in events if e["action"] == "swarm_approval_required"]
    assert matched[-1]["workers"] == 2
    assert "alpha_bot" in matched[-1]["bots"]


def test_partial_pin_does_not_bypass_gate(hermes_home):
    """If only some subtasks pin a bot, treat as ambiguous → require approval."""
    coord = FleetCoordinator(
        manager_token="12345:ABC",
        api_client=_stub_api(),
        delegate_fn=MagicMock(),
    )
    _seed(coord, ["alpha_bot", "beta_bot"])
    with pytest.raises(FleetApprovalRequired):
        coord.orchestrate_swarm(
            objective="X",
            subtasks=[
                {"goal": "a", "bot_username": "alpha_bot"},
                {"goal": "b"},  # not pinned
                {"goal": "c"},  # not pinned
            ],
        )
