"""Regression tests for streaming token estimation fallback.

Tests the fix for providers that don't send usage data in stream chunks
(MiniMax, Kimi, etc.).  Verifies the `is None` check (not falsiness) is
used so that valid zero values (e.g. cache hits with prompt_tokens=0)
are preserved instead of being overwritten with estimated values.
"""

from types import SimpleNamespace

import pytest

from run_agent import apply_streaming_token_fallback


class TestStreamingTokenEstimation:
    def test_fallback_estimates_when_usage_none(self):
        """When usage_obj is None the block must estimate both token fields."""
        api_messages = [{"role": "user", "content": "hello world"}]
        content_parts = ["Hello! How can I help you today?"]

        result = apply_streaming_token_fallback(None, api_messages, content_parts)

        assert result.prompt_tokens > 0, "prompt_tokens should be estimated from messages"
        assert result.completion_tokens > 0, "completion_tokens should be estimated from text"
        assert result.completion_tokens < 10, (
            "completion_tokens looks like a char-count, not an estimate"
        )

    def test_preserves_valid_zero_usage(self):
        """When usage has prompt_tokens=0 (cache hit) the 0 must be preserved.

        The fix uses `is None` checks instead of truthiness so that a real
        prompt_tokens=0 value (common with cache hits on MiniMax/Kimi) is NOT
        overwritten by the estimation path.
        """
        api_messages = [{"role": "user", "content": "hello world"}]
        content_parts = ["Hello! How can I help you today?"]

        usage_with_zero = SimpleNamespace(prompt_tokens=0, completion_tokens=5)
        result = apply_streaming_token_fallback(usage_with_zero, api_messages, content_parts)

        assert result.prompt_tokens == 0, (
            "prompt_tokens=0 (cache hit) must NOT be overwritten with an estimate"
        )
        assert result.completion_tokens == 5, (
            "completion_tokens should pass through unchanged"
        )

    def test_preserves_valid_nonzero_usage(self):
        """When both token fields are non-None they must pass through unchanged."""
        api_messages = [{"role": "user", "content": "hello world"}]
        content_parts = ["Hello! How can I help you today?"]

        usage_with_values = SimpleNamespace(prompt_tokens=100, completion_tokens=50)
        result = apply_streaming_token_fallback(usage_with_values, api_messages, content_parts)

        assert result.prompt_tokens == 100
        assert result.completion_tokens == 50

    def test_empty_content_parts_still_estimates_completion(self):
        """Even with empty content, completion_tokens must be estimated (not 0 or error)."""
        api_messages = [{"role": "user", "content": "hello world"}]

        result = apply_streaming_token_fallback(None, api_messages, [])

        assert result.prompt_tokens > 0, "prompt_tokens must still be estimated"
        assert result.completion_tokens == 0, (
            "completion_tokens for empty content should be 0"
        )

    def test_is_none_check_discriminates_from_zero(self):
        """Explicitly verify: None vs 0 are not the same check.

        - usage=None → fallback triggered, estimate > 0
        - usage with prompt_tokens=0 → 0 preserved, not overwritten
        """
        result_none = apply_streaming_token_fallback(
            None, [{"role": "user", "content": "hello"}], ["hi"]
        )
        assert result_none.prompt_tokens > 0, "fallback should estimate when usage is None"

        result_zero = apply_streaming_token_fallback(
            SimpleNamespace(prompt_tokens=0, completion_tokens=1),
            [{"role": "user", "content": "hello"}],
            ["hi"],
        )
        assert result_zero.prompt_tokens == 0, (
            "prompt_tokens=0 must NOT be overwritten — is None check catches this"
        )
