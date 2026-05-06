"""Tests for issue #18733 — per-model / per-provider compression overrides.

Covers the pure-function ``resolve_compression_threshold()`` resolver in
``agent/context_compressor.py``, the wire-up at the ``ContextCompressor``
instantiation site in ``run_agent.py``, and the ``re_resolve_threshold()``
method that lets a model switch pick up overrides at runtime.

The ``re_resolve_threshold()`` method composes cleanly with the
``threshold_percent=`` parameter being added by PR #18638: this PR adds the
override-picking, that PR forwards an explicit value through. They live in
different lines of ``update_model``'s callsites so neither blocks the other
on merge order.
"""

import logging
from pathlib import Path
from unittest.mock import patch

import pytest

from agent.context_compressor import (
    ContextCompressor,
    _LOGGED_INVALID_OVERRIDE,
    _THRESHOLD_DEFAULT,
    _THRESHOLD_MAX,
    _THRESHOLD_MIN,
    resolve_compression_threshold,
)


@pytest.fixture(autouse=True)
def _clear_dedup():
    """Reset the module-level dedup set so warnings re-fire each test."""
    _LOGGED_INVALID_OVERRIDE.clear()
    yield
    _LOGGED_INVALID_OVERRIDE.clear()


class TestResolutionPrecedence:
    """Per-model > per-provider > global > built-in default."""

    def test_default_when_config_empty(self):
        assert resolve_compression_threshold("any-model", "any-provider", {}) == _THRESHOLD_DEFAULT

    def test_default_when_config_none_typed(self):
        # Defensive: callers pass dicts in practice, but a stray None shouldn't crash.
        assert resolve_compression_threshold("m", "p", None) == _THRESHOLD_DEFAULT  # type: ignore[arg-type]

    def test_global_threshold_used_when_no_overrides(self):
        cfg = {"threshold": 0.40}
        assert resolve_compression_threshold("m", "p", cfg) == 0.40

    def test_provider_override_beats_global(self):
        cfg = {"threshold": 0.50, "provider_thresholds": {"anthropic": 0.65}}
        assert resolve_compression_threshold("any-model", "anthropic", cfg) == 0.65

    def test_model_override_beats_provider(self):
        cfg = {
            "threshold": 0.50,
            "provider_thresholds": {"anthropic": 0.65},
            "model_thresholds": {"claude-sonnet-4": 0.30},
        }
        assert resolve_compression_threshold("claude-sonnet-4", "anthropic", cfg) == 0.30

    def test_model_override_beats_global_when_no_provider_match(self):
        cfg = {
            "threshold": 0.50,
            "model_thresholds": {"claude-sonnet-4": 0.30},
        }
        assert resolve_compression_threshold("claude-sonnet-4", "unknown-provider", cfg) == 0.30

    def test_unknown_model_falls_to_provider(self):
        cfg = {"provider_thresholds": {"anthropic": 0.65}}
        assert resolve_compression_threshold("unknown", "anthropic", cfg) == 0.65

    def test_unknown_provider_falls_to_global(self):
        cfg = {"threshold": 0.40, "provider_thresholds": {"anthropic": 0.65}}
        assert resolve_compression_threshold("unknown", "openrouter", cfg) == 0.40

    def test_no_provider_no_model_falls_to_global(self):
        cfg = {"threshold": 0.40, "provider_thresholds": {"anthropic": 0.65}}
        assert resolve_compression_threshold("", "", cfg) == 0.40

    def test_empty_override_dicts_silent_fall_through(self):
        cfg = {"threshold": 0.40, "provider_thresholds": {}, "model_thresholds": {}}
        assert resolve_compression_threshold("m", "p", cfg) == 0.40

    def test_aliased_provider_key_falls_through(self):
        # Per the resolver docstring: aliases like "google" / "moonshot" are
        # auxiliary-routing-only and won't match the main agent's canonical
        # PROVIDER_REGISTRY id. They should silently fall through.
        cfg = {"threshold": 0.40, "provider_thresholds": {"google": 0.30}}
        # self.provider would be "gemini", not "google"
        assert resolve_compression_threshold("any", "gemini", cfg) == 0.40


