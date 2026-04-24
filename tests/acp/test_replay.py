"""Unit tests for acp_adapter.replay.build_replay_notifications."""

from acp.schema import (
    AgentMessageChunk,
    AgentThoughtChunk,
    ToolCallProgress,
    ToolCallStart,
    UserMessageChunk,
)

from acp_adapter.replay import build_replay_notifications


# ---------------------------------------------------------------------------
# Basic role handling
# ---------------------------------------------------------------------------


class TestBasicRoles:
    def test_empty_history_returns_empty_list(self):
        assert build_replay_notifications([]) == []

    def test_user_message_emits_user_chunk(self):
        updates = build_replay_notifications(
            [{"role": "user", "content": "hello"}]
        )
        assert len(updates) == 1
        assert isinstance(updates[0], UserMessageChunk)

    def test_assistant_text_emits_agent_chunk(self):
        updates = build_replay_notifications(
            [{"role": "assistant", "content": "hi there"}]
        )
        assert len(updates) == 1
        assert isinstance(updates[0], AgentMessageChunk)

    def test_order_is_preserved(self):
        updates = build_replay_notifications([
            {"role": "user", "content": "first"},
            {"role": "assistant", "content": "second"},
            {"role": "user", "content": "third"},
            {"role": "assistant", "content": "fourth"},
        ])
        types = [type(u).__name__ for u in updates]
        assert types == [
            "UserMessageChunk",
            "AgentMessageChunk",
            "UserMessageChunk",
            "AgentMessageChunk",
        ]


# ---------------------------------------------------------------------------
# Skip conditions
# ---------------------------------------------------------------------------


class TestSkipConditions:
    def test_empty_content_user_is_skipped(self):
        assert build_replay_notifications(
            [{"role": "user", "content": ""}]
        ) == []

    def test_whitespace_only_content_is_skipped(self):
        assert build_replay_notifications(
            [{"role": "user", "content": "   \n\t"}]
        ) == []

    def test_none_content_on_assistant_without_tools_emits_nothing(self):
        # Assistant turns that contain only tool_calls may have content=None;
        # when there are also no tool_calls, emit nothing.
        assert build_replay_notifications(
            [{"role": "assistant", "content": None}]
        ) == []

    def test_system_messages_are_skipped(self):
        # System prompts aren't part of the ACP conversation stream.
        assert build_replay_notifications(
            [{"role": "system", "content": "you are a helpful assistant"}]
        ) == []

    def test_non_mapping_entries_are_skipped(self):
        assert build_replay_notifications(
            [None, "not a message", 42, {"role": "user", "content": "real"}]
        ) == [u for u in build_replay_notifications(
            [{"role": "user", "content": "real"}]
        )]

    def test_unknown_role_is_skipped(self):
        assert build_replay_notifications(
            [{"role": "bogus", "content": "nope"}]
        ) == []


# ---------------------------------------------------------------------------
# Reasoning / thinking
# ---------------------------------------------------------------------------


class TestReasoning:
    def test_assistant_reasoning_precedes_text(self):
        updates = build_replay_notifications([{
            "role": "assistant",
            "content": "final answer",
            "reasoning": "let me think...",
        }])
        assert len(updates) == 2
        assert isinstance(updates[0], AgentThoughtChunk)
        assert isinstance(updates[1], AgentMessageChunk)

    def test_reasoning_content_used_when_reasoning_absent(self):
        updates = build_replay_notifications([{
            "role": "assistant",
            "content": "answer",
            "reasoning_content": "internal monologue",
        }])
        assert any(isinstance(u, AgentThoughtChunk) for u in updates)

    def test_reasoning_without_content_still_emits(self):
        # Pure reasoning turn with no visible text (happens during
        # multi-step reasoning-first providers).
        updates = build_replay_notifications([{
            "role": "assistant",
            "content": None,
            "reasoning": "just thinking",
        }])
        assert len(updates) == 1
        assert isinstance(updates[0], AgentThoughtChunk)

    def test_empty_reasoning_does_not_emit_thought_chunk(self):
        updates = build_replay_notifications([{
            "role": "assistant",
            "content": "answer",
            "reasoning": "   ",
        }])
        assert all(not isinstance(u, AgentThoughtChunk) for u in updates)


# ---------------------------------------------------------------------------
# Tool calls
# ---------------------------------------------------------------------------


