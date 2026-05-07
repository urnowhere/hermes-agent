"""Tests for FleetCoordinator — spawn flow, absorb, rotate, decommission, swarm."""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock

import pytest

from gateway.telegram_fleet.api import ManagedBotInfo
from gateway.telegram_fleet.coordinator import (
    FleetCoordinator,
    SpawnResult,
    SwarmTaskResult,
)
from gateway.telegram_fleet.guardrails import (
    FleetGuardrailError,
    SpawnApprovalRequired,
    reset_rate_limits,
)
from gateway.telegram_fleet.roster import ChildBot, FleetRoster, save_roster


@pytest.fixture
def hermes_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    return tmp_path


@pytest.fixture(autouse=True)
def _isolate_rate_limits():
    reset_rate_limits()
    yield
    reset_rate_limits()


def _stub_api(*, manager_username: str = "TestMgr") -> MagicMock:
    api = MagicMock()
    api.get_me.return_value = {"id": 1, "username": manager_username, "can_manage_bots": True}
    api.replace_managed_bot_token.return_value = ManagedBotInfo(
        token="999:newToken", bot_id=999, bot_username="rotated_bot"
    )
    api.send_message_as.return_value = {"message_id": 17}
    return api


def _delegate_returning(text: str):
    """Build a delegate stub that mirrors the Hermes ``delegate_task`` shape."""
    def fake(goal=None, context=None, toolsets=None, role=None, parent_agent=None, **_):
        return text
    return fake


# ── Spawn ─────────────────────────────────────────────────────────────


def test_spawn_writes_pending_entry_and_deep_link(hermes_home):
    api = _stub_api()
    coord = FleetCoordinator(manager_token="12345:ABC", api_client=api)
    result = coord.spawn_bot("hermes_research_bot", persona="research lead")
    assert isinstance(result, SpawnResult)
    assert result.suggested_username == "hermes_research_bot"
    assert "t.me/newbot/TestMgr/hermes_research_bot" in result.deep_link
    assert len(result.nonce) >= 8

    children = coord.list_children()
    assert [c.username for c in children] == ["hermes_research_bot"]
    assert children[0].status == "pending"
    assert children[0].nonce == result.nonce


def test_spawn_blocked_when_username_collides(hermes_home):
    api = _stub_api()
    coord = FleetCoordinator(manager_token="12345:ABC", api_client=api)
    coord.spawn_bot("dup_bot", persona="x")
    with pytest.raises(FleetGuardrailError, match="already in the roster"):
        coord.spawn_bot("dup_bot", persona="y")


def test_spawn_respects_max_size(hermes_home):
    api = _stub_api()
    roster = FleetRoster(max_size=2, manager_bot_username="TestMgr")
    save_roster(roster)
    coord = FleetCoordinator(manager_token="12345:ABC", api_client=api)
    coord.spawn_bot("a_bot")
    coord.spawn_bot("b_bot")
    with pytest.raises(FleetGuardrailError, match="capacity"):
        coord.spawn_bot("c_bot")


def test_spawn_rejected_when_approval_disabled(hermes_home):
    api = _stub_api()
    roster = FleetRoster(spawn_enabled=False, manager_bot_username="TestMgr")
    save_roster(roster)
    coord = FleetCoordinator(manager_token="12345:ABC", api_client=api)
    with pytest.raises(SpawnApprovalRequired):
        coord.spawn_bot("x_bot")


def test_spawn_persists_to_disk(hermes_home):
    api = _stub_api()
    coord = FleetCoordinator(manager_token="12345:ABC", api_client=api)
    coord.spawn_bot("research_bot", persona="researcher")
    # Re-instantiate the coordinator → reads from disk → entry survives.
    coord2 = FleetCoordinator(manager_token="12345:ABC", api_client=api)
    assert coord2.find("research_bot") is not None


# ── Absorb ────────────────────────────────────────────────────────────


def test_absorb_promotes_pending_to_active(hermes_home):
    api = _stub_api()
    coord = FleetCoordinator(manager_token="12345:ABC", api_client=api)
    coord.spawn_bot("worker_bot", persona="general")
    info = ManagedBotInfo(token="123:childTok", bot_id=42, bot_username="worker_bot")
    child = coord.absorb_managed_bot(info)
    assert child is not None
    assert child.status == "active"
    assert child.token == "123:childTok"
    assert child.bot_id == 42


def test_absorb_unknown_username_returns_none(hermes_home):
    api = _stub_api()
    coord = FleetCoordinator(manager_token="12345:ABC", api_client=api)
    info = ManagedBotInfo(token="1:x", bot_id=1, bot_username="stranger_bot")
    assert coord.absorb_managed_bot(info) is None
    assert coord.list_children() == []


