"""Tests for _max_tokens_param model-aware selection (#13901).

Custom OpenAI-compatible endpoints serving gpt-4o/o-series/gpt-5+ models
need max_completion_tokens, but the old code only checked the URL host.
"""
import pytest
from unittest.mock import patch, PropertyMock


class TestMaxTokensParamModelAware:
    """_max_tokens_param must check model name, not just URL (#13901)."""

    def _make_agent(self, model, base_url="https://my-proxy.internal/v1"):
        from run_agent import AIAgent
        agent = AIAgent.__new__(AIAgent)
        agent.model = model
        agent.base_url = base_url
        agent._base_url_hostname = "my-proxy.internal"
        agent._base_url_lower = base_url.lower()
        return agent

    @pytest.mark.parametrize("model", [
        "gpt-4o",
        "gpt-4o-mini",
        "gpt-4.1",
        "gpt-5",
        "gpt-5.4",
        "o1-preview",
        "o3-mini",
        "o4-mini",
    ])
    def test_new_openai_models_use_max_completion_tokens(self, model):
        """Models that require max_completion_tokens should get it even on custom endpoints."""
        agent = self._make_agent(model)
        result = agent._max_tokens_param(4096)
        assert "max_completion_tokens" in result, (
            f"Model {model} on custom endpoint got max_tokens instead of max_completion_tokens"
        )

    @pytest.mark.parametrize("model", [
        "gpt-3.5-turbo",
        "gpt-4-turbo",
        "llama-3.1-70b",
        "claude-3-opus",
        "mixtral-8x7b",
        "qwen2.5-72b",
    ])
    def test_other_models_use_max_tokens(self, model):
        """Non-OpenAI-new models should use max_tokens."""
        agent = self._make_agent(model)
        result = agent._max_tokens_param(4096)
        assert "max_tokens" in result, (
            f"Model {model} got max_completion_tokens instead of max_tokens"
        )

    def test_direct_openai_always_uses_max_completion_tokens(self):
        """Direct api.openai.com should always use max_completion_tokens."""
        agent = self._make_agent("gpt-3.5-turbo", base_url="https://api.openai.com/v1")
        agent._base_url_hostname = "api.openai.com"
        result = agent._max_tokens_param(4096)
        assert "max_completion_tokens" in result
