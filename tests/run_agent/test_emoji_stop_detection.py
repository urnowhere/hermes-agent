"""Tests for _has_natural_response_ending emoji/symbol handling (#14572).

Ollama/GLM stop-to-length heuristic false-triggers on emoji sign-offs
because the punctuation charset didn't include emoji characters.
"""
import pytest
from run_agent import AIAgent


class TestHasNaturalResponseEndingEmoji:
    """Emoji sign-offs must be recognized as natural endings (#14572)."""

    @pytest.mark.parametrize("text", [
        "Thank you! 🌙",
        "Done ✅",
        "Here's the result 👍",
        "All set! 🎉",
        "Check it out →",   # Sm — math symbol
        "Total: ¥",         # Sc — currency symbol
        "Rating: ★",        # So — other symbol
    ])
    def test_emoji_and_symbol_endings_are_natural(self, text):
        assert AIAgent._has_natural_response_ending(text) is True

    @pytest.mark.parametrize("text", [
        "Normal ending.",
        "Normal ending!",
        "Question?",
        'She said "hello"',
        "Code block ending```",
        "日本語の文。",
        "中文问号？",
    ])
    def test_punctuation_endings_still_work(self, text):
        assert AIAgent._has_natural_response_ending(text) is True

    @pytest.mark.parametrize("text", [
        "No ending here",
        "trailing word",
        "abc",
    ])
    def test_non_natural_endings(self, text):
        assert AIAgent._has_natural_response_ending(text) is False

    def test_empty_and_whitespace(self):
        assert AIAgent._has_natural_response_ending("") is False
        assert AIAgent._has_natural_response_ending("   ") is False
        assert AIAgent._has_natural_response_ending(None) is False
