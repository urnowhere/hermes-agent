"""Tests for the roster-aware system prompt injection (Layer 4 of swarm discovery).

The injection should:
* Be silent when no manager token is set (don't burden non-Telegram users).
* Be silent when the roster has no active children.
* Surface active fleet members + their personas otherwise, so the leader
  knows specific named workers exist before calling telegram_fleet_list.
"""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from agent.prompt_builder import build_telegram_fleet_hint
from gateway.telegram_fleet.api import ManagedBotInfo
from gateway.telegram_fleet.coordinator import FleetCoordinator, reset_coordinator
from gateway.telegram_fleet.guardrails import reset_rate_limits


@pytest.fixture
def hermes_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    return tmp_path


@pytest.fixture(autouse=True)
def _isolate():
    reset_coordinator()
    reset_rate_limits()
    yield
    reset_coordinator()
    reset_rate_limits()


def _seed_fleet(coord: FleetCoordinator, names_to_personas):
    for name, persona in names_to_personas:
        coord.spawn_bot(name, persona=persona)
        coord.absorb_managed_bot(
            ManagedBotInfo(token=f"x:tok_{name}", bot_id=hash(name) & 0xFFFF, bot_username=name)
        )


def _coord(api):
    return FleetCoordinator(manager_token="12345:ABC", api_client=api)


def _stub_api():
    from unittest.mock import MagicMock

    api = MagicMock()
    api.get_me.return_value = {"id": 1, "username": "TestMgr", "can_manage_bots": True}
    return api


def test_silent_when_no_manager_token(hermes_home, monkeypatch):
    monkeypatch.delenv("TELEGRAM_FLEET_MANAGER_TOKEN", raising=False)
    assert build_telegram_fleet_hint() == ""


def test_silent_when_no_active_children(hermes_home, monkeypatch):
    monkeypatch.setenv("TELEGRAM_FLEET_MANAGER_TOKEN", "12345:ABC")
    coord = _coord(_stub_api())
    coord.spawn_bot("only_pending_bot")  # never absorbed → status=pending
    assert build_telegram_fleet_hint() == ""


def test_emits_roster_when_fleet_active(hermes_home, monkeypatch):
    monkeypatch.setenv("TELEGRAM_FLEET_MANAGER_TOKEN", "12345:ABC")
    coord = _coord(_stub_api())
    _seed_fleet(coord, [
        ("legal_bot", "regulatory analyst"),
        ("market_bot", "market sizing specialist"),
    ])
    hint = build_telegram_fleet_hint()
    assert hint != ""
    assert "@legal_bot" in hint
    assert "regulatory analyst" in hint
    assert "@market_bot" in hint
    assert "market sizing specialist" in hint
    assert "telegram_orchestrate_swarm" in hint
    assert "hermes_swarm" in hint  # mentions the in-process default for non-visible swarms


def test_truncates_long_personas(hermes_home, monkeypatch):
    monkeypatch.setenv("TELEGRAM_FLEET_MANAGER_TOKEN", "12345:ABC")
    coord = _coord(_stub_api())
    _seed_fleet(coord, [("verbose_bot", "x" * 500)])
    hint = build_telegram_fleet_hint()
    assert "@verbose_bot" in hint
    # Persona truncated to ~120 chars; verify it's not the full 500.
    bot_line = next(line for line in hint.splitlines() if "@verbose_bot" in line)
    assert len(bot_line) < 200


def test_handles_missing_manager_username(hermes_home, monkeypatch):
    """Roster without a recorded manager username still works."""
    monkeypatch.setenv("TELEGRAM_FLEET_MANAGER_TOKEN", "12345:ABC")
    api = _stub_api()
    coord = FleetCoordinator(manager_token="12345:ABC", api_client=api)
    coord.spawn_bot("worker_bot", persona="general")
    coord.absorb_managed_bot(
        ManagedBotInfo(token="42:tok", bot_id=42, bot_username="worker_bot")
    )
    # spawn_bot already populates manager_bot_username via api.get_me; clear it
    # to simulate the corner case.
    from gateway.telegram_fleet.roster import load_roster, save_roster

    r = load_roster()
    r.manager_bot_username = ""
    save_roster(r)
    hint = build_telegram_fleet_hint()
    assert hint != ""
    assert "@worker_bot" in hint


def test_silent_on_corrupt_roster(hermes_home, monkeypatch):
    """A broken roster file must not crash prompt assembly."""
    monkeypatch.setenv("TELEGRAM_FLEET_MANAGER_TOKEN", "12345:ABC")
    (hermes_home / "telegram_fleet.yaml").write_text(": : : not yaml :\n")
    # No exception, returns empty string.
    assert build_telegram_fleet_hint() == ""
