"""Unit tests for gateway.response_prefix — the opt-in response prefix
prepended to the first gateway reply."""

from __future__ import annotations

import pytest

from gateway.response_prefix import (
    _model_short,
    build_prefix_line,
    interpolate_prefix_template,
    resolve_prefix_config,
)


# ---------------------------------------------------------------------------
# _model_short
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "model,expected",
    [
        ("github-copilot/claude-opus-4.6", "claude-opus-4.6"),
        ("openai-codex/gpt-5.4", "gpt-5.4"),
        ("anthropic/claude-sonnet-4.6", "claude-sonnet-4.6"),
        ("gpt-5.4", "gpt-5.4"),  # no prefix
        ("", ""),
        (None, ""),
    ],
)
def test_model_short_drops_vendor_prefix(model, expected):
    assert _model_short(model) == expected


# ---------------------------------------------------------------------------
# interpolate_prefix_template
# ---------------------------------------------------------------------------

def test_interpolate_all_variables():
    out = interpolate_prefix_template(
        "[{provider}/{model}]",
        model="github-copilot/claude-opus-4.6",
        provider="github-copilot",
        thinking="high",
    )
    assert out == "[github-copilot/claude-opus-4.6]"


def test_interpolate_short_model():
    out = interpolate_prefix_template(
        "[{model}]",
        model="github-copilot/claude-opus-4.6",
        provider="github-copilot",
    )
    assert out == "[claude-opus-4.6]"


def test_interpolate_model_full():
    out = interpolate_prefix_template(
        "{modelFull}",
        model="github-copilot/claude-opus-4.6",
        provider="github-copilot",
    )
    assert out == "github-copilot/claude-opus-4.6"


def test_interpolate_provider_only():
    out = interpolate_prefix_template(
        "via {provider}",
        model="github-copilot/claude-opus-4.6",
        provider="github-copilot",
    )
    assert out == "via github-copilot"


def test_interpolate_thinking_variants():
    # Case-insensitive variable names
    for var in ("thinking", "thinkingLevel", "thinking_level"):
        out = interpolate_prefix_template(
            f"{{{var}}}",
            thinking="high",
        )
        assert out == "high"


def test_interpolate_unresolved_vars_remain_literal():
    out = interpolate_prefix_template(
        "[{model}] {unknown}",
        model="openai/gpt-5.4",
    )
    assert out == "[gpt-5.4] {unknown}"


def test_interpolate_missing_values_remain_literal():
    out = interpolate_prefix_template(
        "[{provider}/{model}]",
        model=None,
        provider=None,
    )
    # Both unresolved → remain as literal text
    assert out == "[{provider}/{model}]"


def test_interpolate_partial_resolution():
    out = interpolate_prefix_template(
        "[{provider}/{model}]",
        model="openai/gpt-5.4",
        provider=None,
    )
    # model resolves, provider doesn't
    assert out == "[{provider}/gpt-5.4]"


def test_interpolate_with_surrounding_text():
    out = interpolate_prefix_template(
        "🤖 {model} │ ",
        model="github-copilot/claude-opus-4.6",
    )
    assert out == "🤖 claude-opus-4.6 │ "


def test_interpolate_no_variables_returns_as_is():
    out = interpolate_prefix_template(
        "[HERMES] ",
        model="openai/gpt-5.4",
    )
    assert out == "[HERMES] "


def test_interpolate_empty_template():
    assert interpolate_prefix_template("", model="x") == ""


def test_interpolate_case_insensitive():
    # Variable names are case-insensitive
    out = interpolate_prefix_template(
        "[{MODEL}/{PROVIDER}]",
        model="openai/gpt-5.4",
        provider="openai",
    )
    assert out == "[gpt-5.4/openai]"


# ---------------------------------------------------------------------------
# resolve_prefix_config
# ---------------------------------------------------------------------------

def test_resolve_defaults_off_empty_config():
    cfg = resolve_prefix_config({}, "telegram")
    assert cfg == {"enabled": False, "template": ""}


def test_resolve_global_string_template():
    user = {"messages": {"response_prefix": "[{provider}/{model}] "}}
    cfg = resolve_prefix_config(user, "telegram")
    assert cfg["enabled"] is True
    assert cfg["template"] == "[{provider}/{model}] "


