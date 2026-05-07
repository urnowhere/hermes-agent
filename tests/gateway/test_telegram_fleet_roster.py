"""Tests for the Telegram Fleet roster (YAML schema, atomic writes, perms, TTL)."""

from __future__ import annotations

import os
import stat
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from gateway.telegram_fleet.roster import (
    PENDING_TTL_SECONDS,
    SCHEMA_VERSION,
    ChildBot,
    FleetRoster,
    RosterError,
    load_roster,
    save_roster,
)


@pytest.fixture
def hermes_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    return tmp_path


# ── ChildBot ──────────────────────────────────────────────────────────


def test_child_bot_requires_username():
    with pytest.raises(RosterError):
        ChildBot(username="")


def test_child_bot_rejects_invalid_status():
    with pytest.raises(RosterError):
        ChildBot(username="worker_a_bot", status="bogus")


def test_child_bot_round_trips_through_dict():
    c = ChildBot(
        username="hermes_research_bot",
        persona="Research a specific angle",
        bot_id=123,
        token="123:abc",
        model="claude-sonnet-4-6",
        toolset=["web", "file"],
        status="active",
        rate_limit_per_min=20,
        daily_budget_usd=2.5,
        nonce="abc12345",
    )
    blob = c.to_dict()
    rebuilt = ChildBot.from_dict(blob)
    assert rebuilt.to_dict() == blob


def test_child_bot_redacts_token_when_requested():
    c = ChildBot(username="x_bot", token="secret:abc", status="active")
    out = c.to_dict(include_token=False)
    assert "token" not in out


# ── FleetRoster ───────────────────────────────────────────────────────


def test_empty_roster_round_trips():
    r = FleetRoster()
    assert r.schema_version == SCHEMA_VERSION
    assert r.children == []
    assert FleetRoster.from_dict(r.to_dict()).children == []


def test_unknown_schema_version_rejected():
    with pytest.raises(RosterError):
        FleetRoster(schema_version=99)


def test_upsert_replaces_existing():
    r = FleetRoster()
    r.upsert(ChildBot(username="x_bot", persona="v1"))
    r.upsert(ChildBot(username="x_bot", persona="v2"))
    assert len(r.children) == 1
    assert r.find("x_bot").persona == "v2"


def test_find_is_case_insensitive_and_strips_at():
    r = FleetRoster()
    r.upsert(ChildBot(username="x_bot"))
    assert r.find("@X_Bot") is not None
    assert r.find("X_BOT") is not None


def test_active_children_filters_pending_and_no_token():
    r = FleetRoster()
    r.upsert(ChildBot(username="a_bot", status="pending", nonce="n1"))
    r.upsert(ChildBot(username="b_bot", status="active", token="123:abc"))
    r.upsert(ChildBot(username="c_bot", status="active"))  # no token → not active
    r.upsert(ChildBot(username="d_bot", status="decommissioned"))
    active = [c.username for c in r.active_children()]
    assert active == ["b_bot"]


def test_prune_expired_pending_drops_old_entries():
    r = FleetRoster()
    old = ChildBot(username="old_bot", status="pending", nonce="n1")
    old.created_at = (datetime.now(timezone.utc) - timedelta(seconds=PENDING_TTL_SECONDS + 60)).isoformat()
    fresh = ChildBot(username="new_bot", status="pending", nonce="n2")
    keep = ChildBot(username="active_bot", status="active", token="123:abc")
    r.upsert(old)
    r.upsert(fresh)
    r.upsert(keep)
    removed = r.prune_expired_pending()
    assert removed == 1
    remaining = [c.username for c in r.children]
    assert "old_bot" not in remaining
    assert "new_bot" in remaining
    assert "active_bot" in remaining


# ── Persistence ───────────────────────────────────────────────────────


def test_save_load_roundtrip(hermes_home):
    r = FleetRoster(manager_bot_username="HermesMgr", max_size=8)
    r.upsert(
        ChildBot(
            username="research_bot",
            persona="Research lead",
            bot_id=42,
            token="42:tokenA",
            status="active",
        )
    )
    save_roster(r)
    loaded = load_roster()
    assert loaded.manager_bot_username == "HermesMgr"
    assert loaded.max_size == 8
    assert len(loaded.children) == 1
    assert loaded.children[0].token == "42:tokenA"


def test_saved_file_is_mode_0600(hermes_home):
    r = FleetRoster()
    r.upsert(ChildBot(username="secret_bot", token="42:secret", status="active"))
    save_roster(r)
    path = hermes_home / "telegram_fleet.yaml"
    mode = stat.S_IMODE(path.stat().st_mode)
    if os.name == "posix":
        assert mode == 0o600
    # On non-POSIX, just assert the file exists; chmod is a no-op there.


def test_load_missing_returns_empty(hermes_home):
    r = load_roster()
    assert r.children == []
    assert r.schema_version == SCHEMA_VERSION


def test_load_malformed_raises(hermes_home):
    (hermes_home / "telegram_fleet.yaml").write_text("not: [valid: yaml: at: all\n")
    with pytest.raises(RosterError):
        load_roster()


def test_save_then_load_drops_empty_optional_fields(hermes_home):
    r = FleetRoster()
    r.upsert(ChildBot(username="minimal_bot", status="pending", nonce="n1"))
    save_roster(r)
    loaded = load_roster()
    assert loaded.children[0].model is None
    assert loaded.children[0].toolset is None


def test_to_dict_omits_tokens_when_requested():
    r = FleetRoster()
    r.upsert(ChildBot(username="x_bot", token="42:abc", status="active"))
    blob = r.to_dict(include_tokens=False)
    assert "token" not in blob["children"][0]
