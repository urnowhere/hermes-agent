"""Tests for the agent-facing telegram_fleet_* tools."""

from __future__ import annotations

import json
from typing import List
from unittest.mock import MagicMock

import pytest

from gateway.telegram_fleet.api import ManagedBotInfo
from gateway.telegram_fleet import coordinator as coordinator_module
from gateway.telegram_fleet.coordinator import FleetCoordinator, reset_coordinator
from gateway.telegram_fleet.guardrails import reset_rate_limits


@pytest.fixture
def hermes_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    return tmp_path


@pytest.fixture
def fleet(hermes_home, monkeypatch):
    """Install a singleton FleetCoordinator backed by a mock Bot API client."""
    api = MagicMock()
    api.get_me.return_value = {"id": 1, "username": "TestMgr", "can_manage_bots": True}
    api.send_message_as.return_value = {"message_id": 9}
    api.replace_managed_bot_token.return_value = ManagedBotInfo(
        token="x:rotated", bot_id=42, bot_username="rotated_bot"
    )
    delegate = MagicMock(side_effect=lambda **kw: f"answered: {kw['goal']}")
    coord = FleetCoordinator(
        manager_token="12345:ABC", api_client=api, delegate_fn=delegate
    )
    # Pin the singleton.
    monkeypatch.setattr(coordinator_module, "_singleton", coord)
    monkeypatch.setenv("TELEGRAM_FLEET_MANAGER_TOKEN", "12345:ABC")
    yield coord, api, delegate
    reset_coordinator()
    reset_rate_limits()


def _seed_active(coord: FleetCoordinator, names: List[str]) -> None:
    for n in names:
        coord.spawn_bot(n, persona=f"persona for {n}")
        coord.absorb_managed_bot(
            ManagedBotInfo(token=f"x:tok_{n}", bot_id=hash(n) & 0xFFFF, bot_username=n)
        )


# Each test imports the tool module to trigger registration. We import inside
# the test so the env-var-gated check_fn sees the test fixture's env.


def test_spawn_tool_returns_deep_link_and_persists(fleet):
    from tools.telegram_fleet_tool import telegram_spawn_bot

    raw = telegram_spawn_bot(suggested_username="research_bot", persona="legal angle")
    payload = json.loads(raw)
    assert payload["success"] is True
    assert payload["suggested_username"] == "research_bot"
    assert "t.me/newbot/TestMgr/research_bot" in payload["deep_link"]
    assert payload["nonce_preview"].endswith("…")
    # Roster has the pending entry.
    coord, _, _ = fleet
    assert coord.find("research_bot") is not None


def test_list_tool_redacts_tokens(fleet):
    from tools.telegram_fleet_tool import telegram_fleet_list, telegram_spawn_bot

    coord, _, _ = fleet
    _seed_active(coord, ["alpha_bot"])
    raw = telegram_fleet_list()
    payload = json.loads(raw)
    assert payload["success"] is True
    assert payload["children"][0]["username"] == "alpha_bot"
    assert "token" not in payload["children"][0]
    assert payload["children"][0]["has_token"] is True


def test_list_tool_filters_by_status(fleet):
    from tools.telegram_fleet_tool import telegram_fleet_list

    coord, _, _ = fleet
    coord.spawn_bot("pending_bot")
    _seed_active(coord, ["active_bot"])
    raw = telegram_fleet_list(status="pending")
    payload = json.loads(raw)
    assert {c["username"] for c in payload["children"]} == {"pending_bot"}


def test_delegate_tool_invokes_send_as(fleet):
    from tools.telegram_fleet_tool import telegram_delegate

    coord, api, _ = fleet
    _seed_active(coord, ["speaker_bot"])
    raw = telegram_delegate(target_bot="speaker_bot", chat_id="555", text="hi")
    payload = json.loads(raw)
    assert payload["success"] is True
    assert payload["message_id"] == 9
    api.send_message_as.assert_called_once()


def test_delegate_tool_surfaces_guardrail(fleet):
    from tools.telegram_fleet_tool import telegram_delegate

    raw = telegram_delegate(target_bot="ghost_bot", chat_id="1", text="hi")
    payload = json.loads(raw)
    assert payload.get("error")
    assert payload.get("code") == "guardrail"


