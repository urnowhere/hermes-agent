"""Post-compression token estimate must include tools schema overhead (#14695).

Before the fix, _compress_context computed the post-compression token
estimate with estimate_tokens_rough(system) + estimate_messages_tokens_rough(msgs),
completely ignoring the tools schema. With 50+ tools, schemas alone can add
20-30K tokens — a blind spot that led to under-reporting and premature
re-compression triggers.

The fix switches to estimate_request_tokens_rough() which already accepts a
``tools`` parameter, matching what the API call actually sends.
"""

import pytest
from unittest.mock import MagicMock, patch

from run_agent import AIAgent
from agent.model_metadata import (
    estimate_tokens_rough,
    estimate_messages_tokens_rough,
    estimate_request_tokens_rough,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_tool_defs(*names: str) -> list:
    return [
        {
            "type": "function",
            "function": {
                "name": n,
                "description": f"A tool that does {n} things with arguments",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "arg1": {"type": "string", "description": f"First argument for {n}"},
                        "arg2": {"type": "integer", "description": f"Second argument for {n}"},
                    },
                },
            },
        }
        for n in names
    ]


# Generate a sizeable tool list to make the schema overhead meaningful
_TOOL_NAMES = [f"tool_{i}" for i in range(30)]
_TOOLS = _make_tool_defs(*_TOOL_NAMES)


@pytest.fixture()
def agent():
    with (
        patch("run_agent.get_tool_definitions", return_value=list(_TOOLS)),
        patch("run_agent.check_toolset_requirements", return_value={}),
        patch("run_agent.OpenAI"),
    ):
        a = AIAgent(
            api_key="test-key-1234567890",
            base_url="https://openrouter.ai/api/v1",
            quiet_mode=True,
            skip_context_files=True,
            skip_memory=True,
        )
        a.client = MagicMock()
        a._cached_system_prompt = "You are helpful."
        a._use_prompt_caching = False
        a.tool_delay = 0
        a.compression_enabled = False
        a.save_trajectories = False
        return a


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestCompressionTokenEstimateIncludesTools:
    """Verify that post-compression token estimate counts tools schema."""

    def test_estimate_request_includes_tools_overhead(self):
        """estimate_request_tokens_rough with tools > without tools."""
        system = "You are helpful."
        msgs = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi there"},
        ]
        est_without = (
            estimate_tokens_rough(system)
            + estimate_messages_tokens_rough(msgs)
        )
        est_with = estimate_request_tokens_rough(
            msgs, system_prompt=system, tools=_TOOLS,
        )
        # With 30 tools the schema adds thousands of chars -> hundreds of tokens
        assert est_with > est_without, (
            f"Expected tools overhead to increase estimate: "
            f"with_tools={est_with} vs without={est_without}"
        )
        # Verify the overhead is substantial (30 tool schemas ~= 2000+ tokens)
        assert est_with - est_without > 1000, (
            f"Tools overhead too small ({est_with - est_without}) for 30 tools"
        )

    def test_compress_context_sets_estimate_with_tools(self, agent):
        """After _compress_context, last_prompt_tokens must reflect tools."""
        system_prompt = "You are helpful."
        messages = [
            {"role": "user", "content": "first question"},
            {"role": "assistant", "content": "first answer"},
            {"role": "user", "content": "second question"},
            {"role": "assistant", "content": "second answer"},
        ]

        compressed_msgs = [{"role": "user", "content": "summary of conversation"}]
        # _build_system_prompt produces the actual system prompt used in
        # the estimate; mock it to a known value for deterministic assertions.
        known_sys = "You are a test assistant."

        with (
            patch.object(
                agent.context_compressor,
                "compress",
                return_value=compressed_msgs,
            ),
            patch.object(agent, "_build_system_prompt", return_value=known_sys),
            patch.object(agent, "_invalidate_system_prompt"),
            patch.object(agent, "_session_db", None),
            patch.object(agent, "commit_memory_session"),
            patch.object(agent, "flush_memories"),
        ):
            agent._compress_context(messages, system_prompt)

        actual_est = agent.context_compressor.last_prompt_tokens

        # Compute what the OLD (buggy) estimate would have been -- no tools
        old_est = (
            estimate_tokens_rough(known_sys)
            + estimate_messages_tokens_rough(compressed_msgs)
        )
        # The fix should produce a LARGER estimate because tools are now counted
        assert actual_est > old_est, (
            f"Post-compression estimate should include tools overhead: "
            f"actual={actual_est} vs old_no_tools={old_est}"
        )

        # Verify the estimate matches what estimate_request_tokens_rough gives
        expected = estimate_request_tokens_rough(
            compressed_msgs, system_prompt=known_sys, tools=agent.tools,
        )
        assert actual_est == expected, (
            f"Estimate mismatch: actual={actual_est} vs expected={expected}"
        )

    def test_compress_context_no_tools_still_works(self, agent):
        """When tools is None/empty, the estimate should still be correct."""
        agent.tools = None
        system_prompt = "You are helpful."
        messages = [
            {"role": "user", "content": "question"},
            {"role": "assistant", "content": "answer"},
        ]
        compressed_msgs = [{"role": "user", "content": "summary"}]
        known_sys = "You are a test assistant."

        with (
            patch.object(
                agent.context_compressor,
                "compress",
                return_value=compressed_msgs,
            ),
            patch.object(agent, "_build_system_prompt", return_value=known_sys),
            patch.object(agent, "_invalidate_system_prompt"),
            patch.object(agent, "_session_db", None),
            patch.object(agent, "commit_memory_session"),
            patch.object(agent, "flush_memories"),
        ):
            agent._compress_context(messages, system_prompt)

        actual_est = agent.context_compressor.last_prompt_tokens
        expected = estimate_request_tokens_rough(
            compressed_msgs, system_prompt=known_sys, tools=None,
        )
        assert actual_est == expected