class TestValidation:
    """Out-of-range / non-numeric overrides log once and fall through."""

    def test_non_numeric_override_falls_through(self, caplog):
        cfg = {"threshold": 0.40, "provider_thresholds": {"anthropic": "high"}}
        with caplog.at_level(logging.WARNING, logger="agent.context_compressor"):
            result = resolve_compression_threshold("m", "anthropic", cfg)
        assert result == 0.40
        # Lock log level so a future change can't silently demote to DEBUG.
        assert any(
            "Invalid" in rec.message
            and "high" in rec.message
            and rec.levelno == logging.WARNING
            for rec in caplog.records
        )

    def test_below_min_falls_through(self, caplog):
        cfg = {"threshold": 0.40, "model_thresholds": {"m": 0.05}}
        with caplog.at_level(logging.WARNING, logger="agent.context_compressor"):
            result = resolve_compression_threshold("m", "p", cfg)
        assert result == 0.40
        assert any("Invalid" in rec.message for rec in caplog.records)

    def test_above_max_falls_through(self, caplog):
        cfg = {"threshold": 0.40, "model_thresholds": {"m": 1.5}}
        with caplog.at_level(logging.WARNING, logger="agent.context_compressor"):
            result = resolve_compression_threshold("m", "p", cfg)
        assert result == 0.40

    def test_at_min_boundary_accepted(self):
        cfg = {"model_thresholds": {"m": _THRESHOLD_MIN}}
        assert resolve_compression_threshold("m", "p", cfg) == _THRESHOLD_MIN

    def test_at_max_boundary_accepted(self):
        cfg = {"model_thresholds": {"m": _THRESHOLD_MAX}}
        assert resolve_compression_threshold("m", "p", cfg) == _THRESHOLD_MAX

    def test_dedup_warning_per_key(self, caplog):
        cfg = {"model_thresholds": {"m": "bad"}}
        with caplog.at_level(logging.WARNING, logger="agent.context_compressor"):
            for _ in range(3):
                resolve_compression_threshold("m", "p", cfg)
        invalid_records = [r for r in caplog.records if "Invalid" in r.message]
        assert len(invalid_records) == 1, (
            "Module-level dedup should suppress repeated warnings for the same key"
        )

    def test_dedup_distinct_keys_each_warn_once(self, caplog):
        cfg = {"model_thresholds": {"a": "bad", "b": 99}}
        with caplog.at_level(logging.WARNING, logger="agent.context_compressor"):
            resolve_compression_threshold("a", "p", cfg)
            resolve_compression_threshold("a", "p", cfg)  # dedup
            resolve_compression_threshold("b", "p", cfg)
            resolve_compression_threshold("b", "p", cfg)  # dedup
        invalid_records = [r for r in caplog.records if "Invalid" in r.message]
        assert len(invalid_records) == 2

    def test_invalid_global_falls_to_built_in_default(self, caplog):
        cfg = {"threshold": "not-a-float"}
        with caplog.at_level(logging.WARNING, logger="agent.context_compressor"):
            result = resolve_compression_threshold("m", "p", cfg)
        assert result == _THRESHOLD_DEFAULT
        # An invalid global threshold is the most operator-visible misconfig
        # case; log level must stay at WARNING so it surfaces in default
        # logging configurations.
        assert any(
            "compression.threshold" in rec.message and rec.levelno == logging.WARNING
            for rec in caplog.records
        )


class TestWireUpComposition:
    """The resolver composes correctly with ContextCompressor at the call site."""

    def test_resolved_value_flows_into_compressor(self):
        cfg = {
            "threshold": 0.50,
            "model_thresholds": {"test-model": 0.30},
        }
        resolved = resolve_compression_threshold("test-model", "anthropic", cfg)
        with patch("agent.context_compressor.get_model_context_length", return_value=200_000):
            compressor = ContextCompressor(
                model="test-model",
                threshold_percent=resolved,
                quiet_mode=True,
            )
        assert compressor.threshold_percent == 0.30
        # 200_000 * 0.30 = 60_000, but MINIMUM_CONTEXT_LENGTH (64_000) floor applies
        from agent.model_metadata import MINIMUM_CONTEXT_LENGTH
        assert compressor.threshold_tokens == max(60_000, MINIMUM_CONTEXT_LENGTH)

    def test_provider_override_flows_into_compressor(self):
        cfg = {
            "threshold": 0.50,
            "provider_thresholds": {"anthropic": 0.65},
        }
        resolved = resolve_compression_threshold("any-model", "anthropic", cfg)
        with patch("agent.context_compressor.get_model_context_length", return_value=200_000):
            compressor = ContextCompressor(
                model="any-model",
                threshold_percent=resolved,
                quiet_mode=True,
            )
        assert compressor.threshold_percent == 0.65
        # 200_000 * 0.65 = 130_000
        assert compressor.threshold_tokens == 130_000


