"""Regression tests for ACP Copilot conversation_history normalization.

Verifies that non-iterable types (e.g. SimpleNamespace) passed as
conversation_history are handled gracefully instead of crashing.

See: issue #11732
"""

from types import SimpleNamespace

import pytest

from run_agent import normalize_conversation_history


class TestConversationHistoryNormalization:
    """Tests for normalize_conversation_history()."""

    def test_normalizes_simplenamespace(self, caplog):
        """ACP Copilot can pass a SimpleNamespace instead of a list."""
        ns = SimpleNamespace(foo="bar", baz=123)
        result = normalize_conversation_history(ns)

        assert result == []
        assert any(
            "SimpleNamespace" in record.message and "not iterable" in record.message
            for record in caplog.records
        )

    def test_normalizes_none(self, caplog):
        """Passing None should be treated as an empty list."""
        result = normalize_conversation_history(None)
        assert result == []
        assert not any(
            "not iterable" in record.message for record in caplog.records
        )

    def test_preserves_valid_list(self):
        """A normal list of message dicts passes through unchanged."""
        messages = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi there"},
        ]
        result = normalize_conversation_history(messages)
        assert result == messages

    def test_preserves_empty_list(self):
        """An empty list remains an empty list."""
        result = normalize_conversation_history([])
        assert result == []

    def test_normalizes_non_iterable_int(self, caplog):
        """An int is not iterable and should be treated as empty with a warning."""
        result = normalize_conversation_history(42)
        assert result == []
        assert any(
            "int" in record.message and "not iterable" in record.message
            for record in caplog.records
        )

    def test_string_becomes_list_of_chars(self):
        """A bare string is iterable (chars) — list() works, documenting current behavior."""
        result = normalize_conversation_history("hello")
        assert result == list("hello")
