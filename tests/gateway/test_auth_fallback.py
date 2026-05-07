"""Test that AuthError triggers fallback provider resolution (#7230)."""

import os
from unittest.mock import patch, MagicMock

import pytest


class TestResolveRuntimeAgentKwargsAuthFallback:
    """_resolve_runtime_agent_kwargs should try fallback on AuthError."""

    def test_auth_error_tries_fallback(self, tmp_path, monkeypatch):
        """When primary provider raises AuthError, fallback is attempted."""
        from hermes_cli.auth import AuthError

        # Create a config with fallback
        config_path = tmp_path / "config.yaml"
        config_path.write_text(
            "model:\n  provider: openai-codex\n"
            "fallback_model:\n  provider: openrouter\n"
            "  model: meta-llama/llama-4-maverick\n"
        )

        monkeypatch.setattr("gateway.run._hermes_home", tmp_path)

        call_count = {"n": 0}

        def _mock_resolve(**kwargs):
            call_count["n"] += 1
            requested = kwargs.get("requested", "")
            if requested and "codex" in str(requested).lower():
                raise AuthError("Codex token refresh failed with status 401")
            return {
                "api_key": "fallback-key",
                "base_url": "https://openrouter.ai/api/v1",
                "provider": "openrouter",
                "api_mode": "openai_chat",
                "command": None,
                "args": None,
                "credential_pool": None,
            }

        monkeypatch.setenv("HERMES_INFERENCE_PROVIDER", "openai-codex")

        with patch(
            "hermes_cli.runtime_provider.resolve_runtime_provider",
            side_effect=_mock_resolve,
        ):
            from gateway.run import _resolve_runtime_agent_kwargs
            result = _resolve_runtime_agent_kwargs()

        assert result["provider"] == "openrouter"
        assert result["api_key"] == "fallback-key"
        # Should have been called at least twice (primary + fallback)
        assert call_count["n"] >= 2

    def test_auth_error_no_fallback_raises(self, tmp_path, monkeypatch):
        """When primary fails and no fallback configured, RuntimeError is raised."""
        from hermes_cli.auth import AuthError

        config_path = tmp_path / "config.yaml"
        config_path.write_text("model:\n  provider: openai-codex\n")

        monkeypatch.setattr("gateway.run._hermes_home", tmp_path)
        monkeypatch.setenv("HERMES_INFERENCE_PROVIDER", "openai-codex")

        with patch(
            "hermes_cli.runtime_provider.resolve_runtime_provider",
            side_effect=AuthError("token expired"),
        ):
            from gateway.run import _resolve_runtime_agent_kwargs
            with pytest.raises(RuntimeError):
                _resolve_runtime_agent_kwargs()

    def test_auth_error_expands_fallback_env_reference(self, tmp_path, monkeypatch):
        """Gateway auth fallback should resolve ${VAR} references before client creation."""
        from hermes_cli.auth import AuthError

        config_path = tmp_path / "config.yaml"
        config_path.write_text(
            "model:\n  provider: openai-codex\n"
            "fallback_model:\n"
            "  provider: custom\n"
            "  model: deepseek-chat\n"
            "  base_url: https://api.deepseek.com/\n"
            "  api_key: ${FALLBACK_DEEPSEEK_KEY}\n",
            encoding="utf-8",
        )

        monkeypatch.setattr("gateway.run._hermes_home", tmp_path)
        monkeypatch.setenv("HERMES_INFERENCE_PROVIDER", "openai-codex")
        monkeypatch.setenv("FALLBACK_DEEPSEEK_KEY", "resolved-fallback-key")

        seen_fallback_keys = []

        def _mock_resolve(**kwargs):
            requested = kwargs.get("requested", "")
            if requested and "codex" in str(requested).lower():
                raise AuthError("Codex token refresh failed with status 401")
            seen_fallback_keys.append(kwargs.get("explicit_api_key"))
            return {
                "api_key": kwargs.get("explicit_api_key"),
                "base_url": kwargs.get("explicit_base_url"),
                "provider": requested,
                "api_mode": "openai_chat",
                "command": None,
                "args": None,
                "credential_pool": None,
            }

        with patch(
            "hermes_cli.runtime_provider.resolve_runtime_provider",
            side_effect=_mock_resolve,
        ):
            from gateway.run import _resolve_runtime_agent_kwargs
            result = _resolve_runtime_agent_kwargs()

        assert seen_fallback_keys == ["resolved-fallback-key"]
        assert result["api_key"] == "resolved-fallback-key"


def test_load_fallback_model_expands_env_references(tmp_path, monkeypatch):
    """Gateway's fallback model loader should match CLI config env expansion."""
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "fallback_model:\n"
        "  provider: custom\n"
        "  model: deepseek-chat\n"
        "  api_key: ${FALLBACK_MODEL_KEY}\n",
        encoding="utf-8",
    )

    monkeypatch.setattr("gateway.run._hermes_home", tmp_path)
    monkeypatch.setenv("FALLBACK_MODEL_KEY", "resolved-model-key")

    from gateway.run import GatewayRunner

    assert GatewayRunner._load_fallback_model() == {
        "provider": "custom",
        "model": "deepseek-chat",
        "api_key": "resolved-model-key",
    }
