"""Tests for the LLM Gateway (llmgateway.io) provider — OpenAI-compatible aggregator."""

import pytest

from hermes_cli.auth import (
    PROVIDER_REGISTRY,
    get_api_key_provider_status,
    resolve_api_key_provider_credentials,
    resolve_provider,
)


_OTHER_PROVIDER_KEYS = (
    "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "DEEPSEEK_API_KEY",
    "GOOGLE_API_KEY", "GEMINI_API_KEY", "DASHSCOPE_API_KEY",
    "XAI_API_KEY", "KIMI_API_KEY", "KIMI_CN_API_KEY",
    "MINIMAX_API_KEY", "MINIMAX_CN_API_KEY", "AI_GATEWAY_API_KEY",
    "KILOCODE_API_KEY", "HF_TOKEN", "GLM_API_KEY", "ZAI_API_KEY",
    "XIAOMI_API_KEY", "COPILOT_GITHUB_TOKEN", "GH_TOKEN", "GITHUB_TOKEN",
    "ARCEEAI_API_KEY", "NVIDIA_API_KEY", "OPENROUTER_API_KEY",
)


class TestLLMGatewayProviderRegistry:
    def test_registered(self):
        assert "llmgateway" in PROVIDER_REGISTRY

    def test_name(self):
        assert PROVIDER_REGISTRY["llmgateway"].name == "LLM Gateway"

    def test_auth_type(self):
        assert PROVIDER_REGISTRY["llmgateway"].auth_type == "api_key"

    def test_inference_base_url(self):
        assert (
            PROVIDER_REGISTRY["llmgateway"].inference_base_url
            == "https://api.llmgateway.io/v1"
        )

    def test_api_key_env_vars(self):
        assert PROVIDER_REGISTRY["llmgateway"].api_key_env_vars == (
            "LLM_GATEWAY_API_KEY",
            "LLMGATEWAY_API_KEY",
        )

    def test_base_url_env_var(self):
        assert PROVIDER_REGISTRY["llmgateway"].base_url_env_var == "LLM_GATEWAY_BASE_URL"


class TestLLMGatewayAliases:
    @pytest.mark.parametrize(
        "alias",
        ["llmgateway", "llm-gateway", "llmgateway.io", "llm_gateway"],
    )
    def test_alias_resolves(self, alias, monkeypatch):
        for key in _OTHER_PROVIDER_KEYS:
            monkeypatch.delenv(key, raising=False)
        monkeypatch.setenv("LLM_GATEWAY_API_KEY", "lg-test-12345")
        assert resolve_provider(alias) == "llmgateway"

    def test_normalize_provider_models_py(self):
        from hermes_cli.models import normalize_provider
        assert normalize_provider("llm-gateway") == "llmgateway"
        assert normalize_provider("llmgateway.io") == "llmgateway"
        assert normalize_provider("llm_gateway") == "llmgateway"

    def test_normalize_provider_providers_py(self):
        from hermes_cli.providers import normalize_provider
        assert normalize_provider("llm-gateway") == "llmgateway"
        assert normalize_provider("llmgateway.io") == "llmgateway"