# ── Rotate ────────────────────────────────────────────────────────────


def test_rotate_token_updates_roster(hermes_home):
    api = _stub_api()
    api.replace_managed_bot_token.return_value = ManagedBotInfo(
        token="42:freshToken", bot_id=42, bot_username="worker_bot"
    )
    coord = FleetCoordinator(manager_token="12345:ABC", api_client=api)
    coord.spawn_bot("worker_bot")
    coord.absorb_managed_bot(
        ManagedBotInfo(token="42:oldToken", bot_id=42, bot_username="worker_bot")
    )
    rotated = coord.rotate_token("worker_bot")
    api.replace_managed_bot_token.assert_called_once_with(42)
    assert rotated.token == "42:freshToken"
    assert rotated.last_rotated_at  # set


def test_rotate_rejects_inactive_bot(hermes_home):
    api = _stub_api()
    coord = FleetCoordinator(manager_token="12345:ABC", api_client=api)
    coord.spawn_bot("pending_bot")
    with pytest.raises(FleetGuardrailError, match="not an active"):
        coord.rotate_token("pending_bot")


# ── Decommission ──────────────────────────────────────────────────────


def test_decommission_zeros_token(hermes_home):
    api = _stub_api()
    coord = FleetCoordinator(manager_token="12345:ABC", api_client=api)
    coord.spawn_bot("worker_bot")
    coord.absorb_managed_bot(
        ManagedBotInfo(token="42:tok", bot_id=42, bot_username="worker_bot")
    )
    assert coord.decommission("worker_bot") is True
    child = coord.find("worker_bot")
    assert child.status == "decommissioned"
    assert child.token is None


def test_decommission_unknown_returns_false(hermes_home):
    api = _stub_api()
    coord = FleetCoordinator(manager_token="12345:ABC", api_client=api)
    assert coord.decommission("ghost_bot") is False


# ── Delegate ──────────────────────────────────────────────────────────


def test_delegate_message_calls_send_as(hermes_home):
    api = _stub_api()
    coord = FleetCoordinator(manager_token="12345:ABC", api_client=api)
    coord.spawn_bot("speaker_bot")
    coord.absorb_managed_bot(
        ManagedBotInfo(token="42:childTok", bot_id=42, bot_username="speaker_bot")
    )
    result = coord.delegate_message("speaker_bot", "987654", "hello")
    api.send_message_as.assert_called_once()
    args, kwargs = api.send_message_as.call_args
    assert args[0] == "42:childTok"
    assert args[1] == "987654"
    assert args[2] == "hello"
    assert result == {"message_id": 17}


def test_delegate_respects_rate_limit(hermes_home):
    api = _stub_api()
    coord = FleetCoordinator(manager_token="12345:ABC", api_client=api)
    coord.spawn_bot("burst_bot", rate_limit_per_min=2)
    coord.absorb_managed_bot(
        ManagedBotInfo(token="42:tok", bot_id=42, bot_username="burst_bot")
    )
    # First two pass.
    coord.delegate_message("burst_bot", "1", "x")
    coord.delegate_message("burst_bot", "1", "x")
    # Third is rate-limited.
    with pytest.raises(FleetGuardrailError, match="rate limit"):
        coord.delegate_message("burst_bot", "1", "x")


# ── orchestrate_swarm ─────────────────────────────────────────────────


def _seed_active_fleet(coord: FleetCoordinator, names: List[str]) -> None:
    for n in names:
        coord.spawn_bot(n, persona=f"persona for {n}")
        coord.absorb_managed_bot(
            ManagedBotInfo(token=f"x:tok_{n}", bot_id=hash(n) & 0xFFFF, bot_username=n)
        )


def test_orchestrate_swarm_fans_out_subtasks(hermes_home):
    api = _stub_api()
    delegate = MagicMock(side_effect=lambda **kw: f"answered: {kw['goal']}")
    coord = FleetCoordinator(
        manager_token="12345:ABC", api_client=api, delegate_fn=delegate
    )
    _seed_active_fleet(coord, ["alpha_bot", "beta_bot", "gamma_bot"])

    result = coord.orchestrate_swarm(
        objective="Research X",
        subtasks=[
            {"goal": "legal angle"},
            {"goal": "market angle"},
            {"goal": "tech angle"},
        ],
        user_approved=True,  # approval gate covered separately
    )
    assert delegate.call_count == 3
    # Each result should pair a goal with a non-empty response.
    goals = sorted(r["goal"] for r in result["results"])
    assert goals == ["legal angle", "market angle", "tech angle"]
    assert all(r["response"].startswith("answered:") for r in result["results"])
    assert "summary" in result
    assert "Workers: 3" in result["summary"]


