"""Regression tests for /model support of config.yaml custom_providers.

The terminal `hermes model` flow already exposes `custom_providers`, but the
shared slash-command pipeline (`/model` in CLI/gateway/Telegram) historically
only looked at `providers:`.
"""

import hermes_cli.providers as providers_mod
from hermes_cli.model_switch import list_authenticated_providers, switch_model
from hermes_cli.providers import resolve_provider_full


_MOCK_VALIDATION = {
    "accepted": True,
    "persist": True,
    "recognized": True,
    "message": None,
}


def test_list_authenticated_providers_includes_custom_providers(monkeypatch):
    """No-args /model menus should include saved custom_providers entries."""
    monkeypatch.setattr("agent.models_dev.fetch_models_dev", lambda: {})
    monkeypatch.setattr(providers_mod, "HERMES_OVERLAYS", {})
    monkeypatch.setattr("hermes_cli.models.probe_api_models", lambda *a, **k: {"models": []})

    providers = list_authenticated_providers(
        current_provider="openai-codex",
        user_providers={},
        custom_providers=[
            {
                "name": "Local (127.0.0.1:4141)",
                "base_url": "http://127.0.0.1:4141/v1",
                "model": "rotator-openrouter-coding",
            }
        ],
        max_models=50,
    )

    assert any(
        p["slug"] == "custom:local-(127.0.0.1:4141)"
        and p["name"] == "Local (127.0.0.1:4141)"
        and p["models"] == ["rotator-openrouter-coding"]
        and p["api_url"] == "http://127.0.0.1:4141/v1"
        for p in providers
    )


def test_list_authenticated_providers_prefers_custom_models_mapping(monkeypatch):
    """Named custom providers should trust explicit mapped models."""
    monkeypatch.setattr("agent.models_dev.fetch_models_dev", lambda: {})
    monkeypatch.setattr(providers_mod, "HERMES_OVERLAYS", {})

    def _boom(*args, **kwargs):
        raise AssertionError("explicit custom_providers[].models should skip probing")

    monkeypatch.setattr("hermes_cli.models.probe_api_models", _boom)

    providers = list_authenticated_providers(
        current_provider="openai-codex",
        user_providers={},
        custom_providers=[
            {
                "name": "Local Router",
                "base_url": "http://127.0.0.1:4141/v1",
                "api_key": "custom-key",
                "model": "fallback-model",
                "models": {
                    "model-alpha": {},
                    "model-beta": {},
                },
            }
        ],
        max_models=50,
    )

    assert any(
        p["slug"] == "custom:local-router"
        and p["models"] == ["model-alpha", "model-beta"]
        and p["total_models"] == 2
        for p in providers
    )


def test_list_authenticated_providers_uses_probe_when_mapping_missing(monkeypatch):
    """Named custom providers should probe /models when no explicit model map exists."""
    monkeypatch.setattr("agent.models_dev.fetch_models_dev", lambda: {})
    monkeypatch.setattr(providers_mod, "HERMES_OVERLAYS", {})

    probe_calls = []

    def fake_probe(api_key, base_url, timeout=2.0):
        probe_calls.append((api_key, base_url, timeout))
        return {"models": ["model-alpha", "model-beta", "model-alpha"]}

    monkeypatch.setattr("hermes_cli.models.probe_api_models", fake_probe)

    providers = list_authenticated_providers(
        current_provider="openai-codex",
        user_providers={},
        custom_providers=[
            {
                "name": "Local Router",
                "base_url": "http://127.0.0.1:4141/v1",
                "api_key": "custom-key",
                "model": "fallback-model",
            }
        ],
        max_models=50,
    )

    assert probe_calls == [("custom-key", "http://127.0.0.1:4141/v1", 2.0)]
    assert any(
        p["slug"] == "custom:local-router"
        and p["models"] == ["model-alpha", "model-beta"]
        and p["total_models"] == 2
        for p in providers
    )


def test_list_authenticated_providers_skips_probe_when_max_models_zero(monkeypatch):
    """Slug-only listing should not probe named custom endpoints."""
    monkeypatch.setattr("agent.models_dev.fetch_models_dev", lambda: {})
    monkeypatch.setattr(providers_mod, "HERMES_OVERLAYS", {})

    def _boom(*args, **kwargs):
        raise AssertionError("probe_api_models should not be called when max_models=0")

    monkeypatch.setattr("hermes_cli.models.probe_api_models", _boom)

    providers = list_authenticated_providers(
        current_provider="openai-codex",
        user_providers={},
        custom_providers=[
            {
                "name": "Local Router",
                "base_url": "http://127.0.0.1:4141/v1",
                "api_key": "custom-key",
                "model": "fallback-model",
                "models": {
                    "model-alpha": {},
                    "model-beta": {},
                },
            }
        ],
        max_models=0,
    )

    assert any(
        p["slug"] == "custom:local-router"
        and p["models"] == []
        and p["total_models"] == 2
        for p in providers
    )


def test_resolve_provider_full_finds_named_custom_provider():
    """Explicit /model --provider should resolve saved custom_providers entries."""
    resolved = resolve_provider_full(
        "custom:local-(127.0.0.1:4141)",
        user_providers={},
        custom_providers=[
            {
                "name": "Local (127.0.0.1:4141)",
                "base_url": "http://127.0.0.1:4141/v1",
            }
        ],
    )

    assert resolved is not None
    assert resolved.id == "custom:local-(127.0.0.1:4141)"
    assert resolved.name == "Local (127.0.0.1:4141)"
    assert resolved.base_url == "http://127.0.0.1:4141/v1"
    assert resolved.source == "user-config"


def test_switch_model_accepts_explicit_named_custom_provider(monkeypatch):
    """Shared /model switch pipeline should accept --provider for custom_providers."""
    monkeypatch.setattr(
        "hermes_cli.runtime_provider.resolve_runtime_provider",
        lambda requested: {
            "api_key": "no-key-required",
            "base_url": "http://127.0.0.1:4141/v1",
            "api_mode": "chat_completions",
        },
    )
    monkeypatch.setattr("hermes_cli.models.validate_requested_model", lambda *a, **k: _MOCK_VALIDATION)
    monkeypatch.setattr("hermes_cli.model_switch.get_model_info", lambda *a, **k: None)
    monkeypatch.setattr("hermes_cli.model_switch.get_model_capabilities", lambda *a, **k: None)

    result = switch_model(
        raw_input="rotator-openrouter-coding",
        current_provider="openai-codex",
        current_model="gpt-5.4",
        current_base_url="https://chatgpt.com/backend-api/codex",
        current_api_key="",
        explicit_provider="custom:local-(127.0.0.1:4141)",
        user_providers={},
        custom_providers=[
            {
                "name": "Local (127.0.0.1:4141)",
                "base_url": "http://127.0.0.1:4141/v1",
                "model": "rotator-openrouter-coding",
            }
        ],
    )

    assert result.success is True
    assert result.target_provider == "custom:local-(127.0.0.1:4141)"
    assert result.provider_label == "Local (127.0.0.1:4141)"
    assert result.new_model == "rotator-openrouter-coding"
    assert result.base_url == "http://127.0.0.1:4141/v1"
    assert result.api_key == "no-key-required"
