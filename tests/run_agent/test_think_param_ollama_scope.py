"""Tests for Ollama-specific `think=False` parameter scoping (issue #11237).

The `think` parameter is Ollama-native. Before the fix it was sent to ALL
``provider == "custom"`` endpoints, causing HTTP 422 rejections from providers
like Mistral, Fireworks, Together.ai, and vLLM.

These tests verify:
  1. ``think=False`` is NOT sent to cloud custom providers (the bug).
  2. ``think=False`` IS sent to Ollama endpoints (preserving correct behavior).
  3. ``_is_ollama()`` detection works via both num_ctx probe and URL heuristics.
"""

import types
from unittest.mock import patch, MagicMock

import pytest

import sys
sys.modules.setdefault("fire", types.SimpleNamespace(Fire=lambda *a, **k: None))
sys.modules.setdefault("firecrawl", types.SimpleNamespace(Firecrawl=object))
sys.modules.setdefault("fal_client", types.SimpleNamespace())

from run_agent import AIAgent


# ── Helpers ──────────────────────────────────────────────────────────────────


def _tool_defs(*names):
    return [
        {
            "type": "function",
            "function": {
                "name": n,
                "description": f"{n} tool",
                "parameters": {"type": "object", "properties": {}},
            },
        }
        for n in names
    ]


class _FakeOpenAI:
    def __init__(self, **kw):
        self.api_key = kw.get("api_key", "test")
        self.base_url = kw.get("base_url", "http://test")

    def close(self):
        pass


def _make_agent(monkeypatch, provider, base_url, model=None, reasoning_config=None):
    """Create a minimal AIAgent suitable for testing _build_api_kwargs."""
    monkeypatch.setattr("run_agent.get_tool_definitions", lambda **kw: _tool_defs("web_search"))
    monkeypatch.setattr("run_agent.check_toolset_requirements", lambda: {})
    monkeypatch.setattr("run_agent.OpenAI", _FakeOpenAI)
    kwargs = dict(
        api_key="test-key",
        base_url=base_url,
        provider=provider,
        api_mode="chat_completions",
        max_iterations=4,
        quiet_mode=True,
        skip_context_files=True,
        skip_memory=True,
    )
    if model:
        kwargs["model"] = model
    agent = AIAgent(**kwargs)
    if reasoning_config is not None:
        agent.reasoning_config = reasoning_config
    return agent


def _get_think_from_kwargs(agent, messages=None):
    """Build API kwargs and return the value of extra_body['think'] or sentinel."""
    if messages is None:
        messages = [{"role": "user", "content": "hello"}]
    kwargs = agent._build_api_kwargs(messages)
    extra = kwargs.get("extra_body", {})
    return extra.get("think", "<ABSENT>")


# ── _is_ollama() unit tests ─────────────────────────────────────────────────


class TestIsOllama:
    """Unit tests for the _is_ollama() detection method."""

    def test_detects_via_num_ctx_probe(self, monkeypatch):
        """If _ollama_num_ctx was detected (via /api/show), endpoint is Ollama."""
        agent = _make_agent(
            monkeypatch, "custom", "http://localhost:11434/v1"
        )
        agent._ollama_num_ctx = 32768
        assert agent._is_ollama() is True

    def test_detects_via_ollama_in_url(self, monkeypatch):
        """URL containing 'ollama' is detected even without num_ctx probe."""
        agent = _make_agent(
            monkeypatch, "custom", "http://my-ollama-server.local/v1"
        )
        agent._ollama_num_ctx = None
        assert agent._is_ollama() is True

    def test_detects_via_port_11434(self, monkeypatch):
        """Default Ollama port :11434 is detected."""
        agent = _make_agent(
            monkeypatch, "custom", "http://192.168.1.100:11434/v1"
        )
        agent._ollama_num_ctx = None
        assert agent._is_ollama() is True

    def test_rejects_cloud_custom_provider(self, monkeypatch):
        """Cloud custom provider (Mistral) is not detected as Ollama."""
        agent = _make_agent(
            monkeypatch, "custom", "https://api.mistral.ai/v1"
        )
        agent._ollama_num_ctx = None
        assert agent._is_ollama() is False

    def test_rejects_lm_studio(self, monkeypatch):
        """Local LM Studio (port 1234) is not detected as Ollama."""
        agent = _make_agent(
            monkeypatch, "custom", "http://localhost:1234/v1"
        )
        agent._ollama_num_ctx = None
        assert agent._is_ollama() is False

    def test_rejects_vllm_remote(self, monkeypatch):
        """Remote vLLM server is not detected as Ollama."""
        agent = _make_agent(
            monkeypatch, "custom", "https://vllm.mycompany.com/v1"
        )
        agent._ollama_num_ctx = None
        assert agent._is_ollama() is False

    def test_rejects_openrouter(self, monkeypatch):
        """OpenRouter is never detected as Ollama."""
        agent = _make_agent(
            monkeypatch, "openrouter", "https://openrouter.ai/api/v1"
        )
        agent._ollama_num_ctx = None
        assert agent._is_ollama() is False


# ── think=False scoping tests ────────────────────────────────────────────────


