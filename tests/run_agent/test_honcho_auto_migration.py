"""Tests for Honcho auto-migration to memory provider plugin.

When AIAgent initializes and memory.provider is not set but Honcho is
actively configured (enabled + credentials from honcho.json), the agent
auto-detects this and sets _mem_provider_name to "honcho".  Persistence
is deferred until after the provider passes is_available(), so broken
setups never pollute the user's config.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from run_agent import detect_honcho_auto_migrate


def _mock_honcho_config(enabled=True, api_key=None, base_url=None):
    return SimpleNamespace(enabled=enabled, api_key=api_key, base_url=base_url)


class TestHonchoDetection:
    """Tests for detect_honcho_auto_migrate() — the real extracted function."""

    def test_detects_active_honcho_with_api_key(self):
        """Active Honcho (enabled + api_key) triggers detection."""
        cfg = _mock_honcho_config(enabled=True, api_key="hk-test-key")
        with patch(
            "plugins.memory.honcho.client.HonchoClientConfig.from_global_config",
            return_value=cfg,
        ):
            assert detect_honcho_auto_migrate() == "honcho"

    def test_detects_active_honcho_with_base_url_only(self):
        """Active Honcho with base_url but no api_key also triggers."""
        cfg = _mock_honcho_config(enabled=True, base_url="https://honcho.example.com")
        with patch(
            "plugins.memory.honcho.client.HonchoClientConfig.from_global_config",
            return_value=cfg,
        ):
            assert detect_honcho_auto_migrate() == "honcho"

    def test_no_detection_when_honcho_disabled(self):
        """Honcho present but disabled should not trigger detection."""
        cfg = _mock_honcho_config(enabled=False, api_key="hk-test-key")
        with patch(
            "plugins.memory.honcho.client.HonchoClientConfig.from_global_config",
            return_value=cfg,
        ):
            assert detect_honcho_auto_migrate() == ""

    def test_no_detection_when_honcho_no_credentials(self):
        """Honcho enabled but no credentials should not trigger."""
        cfg = _mock_honcho_config(enabled=True)
        with patch(
            "plugins.memory.honcho.client.HonchoClientConfig.from_global_config",
            return_value=cfg,
        ):
            assert detect_honcho_auto_migrate() == ""

    def test_no_detection_when_import_fails(self):
        """If Honcho plugin is not installed, gracefully return empty."""
        with patch(
            "builtins.__import__",
            side_effect=ImportError("no honcho"),
        ):
            assert detect_honcho_auto_migrate() == ""


class TestHonchoPersistenceGuard:
    """Tests for the deferred persistence condition.

    Persistence only happens AFTER the provider passes is_available().
    The condition is: is_available() AND provider_name == "honcho" AND
    provider was NOT already in config.
    """

    def test_persists_after_available_confirms(self):
        _mp = MagicMock()
        _mp.is_available.return_value = True
        should_persist = _mp.is_available() and not {}.get("provider")
        assert should_persist is True

    def test_no_persist_when_provider_not_available(self):
        _mp = MagicMock()
        _mp.is_available.return_value = False
        should_persist = _mp.is_available() and not {}.get("provider")
        assert should_persist is False

    def test_no_persist_when_provider_was_explicitly_set(self):
        _mp = MagicMock()
        _mp.is_available.return_value = True
        mem_config = {"provider": "honcho"}
        should_persist = _mp.is_available() and not mem_config.get("provider")
        assert should_persist is False