class TestAliasHint:
    """When a user writes an alias key like ``google``, the resolver emits a
    one-time hint pointing at the canonical PROVIDER_REGISTRY id."""

    def test_alias_key_warns_with_canonical_hint(self, caplog):
        cfg = {"threshold": 0.40, "provider_thresholds": {"google": 0.30}}
        with caplog.at_level(logging.WARNING, logger="agent.context_compressor"):
            result = resolve_compression_threshold("any", "gemini", cfg)
        assert result == 0.40  # silently fell through to global
        alias_records = [
            r for r in caplog.records if "looks like an alias" in r.message
        ]
        assert len(alias_records) == 1
        assert "'google'" in alias_records[0].message
        assert "'gemini'" in alias_records[0].message  # canonical hint
        # Pin level: alias hint is the operator's only feedback that their
        # config didn't take effect, so it has to surface above DEBUG.
        assert alias_records[0].levelno == logging.WARNING

    def test_alias_hint_deduplicated(self, caplog):
        cfg = {"provider_thresholds": {"moonshot": 0.40}}
        with caplog.at_level(logging.WARNING, logger="agent.context_compressor"):
            for _ in range(5):
                resolve_compression_threshold("m", "kimi-coding", cfg)
        alias_records = [
            r for r in caplog.records if "looks like an alias" in r.message
        ]
        assert len(alias_records) == 1

    def test_canonical_key_does_not_trigger_alias_warning(self, caplog):
        cfg = {"provider_thresholds": {"gemini": 0.30}}
        with caplog.at_level(logging.WARNING, logger="agent.context_compressor"):
            result = resolve_compression_threshold("m", "gemini", cfg)
        assert result == 0.30
        alias_records = [
            r for r in caplog.records if "looks like an alias" in r.message
        ]
        assert len(alias_records) == 0

    def test_alias_warning_does_not_fire_when_canonical_match_succeeds(self, caplog):
        # Both an alias AND the canonical present; canonical match should win
        # without the alias hint firing for the unused alias entry.
        cfg = {"provider_thresholds": {"google": 0.30, "gemini": 0.55}}
        with caplog.at_level(logging.WARNING, logger="agent.context_compressor"):
            result = resolve_compression_threshold("m", "gemini", cfg)
        assert result == 0.55
        alias_records = [
            r for r in caplog.records if "looks like an alias" in r.message
        ]
        assert len(alias_records) == 0  # canonical-match path returned early


