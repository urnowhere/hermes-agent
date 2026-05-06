"""Regression test: vLLM-served thinking models with inline <think> replay.

vLLM (and other OpenAI-compat endpoints like MiniMax M2.7) require prior
assistant turns to carry reasoning *inline* in the ``content`` field as
``<think>reasoning</think>`` rather than a separate top-level
``reasoning_content`` key that these endpoints silently ignore.

MiniMax's documentation mandates this shape for Interleaved Thinking:

    "You must preserve the model's thinking content completely, i.e.
    <think>reasoning_content</think>. This is essential to ensure
    Interleaved Thinking works effectively."

Users opt in via ``embeds_reasoning_in_content: true`` on their
``custom_providers`` entry.  Without this flag the default path is
unchanged — ``reasoning_content`` is promoted to the top-level field as
before.

Fixes issue #20577.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from run_agent import AIAgent


def _make_agent(provider: str = "custom", base_url: str = "http://vllm:8000/v1") -> AIAgent:
    agent = object.__new__(AIAgent)
    agent.provider = provider
    agent.model = "MiniMax-M2.7"
    agent.base_url = base_url
    agent.verbose_logging = False
    agent.reasoning_callback = None
    agent.stream_delta_callback = None
    agent._stream_callback = None
    return agent


def _patch_custom_provider(base_url: str, embeds: bool):
    """Patch load_config / get_compatible_custom_providers to return a fake entry."""
    fake_entry = {
        "name": "vllm-test",
        "base_url": base_url,
        "embeds_reasoning_in_content": embeds,
    }

    def _fake_load_config():
        return {"custom_providers": [fake_entry]}

    def _fake_get_compat(cfg):
        return cfg.get("custom_providers", [])

    return patch.multiple(
        "run_agent",
        **{},
    ), patch(
        "hermes_cli.config.load_config", side_effect=_fake_load_config
    ), patch(
        "hermes_cli.config.get_compatible_custom_providers",
        side_effect=_fake_get_compat,
    )


# ---------------------------------------------------------------------------
# _active_custom_provider_embeds_reasoning
# ---------------------------------------------------------------------------

class TestActiveCustomProviderEmbedsReasoning:
    def test_returns_false_for_non_custom_provider(self):
        """Non-custom providers never use inline think blocks."""
        agent = _make_agent(provider="deepseek", base_url="https://api.deepseek.com")
        # Even if deepseek had such a flag we should never see it via this path
        assert agent._active_custom_provider_embeds_reasoning() is False

    def test_returns_false_when_flag_absent(self):
        """custom provider without the flag → False (default path unchanged)."""
        base_url = "http://vllm:8000/v1"
        agent = _make_agent(base_url=base_url)
        fake_entry = {"name": "vllm-test", "base_url": base_url}

        with patch("hermes_cli.config.load_config", return_value={"custom_providers": [fake_entry]}), \
             patch("hermes_cli.config.get_compatible_custom_providers", return_value=[fake_entry]):
            assert agent._active_custom_provider_embeds_reasoning() is False

    def test_returns_true_when_flag_set(self):
        """custom provider with embeds_reasoning_in_content: true → True."""
        base_url = "http://vllm:8000/v1"
        agent = _make_agent(base_url=base_url)
        fake_entry = {
            "name": "vllm-test",
            "base_url": base_url,
            "embeds_reasoning_in_content": True,
        }

        with patch("hermes_cli.config.load_config", return_value={"custom_providers": [fake_entry]}), \
             patch("hermes_cli.config.get_compatible_custom_providers", return_value=[fake_entry]):
            assert agent._active_custom_provider_embeds_reasoning() is True

    def test_url_matching_strips_trailing_slash(self):
        """base_url comparison normalises trailing slashes."""
        base_url = "http://vllm:8000/v1/"
        agent = _make_agent(base_url=base_url)
        fake_entry = {
            "name": "vllm-test",
            "base_url": "http://vllm:8000/v1",  # no trailing slash in config
            "embeds_reasoning_in_content": True,
        }

        with patch("hermes_cli.config.load_config", return_value={"custom_providers": [fake_entry]}), \
             patch("hermes_cli.config.get_compatible_custom_providers", return_value=[fake_entry]):
            assert agent._active_custom_provider_embeds_reasoning() is True

    def test_returns_false_on_config_error(self):
        """Config load errors silently fall back to False — no crash."""
        agent = _make_agent()
        with patch("hermes_cli.config.load_config", side_effect=Exception("disk error")):
            assert agent._active_custom_provider_embeds_reasoning() is False


# ---------------------------------------------------------------------------
# _copy_reasoning_content_for_api — embeds_reasoning_in_content path
# ---------------------------------------------------------------------------

class TestCopyReasoningInlineEmbed:
    def _agent_with_embed(self, base_url: str = "http://vllm:8000/v1") -> AIAgent:
        agent = _make_agent(base_url=base_url)
        fake_entry = {
            "name": "vllm-test",
            "base_url": base_url,
            "embeds_reasoning_in_content": True,
        }
        agent._fake_entry = fake_entry
        return agent

    def _call(self, agent: AIAgent, source_msg: dict, api_msg: dict | None = None) -> dict:
        if api_msg is None:
            api_msg = {k: v for k, v in source_msg.items()}
        fake_entry = agent._fake_entry
        with patch("hermes_cli.config.load_config", return_value={"custom_providers": [fake_entry]}), \
             patch("hermes_cli.config.get_compatible_custom_providers", return_value=[fake_entry]):
            agent._copy_reasoning_content_for_api(source_msg, api_msg)
        return api_msg

    def test_reasoning_content_embedded_inline(self):
        """reasoning_content is prepended as <think>…</think> in content."""
        agent = self._agent_with_embed()
        source = {
            "role": "assistant",
            "content": "The answer is 42.",
            "reasoning_content": "Let me think step by step…",
        }
        api_msg = {"role": "assistant", "content": "The answer is 42."}
        result = self._call(agent, source, api_msg)

        assert "<think>" in result["content"]
        assert "Let me think step by step" in result["content"]
        assert "The answer is 42." in result["content"]
        # Must NOT set top-level reasoning_content
        assert "reasoning_content" not in result

    def test_reasoning_field_used_when_no_reasoning_content(self):
        """Falls back to 'reasoning' field when 'reasoning_content' is absent."""
        agent = self._agent_with_embed()
        source = {
            "role": "assistant",
            "content": "Here is my answer.",
            "reasoning": "My internal chain-of-thought.",
        }
        api_msg = {"role": "assistant", "content": "Here is my answer."}
        result = self._call(agent, source, api_msg)

        assert "<think>\nMy internal chain-of-thought.\n</think>" in result["content"]
        assert "reasoning_content" not in result

    def test_no_reasoning_leaves_content_unchanged(self):
        """No reasoning → content is passed through untouched."""
        agent = self._agent_with_embed()
        source = {
            "role": "assistant",
            "content": "Plain response, no thinking.",
        }
        api_msg = {"role": "assistant", "content": "Plain response, no thinking."}
        result = self._call(agent, source, api_msg)

        assert result["content"] == "Plain response, no thinking."
        assert "reasoning_content" not in result

    def test_existing_reasoning_content_key_removed(self):
        """Any reasoning_content already on api_msg is stripped for embed providers."""
        agent = self._agent_with_embed()
        source = {
            "role": "assistant",
            "content": "Answer.",
            "reasoning_content": "Thinking…",
        }
        api_msg = {
            "role": "assistant",
            "content": "Answer.",
            "reasoning_content": "should be removed",
        }
        result = self._call(agent, source, api_msg)

        assert "reasoning_content" not in result
        assert "<think>" in result["content"]

    def test_non_assistant_role_is_skipped(self):
        """Non-assistant messages are ignored (unchanged behaviour)."""
        agent = self._agent_with_embed()
        source = {"role": "user", "content": "Hello"}
        api_msg = {"role": "user", "content": "Hello"}
        result = self._call(agent, source, api_msg)

        assert result == {"role": "user", "content": "Hello"}

    def test_think_block_ordering(self):
        """<think> block is prepended before the visible content, not appended."""
        agent = self._agent_with_embed()
        source = {
            "role": "assistant",
            "content": "Final answer.",
            "reasoning_content": "Step 1: consider options…",
        }
        api_msg = {"role": "assistant", "content": "Final answer."}
        result = self._call(agent, source, api_msg)

        think_pos = result["content"].index("<think>")
        answer_pos = result["content"].index("Final answer.")
        assert think_pos < answer_pos, "Reasoning block must precede the visible answer"


# ---------------------------------------------------------------------------
# _copy_reasoning_content_for_api — default path unaffected
# ---------------------------------------------------------------------------

class TestDefaultPathUnchanged:
    """Verify that providers without the flag still use reasoning_content."""

    def test_deepseek_still_uses_reasoning_content_field(self):
        """DeepSeek (non-custom) still promotes reasoning to reasoning_content."""
        agent = object.__new__(AIAgent)
        agent.provider = "deepseek"
        agent.model = "deepseek-v4"
        agent.base_url = "https://api.deepseek.com"
        agent.verbose_logging = False
        agent.reasoning_callback = None
        agent.stream_delta_callback = None
        agent._stream_callback = None

        source = {
            "role": "assistant",
            "content": "DeepSeek answer.",
            "reasoning": "DeepSeek chain-of-thought.",
        }
        api_msg = {"role": "assistant", "content": "DeepSeek answer."}
        agent._copy_reasoning_content_for_api(source, api_msg)

        assert api_msg.get("reasoning_content") == "DeepSeek chain-of-thought."
        # content must NOT have <think> embedded
        assert "<think>" not in api_msg.get("content", "")

    def test_custom_provider_without_flag_uses_reasoning_content(self):
        """Custom provider without embeds_reasoning_in_content uses default path."""
        agent = object.__new__(AIAgent)
        agent.provider = "custom"
        agent.model = "some-model"
        agent.base_url = "http://custom-endpoint/v1"
        agent.verbose_logging = False
        agent.reasoning_callback = None
        agent.stream_delta_callback = None
        agent._stream_callback = None

        fake_entry = {
            "name": "custom-no-embed",
            "base_url": "http://custom-endpoint/v1",
            # no embeds_reasoning_in_content key
        }

        source = {
            "role": "assistant",
            "content": "Answer.",
            "reasoning": "Thinking.",
        }
        api_msg = {"role": "assistant", "content": "Answer."}

        with patch("hermes_cli.config.load_config", return_value={"custom_providers": [fake_entry]}), \
             patch("hermes_cli.config.get_compatible_custom_providers", return_value=[fake_entry]):
            agent._copy_reasoning_content_for_api(source, api_msg)

        assert api_msg.get("reasoning_content") == "Thinking."
        assert "<think>" not in api_msg.get("content", "")
