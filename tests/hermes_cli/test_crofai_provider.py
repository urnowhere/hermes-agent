from __future__ import annotations


def test_crofai_provider_is_registered_and_aliased():
    from hermes_cli.auth import PROVIDER_REGISTRY, resolve_provider
    from hermes_cli.models import normalize_provider, provider_label

    assert "crofai" in PROVIDER_REGISTRY
    pconfig = PROVIDER_REGISTRY["crofai"]
    assert pconfig.api_key_env_vars == ("CROFAI_API_KEY",)
    assert pconfig.base_url_env_var == "CROFAI_BASE_URL"
    assert normalize_provider("crof.ai") == "crofai"
    assert normalize_provider("crof-ai") == "crofai"
    assert resolve_provider("crof.ai") == "crofai"
    assert provider_label("crofai") == "Crof.ai"


def test_crofai_provider_model_ids_uses_live_catalog(monkeypatch):
    from hermes_cli import models

    monkeypatch.setattr(models, "fetch_crofai_models", lambda **kwargs: ["live-a", "live-b"])

    assert models.provider_model_ids("crofai") == ["live-a", "live-b"]


def test_crofai_model_prefix_is_stripped():
    from hermes_cli.model_normalize import normalize_model_for_provider

    assert normalize_model_for_provider("crofai/glm-5.1", "crofai") == "glm-5.1"
    assert normalize_model_for_provider("crof.ai/glm-5.1", "crofai") == "glm-5.1"