def test_decommission_tool_marks_status(fleet):
    from tools.telegram_fleet_tool import telegram_decommission_bot

    coord, _, _ = fleet
    _seed_active(coord, ["retired_bot"])
    raw = telegram_decommission_bot(target_bot="retired_bot")
    payload = json.loads(raw)
    assert payload["success"] is True
    assert coord.find("retired_bot").status == "decommissioned"


def test_decommission_tool_handles_unknown(fleet):
    from tools.telegram_fleet_tool import telegram_decommission_bot

    raw = telegram_decommission_bot(target_bot="ghost_bot")
    payload = json.loads(raw)
    assert payload.get("error")
    assert payload.get("code") == "not_found"


def test_rotate_tool_calls_replace_endpoint(fleet):
    from tools.telegram_fleet_tool import telegram_rotate_bot_token

    coord, api, _ = fleet
    _seed_active(coord, ["rotated_bot"])
    raw = telegram_rotate_bot_token(target_bot="rotated_bot")
    payload = json.loads(raw)
    assert payload["success"] is True
    api.replace_managed_bot_token.assert_called_once()


# ── orchestrate_swarm: the user-visible end-to-end test ────────────────


def test_orchestrate_swarm_end_to_end(fleet):
    """Full user-visible flow: leader → subtasks → workers → aggregate."""
    from tools.telegram_fleet_tool import telegram_orchestrate_swarm

    coord, _, delegate = fleet
    _seed_active(coord, ["legal_bot", "market_bot", "tech_bot"])

    raw = telegram_orchestrate_swarm(
        objective="Research the impact of new EU AI regulations",
        subtasks=[
            {"goal": "summarise the legal text", "bot_username": "legal_bot"},
            {"goal": "estimate market impact", "bot_username": "market_bot"},
            {"goal": "review technical feasibility constraints", "bot_username": "tech_bot"},
        ],
    )
    payload = json.loads(raw)
    assert payload["success"] is True
    assert delegate.call_count == 3
    bots = sorted(r["bot_username"] for r in payload["results"])
    assert bots == ["legal_bot", "market_bot", "tech_bot"]
    # All three answers carried back in structured form for the leader to synthesise.
    assert all(r["response"].startswith("answered:") for r in payload["results"])
    assert "Workers: 3" in payload["summary"]
    assert "ok: 3" in payload["summary"]


def test_parent_agent_threads_through_handle_function_call(fleet):
    """Regression: parent_agent must reach delegate_task or it errors.

    The user-reported bug was ``{"error": "delegate_task requires a parent
    agent context."}`` because handle_function_call → registry.dispatch
    only forwarded task_id/user_task, never parent_agent.  This pins the
    fix: a sentinel agent passed to handle_function_call must reach the
    delegate_fn each worker calls.
    """
    from unittest.mock import MagicMock
    from model_tools import handle_function_call

    coord, _, delegate = fleet
    _seed_active(coord, ["worker_bot"])

    # Use a sentinel object so we can assert identity, not just truthy.
    sentinel_agent = MagicMock(name="AGENT")
    sentinel_agent._delegate_depth = 0

    handle_function_call(
        "telegram_orchestrate_swarm",
        {
            "objective": "X",
            "subtasks": [{"goal": "task", "bot_username": "worker_bot"}],
        },
        parent_agent=sentinel_agent,
    )
    # Verify the sentinel reached the delegate call.
    assert delegate.call_count == 1
    _, call_kwargs = delegate.call_args_list[0].args, delegate.call_args_list[0].kwargs
    assert call_kwargs.get("parent_agent") is sentinel_agent


def test_orchestrate_swarm_rejects_when_fleet_empty(fleet):
    from tools.telegram_fleet_tool import telegram_orchestrate_swarm

    raw = telegram_orchestrate_swarm(
        objective="X",
        subtasks=[{"goal": "task"}],
    )
    payload = json.loads(raw)
    assert payload.get("error")
    assert "no active fleet" in payload["error"].lower()


def test_orchestrate_swarm_streams_status_to_report_chat(fleet):
    from tools.telegram_fleet_tool import telegram_orchestrate_swarm

    coord, api, _ = fleet
    _seed_active(coord, ["worker_bot"])
    telegram_orchestrate_swarm(
        objective="X",
        subtasks=[{"goal": "task"}],
        report_chat_id="555",
        user_approved=True,
    )
    # Expect at least a "starting" + a "done" message posted as the worker.
    assert api.send_message_as.call_count >= 2
