"""Tests for Honcho client configuration."""

import json
import os
import tempfile
from pathlib import Path

import pytest

from plugins.memory.honcho.client import HonchoClientConfig


class TestHonchoClientConfigAutoEnable:
    """Test auto-enable behavior when API key is present."""

    def test_auto_enables_when_api_key_present_no_explicit_enabled(self, tmp_path):
        """When API key exists and enabled is not set, should auto-enable."""
        config_path = tmp_path / "config.json"
        config_path.write_text(json.dumps({
            "apiKey": "test-api-key-12345",
            # Note: no "enabled" field
        }))

        cfg = HonchoClientConfig.from_global_config(config_path=config_path)

        assert cfg.api_key == "test-api-key-12345"
        assert cfg.enabled is True  # Auto-enabled because API key exists

    def test_respects_explicit_enabled_false(self, tmp_path):
        """When enabled is explicitly False, should stay disabled even with API key."""
        config_path = tmp_path / "config.json"
        config_path.write_text(json.dumps({
            "apiKey": "test-api-key-12345",
            "enabled": False,  # Explicitly disabled
        }))

        cfg = HonchoClientConfig.from_global_config(config_path=config_path)

        assert cfg.api_key == "test-api-key-12345"
        assert cfg.enabled is False  # Respects explicit setting

    def test_respects_explicit_enabled_true(self, tmp_path):
        """When enabled is explicitly True, should be enabled."""
        config_path = tmp_path / "config.json"
        config_path.write_text(json.dumps({
            "apiKey": "test-api-key-12345",
            "enabled": True,
        }))

        cfg = HonchoClientConfig.from_global_config(config_path=config_path)

        assert cfg.api_key == "test-api-key-12345"
        assert cfg.enabled is True

    def test_disabled_when_no_api_key_and_no_explicit_enabled(self, tmp_path):
        """When no API key and enabled not set, should be disabled."""
        config_path = tmp_path / "config.json"
        config_path.write_text(json.dumps({
            "workspace": "test",
            # No apiKey, no enabled
        }))

        # Clear env var if set
        env_key = os.environ.pop("HONCHO_API_KEY", None)
        try:
            cfg = HonchoClientConfig.from_global_config(config_path=config_path)
            assert cfg.api_key is None
            assert cfg.enabled is False  # No API key = not enabled
        finally:
            if env_key:
                os.environ["HONCHO_API_KEY"] = env_key

    def test_auto_enables_with_env_var_api_key(self, tmp_path, monkeypatch):
        """When API key is in env var (not config), should auto-enable."""
        config_path = tmp_path / "config.json"
        config_path.write_text(json.dumps({
            "workspace": "test",
            # No apiKey in config
        }))

        monkeypatch.setenv("HONCHO_API_KEY", "env-api-key-67890")

        cfg = HonchoClientConfig.from_global_config(config_path=config_path)

        assert cfg.api_key == "env-api-key-67890"
        assert cfg.enabled is True  # Auto-enabled from env var API key

    def test_from_env_always_enabled(self, monkeypatch):
        """from_env() should always set enabled=True."""
        monkeypatch.setenv("HONCHO_API_KEY", "env-test-key")

        cfg = HonchoClientConfig.from_env()

        assert cfg.api_key == "env-test-key"
        assert cfg.enabled is True

    def test_falls_back_to_env_when_no_config_file(self, tmp_path, monkeypatch):
        """When config file doesn't exist, should fall back to from_env()."""
        nonexistent = tmp_path / "nonexistent.json"
        monkeypatch.setenv("HONCHO_API_KEY", "fallback-key")

        cfg = HonchoClientConfig.from_global_config(config_path=nonexistent)

        assert cfg.api_key == "fallback-key"
        assert cfg.enabled is True  # from_env() sets enabled=True


class TestHonchoClientConfigContextInjection:
    """Test section-level Honcho base context injection config."""

    def test_defaults_all_sections_enabled(self):
        cfg = HonchoClientConfig()

        assert cfg.context_injection == {
            "sessionSummary": True,
            "userRepresentation": True,
            "userPeerCard": True,
            "aiRepresentation": True,
            "aiPeerCard": True,
        }

    def test_root_level_partial_override(self, tmp_path):
        config_path = tmp_path / "config.json"
        config_path.write_text(json.dumps({
            "contextInjection": {
                "sessionSummary": False,
                "aiPeerCard": False,
            },
        }))

        cfg = HonchoClientConfig.from_global_config(config_path=config_path)

        assert cfg.context_injection["sessionSummary"] is False
        assert cfg.context_injection["aiPeerCard"] is False
        assert cfg.context_injection["userRepresentation"] is True
        assert cfg.context_injection["userPeerCard"] is True
        assert cfg.context_injection["aiRepresentation"] is True

    def test_host_level_per_key_override_over_root(self, tmp_path):
        config_path = tmp_path / "config.json"
        config_path.write_text(json.dumps({
            "contextInjection": {
                "sessionSummary": False,
                "userRepresentation": False,
                "aiPeerCard": False,
            },
            "hosts": {
                "hermes": {
                    "contextInjection": {
                        "sessionSummary": True,
                    },
                },
            },
        }))

        cfg = HonchoClientConfig.from_global_config(config_path=config_path)

        assert cfg.context_injection["sessionSummary"] is True
        assert cfg.context_injection["userRepresentation"] is False
        assert cfg.context_injection["aiPeerCard"] is False

    def test_host_false_overrides_root_true(self, tmp_path):
        config_path = tmp_path / "config.json"
        config_path.write_text(json.dumps({
            "contextInjection": {
                "userPeerCard": True,
            },
            "hosts": {
                "hermes": {
                    "contextInjection": {
                        "userPeerCard": False,
                    },
                },
            },
        }))

        cfg = HonchoClientConfig.from_global_config(config_path=config_path)

        assert cfg.context_injection["userPeerCard"] is False

    def test_unknown_keys_ignored(self, tmp_path):
        config_path = tmp_path / "config.json"
        config_path.write_text(json.dumps({
            "contextInjection": {
                "sessionSummary": False,
                "futureSection": False,
            },
            "hosts": {
                "hermes": {
                    "contextInjection": {
                        "anotherUnknown": False,
                    },
                },
            },
        }))

        cfg = HonchoClientConfig.from_global_config(config_path=config_path)

        assert "futureSection" not in cfg.context_injection
        assert "anotherUnknown" not in cfg.context_injection
        assert cfg.context_injection["sessionSummary"] is False

    def test_non_dict_root_and_host_ignored(self, tmp_path):
        config_path = tmp_path / "config.json"
        config_path.write_text(json.dumps({
            "contextInjection": False,
            "hosts": {
                "hermes": {
                    "contextInjection": ["sessionSummary"],
                },
            },
        }))

        cfg = HonchoClientConfig.from_global_config(config_path=config_path)

        assert all(cfg.context_injection.values())
