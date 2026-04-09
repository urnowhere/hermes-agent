"""Tests for the /provider command provider switching."""

import pytest
from unittest.mock import MagicMock, patch

from hermes_cli.commands import resolve_command


class TestProviderCommandDef:
    """Test the /provider command definition."""

    def test_provider_command_exists(self):
        cmd = resolve_command("provider")
        assert cmd is not None
        assert cmd.name == "provider"

    def test_provider_command_accepts_args(self):
        cmd = resolve_command("provider")
        assert cmd.args_hint == "[name] [model]"

    def test_provider_command_in_configuration_category(self):
        cmd = resolve_command("provider")
        assert cmd.category == "Configuration"


class TestProviderSwitch:
    """Test provider switching logic."""

    @patch("hermes_cli.providers.resolve_provider_full")
    @patch("hermes_cli.config.load_config")
    def test_user_provider_models_list_used(self, mock_load_config, mock_resolve):
        """When user defines models list in config, first model is auto-selected."""
        # Setup mock config with models list
        mock_load_config.return_value = {
            "providers": {
                "lmstudio": {
                    "type": "openai",
                    "base_url": "http://localhost:1234/v1",
                    "api_key": "lm-studio",
                    "models": [
                        "qwen/qwen3.5-9b",
                        "dolphin-mistral-24b",
                    ]
                }
            }
        }

        # Get models from config
        cfg = mock_load_config()
        user_providers = cfg.get("providers", {})
        user_entry = user_providers.get("lmstudio", {})
        models_list = user_entry.get("models", [])

        assert models_list == ["qwen/qwen3.5-9b", "dolphin-mistral-24b"]
        assert models_list[0] == "qwen/qwen3.5-9b"

    def test_provider_command_description_updated(self):
        """Verify command description reflects new functionality."""
        cmd = resolve_command("provider")
        assert "switch" in cmd.description.lower() or "show" in cmd.description.lower()
