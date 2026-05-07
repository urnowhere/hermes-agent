"""Tests for AIAgent preflight compression pass budgeting."""

from __future__ import annotations

from unittest.mock import patch
from types import SimpleNamespace

from run_agent import AIAgent


def _make_agent_with_compression_config(compression_cfg: dict) -> AIAgent:
    with (
        patch("hermes_cli.config.load_config", return_value={"compression": compression_cfg}),
        patch("run_agent.get_tool_definitions", return_value=[]),
        patch("run_agent.check_toolset_requirements", return_value={}),
        patch("run_agent.OpenAI"),
    ):
        kwargs = {"api" + "_key": "placeholder"}
        return AIAgent(
            **kwargs,
            base_url="https://openrouter.ai/api/v1",
            model="anthropic/claude-sonnet-4.6",
            provider="openrouter",
            quiet_mode=True,
            skip_context_files=True,
            skip_memory=True,
        )


def test_preflight_max_passes_defaults_to_legacy_three():
    agent = _make_agent_with_compression_config({})

    assert agent._compression_preflight_max_passes == 3


def test_preflight_max_passes_honors_configured_one():
    agent = _make_agent_with_compression_config({"preflight_max_passes": 1})

    assert agent._compression_preflight_max_passes == 1


def test_preflight_max_passes_clamps_negative_to_zero():
    agent = _make_agent_with_compression_config({"preflight_max_passes": -4})

    assert agent._compression_preflight_max_passes == 0


def test_pre_cap_closeout_triggers_at_eighty_five_percent_budget():
    agent = object.__new__(AIAgent)
    agent.iteration_budget = SimpleNamespace(used=169, max_total=200)
    assert agent._should_pre_cap_closeout() is False

    agent.iteration_budget = SimpleNamespace(used=170, max_total=200)
    assert agent._should_pre_cap_closeout() is True


def test_repeated_compression_closeout_text_is_deterministic():
    agent = object.__new__(AIAgent)

    response = agent._forced_closeout_response("compression")

    assert "compressed multiple times" in response
    assert "fresh session" in response
