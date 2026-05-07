"""Tests for gateway /fast handling with fallback providers."""

from types import SimpleNamespace

import pytest

import gateway.run as gateway_run
from gateway.platforms.base import MessageEvent


class _DummyPath:
    def __truediv__(self, _name):
        return self

    def exists(self):
        return False


class _DummyCompressor:
    def update_model(self, **kwargs):
        self.kwargs = kwargs


def _make_runner():
    runner = object.__new__(gateway_run.GatewayRunner)
    runner._service_tier = None
    return runner


@pytest.mark.asyncio
async def test_fast_command_allows_fast_capable_fallback(monkeypatch):
    """A non-fast primary should not block /fast when fallbacks include GPT/Claude."""
    monkeypatch.setattr(gateway_run, "_hermes_home", _DummyPath())
    monkeypatch.setattr(gateway_run, "_load_gateway_config", lambda: {
        "model": {"default": "glm-5.1"},
        "fallback_providers": [
            {"provider": "cliproxy", "model": "gpt-5.5"},
        ],
    })
    monkeypatch.setattr(gateway_run, "_resolve_gateway_model", lambda _cfg=None: "glm-5.1")
    monkeypatch.setattr(gateway_run.GatewayRunner, "_load_service_tier", staticmethod(lambda: None))

    saved = {}
    monkeypatch.setattr(gateway_run, "atomic_yaml_write", lambda _path, cfg: saved.update(cfg))

    runner = _make_runner()
    event = MessageEvent(text="/fast fast")

    response = await runner._handle_fast_command(event)

    assert "Priority Processing: **FAST**" in response
    assert saved["agent"]["service_tier"] == "fast"


def test_fallback_activation_recomputes_fast_request_overrides(monkeypatch):
    """Fallback to a fast-capable model should refresh request_overrides."""
    from run_agent import AIAgent

    agent = object.__new__(AIAgent)
    agent.service_tier = "priority"
    agent.request_overrides = {}
    agent._fallback_chain = [{
        "provider": "custom",
        "model": "gpt-5.5",
        "base_url": "https://example.test/v1",
        "api_key": "test-key",
    }]
    agent._fallback_index = 0
    agent._fallback_activated = False
    agent._primary_runtime = {"provider": "opencode-go"}
    agent._rate_limited_until = 0
    agent.model = "glm-5.1"
    agent.provider = "opencode-go"
    agent.base_url = "https://primary.test/v1"
    agent.api_key = "primary-key"
    agent.api_mode = "chat_completions"
    agent._api_mode_explicit = False
    agent._credential_pool = None
    agent._client_kwargs = {}
    agent.client = SimpleNamespace(api_key="primary-key")
    agent.context_compressor = _DummyCompressor()
    agent._config_context_length = 128000
    agent.quiet_mode = True
    agent._force_ascii_payload = False
    agent._emit_status = lambda *_args, **_kwargs: None
    agent._ensure_lmstudio_runtime_loaded = lambda: None
    agent._replace_primary_openai_client = lambda **_kwargs: None
    agent._anthropic_prompt_cache_policy = lambda **_kwargs: (False, False)
    agent._provider_model_requires_responses_api = lambda *_args, **_kwargs: False
    agent._is_azure_openai_url = lambda *_args, **_kwargs: False
    agent._is_direct_openai_url = lambda *_args, **_kwargs: False

    import agent.auxiliary_client as auxiliary_client

    monkeypatch.setattr(
        auxiliary_client,
        "resolve_provider_client",
        lambda *_args, **_kwargs: (
            SimpleNamespace(api_key="fallback-key", base_url="https://example.test/v1"),
            "gpt-5.5",
        ),
    )

    assert agent._try_activate_fallback() is True
    assert agent.model == "gpt-5.5"
    assert agent.request_overrides == {"service_tier": "priority"}
