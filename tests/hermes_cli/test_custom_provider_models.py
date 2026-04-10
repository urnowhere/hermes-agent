"""Tests for custom/user-defined provider model discovery via /v1/models probe."""

import json
from unittest.mock import patch, MagicMock

import pytest


class TestProbeEndpointModels:
    """Unit tests for _probe_endpoint_models()."""

    def _make_response(self, model_ids):
        """Build a mock urllib response with OpenAI /models format."""
        data = {"data": [{"id": mid, "object": "model"} for mid in model_ids]}
        body = json.dumps(data).encode()
        resp = MagicMock()
        resp.read.return_value = body
        resp.__enter__ = lambda s: s
        resp.__exit__ = MagicMock(return_value=False)
        return resp

    def test_returns_models_from_standard_endpoint(self):
        from hermes_cli.model_switch import _probe_endpoint_models

        resp = self._make_response(["llama-3.3-70b", "qwen-2.5-72b"])
        with patch("urllib.request.urlopen", return_value=resp):
            result = _probe_endpoint_models("http://localhost:1234/v1")
        assert result == ["llama-3.3-70b", "qwen-2.5-72b"]

    def test_returns_empty_on_timeout(self):
        from hermes_cli.model_switch import _probe_endpoint_models
        import urllib.error

        with patch("urllib.request.urlopen", side_effect=TimeoutError):
            result = _probe_endpoint_models("http://localhost:1234/v1")
        assert result == []

    def test_returns_empty_on_404(self):
        from hermes_cli.model_switch import _probe_endpoint_models
        import urllib.error

        with patch("urllib.request.urlopen", side_effect=urllib.error.HTTPError(
            "http://x/models", 404, "Not Found", {}, None
        )):
            result = _probe_endpoint_models("http://localhost:1234/v1")
        assert result == []

    def test_returns_empty_for_empty_url(self):
        from hermes_cli.model_switch import _probe_endpoint_models
        assert _probe_endpoint_models("") == []

    def test_uses_models_url_override(self):
        from hermes_cli.model_switch import _probe_endpoint_models

        resp = self._make_response(["custom-model-1"])
        with patch("urllib.request.urlopen", return_value=resp) as mock_open:
            result = _probe_endpoint_models(
                "http://localhost:1234/v1",
                models_url="http://admin.example.com/api/models",
            )
        assert result == ["custom-model-1"]
        # Should have called the models_url, not the base_url
        called_url = mock_open.call_args[0][0].full_url
        assert "admin.example.com" in called_url

    def test_sends_bearer_auth(self):
        from hermes_cli.model_switch import _probe_endpoint_models

        resp = self._make_response(["model-1"])
        with patch("urllib.request.urlopen", return_value=resp) as mock_open:
            _probe_endpoint_models("http://localhost/v1", api_key="sk-test-123")
        req = mock_open.call_args[0][0]
        assert req.get_header("Authorization") == "Bearer sk-test-123"

    def test_rewrites_anthropic_suffix_to_v1(self):
        from hermes_cli.model_switch import _probe_endpoint_models

        resp = self._make_response(["model-1"])
        with patch("urllib.request.urlopen", return_value=resp) as mock_open:
            result = _probe_endpoint_models("https://api.minimax.io/anthropic")
        assert result == ["model-1"]
        # Should have probed /v1/models, not /anthropic/models
        called_url = mock_open.call_args[0][0].full_url
        assert "/v1/models" in called_url
        assert "/anthropic/models" not in called_url

    def test_filters_empty_ids(self):
        from hermes_cli.model_switch import _probe_endpoint_models

        data = {"data": [{"id": "good"}, {"id": ""}, {"object": "model"}, {"id": "also-good"}]}
        body = json.dumps(data).encode()
        resp = MagicMock()
        resp.read.return_value = body
        resp.__enter__ = lambda s: s
        resp.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=resp):
            result = _probe_endpoint_models("http://localhost/v1")
        assert result == ["good", "also-good"]


class TestListAuthenticatedProvidersCustomModels:
    """Verify that list_authenticated_providers() probes custom endpoints."""

    def test_custom_provider_shows_probed_models(self):
        from hermes_cli.model_switch import list_authenticated_providers

        with patch("hermes_cli.model_switch._probe_endpoint_models", return_value=["model-a", "model-b"]):
            results = list_authenticated_providers(
                custom_providers=[{
                    "name": "My Server",
                    "base_url": "http://localhost:1234/v1",
                    "model": "model-a",
                }],
            )
        custom = [r for r in results if r.get("source") == "user-config"]
        assert len(custom) == 1
        assert "model-a" in custom[0]["models"]
        assert "model-b" in custom[0]["models"]
        assert custom[0]["total_models"] == 2

    def test_custom_provider_preserves_saved_model_on_probe_failure(self):
        from hermes_cli.model_switch import list_authenticated_providers

        with patch("hermes_cli.model_switch._probe_endpoint_models", return_value=[]):
            results = list_authenticated_providers(
                custom_providers=[{
                    "name": "Offline Server",
                    "base_url": "http://localhost:9999/v1",
                    "model": "saved-model",
                }],
            )
        custom = [r for r in results if r.get("source") == "user-config"]
        assert len(custom) == 1
        assert custom[0]["models"] == ["saved-model"]

    def test_user_provider_shows_probed_models(self):
        from hermes_cli.model_switch import list_authenticated_providers

        with patch("hermes_cli.model_switch._probe_endpoint_models", return_value=["llama-70b", "mistral-7b"]):
            results = list_authenticated_providers(
                user_providers={
                    "litellm": {
                        "name": "LiteLLM Proxy",
                        "api": "http://localhost:4000/v1",
                        "default_model": "llama-70b",
                    }
                },
            )
        user_defined = [r for r in results if r.get("source") == "user-config"]
        assert len(user_defined) == 1
        assert user_defined[0]["total_models"] == 2

    def test_custom_provider_passes_models_url(self):
        from hermes_cli.model_switch import list_authenticated_providers

        with patch("hermes_cli.model_switch._probe_endpoint_models") as mock_probe:
            mock_probe.return_value = ["m1"]
            list_authenticated_providers(
                custom_providers=[{
                    "name": "Custom",
                    "base_url": "http://x/v1",
                    "models_url": "http://x/admin/models",
                }],
            )
        mock_probe.assert_called_once()
        call_kwargs = mock_probe.call_args
        assert call_kwargs[1].get("models_url") == "http://x/admin/models" or \
               (len(call_kwargs[0]) >= 3 and call_kwargs[0][2] == "http://x/admin/models")


class TestModelsUrlConfigValidation:
    """Verify models_url is accepted in custom_providers config validation."""

    def test_models_url_is_valid_field(self):
        from hermes_cli.config import _VALID_CUSTOM_PROVIDER_FIELDS
        assert "models_url" in _VALID_CUSTOM_PROVIDER_FIELDS
