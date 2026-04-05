"""Tests for shared /model switch pipeline."""

from unittest.mock import patch

from hermes_cli.model_switch import switch_model, switch_to_custom_provider


def test_switch_model_changes_provider_on_provider_colon_syntax():
    with patch(
        "hermes_cli.runtime_provider.resolve_runtime_provider",
        return_value={"api_key": "k", "base_url": "https://openrouter.ai/api/v1"},
    ), patch(
        "hermes_cli.models.validate_requested_model",
        return_value={"accepted": True, "persist": True, "recognized": True, "message": None},
    ):
        result = switch_model(
            raw_input="openrouter:anthropic/claude-sonnet-4.5",
            current_provider="nous",
            current_base_url="",
            current_api_key="",
        )

    assert result.success is True
    assert result.target_provider == "openrouter"
    assert result.provider_changed is True
    assert result.new_model == "anthropic/claude-sonnet-4.5"
    assert result.persist is True


def test_switch_model_auto_detects_provider_for_plain_model_name():
    with patch(
        "hermes_cli.models.detect_provider_for_model",
        return_value=("anthropic", "claude-opus-4.6"),
    ), patch(
        "hermes_cli.runtime_provider.resolve_runtime_provider",
        return_value={"api_key": "k", "base_url": "https://api.anthropic.com/v1"},
    ), patch(
        "hermes_cli.models.validate_requested_model",
        return_value={"accepted": True, "persist": True, "recognized": True, "message": None},
    ):
        result = switch_model(
            raw_input="claude-opus-4.6",
            current_provider="openrouter",
            current_base_url="",
            current_api_key="",
        )

    assert result.success is True
    assert result.target_provider == "anthropic"
    assert result.new_model == "claude-opus-4.6"


def test_switch_to_custom_provider_auto_detects_model():
    with patch(
        "hermes_cli.runtime_provider.resolve_runtime_provider",
        return_value={"api_key": "", "base_url": "http://localhost:11434/v1"},
    ), patch(
        "hermes_cli.runtime_provider._auto_detect_local_model",
        return_value="qwen2.5-coder",
    ):
        result = switch_to_custom_provider()

    assert result.success is True
    assert result.base_url == "http://localhost:11434/v1"
    assert result.model == "qwen2.5-coder"