def test_resolve_global_empty_string_disabled():
    user = {"messages": {"response_prefix": ""}}
    cfg = resolve_prefix_config(user, "telegram")
    assert cfg["enabled"] is False
    assert cfg["template"] == ""


def test_resolve_global_dict_format():
    user = {"messages": {"response_prefix": {"enabled": True, "template": "[{model}] "}}}
    cfg = resolve_prefix_config(user, "telegram")
    assert cfg["enabled"] is True
    assert cfg["template"] == "[{model}] "


def test_resolve_platform_override_wins():
    user = {
        "messages": {
            "response_prefix": "[{model}] ",
            "platforms": {
                "slack": {"response_prefix": "[SLACK {model}] "},
            },
        },
    }
    # Telegram picks up the global template
    tg = resolve_prefix_config(user, "telegram")
    assert tg["template"] == "[{model}] "
    # Slack overrides
    sl = resolve_prefix_config(user, "slack")
    assert sl["template"] == "[SLACK {model}] "


def test_resolve_platform_can_disable():
    user = {
        "messages": {
            "response_prefix": "[{model}] ",
            "platforms": {
                "slack": {"response_prefix": ""},
            },
        },
    }
    tg = resolve_prefix_config(user, "telegram")
    assert tg["enabled"] is True
    sl = resolve_prefix_config(user, "slack")
    assert sl["enabled"] is False


def test_resolve_platform_dict_format():
    user = {
        "messages": {
            "response_prefix": "[{model}] ",
            "platforms": {
                "discord": {"response_prefix": {"enabled": True, "template": "[DC {model}] "}},
            },
        },
    }
    dc = resolve_prefix_config(user, "discord")
    assert dc["enabled"] is True
    assert dc["template"] == "[DC {model}] "


def test_resolve_ignores_malformed_config():
    # Non-dict/non-string response_prefix shouldn't crash
    user = {"messages": {"response_prefix": 123}}
    cfg = resolve_prefix_config(user, "telegram")
    assert cfg["enabled"] is False


def test_resolve_no_messages_key():
    cfg = resolve_prefix_config({}, "telegram")
    assert cfg == {"enabled": False, "template": ""}


# ---------------------------------------------------------------------------
# build_prefix_line — top-level entry point used by gateway/run.py
# ---------------------------------------------------------------------------

def test_build_prefix_empty_when_disabled():
    out = build_prefix_line(
        user_config={},
        platform_key="telegram",
        model="openai/gpt-5.4",
        provider="openai",
    )
    assert out == ""


def test_build_prefix_returns_rendered_when_enabled():
    out = build_prefix_line(
        user_config={"messages": {"response_prefix": "[{provider}/{model}] "}},
        platform_key="telegram",
        model="openai/gpt-5.4",
        provider="openai",
    )
    assert out == "[openai/gpt-5.4] "


def test_build_prefix_per_platform_suppresses():
    user = {
        "messages": {
            "response_prefix": "[{model}] ",
            "platforms": {"slack": {"response_prefix": ""}},
        },
    }
    out = build_prefix_line(
        user_config=user,
        platform_key="slack",
        model="openai/gpt-5.4",
        provider="openai",
    )
    assert out == ""


def test_build_prefix_provider_from_config():
    """Provider derived from config model.provider when agent result lacks it."""
    config = {
        "messages": {"response_prefix": "[{provider}/{model}]"},
        "model": {"provider": "alibaba", "default": "qwen3.6-plus"},
    }
    out = build_prefix_line(
        user_config=config,
        platform_key=None,
        model="qwen3.6-plus",
        provider="",
    )
    assert out == "[alibaba/qwen3.6-plus]"


def test_build_prefix_no_model_provider_returns_template():
    # Template variables remain literal when no data
    out = build_prefix_line(
        user_config={"messages": {"response_prefix": "[{provider}/{model}] "}},
        platform_key="telegram",
        model=None,
        provider=None,
    )
    # Unresolved vars remain as literal text
    assert out == "[{provider}/{model}] "


def test_build_prefix_thinking_level():
    out = build_prefix_line(
        user_config={"messages": {"response_prefix": "[{model} | think:{thinking}] "}},
        platform_key="telegram",
        model="openai/gpt-5.4",
        thinking="high",
    )
    assert out == "[gpt-5.4 | think:high] "
