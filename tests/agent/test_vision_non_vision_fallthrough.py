"""Verify vision auto-detection skips non-vision models to aggregator fallback.

Regression test for #14744 -- when the user's main provider (e.g. ollama-cloud)
is not in _PROVIDER_VISION_MODELS and the main model is not vision-capable,
the auto-detection should skip directly to aggregator fallbacks instead of
sending an image payload to a text-only model.
"""

from unittest.mock import patch, MagicMock

import pytest

from agent.auxiliary_client import _is_likely_vision_model


# -- _is_likely_vision_model heuristic --


class TestIsLikelyVisionModel:
    """Tests for the vision model name heuristic."""

    @pytest.mark.parametrize("model", [
        "gpt-4o",
        "gpt-4o-mini",
        "claude-sonnet-4.6",
        "gemini-3-flash-preview",
        "mimo-v2-omni",
        "mimo-v2.5",
        "llava-v1.6",
        "qwen-vl-plus",
        "qwen2-vl-7b",
        "glm-5v-turbo",
        "glm-4v",
        "pixtral-12b",
        "internvl2-8b",
        "cogvlm-17b",
        "idefics2-8b",
        "some-vision-model",
        "my-multimodal-v3",
        "deepseek-vl-7b",
    ])
    def test_vision_models_detected(self, model):
        assert _is_likely_vision_model(model) is True

    @pytest.mark.parametrize("model", [
        "llama3",
        "llama3:70b",
        "mistral-7b",
        "qwen3:14b",
        "deepseek-coder-v2",
        "codestral-latest",
        "nemotron-3-nano:30b",
        "phi-3-mini",
        "mixtral-8x7b",
        "",
    ])
    def test_non_vision_models_rejected(self, model):
        assert _is_likely_vision_model(model) is False

    def test_none_returns_false(self):
        assert _is_likely_vision_model("") is False


# -- Vision auto-detection with non-vision main model --


class TestVisionNonVisionFallthrough:
    """Vision auto-detect must skip non-vision main models (#14744)."""

    def test_ollama_cloud_non_vision_skips_to_aggregator(self):
        """ollama-cloud with llama3 must skip to aggregator, not try llama3."""
        mock_aggregator_client = MagicMock()

        with patch(
            "agent.auxiliary_client._read_main_provider",
            return_value="ollama-cloud",
        ), patch(
            "agent.auxiliary_client._read_main_model",
            return_value="llama3",
        ), patch(
            "agent.auxiliary_client._resolve_task_provider_model",
            return_value=("auto", None, None, None, None),
        ), patch(
            "agent.auxiliary_client.resolve_provider_client",
        ) as mock_resolve, patch(
            "agent.auxiliary_client._resolve_strict_vision_backend",
            return_value=(mock_aggregator_client, "google/gemini-3-flash-preview"),
        ):
            from agent.auxiliary_client import resolve_vision_provider_client

            provider, client, model = resolve_vision_provider_client()

        # Should have fallen through to the aggregator
        assert client is mock_aggregator_client
        assert model == "google/gemini-3-flash-preview"
        # resolve_provider_client must NOT have been called with ollama-cloud
        for call in mock_resolve.call_args_list:
            assert call.args[0] != "ollama-cloud" or call.args[1] != "llama3", (
                "Should not have tried llama3 on ollama-cloud for vision"
            )

    def test_ollama_with_llava_uses_main_provider(self):
        """ollama with llava (vision model) must use main provider directly."""
        mock_client = MagicMock()

        with patch(
            "agent.auxiliary_client._read_main_provider",
            return_value="ollama",
        ), patch(
            "agent.auxiliary_client._read_main_model",
            return_value="llava-v1.6",
        ), patch(
            "agent.auxiliary_client._resolve_task_provider_model",
            return_value=("auto", None, None, None, None),
        ), patch(
            "agent.auxiliary_client.resolve_provider_client",
            return_value=(mock_client, "llava-v1.6"),
        ):
            from agent.auxiliary_client import resolve_vision_provider_client

            provider, client, model = resolve_vision_provider_client()

        assert provider == "ollama"
        assert client is mock_client
        assert model == "llava-v1.6"

    def test_provider_with_vision_override_still_works(self):
        """xiaomi with explicit vision override must still use the override."""
        mock_client = MagicMock()

        with patch(
            "agent.auxiliary_client._read_main_provider",
            return_value="xiaomi",
        ), patch(
            "agent.auxiliary_client._read_main_model",
            return_value="mimo-v2-pro",
        ), patch(
            "agent.auxiliary_client._resolve_task_provider_model",
            return_value=("auto", None, None, None, None),
        ), patch(
            "agent.auxiliary_client.resolve_provider_client",
            return_value=(mock_client, "mimo-v2.5"),
        ):
            from agent.auxiliary_client import resolve_vision_provider_client

            provider, client, model = resolve_vision_provider_client()

        assert provider == "xiaomi"
        assert client is mock_client
        assert model == "mimo-v2.5"

    def test_non_vision_model_all_aggregators_fail(self):
        """Non-vision main + no aggregators available must return None."""
        with patch(
            "agent.auxiliary_client._read_main_provider",
            return_value="ollama-cloud",
        ), patch(
            "agent.auxiliary_client._read_main_model",
            return_value="qwen3:14b",
        ), patch(
            "agent.auxiliary_client._resolve_task_provider_model",
            return_value=("auto", None, None, None, None),
        ), patch(
            "agent.auxiliary_client._resolve_strict_vision_backend",
            return_value=(None, None),
        ):
            from agent.auxiliary_client import resolve_vision_provider_client

            provider, client, model = resolve_vision_provider_client()

        assert client is None
        assert model is None
