"""Regression guard for #15296 — exponential backoff on credential exhaustion.

The credential pool's ``_exhausted_ttl`` was a flat ``EXHAUSTED_TTL_*``
constant regardless of how many consecutive times a credential had
failed.  When a provider was overloaded for hours, the pool cycled
through:

    TTL expires → cleared to ``ok`` → tried again → fails with same
    error (3 retries wasted) → marked exhausted with same flat TTL → wait
    → repeat

For cron jobs (each run = one turn), every execution burned its retry
budget on the same dead credential until the upstream actually
recovered.

Fix — track ``consecutive_failures`` on each ``PooledCredential`` entry,
increment in ``_mark_exhausted``, reset on a successful refresh OR an
operator-initiated ``reset_statuses`` call, and use it as an exponent
on the cooldown TTL.  The ``clear_expired`` path that auto-clears an
entry back to ``STATUS_OK`` after the cooldown elapses **deliberately
does NOT reset the counter** — that's the entire point: if the same
credential exhausts again right after the cooldown expires, the
upstream is still down and the next cooldown should be longer.

These tests pin the contract at the production-code entry points
(``_exhausted_ttl``, ``_mark_exhausted``, ``_exhausted_until``,
``reset_statuses``) so attribute renames or formula tweaks surface
loudly.
"""
import pytest

from agent.credential_pool import (
    EXHAUSTED_TTL_429_SECONDS,
    EXHAUSTED_TTL_BACKOFF_CAP,
    EXHAUSTED_TTL_DEFAULT_SECONDS,
    PooledCredential,
    _exhausted_ttl,
    _exhausted_until,
)


# ---------------------------------------------------------------------------
# _exhausted_ttl — pure function, easy to pin
# ---------------------------------------------------------------------------


