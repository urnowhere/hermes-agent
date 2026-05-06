"""Unit tests for ``agent/smart_model_routing.py``.

These tests are deliberately pure: they do not import ``run_agent`` or
construct ``AIAgent``, so no provider SDK, HTTP client, or local model
endpoint is touched.  Integration is exercised by
``tests/run_agent/test_smart_model_routing_runtime.py``.
"""

from __future__ import annotations

import pytest

from agent.smart_model_routing import (
    DEFAULT_MAX_SIMPLE_CHARS,
    DEFAULT_MAX_SIMPLE_WORDS,
    LOCAL_LIKE_PROVIDERS,
    RoutingDecision,
    RoutingTarget,
    classify_difficulty,
    decide_route,
)


# ── classify_difficulty ──────────────────────────────────────────────────


@pytest.mark.parametrize(
    "text",
    [
        "",
        "   ",
        "what time is it",
        "summarize this email",
        "show me my latest invoice",
        "open the doc",
        "draft a quick reply saying yes",
    ],
)
def test_classify_simple(text):
    assert classify_difficulty(text) == "simple"


@pytest.mark.parametrize(
    "text",
    [
        "debug this build failure",
        "investigate why the deploy is failing",
        "refactor the auth module so it composes",
        "design a multi-step rollout plan",
        "stack trace says NoneType",
        "review the contract risk for this lawsuit",
        "root cause for the regression?",
        "permit escalation across multiple agencies",
    ],
)
def test_classify_hard_keywords(text):
    assert classify_difficulty(text) == "hard"


def test_classify_hard_by_length_chars():
    text = "a" * (DEFAULT_MAX_SIMPLE_CHARS + 1)
    assert classify_difficulty(text) == "hard"


def test_classify_hard_by_length_words():
    text = " ".join(["word"] * (DEFAULT_MAX_SIMPLE_WORDS + 1))
    assert classify_difficulty(text) == "hard"


def test_classify_hard_code_fence():
    assert classify_difficulty("look at this:\n```py\nprint('x')\n```") == "hard"


def test_classify_hard_traceback():
    assert classify_difficulty("Traceback (most recent call last)\n  File 'x.py'") == "hard"


def test_classify_hard_three_questions():
    assert classify_difficulty("a? b? c?") == "hard"


def test_classify_extra_keywords_extends_hard_set():
    assert classify_difficulty("zelda quest") == "simple"
    assert classify_difficulty("zelda quest", extra_hard_keywords={"zelda"}) == "hard"


def test_classify_partial_token_does_not_trigger():
    # "encoded" should NOT match HARD keyword "code"
    assert classify_difficulty("the message was encoded") == "simple"


# ── decide_route ─────────────────────────────────────────────────────────


SMART = {"provider": "openrouter", "model": "anthropic/claude-sonnet-4"}
CHEAP = {"provider": "custom", "model": "fast-local", "base_url": "http://fred:9069/v1"}


def test_disabled_config_returns_primary():
    d = decide_route(
        user_message="debug the build",
        primary_provider="custom",
        primary_model="fast-local",
        config={"enabled": False, "smart_model": SMART},
    )
    assert d.route == "primary"
    assert d.target is None
    assert d.mode == "disabled"


def test_no_routes_configured_returns_primary():
    d = decide_route(
        user_message="hard task: debug",
        primary_provider="custom",
        primary_model="fast-local",
        config={"enabled": True},
        fallback_chain=[],
    )
    assert d.route == "primary"
    assert d.mode == "disabled"


def test_local_first_hard_routes_to_smart_model():
    d = decide_route(
        user_message="please debug this build failure",
        primary_provider="custom",
        primary_model="fast-local",
        config={"enabled": True, "smart_model": SMART},
    )
    assert d.route == "smart"
    assert d.target is not None
    assert d.target.provider == "openrouter"
    assert d.target.model == "anthropic/claude-sonnet-4"
    assert d.mode == "local-first"
    assert d.classification == "hard"


def test_local_first_hard_uses_fallback_when_smart_missing():
    d = decide_route(
        user_message="root cause investigation needed",
        primary_provider="custom",
        primary_model="fast-local",
        config={"enabled": True},
        fallback_chain=[SMART, {"provider": "anthropic", "model": "claude-3-5-haiku"}],
    )
    assert d.route == "smart"
    assert d.target.provider == "openrouter"
    assert d.mode == "local-first"
    assert "fallback" in d.reason.lower()


