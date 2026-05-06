"""Regression tests for #20004 — max_tokens from custom_providers.

The gateway's runtime resolution previously dropped any ``max_tokens`` set
on a ``custom_providers`` (or ``providers``) entry: config normalization
discarded the key as "unknown" and runtime resolution never carried it
through.  Result: an explicit per-endpoint output cap was silently
overridden by either ``model.max_tokens`` (also ignored) or the
transport-layer hardcoded default.

These tests pin the new resolution chain in two layers:

1. ``_normalize_custom_provider_entry`` accepts and propagates
   positive-int ``max_tokens``; rejects bogus values.
2. ``_attach_custom_provider_max_tokens`` lifts the value onto the
   runtime dict so it reaches AIAgent via ``turn_route['runtime']``.
3. ``_get_named_custom_provider`` uses the helper across all three
   provider lookup paths (providers-dict-by-key, providers-dict
   -by-display-name, legacy custom_providers list).
"""
from __future__ import annotations

import pytest

from hermes_cli import runtime_provider as rp
from hermes_cli.config import _normalize_custom_provider_entry


# ---------------------------------------------------------------------------
# Layer 1: config normalization
# ---------------------------------------------------------------------------


class TestNormalizeAcceptsMaxTokens:
    def test_positive_int_propagated(self):
        normalized = _normalize_custom_provider_entry(
            {
                "name": "ark",
                "base_url": "https://example.invalid/v1",
                "max_tokens": 131_072,
            }
        )
        assert normalized["max_tokens"] == 131_072

    def test_missing_key_does_not_inject_default(self):
        normalized = _normalize_custom_provider_entry(
            {"name": "ark", "base_url": "https://example.invalid/v1"}
        )
        assert "max_tokens" not in normalized

    def test_zero_rejected(self):
        normalized = _normalize_custom_provider_entry(
            {"name": "ark", "base_url": "https://example.invalid/v1", "max_tokens": 0}
        )
        assert "max_tokens" not in normalized

    def test_negative_rejected(self):
        normalized = _normalize_custom_provider_entry(
            {"name": "ark", "base_url": "https://example.invalid/v1", "max_tokens": -1}
        )
        assert "max_tokens" not in normalized

    def test_string_rejected(self):
        # "64K" should not crash — we want to fall through cleanly.
        normalized = _normalize_custom_provider_entry(
            {"name": "ark", "base_url": "https://example.invalid/v1", "max_tokens": "64K"}
        )
        assert "max_tokens" not in normalized


# ---------------------------------------------------------------------------
# Layer 2: helper lifts the value (or doesn't) onto a runtime dict
# ---------------------------------------------------------------------------


class TestAttachHelper:
    def test_positive_int_lifted(self):
        result = {}
        rp._attach_custom_provider_max_tokens(result, {"max_tokens": 131_072})
        assert result["max_tokens"] == 131_072

    def test_missing_no_op(self):
        result = {"existing": 1}
        rp._attach_custom_provider_max_tokens(result, {"name": "ark"})
        assert "max_tokens" not in result
        assert result == {"existing": 1}

    def test_zero_rejected(self):
        result = {}
        rp._attach_custom_provider_max_tokens(result, {"max_tokens": 0})
        assert "max_tokens" not in result

    def test_negative_rejected(self):
        result = {}
        rp._attach_custom_provider_max_tokens(result, {"max_tokens": -10})
        assert "max_tokens" not in result

    def test_string_rejected(self):
        result = {}
        rp._attach_custom_provider_max_tokens(result, {"max_tokens": "64K"})
        assert "max_tokens" not in result

    def test_none_rejected(self):
        result = {}
        rp._attach_custom_provider_max_tokens(result, {"max_tokens": None})
        assert "max_tokens" not in result

    def test_does_not_overwrite_existing(self):
        # Helper only assigns when the source has a valid value, so a None
        # source must leave a previously-set max_tokens alone.
        result = {"max_tokens": 99}
        rp._attach_custom_provider_max_tokens(result, {})
        assert result["max_tokens"] == 99


# ---------------------------------------------------------------------------
# Layer 3: end-to-end through _get_named_custom_provider
# ---------------------------------------------------------------------------


@pytest.fixture
def patched_config(monkeypatch):
    """Allow each test to set the config dict that ``load_config()`` returns."""
    state = {"cfg": {}}

    def _fake_load_config():
        return state["cfg"]

    monkeypatch.setattr(rp, "load_config", _fake_load_config)
    # Bypass the credential-pool path inside the lookup.
    monkeypatch.setattr(rp, "load_pool", lambda provider: None)

    def _set(cfg):
        state["cfg"] = cfg

    return _set


class TestRuntimeLookupCarriesMaxTokens:
    def test_legacy_custom_providers_list(self, patched_config):
        patched_config(
            {
                "custom_providers": [
                    {
                        "name": "ark",
                        "base_url": "https://example.invalid/v1",
                        "model": "ark-fast",
                        "api_key": "xx",
                        "max_tokens": 131_072,
                    }
                ]
            }
        )

        resolved = rp._get_named_custom_provider("ark")

        assert resolved is not None
        assert resolved["max_tokens"] == 131_072

    def test_providers_dict_keyed(self, patched_config):
        patched_config(
            {
                "providers": {
                    "ark": {
                        "base_url": "https://example.invalid/v1",
                        "api_key": "xx",
                        "max_tokens": 64_000,
                    }
                }
            }
        )

        resolved = rp._get_named_custom_provider("ark")

        assert resolved is not None
        assert resolved["max_tokens"] == 64_000

    def test_providers_dict_by_display_name(self, patched_config):
        patched_config(
            {
                "providers": {
                    "ark_v1": {
                        "name": "Ark v1",
                        "base_url": "https://example.invalid/v1",
                        "api_key": "xx",
                        "max_tokens": 32_000,
                    }
                }
            }
        )

        resolved = rp._get_named_custom_provider("Ark v1")

        assert resolved is not None
        assert resolved["max_tokens"] == 32_000

    def test_omitted_key_means_no_runtime_override(self, patched_config):
        patched_config(
            {
                "custom_providers": [
                    {
                        "name": "ark",
                        "base_url": "https://example.invalid/v1",
                        "api_key": "x",
                    }
                ]
            }
        )

        resolved = rp._get_named_custom_provider("ark")

        assert resolved is not None
        assert "max_tokens" not in resolved

    def test_zero_does_not_pollute_runtime(self, patched_config):
        # Defence-in-depth: even if normalization is bypassed somehow,
        # the helper rejects zero/negative values at lift time too.
        patched_config(
            {
                "custom_providers": [
                    {
                        "name": "ark",
                        "base_url": "https://example.invalid/v1",
                        "api_key": "x",
                        "max_tokens": 0,
                    }
                ]
            }
        )

        resolved = rp._get_named_custom_provider("ark")

        assert resolved is not None
        assert "max_tokens" not in resolved