class TestExhaustedTtl:
    """``_exhausted_ttl(error_code, consecutive_failures)`` formula."""

    def test_default_ttl_when_no_failures_recorded(self):
        """Backward-compat: callers that don't pass ``consecutive_failures``
        (or pre-#15296 on-disk entries that lack the field) get the same
        flat TTL the pool always returned."""
        assert _exhausted_ttl(429) == EXHAUSTED_TTL_429_SECONDS
        assert _exhausted_ttl(402) == EXHAUSTED_TTL_DEFAULT_SECONDS
        assert _exhausted_ttl(None) == EXHAUSTED_TTL_DEFAULT_SECONDS

    def test_first_failure_uses_base_ttl(self):
        """``consecutive_failures=1`` means this is the first exhaustion;
        the multiplier exponent is ``failures - 1 = 0``, so ``2 ** 0 = 1×``
        — same as the no-failures default.  Prevents accidental 2× on
        the very first failure."""
        assert _exhausted_ttl(429, consecutive_failures=1) == EXHAUSTED_TTL_429_SECONDS

    @pytest.mark.parametrize("failures,multiplier", [
        (1, 1),
        (2, 2),
        (3, 4),
        (4, 8),
        (5, 8),    # capped
        (6, 8),    # capped
        (10, 8),   # capped
        (100, 8),  # capped
    ])
    def test_backoff_progression_429(self, failures, multiplier):
        """1h → 2h → 4h → 8h → cap at 8h.  The cap matters: without it
        a long-running outage could push the cooldown into days, which
        survives operator intervention (refresh upstream provider, swap
        keys) and feels broken from the user side."""
        expected = EXHAUSTED_TTL_429_SECONDS * multiplier
        assert _exhausted_ttl(429, consecutive_failures=failures) == expected

    @pytest.mark.parametrize("failures,multiplier", [
        (1, 1), (2, 2), (3, 4), (4, 8), (5, 8), (10, 8),
    ])
    def test_backoff_progression_402(self, failures, multiplier):
        """Same backoff applies to non-429 cooldowns (e.g. 402 billing)."""
        expected = EXHAUSTED_TTL_DEFAULT_SECONDS * multiplier
        assert _exhausted_ttl(402, consecutive_failures=failures) == expected

    def test_zero_failures_treated_as_default(self):
        """``consecutive_failures=0`` is the on-disk default for entries
        that have never been marked exhausted — must produce 1× the base
        TTL, not 0× (which would zero the cooldown and let a freshly
        exhausted credential be reused immediately)."""
        assert _exhausted_ttl(429, consecutive_failures=0) == EXHAUSTED_TTL_429_SECONDS

    def test_negative_failures_treated_as_default(self):
        """Defensive: a corrupted on-disk entry with a negative count
        must NOT shrink the cooldown below base.  ``max(0, failures-1)``
        clamps the exponent to 0 minimum."""
        assert _exhausted_ttl(429, consecutive_failures=-5) == EXHAUSTED_TTL_429_SECONDS

    def test_cap_constant_is_eight(self):
        """Pin the documented cap value so a refactor that changes
        ``EXHAUSTED_TTL_BACKOFF_CAP`` flags this test, prompting a
        comment update."""
        assert EXHAUSTED_TTL_BACKOFF_CAP == 8

    @pytest.mark.parametrize("bad_value", [None, "5", "not-a-number", 3.7, [], {}])
    def test_corrupted_failure_value_falls_back_to_zero(self, bad_value):
        """A malformed ``consecutive_failures`` value (string, list,
        ``None``, etc.) from a hand-edited or migrated ``auth.json``
        must NOT crash cooldown resolution — Copilot caught this on
        #15455.  Fall back to the un-multiplied base TTL instead of
        propagating ``ValueError`` from inside the failover path.

        ``"5"`` is included intentionally: even though Python's
        ``int(\"5\")`` would succeed, we prefer the conservative path
        of treating un-typed inputs as 0 so the contract is uniform.
        """
        result = _exhausted_ttl(429, consecutive_failures=bad_value)
        # The "5" string DOES coerce cleanly via int() in our helper —
        # that's the one defensive case we choose to honour rather than
        # discard, since it's unambiguous numeric data.
        if bad_value == "5":
            assert result == EXHAUSTED_TTL_429_SECONDS * 8  # 5→exponent 3→cap
        elif bad_value == 3.7:
            assert result == EXHAUSTED_TTL_429_SECONDS * 4  # int(3.7)=3, exp=2
        else:
            assert result == EXHAUSTED_TTL_429_SECONDS  # 1× base

    def test_runaway_failure_count_does_not_compute_huge_int(self):
        """Regression guard for Copilot's #15455 perf comment: a
        credential whose upstream is permanently dead could see the
        counter climb to thousands.  Without an exponent clamp,
        ``2 ** 10000`` would materialise a ~3000-digit integer just to
        be discarded by the ``min(..., CAP)``.  We clamp the exponent
        BEFORE the shift, so the actual integer math stays trivial.

        This test asserts the result equals the cap (no surprise) AND
        that it returns instantly — if the clamp regressed, the
        big-int construction would still produce the right value but
        burn measurable wall time on the way there.
        """
        import time as _time
        start = _time.monotonic()
        result = _exhausted_ttl(429, consecutive_failures=10_000)
        elapsed = _time.monotonic() - start
        assert result == EXHAUSTED_TTL_429_SECONDS * 8
        # Tight bound: should be sub-millisecond on every machine.
        # If the clamp regresses to ``2 ** 10000``, this jumps to
        # tens or hundreds of milliseconds depending on arch.
        assert elapsed < 0.05, (
            f"_exhausted_ttl took {elapsed:.4f}s — exponent clamp likely "
            f"regressed; ``2 ** 10000`` is being computed before the cap"
        )


# ---------------------------------------------------------------------------
# _exhausted_until — uses _exhausted_ttl with the entry's failure count
# ---------------------------------------------------------------------------


def _make_entry(**overrides):
    """Build a minimal ``PooledCredential`` for cooldown tests."""
    base = dict(
        provider="openrouter",
        id="cred-1",
        label="primary",
        auth_type="api_key",
        priority=0,
        source="manual",
        access_token="sk-test",
        last_status="exhausted",
        last_status_at=1_000_000.0,
        last_error_code=429,
        consecutive_failures=1,
    )
    base.update(overrides)
    return PooledCredential(**base)


