"""Runtime integration tests for AIAgent's smart_model_routing hook.

These tests do NOT construct a real ``AIAgent`` (which would resolve
provider credentials and import the full toolset).  Instead they bind
``AIAgent._maybe_apply_smart_routing`` to a lightweight stub that mimics
the agent attributes the method touches, and they patch
``agent.auxiliary_client.resolve_provider_client`` so no HTTP request or
local model endpoint is ever called.
"""

from __future__ import annotations

import types
from unittest.mock import MagicMock, patch

import pytest


# ── Stub plumbing ────────────────────────────────────────────────────────


class FakeClient:
    def __init__(self, base_url: str = "https://api.example.com/v1", api_key: str = "k"):
        self.base_url = base_url
        self.api_key = api_key
        self._custom_headers = None
        self.default_headers = None


def _build_stub_agent(
    *,
    primary_provider: str = "custom",
    primary_model: str = "fast-local",
    smart_routing_config: dict | None = None,
    fallback_chain: list | None = None,
):
    from run_agent import AIAgent

    stub = types.SimpleNamespace()
    stub.model = primary_model
    stub.provider = primary_provider
    stub.base_url = "http://fred:9069/v1"
    stub.api_mode = "chat_completions"
    stub.api_key = "local"
    stub.client = FakeClient(stub.base_url, stub.api_key)
    stub._anthropic_client = None
    stub._anthropic_api_key = ""
    stub._anthropic_base_url = None
    stub._is_anthropic_oauth = False
    stub._client_kwargs = {"api_key": stub.api_key, "base_url": stub.base_url}
    stub._transport_cache = {}
    stub._fallback_chain = list(fallback_chain or [])
    stub._fallback_index = 0
    stub._fallback_activated = False
    stub._smart_routing_config = dict(smart_routing_config or {})
    stub._smart_routing_active = False
    stub._last_smart_routing_decision = None
    stub._primary_runtime = {"provider": primary_provider, "model": primary_model}
    stub._use_prompt_caching = False
    stub._use_native_cache_layout = False
    stub.context_compressor = None
    stub._config_context_length = None

    # Method stubs that the routing path may call.
    stub._is_azure_openai_url = lambda url: False
    stub._is_direct_openai_url = lambda url: False
    stub._provider_model_requires_responses_api = lambda model, provider=None: False
    stub._anthropic_prompt_cache_policy = lambda **kw: (False, False)
    stub._emit_status = MagicMock()

    # Bind the real method.
    stub._maybe_apply_smart_routing = AIAgent._maybe_apply_smart_routing.__get__(stub)
    return stub


@pytest.fixture
def patched_resolver():
    """Patch resolve_provider_client to return a FakeClient — no HTTP."""
    with patch(
        "agent.auxiliary_client.resolve_provider_client",
        return_value=(FakeClient("https://openrouter.ai/api/v1", "rk"), "anthropic/claude-sonnet-4"),
    ) as m:
        yield m


# ── Tests ────────────────────────────────────────────────────────────────


def test_disabled_config_is_noop(patched_resolver):
    stub = _build_stub_agent(smart_routing_config={"enabled": False})
    assert stub._maybe_apply_smart_routing("debug this") is False
    assert stub._smart_routing_active is False
    assert stub.model == "fast-local"
    assert patched_resolver.call_count == 0


def test_local_first_hard_routes_to_smart(patched_resolver):
    stub = _build_stub_agent(
        primary_provider="custom",
        primary_model="fast-local",
        smart_routing_config={
            "enabled": True,
            "smart_model": {
                "provider": "openrouter",
                "model": "anthropic/claude-sonnet-4",
            },
        },
    )
    assert stub._maybe_apply_smart_routing("debug this build failure") is True
    assert stub._smart_routing_active is True
    assert stub._fallback_activated is True
    assert stub.provider == "openrouter"
    assert stub.model == "anthropic/claude-sonnet-4"
    assert patched_resolver.called
    assert stub._last_smart_routing_decision["route"] == "smart"
    assert stub._last_smart_routing_decision["classification"] == "hard"
    stub._emit_status.assert_called_once()