def test_local_first_simple_stays_on_primary():
    d = decide_route(
        user_message="what time is it",
        primary_provider="custom",
        primary_model="fast-local",
        config={"enabled": True, "smart_model": SMART},
    )
    assert d.route == "primary"
    assert d.target is None
    assert d.mode == "local-first"
    assert d.classification == "simple"


def test_smart_primary_simple_routes_to_cheap_model():
    d = decide_route(
        user_message="summarize this thread",
        primary_provider="openrouter",
        primary_model="anthropic/claude-sonnet-4",
        config={"enabled": True, "cheap_model": CHEAP},
    )
    assert d.route == "cheap"
    assert d.target is not None
    assert d.target.provider == "custom"
    assert d.target.base_url == "http://fred:9069/v1"
    assert d.mode == "smart-primary"


def test_smart_primary_hard_stays_on_primary():
    d = decide_route(
        user_message="please debug this build failure",
        primary_provider="openrouter",
        primary_model="anthropic/claude-sonnet-4",
        config={"enabled": True, "cheap_model": CHEAP},
    )
    assert d.route == "primary"
    assert d.mode == "smart-primary"
    assert d.classification == "hard"


def test_both_targets_local_primary_picks_local_first_mode():
    # Auto-detection picks local-first when primary provider is local-like
    d = decide_route(
        user_message="debug the build",
        primary_provider="custom",
        primary_model="fast-local",
        config={"enabled": True, "smart_model": SMART, "cheap_model": CHEAP},
    )
    assert d.mode == "local-first"
    assert d.route == "smart"


def test_both_targets_cloud_primary_picks_smart_primary_mode():
    d = decide_route(
        user_message="quick question",
        primary_provider="openrouter",
        primary_model="anthropic/claude-sonnet-4",
        config={"enabled": True, "smart_model": SMART, "cheap_model": CHEAP},
    )
    assert d.mode == "smart-primary"
    assert d.route == "cheap"


def test_explicit_mode_overrides_auto():
    d = decide_route(
        user_message="quick question",
        primary_provider="custom",  # would auto → local-first
        primary_model="fast-local",
        config={
            "enabled": True,
            "mode": "smart-primary",
            "smart_model": SMART,
            "cheap_model": CHEAP,
        },
    )
    assert d.mode == "smart-primary"


def test_invalid_cheap_model_treated_as_missing():
    d = decide_route(
        user_message="hi",
        primary_provider="openrouter",
        primary_model="anthropic/claude-sonnet-4",
        config={"enabled": True, "cheap_model": {"provider": "", "model": ""}},
    )
    assert d.route == "primary"
    assert d.mode == "disabled"


def test_invalid_smart_model_treated_as_missing_falls_back_to_chain():
    d = decide_route(
        user_message="debug this",
        primary_provider="custom",
        primary_model="fast-local",
        config={"enabled": True, "smart_model": {"provider": "openrouter"}},
        fallback_chain=[SMART],
    )
    assert d.route == "smart"
    assert d.target.provider == "openrouter"


def test_local_like_providers_constant_includes_expected_names():
    assert "ollama" in LOCAL_LIKE_PROVIDERS
    assert "custom" in LOCAL_LIKE_PROVIDERS
    assert "vllm" in LOCAL_LIKE_PROVIDERS
    assert "openrouter" not in LOCAL_LIKE_PROVIDERS
    assert "anthropic" not in LOCAL_LIKE_PROVIDERS


def test_routing_target_to_fallback_entry_round_trip():
    t = RoutingTarget(
        provider="custom", model="x", base_url="http://h:1/v1", api_key="k"
    )
    d = t.to_fallback_entry()
    assert d == {
        "provider": "custom",
        "model": "x",
        "base_url": "http://h:1/v1",
        "api_key": "k",
    }


def test_routing_target_skips_optional_fields_when_empty():
    t = RoutingTarget(provider="p", model="m")
    assert t.to_fallback_entry() == {"provider": "p", "model": "m"}


def test_decide_route_never_raises_on_garbage():
    # Garbage config values should not crash; the function returns a
    # primary decision for safety.
    d = decide_route(
        user_message="x",
        primary_provider="x",
        primary_model="x",
        config={"enabled": True, "smart_model": "not-a-dict", "cheap_model": 42},
    )
    assert d.route == "primary"


def test_routing_decision_dataclass_is_immutable():
    d = RoutingDecision(
        route="primary", target=None, reason="r", mode="disabled", classification="unknown"
    )
    with pytest.raises(Exception):
        d.route = "smart"  # type: ignore[misc]
