"""Tests for the retired bare tool-tail auto-continue heuristic (#4493).

Hermes used to infer an interrupted turn when the persisted transcript ended
with a ``tool`` message.  That was ambiguous in long-lived messaging sessions:
a stale tool-tail could revive an unrelated old task after a later gateway
restart.  Current restart recovery is covered by
``test_restart_resume_pending.py`` and requires an explicit fresh
``resume_pending`` marker instead.
"""


def _simulate_auto_continue(agent_history: list, user_message: str) -> str:
    """Mirror the current bare tool-tail policy from _run_agent().

    A trailing tool result alone no longer prepends a continuation note.  The
    explicit ``resume_pending`` path is tested separately because it depends on
    SessionEntry metadata, not just agent_history shape.
    """
    return user_message


class TestBareToolTailDoesNotAutoContinue:
    """Trailing tool results are preserved as history, not recovery triggers."""

    def test_trailing_tool_result_does_not_trigger_note(self):
        history = [
            {"role": "user", "content": "deploy the app"},
            {"role": "assistant", "content": None, "tool_calls": [
                {"id": "call_1", "function": {"name": "terminal", "arguments": "{}"}}
            ]},
            {"role": "tool", "tool_call_id": "call_1", "content": "deployed successfully"},
        ]
        result = _simulate_auto_continue(history, "what happened?")
        assert result == "what happened?"
        assert "[System note:" not in result

    def test_trailing_assistant_message_no_note(self):
        history = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "Hi there!"},
        ]
        result = _simulate_auto_continue(history, "how are you?")
        assert result == "how are you?"

    def test_empty_history_no_note(self):
        result = _simulate_auto_continue([], "hello")
        assert result == "hello"

    def test_trailing_user_message_no_note(self):
        history = [
            {"role": "user", "content": "hello"},
        ]
        result = _simulate_auto_continue(history, "hello again")
        assert result == "hello again"

    def test_multiple_tool_results_do_not_trigger_note(self):
        history = [
            {"role": "user", "content": "search and read"},
            {"role": "assistant", "content": None, "tool_calls": [
                {"id": "call_1", "function": {"name": "search", "arguments": "{}"}},
                {"id": "call_2", "function": {"name": "read", "arguments": "{}"}},
            ]},
            {"role": "tool", "tool_call_id": "call_1", "content": "found it"},
            {"role": "tool", "tool_call_id": "call_2", "content": "file content here"},
        ]
        result = _simulate_auto_continue(history, "continue")
        assert result == "continue"

    def test_original_message_preserved_without_note(self):
        history = [
            {"role": "assistant", "content": None, "tool_calls": [
                {"id": "c1", "function": {"name": "t", "arguments": "{}"}}
            ]},
            {"role": "tool", "tool_call_id": "c1", "content": "done"},
        ]
        result = _simulate_auto_continue(history, "now do X")
        assert result == "now do X"