def test_local_first_simple_stays_on_primary(patched_resolver):
    stub = _build_stub_agent(
        primary_provider="custom",
        primary_model="fast-local",
        smart_routing_config={
            "enabled": True,
            "smart_model": {
                "provider": "openrouter",
                "model": "anthropic/claude-sonnet-4",
            },
        },
    )
    assert stub._maybe_apply_smart_routing("hi there") is False
    assert stub._smart_routing_active is False
    assert stub.model == "fast-local"
    assert patched_resolver.call_count == 0


def test_smart_primary_simple_routes_to_cheap(patched_resolver):
    # patched_resolver returns the openrouter base; that's fine for
    # cheap-routing too — we just need a non-None client.
    stub = _build_stub_agent(
        primary_provider="openrouter",
        primary_model="anthropic/claude-sonnet-4",
        smart_routing_config={
            "enabled": True,
            "cheap_model": {
                "provider": "custom",
                "model": "fast-local",
                "base_url": "http://fred:9069/v1",
                "api_key": "local",
            },
        },
    )
    assert stub._maybe_apply_smart_routing("summarize this thread") is True
    assert stub._smart_routing_active is True
    assert stub.provider == "custom"
    assert stub.model == "fast-local"


def test_local_first_uses_fallback_chain_when_smart_missing(patched_resolver):
    stub = _build_stub_agent(
        primary_provider="custom",
        primary_model="fast-local",
        smart_routing_config={"enabled": True},
        fallback_chain=[
            {"provider": "openrouter", "model": "anthropic/claude-sonnet-4"},
        ],
    )
    assert stub._maybe_apply_smart_routing("root cause investigation") is True
    assert stub._smart_routing_active is True
    assert stub.provider == "openrouter"


def test_invalid_target_does_not_activate(patched_resolver):
    stub = _build_stub_agent(
        primary_provider="custom",
        primary_model="fast-local",
        smart_routing_config={
            "enabled": True,
            "smart_model": {"provider": "", "model": ""},
        },
    )
    assert stub._maybe_apply_smart_routing("debug this build failure") is False
    assert stub._smart_routing_active is False
    assert patched_resolver.call_count == 0


def test_resolver_returns_none_keeps_primary():
    with patch(
        "agent.auxiliary_client.resolve_provider_client",
        return_value=(None, "anthropic/claude-sonnet-4"),
    ):
        stub = _build_stub_agent(
            smart_routing_config={
                "enabled": True,
                "smart_model": {
                    "provider": "openrouter",
                    "model": "anthropic/claude-sonnet-4",
                },
            },
        )
        assert stub._maybe_apply_smart_routing("debug this build failure") is False
        assert stub._smart_routing_active is False
        assert stub.model == "fast-local"


def test_resolver_raises_keeps_primary():
    with patch(
        "agent.auxiliary_client.resolve_provider_client",
        side_effect=RuntimeError("boom"),
    ):
        stub = _build_stub_agent(
            smart_routing_config={
                "enabled": True,
                "smart_model": {
                    "provider": "openrouter",
                    "model": "anthropic/claude-sonnet-4",
                },
            },
        )
        # Should swallow and stay on primary.
        assert stub._maybe_apply_smart_routing("debug this build failure") is False
        assert stub._smart_routing_active is False
        assert stub.model == "fast-local"


def test_routing_does_not_make_http_calls(patched_resolver):
    """Sanity: the only outbound call we permit is resolve_provider_client,
    which is itself mocked.  No httpx, no requests, no socket activity."""
    import httpx

    with patch.object(httpx.Client, "send", side_effect=AssertionError("HTTP forbidden")):
        stub = _build_stub_agent(
            smart_routing_config={
                "enabled": True,
                "smart_model": {
                    "provider": "openrouter",
                    "model": "anthropic/claude-sonnet-4",
                },
            },
        )
        # Should not raise.
        stub._maybe_apply_smart_routing("debug this build failure")
