"""Tests for set_config_value — verifying secrets route to .env and config to config.yaml."""

import argparse
import os
from pathlib import Path
from unittest.mock import patch, call

import pytest
import yaml

from hermes_cli.config import set_config_value, config_command, _set_nested


@pytest.fixture(autouse=True)
def _isolated_hermes_home(tmp_path):
    """Point HERMES_HOME at a temp dir so tests never touch real config."""
    env_file = tmp_path / ".env"
    env_file.touch()
    with patch.dict(os.environ, {"HERMES_HOME": str(tmp_path)}):
        yield tmp_path


def _read_env(tmp_path):
    return (tmp_path / ".env").read_text()


def _read_config(tmp_path):
    config_path = tmp_path / "config.yaml"
    return config_path.read_text() if config_path.exists() else ""


# ---------------------------------------------------------------------------
# Explicit allowlist keys → .env
# ---------------------------------------------------------------------------

class TestExplicitAllowlist:
    """Keys in the hardcoded allowlist should always go to .env."""

    @pytest.mark.parametrize("key", [
        "OPENROUTER_API_KEY",
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "WANDB_API_KEY",
        "TINKER_API_KEY",
        "HONCHO_API_KEY",
        "FIRECRAWL_API_KEY",
        "BROWSERBASE_API_KEY",
        "FAL_KEY",
        "SUDO_PASSWORD",
        "GITHUB_TOKEN",
        "TELEGRAM_BOT_TOKEN",
        "DISCORD_BOT_TOKEN",
        "SLACK_BOT_TOKEN",
        "SLACK_APP_TOKEN",
    ])
    def test_explicit_key_routes_to_env(self, key, _isolated_hermes_home):
        set_config_value(key, "test-value-123")
        env_content = _read_env(_isolated_hermes_home)
        assert f"{key}=test-value-123" in env_content
        # Must NOT appear in config.yaml
        assert key not in _read_config(_isolated_hermes_home)


# ---------------------------------------------------------------------------
# Catch-all patterns → .env
# ---------------------------------------------------------------------------

class TestCatchAllPatterns:
    """Any key ending in _API_KEY or _TOKEN should route to .env."""

    @pytest.mark.parametrize("key", [
        "DAYTONA_API_KEY",
        "ELEVENLABS_API_KEY",
        "SOME_FUTURE_SERVICE_API_KEY",
        "MY_CUSTOM_TOKEN",
        "WHATSAPP_BOT_TOKEN",
    ])
    def test_api_key_suffix_routes_to_env(self, key, _isolated_hermes_home):
        set_config_value(key, "secret-456")
        env_content = _read_env(_isolated_hermes_home)
        assert f"{key}=secret-456" in env_content
        assert key not in _read_config(_isolated_hermes_home)

    def test_case_insensitive(self, _isolated_hermes_home):
        """Keys should be uppercased regardless of input casing."""
        set_config_value("openai_api_key", "sk-test")
        env_content = _read_env(_isolated_hermes_home)
        assert "OPENAI_API_KEY=sk-test" in env_content

    def test_terminal_ssh_prefix_routes_to_env(self, _isolated_hermes_home):
        set_config_value("TERMINAL_SSH_PORT", "2222")
        env_content = _read_env(_isolated_hermes_home)
        assert "TERMINAL_SSH_PORT=2222" in env_content


# ---------------------------------------------------------------------------
# Non-secret keys → config.yaml
# ---------------------------------------------------------------------------