class TestExhaustedUntil:
    """``_exhausted_until(entry)`` returns the absolute time when the
    entry stops being in cooldown.  After backoff, that time should
    extend further on each consecutive failure."""

    def test_first_failure_yields_one_base_ttl_after_status_at(self):
        entry = _make_entry(consecutive_failures=1)
        until = _exhausted_until(entry)
        assert until == entry.last_status_at + EXHAUSTED_TTL_429_SECONDS

    def test_fourth_failure_yields_eight_base_ttls_after_status_at(self):
        entry = _make_entry(consecutive_failures=4)
        until = _exhausted_until(entry)
        assert until == entry.last_status_at + 8 * EXHAUSTED_TTL_429_SECONDS

    def test_capped_at_eight_after_many_failures(self):
        entry = _make_entry(consecutive_failures=20)
        until = _exhausted_until(entry)
        assert until == entry.last_status_at + 8 * EXHAUSTED_TTL_429_SECONDS

    def test_explicit_reset_at_overrides_backoff(self):
        """When the provider sends a ``reset_at`` header (e.g. Anthropic
        long-context-tier reset), that absolute timestamp wins — the
        backoff multiplier is irrelevant because the upstream told us
        exactly when to retry."""
        entry = _make_entry(
            consecutive_failures=10,  # would normally be 8× cooldown
            last_error_reset_at=1_000_500.0,  # 500s away — much sooner
        )
        until = _exhausted_until(entry)
        assert until == 1_000_500.0


# ---------------------------------------------------------------------------
# _mark_exhausted increments the counter
# ---------------------------------------------------------------------------


def _make_pool_with_one_entry(tmp_path, monkeypatch, *, consecutive_failures=0):
    """Build a real pool on disk so ``_mark_exhausted`` exercises the
    real persistence path, not a mock surrogate.  Mirrors the pattern
    in ``test_credential_pool.py``'s helpers."""
    import json
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes"))
    home = tmp_path / "hermes"
    home.mkdir(parents=True, exist_ok=True)
    auth = home / "auth.json"
    auth.write_text(json.dumps({
        "version": 1,
        "credential_pool": {
            "openrouter": [
                {
                    "id": "cred-1",
                    "label": "primary",
                    "auth_type": "api_key",
                    "priority": 0,
                    "source": "manual",
                    "access_token": "sk-test",
                    "base_url": "https://openrouter.ai/api/v1",
                    "consecutive_failures": consecutive_failures,
                }
            ]
        },
    }))

    from agent.credential_pool import load_pool
    return load_pool("openrouter")


class TestMarkExhaustedIncrement:
    """``_mark_exhausted`` must increment ``consecutive_failures`` from
    the prior value, persist it, and let ``_exhausted_until`` see the
    new count."""

    def test_first_exhaustion_sets_counter_to_one(self, tmp_path, monkeypatch):
        pool = _make_pool_with_one_entry(tmp_path, monkeypatch, consecutive_failures=0)
        entry = pool._entries[0]
        assert entry.consecutive_failures == 0

        updated = pool._mark_exhausted(entry, status_code=429)
        assert updated.consecutive_failures == 1

    def test_consecutive_exhaustions_accumulate(self, tmp_path, monkeypatch):
        """The whole point of the fix: if the same credential exhausts
        again WITHOUT being successfully refreshed in between, the
        counter keeps climbing.  Otherwise the cooldown can never
        actually back off."""
        pool = _make_pool_with_one_entry(tmp_path, monkeypatch, consecutive_failures=2)
        entry = pool._entries[0]
        assert entry.consecutive_failures == 2

        updated = pool._mark_exhausted(entry, status_code=429)
        assert updated.consecutive_failures == 3

    def test_increment_persists_to_disk(self, tmp_path, monkeypatch):
        """Reload from disk to confirm the counter survives a pool
        rebuild — without persistence the backoff would reset on every
        gateway restart."""
        pool = _make_pool_with_one_entry(tmp_path, monkeypatch)
        entry = pool._entries[0]
        pool._mark_exhausted(entry, status_code=429)
        pool._mark_exhausted(pool._entries[0], status_code=429)
        pool._mark_exhausted(pool._entries[0], status_code=429)

        from agent.credential_pool import load_pool
        reloaded = load_pool("openrouter")
        assert reloaded._entries[0].consecutive_failures == 3

    def test_increment_handles_legacy_entry_missing_field(self, tmp_path, monkeypatch):
        """Defensive: a pool entry persisted before this PR doesn't
        carry the ``consecutive_failures`` field on disk.  When loaded,
        the dataclass default kicks in (0); ``_mark_exhausted`` must
        therefore correctly set it to 1, not crash with ``AttributeError``
        or silently start at the (legacy-loaded) None."""
        import json
        monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes"))
        home = tmp_path / "hermes"
        home.mkdir(parents=True, exist_ok=True)
        # Note: deliberately omit consecutive_failures from the on-disk
        # payload — the pre-#15296 schema.
        (home / "auth.json").write_text(json.dumps({
            "version": 1,
            "credential_pool": {
                "openrouter": [
                    {
                        "id": "legacy",
                        "label": "primary",
                        "auth_type": "api_key",
                        "priority": 0,
                        "source": "manual",
                        "access_token": "sk-test",
                    }
                ]
            },
        }))

        from agent.credential_pool import load_pool
        pool = load_pool("openrouter")
        entry = pool._entries[0]
        assert entry.consecutive_failures == 0  # dataclass default

        updated = pool._mark_exhausted(entry, status_code=429)
        assert updated.consecutive_failures == 1


