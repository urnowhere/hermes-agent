"""Tests for user-configurable provider HTTP ``default_headers``.

Covers:

* :func:`hermes_cli.config._sanitize_default_headers` — input validation.
* :func:`hermes_cli.config._normalize_custom_provider_entry` — schema flow-through.
* :func:`run_agent.AIAgent._resolve_user_default_headers` — config lookup with
  per-provider overrides taking precedence over top-level ``model.default_headers``.

The relevant feature is documented in ``cli-config.yaml.example`` next to the
``providers:`` section.
"""
from __future__ import annotations

from typing import Any, Dict
from unittest.mock import patch

import pytest

from hermes_cli.config import (
    _normalize_custom_provider_entry,
    _sanitize_default_headers,
)


class TestSanitizeDefaultHeaders:
    def test_returns_none_for_none(self):
        assert _sanitize_default_headers(None, source="model") is None

    def test_returns_none_for_non_mapping(self):
        assert _sanitize_default_headers("oops", source="model") is None
        assert _sanitize_default_headers(["a", "b"], source="model") is None
        assert _sanitize_default_headers(42, source="model") is None

    def test_returns_none_for_empty_dict(self):
        assert _sanitize_default_headers({}, source="model") is None

    def test_passes_through_clean_string_values(self):
        out = _sanitize_default_headers(
            {"User-Agent": "claude-code/0.1.0"}, source="model"
        )
        assert out == {"User-Agent": "claude-code/0.1.0"}

    def test_coerces_scalar_values_to_string(self):
        out = _sanitize_default_headers(
            {"X-Retry": 3, "X-Cache": True, "X-Ratio": 0.5}, source="model"
        )
        assert out == {"X-Retry": "3", "X-Cache": "True", "X-Ratio": "0.5"}

    def test_strips_whitespace_from_keys(self):
        out = _sanitize_default_headers(
            {"  User-Agent  ": "ua"}, source="model"
        )
        assert out == {"User-Agent": "ua"}

    def test_skips_non_string_keys(self):
        out = _sanitize_default_headers(
            {1: "v", "X-Real": "ok"}, source="model"
        )
        assert out == {"X-Real": "ok"}

    def test_skips_blank_keys(self):
        out = _sanitize_default_headers(
            {"   ": "ignored", "X-Real": "ok"}, source="model"
        )
        assert out == {"X-Real": "ok"}

    def test_skips_non_scalar_values(self):
        out = _sanitize_default_headers(
            {"X-List": [1, 2], "X-Dict": {"a": 1}, "X-Real": "ok"},
            source="model",
        )
        assert out == {"X-Real": "ok"}

    def test_skips_none_values(self):
        # Explicit ``None`` is silently dropped — config readers commonly
        # produce ``None`` for missing YAML scalars; surfacing a warning would
        # be noisy without adding any safety value.
        out = _sanitize_default_headers(
            {"X-None": None, "X-Real": "ok"}, source="model"
        )
        assert out == {"X-Real": "ok"}


class TestNormalizeCustomProviderEntryHeaders:
    def test_default_headers_flow_through(self):
        entry = {
            "name": "openclaudecode",
            "base_url": "https://www.openclaudecode.cn/v1",
            "default_headers": {"User-Agent": "claude-code/0.1.0"},
        }
        normalized = _normalize_custom_provider_entry(
            entry, provider_key="openclaudecode"
        )
        assert normalized is not None
        assert normalized["default_headers"] == {
            "User-Agent": "claude-code/0.1.0"
        }

    def test_missing_default_headers_omits_field(self):
        entry = {
            "name": "openclaudecode",
            "base_url": "https://www.openclaudecode.cn/v1",
        }
        normalized = _normalize_custom_provider_entry(
            entry, provider_key="openclaudecode"
        )
        assert normalized is not None
        assert "default_headers" not in normalized

    def test_invalid_default_headers_dropped(self):
        entry = {
            "name": "openclaudecode",
            "base_url": "https://www.openclaudecode.cn/v1",
            "default_headers": "not-a-dict",
        }
        normalized = _normalize_custom_provider_entry(
            entry, provider_key="openclaudecode"
        )
        assert normalized is not None
        # Malformed value drops silently; sanitizer logs the warning.
        assert "default_headers" not in normalized


