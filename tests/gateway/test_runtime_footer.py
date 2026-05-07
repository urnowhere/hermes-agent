"""Unit tests for gateway.runtime_footer — the opt-in runtime-metadata footer
appended to final gateway replies."""

from __future__ import annotations

import os

import pytest

from gateway.runtime_footer import (
    _home_relative_cwd,
    _model_short,
    build_footer_line,
    extract_search_traces_from_messages,
    format_runtime_footer,
    format_search_traces,
    resolve_footer_config,
)


# ---------------------------------------------------------------------------
# _model_short + _home_relative_cwd
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "model,expected",
    [
        ("openai/gpt-5.4", "gpt-5.4"),
        ("anthropic/claude-sonnet-4.6", "claude-sonnet-4.6"),
        ("gpt-5.4", "gpt-5.4"),
        ("", ""),
        (None, ""),
    ],
)
def test_model_short_drops_vendor_prefix(model, expected):
    assert _model_short(model) == expected


def test_home_relative_cwd_collapses_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    sub = tmp_path / "projects" / "hermes"
    sub.mkdir(parents=True)
    result = _home_relative_cwd(str(sub))
    assert result == "~/projects/hermes"


def test_home_relative_cwd_leaves_abs_path_alone(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "other"))
    result = _home_relative_cwd(str(tmp_path / "outside" / "dir"))
    assert result == str(tmp_path / "outside" / "dir")


def test_home_relative_cwd_empty_returns_empty():
    assert _home_relative_cwd("") == ""


# ---------------------------------------------------------------------------
# format_runtime_footer
# ---------------------------------------------------------------------------