def test_orchestrate_swarm_pins_explicit_bot(hermes_home):
    api = _stub_api()
    delegate = MagicMock(side_effect=lambda **kw: "ok")
    coord = FleetCoordinator(
        manager_token="12345:ABC", api_client=api, delegate_fn=delegate
    )
    _seed_active_fleet(coord, ["alpha_bot", "beta_bot"])

    result = coord.orchestrate_swarm(
        objective="X",
        subtasks=[
            {"goal": "task-A", "bot_username": "beta_bot"},
            {"goal": "task-B", "bot_username": "@beta_bot"},
        ],
    )
    bots = [r["bot_username"] for r in result["results"]]
    assert bots == ["beta_bot", "beta_bot"]


def test_orchestrate_swarm_rejects_pin_to_unknown_bot(hermes_home):
    api = _stub_api()
    coord = FleetCoordinator(
        manager_token="12345:ABC", api_client=api, delegate_fn=MagicMock()
    )
    _seed_active_fleet(coord, ["alpha_bot"])
    with pytest.raises(FleetGuardrailError, match="not an active"):
        coord.orchestrate_swarm(
            objective="X",
            subtasks=[{"goal": "task", "bot_username": "ghost_bot"}],
        )


def test_orchestrate_swarm_requires_active_members(hermes_home):
    api = _stub_api()
    coord = FleetCoordinator(
        manager_token="12345:ABC", api_client=api, delegate_fn=MagicMock()
    )
    with pytest.raises(FleetGuardrailError, match="no active fleet members"):
        coord.orchestrate_swarm(objective="X", subtasks=[{"goal": "t"}])


def test_orchestrate_swarm_captures_subtask_failure(hermes_home):
    api = _stub_api()
    def flaky(**kw):
        if kw["goal"] == "boom":
            raise RuntimeError("kaboom")
        return "ok"
    coord = FleetCoordinator(
        manager_token="12345:ABC", api_client=api, delegate_fn=flaky
    )
    _seed_active_fleet(coord, ["alpha_bot", "beta_bot"])
    result = coord.orchestrate_swarm(
        objective="X",
        subtasks=[{"goal": "ok"}, {"goal": "boom"}],
        user_approved=True,
    )
    failures = [r for r in result["results"] if r.get("error")]
    assert len(failures) == 1
    assert "kaboom" in failures[0]["error"]
    # Other task still ran.
    assert any(r["goal"] == "ok" and not r.get("error") for r in result["results"])


def test_orchestrate_swarm_posts_status_to_report_chat(hermes_home):
    api = _stub_api()
    delegate = MagicMock(side_effect=lambda **kw: "done text")
    coord = FleetCoordinator(
        manager_token="12345:ABC", api_client=api, delegate_fn=delegate
    )
    _seed_active_fleet(coord, ["alpha_bot"])
    coord.orchestrate_swarm(
        objective="X",
        subtasks=[{"goal": "task"}],
        report_chat_id="555",
        user_approved=True,
    )
    # We expect at least one start + one done message posted via the manager API.
    assert api.send_message_as.call_count >= 2


def test_orchestrate_swarm_surfaces_missing_parent_agent_clearly(hermes_home):
    """Production path with parent_agent=None must raise an actionable error,
    NOT let delegate_task fail deeper down with its generic message.
    """
    api = _stub_api()
    coord = FleetCoordinator(manager_token="12345:ABC", api_client=api)
    _seed_active_fleet(coord, ["worker_bot"])
    with pytest.raises(FleetGuardrailError) as exc:
        coord.orchestrate_swarm(
            objective="X",
            subtasks=[{"goal": "task", "bot_username": "worker_bot"}],
            user_approved=True,
            parent_agent=None,
        )
    msg = str(exc.value).lower()
    assert "parent_agent" in msg
    assert "gateway" in msg or "restart" in msg


def test_orchestrate_swarm_rejects_empty_inputs(hermes_home):
    api = _stub_api()
    coord = FleetCoordinator(
        manager_token="12345:ABC", api_client=api, delegate_fn=MagicMock()
    )
    _seed_active_fleet(coord, ["alpha_bot"])
    with pytest.raises(FleetGuardrailError):
        coord.orchestrate_swarm(objective="", subtasks=[{"goal": "t"}])
    with pytest.raises(FleetGuardrailError):
        coord.orchestrate_swarm(objective="X", subtasks=[])
