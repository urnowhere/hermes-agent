"""Regression tests for optional focus_topic compression hints."""

from unittest.mock import MagicMock

import pytest

from run_agent import AIAgent


def _make_agent_with_engine(engine):
    agent = object.__new__(AIAgent)
    agent.context_compressor = engine
    agent.session_id = "sess-1"
    agent.model = "test-model"
    agent.platform = "cli"
    agent.logs_dir = MagicMock()
    agent.quiet_mode = True
    agent._todo_store = MagicMock()
    agent._todo_store.format_for_injection.return_value = ""
    agent._memory_manager = None
    agent._session_db = None
    agent._cached_system_prompt = None
    agent.tools = []
    agent.log_prefix = ""
    agent._vprint = lambda *a, **kw: None
    agent._last_flushed_db_idx = 0
    agent._last_compression_summary_warning = None
    agent._emit_warning = lambda *a, **kw: None
    agent._invalidate_system_prompt = lambda *a, **kw: None
    agent._build_system_prompt = lambda *a, **kw: "new-system-prompt"
    agent.commit_memory_session = lambda *a, **kw: None
    return agent


def test_compress_context_skips_focus_topic_for_legacy_engine():
    """Older plugins without focus_topic in compress() signature don't crash."""
    captured_kwargs = []

    class _StrictOldPluginEngine:
        name = "strict-old"
        compression_count = 0
        last_prompt_tokens = 0
        last_completion_tokens = 0

        def compress(self, messages, current_tokens=None):
            captured_kwargs.append({"current_tokens": current_tokens})
            return [messages[0], messages[-1]]

    engine = _StrictOldPluginEngine()
    agent = _make_agent_with_engine(engine)
    messages = [
        {"role": "user", "content": "one"},
        {"role": "assistant", "content": "two"},
        {"role": "user", "content": "three"},
        {"role": "assistant", "content": "four"},
    ]

    compressed, _ = agent._compress_context(
        messages,
        "system prompt",
        approx_tokens=100,
        focus_topic="database schema",
    )

    assert compressed == [messages[0], messages[-1]]
    assert captured_kwargs == [{"current_tokens": 100}]


def test_compress_context_passes_focus_topic_to_kwargs_engine():
    """Engines accepting **kwargs still receive the focus hint."""
    captured_kwargs = []

    class _KwargsEngine:
        name = "kwargs"
        compression_count = 0
        last_prompt_tokens = 0
        last_completion_tokens = 0

        def compress(self, messages, **kwargs):
            captured_kwargs.append(kwargs)
            return list(messages)

    engine = _KwargsEngine()
    agent = _make_agent_with_engine(engine)
    messages = [{"role": "user", "content": "hello"}]

    agent._compress_context(
        messages,
        "system prompt",
        approx_tokens=1234,
        focus_topic="database schema",
    )

    assert captured_kwargs == [
        {"current_tokens": 1234, "focus_topic": "database schema"}
    ]


def test_compress_context_does_not_mask_internal_type_errors():
    """Internal TypeErrors should not be retried as signature mismatch."""

    class _FailingEngine:
        name = "failing"
        compression_count = 0
        last_prompt_tokens = 0
        last_completion_tokens = 0

        def compress(self, messages, current_tokens=None, focus_topic=None):
            raise TypeError("internal compression bug")

    agent = _make_agent_with_engine(_FailingEngine())

    with pytest.raises(TypeError, match="internal compression bug"):
        agent._compress_context(
            [{"role": "user", "content": "hello"}],
            "system prompt",
            approx_tokens=1234,
            focus_topic="database schema",
        )