def test_format_footer_all_fields(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("TERMINAL_CWD", str(tmp_path / "projects" / "hermes"))
    (tmp_path / "projects" / "hermes").mkdir(parents=True)
    out = format_runtime_footer(
        model="openrouter/openai/gpt-5.4",
        context_tokens=68000,
        context_length=100000,
        cwd=None,  # falls back to TERMINAL_CWD env var
        fields=("model", "context_pct", "cwd"),
    )
    assert out == "gpt-5.4 · 68% · ~/projects/hermes"


def test_format_footer_skips_missing_context_length():
    out = format_runtime_footer(
        model="openai/gpt-5.4",
        context_tokens=500,
        context_length=None,
        cwd="/tmp/wd",
        fields=("model", "context_pct", "cwd"),
    )
    # context_pct dropped silently; no "?%" artifact
    assert "%" not in out
    assert "gpt-5.4" in out
    assert "/tmp/wd" in out


def test_format_footer_context_pct_clamped_to_100():
    out = format_runtime_footer(
        model="m",
        context_tokens=500_000,  # way over
        context_length=100_000,
        cwd="",
        fields=("context_pct",),
    )
    assert out == "100%"


def test_format_footer_context_pct_never_negative():
    out = format_runtime_footer(
        model="m",
        context_tokens=-50,
        context_length=100,
        cwd="",
        fields=("context_pct",),
    )
    # Negative input => no field emitted (we require context_tokens >= 0)
    assert out == ""


def test_format_footer_empty_fields_returns_empty():
    out = format_runtime_footer(
        model="m", context_tokens=0, context_length=100,
        cwd="/x", fields=(),
    )
    assert out == ""


def test_format_footer_drops_cwd_when_empty(monkeypatch):
    monkeypatch.delenv("TERMINAL_CWD", raising=False)
    out = format_runtime_footer(
        model="openai/gpt-5.4",
        context_tokens=50, context_length=100,
        cwd="",
        fields=("model", "context_pct", "cwd"),
    )
    # cwd silently dropped; model + pct remain
    assert out == "gpt-5.4 · 50%"


def test_format_footer_custom_field_order():
    out = format_runtime_footer(
        model="openai/gpt-5.4",
        context_tokens=50, context_length=100,
        cwd="/opt/project",
        fields=("context_pct", "model"),  # swapped + no cwd
    )
    assert out == "50% · gpt-5.4"


def test_format_footer_unknown_field_silently_ignored():
    out = format_runtime_footer(
        model="openai/gpt-5.4",
        context_tokens=50, context_length=100,
        cwd="/x",
        fields=("model", "bogus", "context_pct"),
    )
    assert out == "gpt-5.4 · 50%"


def test_format_footer_renders_codex_native_search_trace():
    out = format_runtime_footer(
        model="openai/gpt-5.4",
        context_tokens=50,
        context_length=100,
        cwd="",
        fields=("search",),
        search_traces=[
            {
                "source": "provider_native",
                "provider": "codex",
                "tool": "web_search",
                "query": "finance: BTC",
                "status": "completed",
            }
        ],
    )
    assert out == "🔎 Codex search: finance: BTC"


def test_format_search_traces_summarizes_external_web_tool():
    out = format_search_traces([
        {
            "source": "hermes_tool",
            "provider": "tavily",
            "tool": "web_search",
            "query": "latest AI news",
            "result_count": 5,
            "status": "completed",
        }
    ])
    assert out == "🔎 Tavily search: latest AI news (5 results)"


def test_extract_search_traces_from_codex_and_web_search_tool_messages():
    messages = [
        {
            "role": "assistant",
            "content": "BTC price",
            "codex_web_search_items": [
                {
                    "type": "web_search_call",
                    "status": "completed",
                    "action": {"type": "search", "query": "finance: BTC"},
                }
            ],
        },
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {
                        "name": "web_search",
                        "arguments": '{"query": "latest AI news", "limit": 5}',
                    },
                }
            ],
        },
        {
            "role": "tool",
            "name": "web_search",
            "tool_call_id": "call_1",
            "content": '{"success": true, "data": {"web": [{"url": "https://example.com/a"}, {"url": "https://example.com/b"}]}}',
        },
    ]

    traces = extract_search_traces_from_messages(messages)

    assert traces == [
        {
            "kind": "search",
            "source": "provider_native",
            "provider": "codex",
            "tool": "web_search",
            "query": "finance: BTC",
            "queries": ["finance: BTC"],
            "status": "completed",
        },
        {
            "kind": "search",
            "source": "hermes_tool",
            "provider": "web",
            "tool": "web_search",
            "query": "latest AI news",
            "queries": ["latest AI news"],
            "result_count": 2,
            "status": "completed",
        },
    ]


def test_extract_search_traces_handles_codex_open_page_action():
    messages = [
        {"role": "user", "content": "open current source"},
        {
            "role": "assistant",
            "codex_web_search_items": [
                {
                    "type": "web_search_call",
                    "status": "completed",
                    "action": {"type": "open_page", "url": "https://example.com/story"},
                }
            ],
        },
    ]

    traces = extract_search_traces_from_messages(messages)

    assert traces == [
        {
            "kind": "extract",
            "source": "provider_native",
            "provider": "codex",
            "tool": "web_extract",
            "urls": ["https://example.com/story"],
            "status": "completed",
        }
    ]



def test_extract_search_traces_defaults_to_current_user_turn_only():
    messages = [
        {
            "role": "assistant",
            "codex_web_search_items": [
                {
                    "type": "web_search_call",
                    "status": "completed",
                    "action": {"type": "search", "query": "old BTC search"},
                }
            ],
        },
        {"role": "user", "content": "search current AI news"},
        {
            "role": "assistant",
            "codex_web_search_items": [
                {
                    "type": "web_search_call",
                    "status": "completed",
                    "action": {"type": "search", "query": "current AI news"},
                }
            ],
        },
    ]

    traces = extract_search_traces_from_messages(messages)

    assert len(traces) == 1
    assert traces[0]["query"] == "current AI news"


# ---------------------------------------------------------------------------
# resolve_footer_config
# ---------------------------------------------------------------------------

def test_resolve_defaults_off_empty_config():
    cfg = resolve_footer_config({}, "telegram")
    assert cfg == {"enabled": False, "fields": ["model", "context_pct", "cwd"]}