class TestToolCalls:
    def test_assistant_tool_call_emits_start(self):
        updates = build_replay_notifications([{
            "role": "assistant",
            "content": None,
            "tool_calls": [{
                "id": "call_abc",
                "type": "function",
                "function": {
                    "name": "terminal",
                    "arguments": '{"command": "ls"}',
                },
            }],
        }])
        starts = [u for u in updates if isinstance(u, ToolCallStart)]
        assert len(starts) == 1
        assert starts[0].tool_call_id == "call_abc"

    def test_tool_result_emits_progress_with_matching_id(self):
        updates = build_replay_notifications([
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [{
                    "id": "call_xyz",
                    "type": "function",
                    "function": {"name": "read_file", "arguments": '{"path": "x"}'},
                }],
            },
            {
                "role": "tool",
                "content": "file contents here",
                "tool_call_id": "call_xyz",
                "tool_name": "read_file",
            },
        ])
        starts = [u for u in updates if isinstance(u, ToolCallStart)]
        progress = [u for u in updates if isinstance(u, ToolCallProgress)]
        assert len(starts) == 1
        assert len(progress) == 1
        assert starts[0].tool_call_id == progress[0].tool_call_id == "call_xyz"

    def test_tool_result_without_id_is_skipped(self):
        # A tool result without tool_call_id can't be correlated to a start
        # event; emitting a stray progress notification would dangle.
        updates = build_replay_notifications([
            {"role": "tool", "content": "orphan", "tool_name": "terminal"},
        ])
        assert updates == []

    def test_tool_call_with_malformed_arguments_is_handled(self):
        updates = build_replay_notifications([{
            "role": "assistant",
            "content": None,
            "tool_calls": [{
                "id": "call_bad",
                "function": {"name": "terminal", "arguments": "not-valid-json"},
            }],
        }])
        starts = [u for u in updates if isinstance(u, ToolCallStart)]
        assert len(starts) == 1
        assert starts[0].tool_call_id == "call_bad"

    def test_tool_call_without_id_generates_fallback(self):
        updates = build_replay_notifications([{
            "role": "assistant",
            "content": None,
            "tool_calls": [{
                "function": {"name": "terminal", "arguments": "{}"},
            }],
        }])
        starts = [u for u in updates if isinstance(u, ToolCallStart)]
        assert len(starts) == 1
        assert starts[0].tool_call_id  # non-empty

    def test_tool_call_without_name_is_skipped(self):
        updates = build_replay_notifications([{
            "role": "assistant",
            "content": None,
            "tool_calls": [{"id": "call_nameless", "function": {}}],
        }])
        assert updates == []

    def test_assistant_text_and_tool_calls_both_emit(self):
        updates = build_replay_notifications([{
            "role": "assistant",
            "content": "let me check that file",
            "tool_calls": [{
                "id": "call_1",
                "function": {"name": "read_file", "arguments": '{"path": "x"}'},
            }],
        }])
        assert any(isinstance(u, AgentMessageChunk) for u in updates)
        assert any(isinstance(u, ToolCallStart) for u in updates)

    def test_tool_calls_list_of_dict_args_is_handled(self):
        # Providers may hand us a dict (already-parsed) rather than a JSON str.
        updates = build_replay_notifications([{
            "role": "assistant",
            "content": None,
            "tool_calls": [{
                "id": "call_parsed",
                "function": {"name": "terminal", "arguments": {"command": "pwd"}},
            }],
        }])
        starts = [u for u in updates if isinstance(u, ToolCallStart)]
        assert len(starts) == 1


# ---------------------------------------------------------------------------
# Realistic conversation
# ---------------------------------------------------------------------------


class TestRealisticConversation:
    def test_full_conversation_roundtrip(self):
        history = [
            {"role": "user", "content": "run `pwd`"},
            {
                "role": "assistant",
                "content": None,
                "reasoning": "I'll run pwd.",
                "tool_calls": [{
                    "id": "call_1",
                    "function": {"name": "terminal", "arguments": '{"command": "pwd"}'},
                }],
            },
            {
                "role": "tool",
                "content": "/home/user",
                "tool_call_id": "call_1",
                "tool_name": "terminal",
            },
            {"role": "assistant", "content": "You're in /home/user."},
            {"role": "user", "content": "thanks"},
        ]
        updates = build_replay_notifications(history)
        types = [type(u).__name__ for u in updates]
        assert types == [
            "UserMessageChunk",       # "run pwd"
            "AgentThoughtChunk",      # reasoning
            "ToolCallStart",          # terminal call_1
            "ToolCallProgress",       # tool result for call_1
            "AgentMessageChunk",      # "You're in /home/user."
            "UserMessageChunk",       # "thanks"
        ]