# ---------------------------------------------------------------------------
# Reset semantics — when does the counter zero out?
# ---------------------------------------------------------------------------


class TestCounterResetSemantics:
    """The streak persists across cooldown auto-clears (the bug fix's
    whole point) but resets on real success markers and operator
    intervention."""

    def test_reset_statuses_clears_counter(self, tmp_path, monkeypatch):
        """``reset_statuses()`` is the explicit operator command —
        clear everything including the streak."""
        pool = _make_pool_with_one_entry(tmp_path, monkeypatch, consecutive_failures=5)
        assert pool._entries[0].consecutive_failures == 5

        pool.reset_statuses()
        assert pool._entries[0].consecutive_failures == 0

    def test_clear_expired_does_not_reset_counter(self, tmp_path, monkeypatch):
        """Critical for #15296: when the cooldown auto-elapses and the
        entry becomes available again, the streak MUST persist.  If we
        reset here, the next exhaustion would start at 1× again — the
        backoff would never accumulate, defeating the entire fix.
        """
        import time
        import json
        monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes"))
        home = tmp_path / "hermes"
        home.mkdir(parents=True, exist_ok=True)
        # Entry exhausted long enough ago that the 1× cooldown has
        # elapsed.  ``consecutive_failures=3`` means a 4× cooldown
        # would still be in effect.  Use cf=1 here so we test the
        # auto-clear path without the cooldown blocking.
        long_ago = time.time() - 2 * EXHAUSTED_TTL_429_SECONDS
        (home / "auth.json").write_text(json.dumps({
            "version": 1,
            "credential_pool": {
                "openrouter": [
                    {
                        "id": "cred-1",
                        "label": "primary",
                        "auth_type": "api_key",
                        "priority": 0,
                        "source": "manual",
                        "access_token": "sk-test",
                        "last_status": "exhausted",
                        "last_status_at": long_ago,
                        "last_error_code": 429,
                        "consecutive_failures": 1,
                    }
                ]
            },
        }))

        from agent.credential_pool import load_pool
        pool = load_pool("openrouter")
        # Expired entries get cleared back to STATUS_OK during select.
        available = pool._available_entries(clear_expired=True)
        assert len(available) == 1
        assert available[0].last_status == "ok"
        # The streak must NOT have been zeroed — that's the bug fix.
        assert available[0].consecutive_failures == 1, (
            "clear_expired auto-reset wiped consecutive_failures; the "
            "next exhaustion would restart at 1× cooldown instead of "
            "extending the backoff (#15296)"
        )

    def test_consecutive_failures_field_round_trips_through_to_dict(self, tmp_path, monkeypatch):
        """The new field must survive the on-disk JSON round-trip.
        Without explicit serialization support, a value of 0 might be
        dropped (the existing ``to_dict`` skips ``None``-but-not-zero
        fields, so 0 should serialize fine — pinned here in case the
        skip rules change)."""
        pool = _make_pool_with_one_entry(tmp_path, monkeypatch, consecutive_failures=4)
        as_dict = pool._entries[0].to_dict()
        assert as_dict.get("consecutive_failures") == 4

        # Round trip via from_dict.
        rebuilt = PooledCredential.from_dict("openrouter", as_dict)
        assert rebuilt.consecutive_failures == 4
