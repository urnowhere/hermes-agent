from agent.smart_model_routing import choose_cheap_model_route


_BASE_CONFIG = {
    "enabled": True,
    "cheap_model": {
        "provider": "openrouter",
        "model": "google/gemini-2.5-flash",
    },
}


def test_returns_none_when_disabled():
    cfg = {**_BASE_CONFIG, "enabled": False}
    assert choose_cheap_model_route("what time is it in tokyo?", cfg) is None


def test_routes_short_simple_prompt():
    result = choose_cheap_model_route("what time is it in tokyo?", _BASE_CONFIG)
    assert result is not None
    assert result["provider"] == "openrouter"
    assert result["model"] == "google/gemini-2.5-flash"
    assert result["routing_reason"] == "simple_turn"


def test_skips_long_prompt():
    prompt = "please summarize this carefully " * 20
    assert choose_cheap_model_route(prompt, _BASE_CONFIG) is None


def test_skips_code_like_prompt():
    prompt = "debug this traceback: ```python\nraise ValueError('bad')\n```"
    assert choose_cheap_model_route(prompt, _BASE_CONFIG) is None


def test_skips_tool_heavy_prompt_keywords():
    prompt = "implement a patch for this docker error"
    assert choose_cheap_model_route(prompt, _BASE_CONFIG) is None


def test_resolve_turn_route_falls_back_to_primary_when_route_runtime_cannot_be_resolved(monkeypatch):
    from agent.smart_model_routing import resolve_turn_route

    monkeypatch.setattr(
        "hermes_cli.runtime_provider.resolve_runtime_provider",
        lambda **kwargs: (_ for _ in ()).throw(RuntimeError("bad route")),
    )
    result = resolve_turn_route(
        "what time is it in tokyo?",
        _BASE_CONFIG,
        {
            "model": "anthropic/claude-sonnet-4",
            "provider": "openrouter",
            "base_url": "https://openrouter.ai/api/v1",
            "api_mode": "chat_completions",
            "api_key": "sk-primary",
        },
    )
    assert result["model"] == "anthropic/claude-sonnet-4"
    assert result["runtime"]["provider"] == "openrouter"
    assert result["label"] is None


# ---------------------------------------------------------------------------
# Refuse-to-route: when estimated request tokens exceed cheap_ctx * ratio,
# smart routing must fall back to primary instead of letting the request hit
# the cheap model (which would trigger destructive preflight/fallback
# compression on a session sized for the primary model).
# ---------------------------------------------------------------------------

_REFUSE_PRIMARY = {
    "model": "anthropic/claude-sonnet-4.6",
    "provider": "openrouter",
    "base_url": "https://openrouter.ai/api/v1",
    "api_mode": "chat_completions",
    "api_key": "sk-primary",
}


def _mock_runtime_ok(monkeypatch):
    """Make resolve_runtime_provider return a fixed cheap-side runtime."""
    monkeypatch.setattr(
        "hermes_cli.runtime_provider.resolve_runtime_provider",
        lambda **kwargs: {
            "api_key": "sk-cheap",
            "base_url": "https://openrouter.ai/api/v1",
            "provider": "openrouter",
            "api_mode": "chat_completions",
            "command": None,
            "args": (),
            "credential_pool": None,
        },
    )


def _mock_cheap_context_length(monkeypatch, ctx_length: int):
    """Make get_model_context_length always return ``ctx_length``."""
    monkeypatch.setattr(
        "agent.model_metadata.get_model_context_length",
        lambda *args, **kwargs: ctx_length,
    )


def test_resolve_turn_route_refuses_when_history_exceeds_cheap_ratio(monkeypatch):
    """80K > 128K * 0.50 = 64K → refuse and fall back to primary."""
    from agent.smart_model_routing import resolve_turn_route

    _mock_runtime_ok(monkeypatch)
    _mock_cheap_context_length(monkeypatch, 128_000)

    result = resolve_turn_route(
        "hi",
        _BASE_CONFIG,
        _REFUSE_PRIMARY,
        current_request_tokens=80_000,
        max_history_ratio=0.50,
    )

    assert result["model"] == _REFUSE_PRIMARY["model"]
    assert result["label"] is None


def test_resolve_turn_route_allows_when_history_fits_cheap_ratio(monkeypatch):
    """50K < 128K * 0.50 = 64K → use cheap route."""
    from agent.smart_model_routing import resolve_turn_route

    _mock_runtime_ok(monkeypatch)
    _mock_cheap_context_length(monkeypatch, 128_000)

    result = resolve_turn_route(
        "hi",
        _BASE_CONFIG,
        _REFUSE_PRIMARY,
        current_request_tokens=50_000,
        max_history_ratio=0.50,
    )

    assert result["model"] == "google/gemini-2.5-flash"
    assert result["label"] is not None


def test_resolve_turn_route_zero_token_count_skips_check(monkeypatch):
    """Backward compat: current_request_tokens=0 (caller didn't estimate)
    must skip the refuse check entirely, even when cheap_ctx is small."""
    from agent.smart_model_routing import resolve_turn_route

    _mock_runtime_ok(monkeypatch)

    # get_model_context_length should never be called in this path.
    def _should_not_be_called(*args, **kwargs):
        raise AssertionError("get_model_context_length should not be called when current_request_tokens=0")

    monkeypatch.setattr(
        "agent.model_metadata.get_model_context_length",
        _should_not_be_called,
    )

    result = resolve_turn_route(
        "hi",
        _BASE_CONFIG,
        _REFUSE_PRIMARY,
        # current_request_tokens defaults to 0
    )

    assert result["model"] == "google/gemini-2.5-flash"
    assert result["label"] is not None


def test_resolve_turn_route_custom_max_history_ratio(monkeypatch):
    """A caller that tunes threshold_percent (e.g. to 0.80) propagates to
    smart routing refusal — 90K fits under 128K * 0.80 = 102K → cheap route."""
    from agent.smart_model_routing import resolve_turn_route

    _mock_runtime_ok(monkeypatch)
    _mock_cheap_context_length(monkeypatch, 128_000)

    # At default 0.50: 90K > 64K would refuse.
    # At 0.80: 90K < 102K → allow.
    result = resolve_turn_route(
        "hi",
        _BASE_CONFIG,
        _REFUSE_PRIMARY,
        current_request_tokens=90_000,
        max_history_ratio=0.80,
    )

    assert result["model"] == "google/gemini-2.5-flash"
    assert result["label"] is not None


def test_resolve_turn_route_refuses_when_cheap_context_unknown_and_estimate_positive(monkeypatch):
    """Defensive: if get_model_context_length returns 0 (unknown), we can't
    compare against the threshold, so the refusal check is a no-op and the
    cheap route is used.  This preserves existing behavior for providers
    whose context length can't be resolved at all."""
    from agent.smart_model_routing import resolve_turn_route

    _mock_runtime_ok(monkeypatch)
    _mock_cheap_context_length(monkeypatch, 0)  # unknown

    result = resolve_turn_route(
        "hi",
        _BASE_CONFIG,
        _REFUSE_PRIMARY,
        current_request_tokens=1_000_000,  # huge, but cheap_ctx=0 means skip check
        max_history_ratio=0.50,
    )

    assert result["model"] == "google/gemini-2.5-flash"
    assert result["label"] is not None
