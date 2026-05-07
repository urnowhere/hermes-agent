"""Tests for fleet guardrails — size cap, spawn approval, rate limit."""

from __future__ import annotations

import pytest

from gateway.telegram_fleet.guardrails import (
    FleetGuardrailError,
    SpawnApprovalRequired,
    check_can_spawn,
    check_rate_limit,
    reset_rate_limits,
)
from gateway.telegram_fleet.roster import ChildBot, FleetRoster


@pytest.fixture(autouse=True)
def _isolate_rate_limits():
    reset_rate_limits()
    yield
    reset_rate_limits()


# ── Spawn-approval guard ──────────────────────────────────────────────


def test_spawn_approval_required_when_disabled():
    r = FleetRoster(spawn_enabled=False)
    with pytest.raises(SpawnApprovalRequired) as e:
        check_can_spawn(r)
    assert "spawn_enabled" in str(e.value).lower()


def test_spawn_approval_default_passes_with_capacity():
    r = FleetRoster(max_size=4)
    check_can_spawn(r)  # no error


def test_spawn_blocked_when_at_capacity():
    r = FleetRoster(max_size=2)
    r.upsert(ChildBot(username="a_bot", status="active", token="1:x"))
    r.upsert(ChildBot(username="b_bot", status="pending", nonce="n1"))
    with pytest.raises(FleetGuardrailError) as e:
        check_can_spawn(r)
    assert "capacity" in str(e.value).lower()


def test_decommissioned_does_not_count_against_capacity():
    r = FleetRoster(max_size=2)
    r.upsert(ChildBot(username="a_bot", status="decommissioned"))
    r.upsert(ChildBot(username="b_bot", status="active", token="1:x"))
    # Active=1, pending=0; decommissioned ignored → spawn allowed.
    check_can_spawn(r)


# ── Rate limiter ──────────────────────────────────────────────────────


def test_rate_limit_allows_under_capacity():
    for _ in range(5):
        assert check_rate_limit("worker_bot", per_minute=10) is True


def test_rate_limit_blocks_over_capacity():
    for _ in range(3):
        assert check_rate_limit("burst_bot", per_minute=3) is True
    assert check_rate_limit("burst_bot") is False  # 4th in <60s


def test_rate_limit_per_username_isolated():
    for _ in range(3):
        assert check_rate_limit("a_bot", per_minute=3) is True
    # b_bot is independent; it has its own counter.
    assert check_rate_limit("b_bot", per_minute=3) is True
    # a_bot is exhausted.
    assert check_rate_limit("a_bot") is False


def test_rate_limit_normalizes_username():
    for _ in range(3):
        assert check_rate_limit("@Foo_Bot", per_minute=3) is True
    assert check_rate_limit("foo_bot") is False


def test_reconfigure_raises_capacity_mid_run():
    for _ in range(2):
        assert check_rate_limit("scaler_bot", per_minute=2) is True
    assert check_rate_limit("scaler_bot") is False
    # Operator raises the cap; the next consume should succeed.
    assert check_rate_limit("scaler_bot", per_minute=10) is True