class TestThinkParamOllamaOnly:
    """Verify think=False is scoped to Ollama endpoints only."""

    # ── Cloud custom providers: think must NOT be sent ──

    @pytest.mark.parametrize("base_url", [
        "https://api.mistral.ai/v1",
        "https://api.fireworks.ai/inference/v1",
        "https://api.together.xyz/v1",
        "https://vllm.example.com/v1",
        "https://my-custom-llm.company.com/openai/v1",
    ])
    def test_cloud_custom_provider_no_think(self, monkeypatch, base_url):
        """think=False must NOT be sent to cloud custom providers.

        This is the core regression test for issue #11237.
        Before the fix, provider=="custom" caused think=False to be sent
        to all custom endpoints, triggering HTTP 422 from strict providers.
        """
        agent = _make_agent(
            monkeypatch, "custom", base_url,
            reasoning_config={"effort": "none", "enabled": False},
        )
        agent._ollama_num_ctx = None
        assert _get_think_from_kwargs(agent) == "<ABSENT>"

    def test_local_lm_studio_no_think(self, monkeypatch):
        """LM Studio on localhost should not receive think=False.

        LM Studio is a local provider but NOT Ollama — it does not
        understand the `think` parameter.
        """
        agent = _make_agent(
            monkeypatch, "custom", "http://localhost:1234/v1",
            reasoning_config={"effort": "none", "enabled": False},
        )
        agent._ollama_num_ctx = None
        assert _get_think_from_kwargs(agent) == "<ABSENT>"

    # ── Ollama endpoints: think must be sent ──

    @pytest.mark.parametrize("base_url", [
        "http://localhost:11434/v1",
        "http://ollama.local:11434/v1",
        "http://192.168.1.50:11434/v1",
    ])
    def test_ollama_url_sends_think_false(self, monkeypatch, base_url):
        """Ollama URLs (with :11434) should still receive think=False."""
        agent = _make_agent(
            monkeypatch, "custom", base_url,
            reasoning_config={"effort": "none", "enabled": False},
        )
        # num_ctx may not be detected yet but URL is clear
        agent._ollama_num_ctx = None
        assert _get_think_from_kwargs(agent) is False

    def test_ollama_num_ctx_probe_sends_think_false(self, monkeypatch):
        """When Ollama is detected via num_ctx probe, think=False is sent.

        This covers the case where the URL might not contain 'ollama' or
        :11434 (e.g. behind a reverse proxy) but the probe succeeded.
        """
        agent = _make_agent(
            monkeypatch, "custom", "http://llm-proxy.internal/v1",
            reasoning_config={"effort": "none", "enabled": False},
        )
        agent._ollama_num_ctx = 32768
        assert _get_think_from_kwargs(agent) is False

    def test_ollama_effort_none_sends_think_false(self, monkeypatch):
        """reasoning_effort='none' triggers think=False on Ollama."""
        agent = _make_agent(
            monkeypatch, "custom", "http://localhost:11434/v1",
            reasoning_config={"effort": "none", "enabled": True},
        )
        agent._ollama_num_ctx = 8192
        assert _get_think_from_kwargs(agent) is False

    def test_ollama_enabled_false_sends_think_false(self, monkeypatch):
        """reasoning enabled=False triggers think=False on Ollama."""
        agent = _make_agent(
            monkeypatch, "custom", "http://localhost:11434/v1",
            reasoning_config={"effort": "medium", "enabled": False},
        )
        agent._ollama_num_ctx = 8192
        assert _get_think_from_kwargs(agent) is False

    def test_ollama_reasoning_enabled_no_think(self, monkeypatch):
        """When reasoning is enabled, think is NOT added (Ollama handles it)."""
        agent = _make_agent(
            monkeypatch, "custom", "http://localhost:11434/v1",
            reasoning_config={"effort": "medium", "enabled": True},
        )
        agent._ollama_num_ctx = 8192
        assert _get_think_from_kwargs(agent) == "<ABSENT>"

    def test_ollama_no_reasoning_config_no_think(self, monkeypatch):
        """When there's no reasoning config at all, think is NOT added."""
        agent = _make_agent(
            monkeypatch, "custom", "http://localhost:11434/v1",
        )
        agent._ollama_num_ctx = 8192
        agent.reasoning_config = None
        assert _get_think_from_kwargs(agent) == "<ABSENT>"

    # ── Non-custom providers are unaffected ──

    def test_openrouter_unaffected(self, monkeypatch):
        """OpenRouter path should never have 'think' in extra_body."""
        agent = _make_agent(
            monkeypatch, "openrouter", "https://openrouter.ai/api/v1",
            reasoning_config={"effort": "none", "enabled": False},
        )
        assert _get_think_from_kwargs(agent) == "<ABSENT>"

    def test_anthropic_unaffected(self, monkeypatch):
        """Anthropic path should never have 'think' in extra_body."""
        agent = _make_agent(
            monkeypatch, "anthropic", "https://api.anthropic.com",
            reasoning_config={"effort": "none", "enabled": False},
        )
        agent.api_mode = "anthropic_messages"
        assert _get_think_from_kwargs(agent) == "<ABSENT>"
