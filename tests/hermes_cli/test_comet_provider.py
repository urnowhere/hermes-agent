"""Tests for CometAPI provider routing."""

from collections import Counter

import pytest

from hermes_cli.auth import PROVIDER_REGISTRY, resolve_provider
from hermes_cli.model_normalize import _AGGREGATOR_PROVIDERS, normalize_model_for_provider
from hermes_cli.models import CANONICAL_PROVIDERS, _PROVIDER_MODELS
from hermes_cli.model_switch import switch_model


class TestCometProviderRegistry:
    def test_registered(self):
        assert "comet" in PROVIDER_REGISTRY

    def test_api_key_config(self):
        pconfig = PROVIDER_REGISTRY["comet"]
        assert pconfig.name == "CometAPI"
        assert pconfig.auth_type == "api_key"
        assert pconfig.inference_base_url == "https://api.cometapi.com/v1"
        assert pconfig.api_key_env_vars == ("COMET_API_KEY",)
        assert pconfig.base_url_env_var == "COMET_BASE_URL"


class TestCometProviderAliases:
    @pytest.mark.parametrize("alias", ["comet", "cometapi", "comet-api"])
    def test_auth_aliases_resolve(self, alias):
        assert resolve_provider(alias) == "comet"

    @pytest.mark.parametrize("alias", ["comet", "cometapi", "comet-api"])
    def test_models_aliases_resolve(self, alias):
        from hermes_cli.models import normalize_provider

        assert normalize_provider(alias) == "comet"

    @pytest.mark.parametrize("alias", ["comet", "cometapi", "comet-api"])
    def test_providers_aliases_resolve(self, alias):
        from hermes_cli.providers import normalize_provider

        assert normalize_provider(alias) == "comet"


class TestCometModelNormalization:
    def test_comet_is_not_openrouter_style_aggregator(self):
        assert "comet" not in _AGGREGATOR_PROVIDERS

    @pytest.mark.parametrize(
        "model_id",
        [
            "gpt-5.5-xhigh-all",
            "deepseek-v4-pro",
            "claude-opus-4-7",
            "gemini-3-flash",
            "qwen3-coder-480b-a35b-instruct",
        ],
    )
    def test_bare_model_ids_pass_through(self, model_id):
        assert normalize_model_for_provider(model_id, "comet") == model_id

    def test_vendor_prefixed_model_ids_pass_through(self):
        assert (
            normalize_model_for_provider("openai/gpt-5.5-xhigh-all", "comet")
            == "openai/gpt-5.5-xhigh-all"
        )


class TestCometModelCatalog:
    def test_static_model_list_uses_bare_ids(self):
        models = _PROVIDER_MODELS["comet"]
        assert "gpt-5.5-xhigh-all" in models
        assert "deepseek-v4-pro" in models
        assert "openai/gpt-5.5-xhigh-all" not in models
        assert "deepseek/deepseek-v4-pro" not in models


class TestCometProvidersModule:
    def test_overlay_exists(self):
        from hermes_cli.providers import HERMES_OVERLAYS

        overlay = HERMES_OVERLAYS["comet"]
        assert overlay.transport == "openai_chat"
        assert overlay.base_url_override == "https://api.cometapi.com/v1"
        assert overlay.base_url_env_var == "COMET_BASE_URL"
        assert overlay.extra_env_vars == ("COMET_API_KEY",)

    def test_is_aggregator_for_catalog_search(self):
        from hermes_cli.providers import is_aggregator

        assert is_aggregator("comet")


class TestCometModelSwitch:
    def test_switch_model_preserves_bare_comet_ids(self, monkeypatch):
        validation = {"accepted": True, "persist": True, "recognized": True, "message": None}
        monkeypatch.setattr("hermes_cli.model_switch.resolve_alias", lambda *a, **k: None)
        monkeypatch.setattr("hermes_cli.model_switch.list_provider_models", lambda provider: [])
        monkeypatch.setattr("hermes_cli.models.detect_provider_for_model", lambda *a, **k: None)
        monkeypatch.setattr("hermes_cli.models.validate_requested_model", lambda *a, **k: validation)
        monkeypatch.setattr("hermes_cli.model_switch.get_model_info", lambda *a, **k: None)
        monkeypatch.setattr("hermes_cli.model_switch.get_model_capabilities", lambda *a, **k: None)
        monkeypatch.setattr(
            "hermes_cli.runtime_provider.resolve_runtime_provider",
            lambda *a, **k: {"api_key": "test", "base_url": "https://api.cometapi.com/v1", "api_mode": "chat_completions"},
        )

        result = switch_model("gpt-5.5-xhigh-all", "comet", "deepseek-v4-pro")

        assert result.success, result.error_message
        assert result.new_model == "gpt-5.5-xhigh-all"
        assert result.target_provider == "comet"


class TestCanonicalProviders:
    def test_provider_slugs_are_unique(self):
        counts = Counter(p.slug for p in CANONICAL_PROVIDERS)
        duplicates = sorted(slug for slug, count in counts.items() if count > 1)
        assert duplicates == []