class TestConfigYamlRouting:
    """Regular config keys should go to config.yaml, NOT .env."""

    def test_simple_key(self, _isolated_hermes_home):
        set_config_value("model", "gpt-4o")
        config = _read_config(_isolated_hermes_home)
        assert "gpt-4o" in config
        assert "model" not in _read_env(_isolated_hermes_home)

    def test_nested_key(self, _isolated_hermes_home):
        set_config_value("terminal.backend", "docker")
        config = _read_config(_isolated_hermes_home)
        assert "docker" in config
        assert "terminal" not in _read_env(_isolated_hermes_home)

    def test_terminal_image_goes_to_config(self, _isolated_hermes_home):
        """TERMINAL_DOCKER_IMAGE doesn't match _API_KEY or _TOKEN, so config.yaml."""
        set_config_value("terminal.docker_image", "python:3.12")
        config = _read_config(_isolated_hermes_home)
        assert "python:3.12" in config

    def test_terminal_docker_cwd_mount_flag_goes_to_config_and_env(self, _isolated_hermes_home):
        set_config_value("terminal.docker_mount_cwd_to_workspace", "true")
        config = _read_config(_isolated_hermes_home)
        env_content = _read_env(_isolated_hermes_home)
        assert "docker_mount_cwd_to_workspace: 'true'" in config or "docker_mount_cwd_to_workspace: true" in config
        assert (
            "TERMINAL_DOCKER_MOUNT_CWD_TO_WORKSPACE=true" in env_content
            or "TERMINAL_DOCKER_MOUNT_CWD_TO_WORKSPACE=True" in env_content
        )

    def test_terminal_vercel_runtime_goes_to_config_and_env(self, _isolated_hermes_home):
        set_config_value("terminal.vercel_runtime", "python3.13")
        config = _read_config(_isolated_hermes_home)
        env_content = _read_env(_isolated_hermes_home)
        assert "vercel_runtime: python3.13" in config
        assert "TERMINAL_VERCEL_RUNTIME=python3.13" in env_content


# ---------------------------------------------------------------------------
# Empty / falsy values — regression tests for #4277
# ---------------------------------------------------------------------------

class TestFalsyValues:
    """config set should accept empty strings and falsy values like '0'."""

    def test_empty_string_routes_to_env(self, _isolated_hermes_home):
        """Blanking an API key should write an empty value to .env."""
        set_config_value("OPENROUTER_API_KEY", "")
        env_content = _read_env(_isolated_hermes_home)
        assert "OPENROUTER_API_KEY=" in env_content

    def test_empty_string_routes_to_config(self, _isolated_hermes_home):
        """Blanking a config key should write an empty string to config.yaml."""
        set_config_value("model", "")
        config = _read_config(_isolated_hermes_home)
        assert "model: ''" in config or "model: \"\"" in config

    def test_zero_routes_to_config(self, _isolated_hermes_home):
        """Setting a config key to '0' should write 0 to config.yaml."""
        set_config_value("verbose", "0")
        config = _read_config(_isolated_hermes_home)
        assert "verbose: 0" in config

    def test_config_command_rejects_missing_value(self):
        """config set with no value arg (None) should still exit."""
        args = argparse.Namespace(config_command="set", key="model", value=None)
        with pytest.raises(SystemExit):
            config_command(args)

    def test_config_command_accepts_empty_string(self, _isolated_hermes_home):
        """config set KEY '' should not exit — it should set the value."""
        args = argparse.Namespace(config_command="set", key="model", value="")
        config_command(args)
        config = _read_config(_isolated_hermes_home)
        assert "model" in config


# ---------------------------------------------------------------------------
# List-typed config fields — regression for #17876
#
# `hermes config set custom_providers.0.api_key X` used to overwrite the entire
# `custom_providers` list with `{"0": {"api_key": "X"}}`, destroying every
# other provider entry and every other field on the targeted provider. The fix
# teaches `_set_nested` to navigate into existing lists by integer index.
# ---------------------------------------------------------------------------