class TestLLMGatewayCredentials:
    def test_status_configured(self, monkeypatch):
        for k in _OTHER_PROVIDER_KEYS:
            monkeypatch.delenv(k, raising=False)
        monkeypatch.setenv("LLM_GATEWAY_API_KEY", "lg-test")
        status = get_api_key_provider_status("llmgateway")
        assert status["configured"]

    def test_status_configured_via_alias_env(self, monkeypatch):
        for k in _OTHER_PROVIDER_KEYS:
            monkeypatch.delenv(k, raising=False)
        monkeypatch.delenv("LLM_GATEWAY_API_KEY", raising=False)
        monkeypatch.setenv("LLMGATEWAY_API_KEY", "lg-alias")
        status = get_api_key_provider_status("llmgateway")
        assert status["configured"]

    def test_status_not_configured(self, monkeypatch):
        for k in _OTHER_PROVIDER_KEYS + ("LLM_GATEWAY_API_KEY", "LLMGATEWAY_API_KEY"):
            monkeypatch.delenv(k, raising=False)
        status = get_api_key_provider_status("llmgateway")
        assert not status["configured"]

    def test_openrouter_key_does_not_leak(self, monkeypatch):
        """OpenRouter users should NOT see llmgateway as configured."""
        for k in _OTHER_PROVIDER_KEYS + ("LLM_GATEWAY_API_KEY", "LLMGATEWAY_API_KEY"):
            monkeypatch.delenv(k, raising=False)
        monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
        status = get_api_key_provider_status("llmgateway")
        assert not status["configured"]

    def test_resolve_credentials(self, monkeypatch):
        for k in _OTHER_PROVIDER_KEYS + ("LLMGATEWAY_API_KEY",):
            monkeypatch.delenv(k, raising=False)
        monkeypatch.setenv("LLM_GATEWAY_API_KEY", "lg-direct-key")
        monkeypatch.delenv("LLM_GATEWAY_BASE_URL", raising=False)
        creds = resolve_api_key_provider_credentials("llmgateway")
        assert creds["api_key"] == "lg-direct-key"
        assert creds["base_url"] == "https://api.llmgateway.io/v1"

    def test_custom_base_url_override(self, monkeypatch):
        monkeypatch.setenv("LLM_GATEWAY_API_KEY", "lg-x")
        monkeypatch.setenv(
            "LLM_GATEWAY_BASE_URL", "https://custom.llmgateway.example/v1"
        )
        creds = resolve_api_key_provider_credentials("llmgateway")
        assert creds["base_url"] == "https://custom.llmgateway.example/v1"


class TestLLMGatewayModelCatalog:
    def test_static_model_list(self):
        from hermes_cli.models import _PROVIDER_MODELS
        assert "llmgateway" in _PROVIDER_MODELS
        assert len(_PROVIDER_MODELS["llmgateway"]) >= 1

    def test_canonical_provider_entry(self):
        from hermes_cli.models import CANONICAL_PROVIDERS
        slugs = [p.slug for p in CANONICAL_PROVIDERS]
        assert "llmgateway" in slugs

    def test_label(self):
        from hermes_cli.models import _PROVIDER_LABELS
        assert _PROVIDER_LABELS["llmgateway"] == "LLM Gateway"


class TestLLMGatewayURLMapping:
    def test_url_to_provider(self):
        from agent.model_metadata import _URL_TO_PROVIDER
        assert _URL_TO_PROVIDER.get("api.llmgateway.io") == "llmgateway"

    def test_provider_prefixes(self):
        from agent.model_metadata import _PROVIDER_PREFIXES
        assert "llmgateway" in _PROVIDER_PREFIXES


class TestLLMGatewayProvidersModule:
    def test_overlay_exists(self):
        from hermes_cli.providers import HERMES_OVERLAYS
        assert "llmgateway" in HERMES_OVERLAYS
        overlay = HERMES_OVERLAYS["llmgateway"]
        assert overlay.transport == "openai_chat"
        assert overlay.is_aggregator
        assert overlay.base_url_env_var == "LLM_GATEWAY_BASE_URL"


class TestLLMGatewayAuxiliary:
    def test_aux_model_default(self):
        from agent.auxiliary_client import _API_KEY_PROVIDER_AUX_MODELS
        assert _API_KEY_PROVIDER_AUX_MODELS["llmgateway"] == "google/gemini-3-flash"


class TestLLMGatewayRuntimeResolution:
    def test_runtime_resolution(self, monkeypatch, tmp_path):
        """resolve_runtime_provider returns llmgateway runtime with correct fields."""
        for k in _OTHER_PROVIDER_KEYS + ("LLMGATEWAY_API_KEY",):
            monkeypatch.delenv(k, raising=False)
        monkeypatch.setenv("LLM_GATEWAY_API_KEY", "lg-runtime-key")
        monkeypatch.delenv("LLM_GATEWAY_BASE_URL", raising=False)
        # Point auth/config storage at a tmp dir to avoid reading the user's real config.
        monkeypatch.setenv("HERMES_CONFIG_DIR", str(tmp_path))

        from hermes_cli.runtime_provider import resolve_runtime_provider

        runtime = resolve_runtime_provider(requested="llmgateway")
        assert runtime["provider"] == "llmgateway"
        assert runtime["api_mode"] == "chat_completions"
        assert runtime["base_url"] == "https://api.llmgateway.io/v1"
        assert runtime["api_key"] == "lg-runtime-key"