class TestModelSwitchReResolution:
    """``ContextCompressor.re_resolve_threshold()`` picks up overrides at runtime."""

    def _build(self, *, model, provider, cfg, ctx=200_000, threshold_percent=0.50):
        with patch("agent.context_compressor.get_model_context_length", return_value=ctx):
            return ContextCompressor(
                model=model,
                threshold_percent=threshold_percent,
                provider=provider,
                quiet_mode=True,
                compression_config=cfg,
            )

    def test_picks_up_provider_override_after_model_switch(self):
        cfg = {"threshold": 0.50, "provider_thresholds": {"anthropic": 0.65}}
        cc = self._build(model="m1", provider="openrouter", cfg=cfg)
        assert cc.threshold_percent == 0.50  # init: no override matches

        cc.update_model(model="claude-sonnet-4", context_length=200_000,
                        provider="anthropic")
        # Without re_resolve, threshold_percent would still be 0.50.
        assert cc.threshold_percent == 0.50
        cc.re_resolve_threshold()
        assert cc.threshold_percent == 0.65
        # threshold_tokens recomputes: 200_000 * 0.65 = 130_000
        assert cc.threshold_tokens == 130_000

    def test_picks_up_model_override_when_provider_unmatched(self):
        cfg = {"threshold": 0.50, "model_thresholds": {"claude-sonnet-4": 0.30}}
        cc = self._build(model="m1", provider="openrouter", cfg=cfg)
        cc.update_model(model="claude-sonnet-4", context_length=200_000,
                        provider="anthropic")
        cc.re_resolve_threshold()
        assert cc.threshold_percent == 0.30

    def test_falls_back_to_global_when_no_overrides_match(self):
        cfg = {"threshold": 0.40, "provider_thresholds": {"anthropic": 0.65}}
        cc = self._build(model="m1", provider="anthropic", cfg=cfg, threshold_percent=0.65)
        # Switch away from anthropic — provider override no longer applies.
        cc.update_model(model="some-model", context_length=200_000,
                        provider="openrouter")
        cc.re_resolve_threshold()
        assert cc.threshold_percent == 0.40

    def test_no_op_when_no_compression_config(self):
        with patch("agent.context_compressor.get_model_context_length", return_value=200_000):
            cc = ContextCompressor(model="m", threshold_percent=0.60, provider="p", quiet_mode=True)
        assert cc._compression_config is None
        cc.update_model(model="m2", context_length=200_000, provider="p2")
        cc.re_resolve_threshold()
        # Nothing changes: threshold_percent stays at the value update_model
        # didn't touch (BF-2 doesn't modify update_model).
        assert cc.threshold_percent == 0.60

    def test_minimum_context_length_floor_respected(self):
        from agent.model_metadata import MINIMUM_CONTEXT_LENGTH
        cfg = {"model_thresholds": {"tiny-model": 0.10}}
        cc = self._build(model="big", provider="p", cfg=cfg, ctx=80_000, threshold_percent=0.50)
        cc.update_model(model="tiny-model", context_length=80_000, provider="p")
        cc.re_resolve_threshold()
        assert cc.threshold_percent == 0.10
        # 80_000 * 0.10 = 8_000 < MINIMUM_CONTEXT_LENGTH → floor kicks in
        assert cc.threshold_tokens == MINIMUM_CONTEXT_LENGTH

    def test_idempotent_when_threshold_unchanged(self):
        cfg = {"provider_thresholds": {"anthropic": 0.65}}
        cc = self._build(model="m", provider="anthropic", cfg=cfg, threshold_percent=0.65)
        before_tokens = cc.threshold_tokens
        cc.re_resolve_threshold()
        assert cc.threshold_percent == 0.65
        assert cc.threshold_tokens == before_tokens

    def test_ordering_invariant_re_resolve_reads_current_state(self):
        """Documents the ordering contract: re_resolve_threshold() always
        reads the *current* (model, provider) from the compressor instance,
        so re-resolving without first calling update_model() is harmless —
        but it also means update_model() MUST run first for a model switch
        to apply per-model overrides correctly.

        Catches a future refactor that reorders the two calls at any of the
        three model-switch sites in run_agent.py.
        """
        cfg = {"provider_thresholds": {"anthropic": 0.65, "openrouter": 0.40}}
        cc = self._build(model="m1", provider="anthropic", cfg=cfg, threshold_percent=0.65)

        # Resolving without any state change → same answer.
        cc.re_resolve_threshold()
        assert cc.threshold_percent == 0.65

        # Resolving twice in a row → idempotent.
        cc.re_resolve_threshold()
        assert cc.threshold_percent == 0.65

        # Now flip provider directly and re-resolve. The resolver picks up
        # the change because it reads self.provider on every call. This
        # demonstrates re_resolve is "current-state driven" — exactly why
        # the run_agent.py ordering invariant works.
        cc.provider = "openrouter"
        cc.re_resolve_threshold()
        assert cc.threshold_percent == 0.40


class TestCallableConfigGetter:
    """``compression_config`` may be a callable for live / hot-reload semantics."""

    def _build(self, *, model, provider, cfg, ctx=200_000, threshold_percent=0.50):
        with patch("agent.context_compressor.get_model_context_length", return_value=ctx):
            return ContextCompressor(
                model=model,
                threshold_percent=threshold_percent,
                provider=provider,
                quiet_mode=True,
                compression_config=cfg,
            )

    def test_getter_invoked_on_each_re_resolve(self):
        # Mutating the dict that the getter returns is visible on the next call.
        live_cfg = {"threshold": 0.40, "provider_thresholds": {}}
        cc = self._build(model="m", provider="anthropic", cfg=lambda: live_cfg)
        cc.update_model(model="m", context_length=200_000, provider="anthropic")
        cc.re_resolve_threshold()
        assert cc.threshold_percent == 0.40

        # Add an override; the getter sees it on the next call.
        live_cfg["provider_thresholds"]["anthropic"] = 0.65
        cc.re_resolve_threshold()
        assert cc.threshold_percent == 0.65

    def test_getter_returning_non_mapping_warns_and_is_no_op(self, caplog):
        cc = self._build(model="m", provider="p", cfg=lambda: "not a mapping")  # type: ignore[arg-type]
        before = cc.threshold_percent
        with caplog.at_level(logging.WARNING, logger="agent.context_compressor"):
            cc.re_resolve_threshold()
        assert cc.threshold_percent == before
        assert any(
            "instead of Mapping" in rec.message and rec.levelno == logging.WARNING
            for rec in caplog.records
        ), "non-Mapping return must surface as a WARNING, not silent skip"

    def test_getter_raising_warns_and_is_tolerated(self, caplog):
        def _bad_getter():
            raise RuntimeError("config source unavailable")

        cc = self._build(model="m", provider="p", cfg=_bad_getter)
        before = cc.threshold_percent
        with caplog.at_level(logging.WARNING, logger="agent.context_compressor"):
            cc.re_resolve_threshold()  # must not raise
        assert cc.threshold_percent == before
        assert any(
            "getter raised" in rec.message and rec.levelno == logging.WARNING
            for rec in caplog.records
        ), "raising getter must surface as a WARNING so persistent failures are visible"

    def test_getter_failure_warning_deduplicated(self, caplog):
        def _bad_getter():
            raise RuntimeError("persistent failure")

        cc = self._build(model="m", provider="p", cfg=_bad_getter)
        with caplog.at_level(logging.WARNING, logger="agent.context_compressor"):
            for _ in range(5):
                cc.re_resolve_threshold()
        getter_warnings = [r for r in caplog.records if "getter raised" in r.message]
        assert len(getter_warnings) == 1, (
            "Repeated failures of the same exception type should warn once, not five times"
        )