class TestListNavigation:
    def _seed_yaml(self, tmp_path: Path, payload: dict) -> Path:
        config_path = tmp_path / "config.yaml"
        config_path.write_text(yaml.safe_dump(payload, sort_keys=False))
        return config_path

    def test_set_nested_helper_indexes_into_existing_list(self):
        config = {
            "custom_providers": [
                {"name": "a", "api_key": "old_a", "base_url": "https://a"},
                {"name": "b", "api_key": "old_b", "base_url": "https://b"},
            ]
        }
        _set_nested(config, "custom_providers.0.api_key", "new_a")

        assert isinstance(config["custom_providers"], list)
        assert len(config["custom_providers"]) == 2
        assert config["custom_providers"][0] == {
            "name": "a",
            "api_key": "new_a",
            "base_url": "https://a",
        }
        assert config["custom_providers"][1]["api_key"] == "old_b"

    def test_set_nested_helper_replaces_whole_list_element(self):
        config = {"custom_providers": [{"name": "a"}, {"name": "b"}]}
        _set_nested(config, "custom_providers.1", {"name": "c", "api_key": "k"})

        assert config["custom_providers"][0] == {"name": "a"}
        assert config["custom_providers"][1] == {"name": "c", "api_key": "k"}

    def test_set_nested_helper_preserves_dict_only_paths(self):
        config = {"tts": {"provider": "old"}}
        _set_nested(config, "tts.provider", "new")
        assert config == {"tts": {"provider": "new"}}

    def test_set_config_value_updates_list_element_field(self, _isolated_hermes_home):
        self._seed_yaml(_isolated_hermes_home, {
            "custom_providers": [
                {"name": "a", "api_key": "old_a", "base_url": "https://a"},
                {"name": "b", "api_key": "old_b", "base_url": "https://b"},
            ],
        })

        set_config_value("custom_providers.0.api_key", "new_a")

        result = yaml.safe_load((_isolated_hermes_home / "config.yaml").read_text())
        assert isinstance(result["custom_providers"], list), \
            "list-typed field must remain a list, not be flattened to a dict"
        assert len(result["custom_providers"]) == 2, \
            "second provider entry must survive the update"
        assert result["custom_providers"][0] == {
            "name": "a",
            "api_key": "new_a",
            "base_url": "https://a",
        }
        assert result["custom_providers"][1] == {
            "name": "b",
            "api_key": "old_b",
            "base_url": "https://b",
        }

    def test_set_config_value_does_not_collapse_list_to_dict(self, _isolated_hermes_home):
        """Regression guard: pre-fix behavior produced ``{"0": {...}}``."""
        self._seed_yaml(_isolated_hermes_home, {
            "custom_providers": [{"name": "a", "api_key": "old"}],
        })

        set_config_value("custom_providers.0.api_key", "new")

        raw = (_isolated_hermes_home / "config.yaml").read_text()
        assert "custom_providers:\n- " in raw or "custom_providers:\n  - " in raw, \
            f"YAML must keep list syntax, got:\n{raw}"

    def test_set_nested_rejects_non_integer_list_index(self):
        config = {"custom_providers": [{"name": "a"}]}
        with pytest.raises(ValueError, match="invalid list index 'foo'"):
            _set_nested(config, "custom_providers.foo.api_key", "x")

    def test_set_nested_rejects_setting_under_scalar(self):
        config = {"custom_providers": ["just_a_string"]}
        with pytest.raises(ValueError, match="cannot set 'api_key'"):
            _set_nested(config, "custom_providers.0.api_key", "x")

    def test_set_nested_rejects_deep_descent_into_scalar(self):
        config = {"custom_providers": ["just_a_string"]}
        with pytest.raises(ValueError, match="cannot descend into 'foo'"):
            _set_nested(config, "custom_providers.0.foo.bar", "x")

    def test_set_nested_out_of_range_index_raises_index_error(self):
        config = {"custom_providers": [{"name": "a"}]}
        with pytest.raises(IndexError):
            _set_nested(config, "custom_providers.5.api_key", "x")

    def test_set_config_value_invalid_index_exits_with_clean_message(
        self, _isolated_hermes_home, capsys
    ):
        self._seed_yaml(_isolated_hermes_home, {
            "custom_providers": [{"name": "a", "api_key": "old"}],
        })

        with pytest.raises(SystemExit):
            set_config_value("custom_providers.foo.api_key", "x")

        captured = capsys.readouterr()
        assert "Error setting custom_providers.foo.api_key" in captured.out
        assert "list components must be integers" in captured.out
        # Original config must remain intact on error.
        result = yaml.safe_load((_isolated_hermes_home / "config.yaml").read_text())
        assert result["custom_providers"][0]["api_key"] == "old"
