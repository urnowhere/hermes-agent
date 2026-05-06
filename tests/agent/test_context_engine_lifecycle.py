"""Layer 2: Integration tests for context-engine lifecycle hooks.

Verifies that AIAgent._notify_ingest and _notify_after_turn forward
calls to the active context engine at the right times.
"""

import pytest
from typing import Any, Dict, List
from unittest.mock import MagicMock

from agent.context_engine import ContextEngine
from tests.agent.test_context_engine import StubEngine


# ---------------------------------------------------------------------------
# Tracking engine
# ---------------------------------------------------------------------------

class TrackingStubEngine(StubEngine):
    """StubEngine that records ingest_message and after_turn calls."""

    def __init__(self, context_length=200000, threshold_pct=0.50):
        super().__init__(context_length, threshold_pct)
        self.ingest_calls: List[tuple] = []
        self.after_turn_calls: List[tuple] = []

    def ingest_message(self, message: Dict[str, Any], token_budget: int = 0) -> None:
        self.ingest_calls.append((message.get("role"), message.get("content", "")[:20], token_budget))

    def after_turn(self, messages: List[Dict[str, Any]], token_budget: int = 0, session_file: str = "") -> None:
        self.after_turn_calls.append((len(messages), token_budget, session_file))


# ---------------------------------------------------------------------------
# _notify_ingest / _notify_after_turn unit tests
# ---------------------------------------------------------------------------

class TestNotifyIngest:
    """Verify AIAgent._notify_ingest delegates correctly."""

    def test_forwards_to_engine(self):
        from run_agent import AIAgent
        agent = AIAgent.__new__(AIAgent)
        agent.context_compressor = TrackingStubEngine()

        msg = {"role": "user", "content": "hello"}
        agent._notify_ingest(msg)

        engine = agent.context_compressor
        assert len(engine.ingest_calls) == 1
        assert engine.ingest_calls[0][0] == "user"
        assert engine.ingest_calls[0][1] == "hello"
        assert engine.ingest_calls[0][2] == 200000  # context_length

    def test_silently_ignores_none_compressor(self):
        from run_agent import AIAgent
        agent = AIAgent.__new__(AIAgent)
        agent.context_compressor = None
        # Should not raise
        agent._notify_ingest({"role": "user", "content": "hi"})

    def test_silently_ignores_engine_without_hook(self):
        from run_agent import AIAgent
        agent = AIAgent.__new__(AIAgent)
        agent.context_compressor = StubEngine()  # no override
        # Should not raise
        agent._notify_ingest({"role": "user", "content": "hi"})

    def test_silently_ignores_hook_exception(self):
        from run_agent import AIAgent
        agent = AIAgent.__new__(AIAgent)
        bad_engine = TrackingStubEngine()
        bad_engine.ingest_message = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
        agent.context_compressor = bad_engine
        # Should not raise
        agent._notify_ingest({"role": "user", "content": "hi"})


class TestNotifyAfterTurn:
    """Verify AIAgent._notify_after_turn delegates correctly."""

    def test_forwards_to_engine(self):
        from run_agent import AIAgent
        agent = AIAgent.__new__(AIAgent)
        agent.context_compressor = TrackingStubEngine()

        msgs = [{"role": "user", "content": "hi"}]
        agent._notify_after_turn(msgs)

        engine = agent.context_compressor
        assert len(engine.after_turn_calls) == 1
        assert engine.after_turn_calls[0] == (1, 200000, "")

    def test_silently_ignores_none_compressor(self):
        from run_agent import AIAgent
        agent = AIAgent.__new__(AIAgent)
        agent.context_compressor = None
        agent._notify_after_turn([])

    def test_silently_ignores_engine_without_hook(self):
        from run_agent import AIAgent
        agent = AIAgent.__new__(AIAgent)
        agent.context_compressor = StubEngine()
        agent._notify_after_turn([])

    def test_silently_ignores_hook_exception(self):
        from run_agent import AIAgent
        agent = AIAgent.__new__(AIAgent)
        bad_engine = TrackingStubEngine()
        bad_engine.after_turn = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
        agent.context_compressor = bad_engine
        agent._notify_after_turn([])


# ---------------------------------------------------------------------------
# End-to-end stub: verify call ordering
# ---------------------------------------------------------------------------

class TestCallOrdering:
    """Simulate a mini conversation turn and verify hook ordering."""

    def test_ingest_ordering(self):
        engine = TrackingStubEngine()
        engine.context_length = 100000

        # Simulate user message
        user_msg = {"role": "user", "content": "hello"}
        engine.ingest_message(user_msg)

        # Simulate assistant response
        assistant_msg = {"role": "assistant", "content": "hi there"}
        engine.ingest_message(assistant_msg)

        # Simulate tool result
        tool_msg = {"role": "tool", "content": "result", "tool_call_id": "tc1"}
        engine.ingest_message(tool_msg)

        assert len(engine.ingest_calls) == 3
        assert engine.ingest_calls[0][0] == "user"
        assert engine.ingest_calls[1][0] == "assistant"
        assert engine.ingest_calls[2][0] == "tool"

    def test_after_turn_receives_full_messages(self):
        engine = TrackingStubEngine()
        msgs = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi"},
        ]
        engine.after_turn(msgs, token_budget=50000, session_file="/tmp/s.jsonl")

        assert len(engine.after_turn_calls) == 1
        assert engine.after_turn_calls[0] == (2, 50000, "/tmp/s.jsonl")