class _FakeAgent:
    """Minimal stub binding ``_resolve_user_default_headers`` for direct calls.

    The helper only reads ``self.provider``; constructing the real ``AIAgent``
    requires network/auth setup that's irrelevant to header resolution.
    """

    def __init__(self, provider: str = ""):
        self.provider = provider

    @staticmethod
    def _resolve(provider: str) -> Dict[str, str]:
        from run_agent import AIAgent

        agent = _FakeAgent(provider)
        return AIAgent._resolve_user_default_headers(agent)


class TestResolveUserDefaultHeaders:
    def _patch_config(self, cfg: Dict[str, Any]):
        return patch("hermes_cli.config.load_config", return_value=cfg)

    def test_returns_empty_when_no_config(self):
        with self._patch_config({}):
            assert _FakeAgent._resolve("custom") == {}

    def test_reads_top_level_model_block(self):
        cfg = {
            "model": {
                "default_headers": {"User-Agent": "claude-code/0.1.0"},
            }
        }
        with self._patch_config(cfg):
            assert _FakeAgent._resolve("custom") == {
                "User-Agent": "claude-code/0.1.0"
            }

    def test_reads_per_provider_block_when_provider_matches(self):
        cfg = {
            "providers": {
                "openclaudecode": {
                    "base_url": "https://www.openclaudecode.cn/v1",
                    "default_headers": {"User-Agent": "claude-code/0.1.0"},
                }
            }
        }
        with self._patch_config(cfg):
            assert _FakeAgent._resolve("openclaudecode") == {
                "User-Agent": "claude-code/0.1.0"
            }

    def test_per_provider_match_is_case_insensitive(self):
        cfg = {
            "providers": {
                "OpenClaudeCode": {
                    "base_url": "https://www.openclaudecode.cn/v1",
                    "default_headers": {"User-Agent": "ua"},
                }
            }
        }
        with self._patch_config(cfg):
            assert _FakeAgent._resolve("openclaudecode") == {"User-Agent": "ua"}

    def test_per_provider_overrides_top_level_on_conflict(self):
        cfg = {
            "model": {
                "default_headers": {
                    "User-Agent": "fallback-ua",
                    "X-Common": "from-model",
                },
            },
            "providers": {
                "custom": {
                    "base_url": "https://example.invalid/v1",
                    "default_headers": {"User-Agent": "winner-ua"},
                },
            },
        }
        with self._patch_config(cfg):
            out = _FakeAgent._resolve("custom")
        assert out == {
            "User-Agent": "winner-ua",  # per-provider wins
            "X-Common": "from-model",   # non-conflicting top-level survives
        }

    def test_provider_block_ignored_when_provider_unset(self):
        cfg = {
            "providers": {
                "custom": {
                    "base_url": "https://example.invalid/v1",
                    "default_headers": {"User-Agent": "should-not-appear"},
                }
            }
        }
        with self._patch_config(cfg):
            assert _FakeAgent._resolve("") == {}

    def test_malformed_provider_block_does_not_crash(self):
        cfg = {
            "model": {
                "default_headers": {"User-Agent": "fallback"},
            },
            "providers": "not-a-dict",
        }
        with self._patch_config(cfg):
            assert _FakeAgent._resolve("custom") == {"User-Agent": "fallback"}

    def test_load_config_failure_is_swallowed(self):
        with patch(
            "hermes_cli.config.load_config", side_effect=RuntimeError("disk on fire")
        ):
            assert _FakeAgent._resolve("custom") == {}


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-v"]))