def test_resolve_global_enable():
    user = {"display": {"runtime_footer": {"enabled": True}}}
    cfg = resolve_footer_config(user, "telegram")
    assert cfg["enabled"] is True
    assert cfg["fields"] == ["model", "context_pct", "cwd"]


def test_resolve_platform_override_wins():
    user = {
        "display": {
            "runtime_footer": {"enabled": True, "fields": ["model"]},
            "platforms": {
                "slack": {"runtime_footer": {"enabled": False}},
            },
        },
    }
    # Telegram picks up the global enable
    assert resolve_footer_config(user, "telegram")["enabled"] is True
    # Slack overrides to off
    assert resolve_footer_config(user, "slack")["enabled"] is False


def test_resolve_platform_can_add_fields_only():
    user = {
        "display": {
            "runtime_footer": {"enabled": True},
            "platforms": {
                "discord": {"runtime_footer": {"fields": ["context_pct"]}},
            },
        },
    }
    tg = resolve_footer_config(user, "telegram")
    assert tg["enabled"] is True
    assert tg["fields"] == ["model", "context_pct", "cwd"]
    dc = resolve_footer_config(user, "discord")
    assert dc["enabled"] is True
    assert dc["fields"] == ["context_pct"]


def test_resolve_ignores_malformed_config():
    # Non-dict runtime_footer shouldn't crash
    user = {"display": {"runtime_footer": "on"}}
    cfg = resolve_footer_config(user, "telegram")
    assert cfg["enabled"] is False


# ---------------------------------------------------------------------------
# build_footer_line — top-level entry point used by gateway/run.py
# ---------------------------------------------------------------------------

def test_build_footer_empty_when_disabled():
    out = build_footer_line(
        user_config={},
        platform_key="telegram",
        model="openai/gpt-5.4",
        context_tokens=10, context_length=100,
        cwd="/tmp",
    )
    assert out == ""


def test_build_footer_returns_rendered_when_enabled(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    out = build_footer_line(
        user_config={"display": {"runtime_footer": {"enabled": True}}},
        platform_key="telegram",
        model="openai/gpt-5.4",
        context_tokens=25, context_length=100,
        cwd=str(tmp_path / "proj"),
    )
    (tmp_path / "proj").mkdir(exist_ok=True)
    assert "gpt-5.4" in out
    assert "25%" in out


def test_build_footer_per_platform_off_suppresses():
    user = {
        "display": {
            "runtime_footer": {"enabled": True},
            "platforms": {"slack": {"runtime_footer": {"enabled": False}}},
        },
    }
    out = build_footer_line(
        user_config=user,
        platform_key="slack",
        model="openai/gpt-5.4",
        context_tokens=10, context_length=100,
        cwd="/tmp",
    )
    assert out == ""


def test_build_footer_no_data_returns_empty_even_when_enabled():
    # Enabled, but context_length is None AND cwd empty AND model empty ⇒ no fields
    out = build_footer_line(
        user_config={"display": {"runtime_footer": {"enabled": True}}},
        platform_key="telegram",
        model="",
        context_tokens=0, context_length=None,
        cwd="",
    )
    # With no TERMINAL_CWD env either
    if not os.environ.get("TERMINAL_CWD"):
        assert out == ""


def test_build_footer_can_render_search_field_from_messages():
    out = build_footer_line(
        user_config={
            "display": {
                "runtime_footer": {"enabled": True, "fields": ["model", "search"]}
            }
        },
        platform_key="telegram",
        model="openai/gpt-5.4",
        context_tokens=0,
        context_length=None,
        cwd="",
        messages=[
            {
                "role": "assistant",
                "codex_web_search_items": [
                    {
                        "type": "web_search_call",
                        "status": "completed",
                        "action": {"type": "search", "query": "finance: BTC"},
                    }
                ],
            }
        ],
    )
    assert out == "gpt-5.4 · 🔎 Codex search: finance: BTC"