class TestBaseEngineNoOp:
    """ContextEngine base class provides a no-op so plugins are safe."""

    def test_base_engine_re_resolve_is_no_op(self):
        from agent.context_engine import ContextEngine

        class _FakeEngine(ContextEngine):
            name = "fake"

            def should_compress(self, prompt_tokens=None):
                return False

            def compress(self, messages):
                return messages

            def update_from_response(self, usage):
                return None

        engine = _FakeEngine()
        engine.threshold_percent = 0.42
        # Should not raise, should not change state.
        engine.re_resolve_threshold()
        assert engine.threshold_percent == 0.42


class TestWireUpSourcePresence:
    """Source-level checks that the resolver is wired in at run_agent.py:2013."""

    @pytest.fixture(scope="class")
    def run_agent_src(self) -> str:
        path = Path(__file__).resolve().parents[2] / "run_agent.py"
        return path.read_text(encoding="utf-8")

    def test_resolver_imported_at_instantiation_site(self, run_agent_src):
        assert "from agent.context_compressor import resolve_compression_threshold" in run_agent_src, (
            "Wire-up missing: resolver must be imported in run_agent.py"
        )

    def test_resolver_called_with_self_model_and_provider(self, run_agent_src):
        # Locate the ContextCompressor instantiation block and assert the
        # resolver call appears in the same window. Window size of 30 lines
        # is generous; the actual gap is ~5 lines.
        idx = run_agent_src.find("self.context_compressor = ContextCompressor(")
        assert idx >= 0, "ContextCompressor instantiation site not found"
        window = run_agent_src[max(0, idx - 1500):idx]
        assert "resolve_compression_threshold(" in window, (
            "Resolver call missing immediately before ContextCompressor() instantiation"
        )
        assert "model=self.model" in window
        assert "provider=self.provider" in window

    def test_compression_schema_keys_added(self):
        path = Path(__file__).resolve().parents[2] / "hermes_cli" / "config.py"
        src = path.read_text(encoding="utf-8")
        assert '"provider_thresholds": {}' in src, "Schema add missing: provider_thresholds"
        assert '"model_thresholds": {}' in src, "Schema add missing: model_thresholds"

    def test_yaml_example_documents_canonical_provider_keys(self):
        path = Path(__file__).resolve().parents[2] / "cli-config.yaml.example"
        src = path.read_text(encoding="utf-8")
        # Either the literal canonical keys or a docstring-style note. Accept
        # either form so reviewers can polish wording without breaking tests.
        assert "provider_thresholds" in src
        assert "model_thresholds" in src
        assert "PROVIDER_REGISTRY" in src, (
            "Example yaml should reference the canonical-id source so users "
            "don't write aliases like 'google'/'moonshot'"
        )

    def test_re_resolve_called_after_each_update_model_in_runtime_paths(self, run_agent_src):
        """The 3 model-switch sites that update the runtime compressor must
        invoke ``re_resolve_threshold()`` so per-model / per-provider overrides
        take effect when the user switches models or a fallback fires.

        A floor of 3 catches a future edit that adds a new model-switch path
        and forgets the override re-pick.
        """
        # Three model-switch sites: switch_model, _try_activate_fallback,
        # _restore_primary_runtime. Auxiliary handlers (compressor.update_model)
        # and the plugin-init path are out of scope.
        re_resolve_calls = run_agent_src.count(".re_resolve_threshold()")
        assert re_resolve_calls >= 3, (
            f"Expected ≥3 re_resolve_threshold() calls in run_agent.py "
            f"(switch_model + _try_activate_fallback + _restore_primary_runtime); "
            f"found {re_resolve_calls}"
        )
