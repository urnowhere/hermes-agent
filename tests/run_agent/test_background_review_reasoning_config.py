"""Regression test for #18871.

When the parent ``AIAgent`` has a configured ``reasoning_config`` (e.g. an
``xhigh`` reasoning effort), spawning a background memory/skill review must
pass that config into the forked review agent — otherwise the review
inherits the transport's default ``{"effort": "medium", "summary": "auto"}``
and silently downgrades the user's configured effort.

This pins the same runtime-inheritance contract that #16006/#15884/#13076
already established for ``base_url``, ``api_key``, ``api_mode``.
"""

from unittest.mock import patch


def _make_agent_stub(agent_cls, reasoning_config):
    """Minimal AIAgent-like stub with just enough state for
    ``_spawn_background_review`` to reach the ``AIAgent(...)`` call site."""
    agent = object.__new__(agent_cls)
    agent.model = "test-model"
    agent.platform = "test"
    agent.provider = "openai"
    agent.session_id = "sess-123"
    agent.quiet_mode = True
    agent._memory_store = None
    agent._memory_enabled = True
    agent._user_profile_enabled = False
    agent._memory_nudge_interval = 5
    agent._skill_nudge_interval = 5
    agent.background_review_callback = None
    agent.status_callback = None
    agent.reasoning_config = reasoning_config
    agent._credential_pool = None
    agent._MEMORY_REVIEW_PROMPT = "review memory"
    agent._SKILL_REVIEW_PROMPT = "review skills"
    agent._COMBINED_REVIEW_PROMPT = "review both"
    return agent


class _SyncThread:
    """Drop-in replacement for ``threading.Thread`` that runs the target inline,
    so the patched ``AIAgent.__init__`` capture happens before the test exits."""

    def __init__(self, *, target=None, daemon=None, name=None):
        self._target = target

    def start(self):
        if self._target:
            self._target()


def _spawn_and_capture(reasoning_config):
    """Run ``_spawn_background_review`` and capture the kwargs the review fork
    would have been initialized with."""
    import run_agent

    agent = _make_agent_stub(run_agent.AIAgent, reasoning_config)
    captured: dict = {}

    def _capture_init(self, *args, **kwargs):
        captured.update(kwargs)
        raise RuntimeError("stop after capturing init args")

    # ``_current_main_runtime`` is called inside the spawn to forward live
    # provider/auth state. Stub it to a benign empty mapping.
    with patch.object(run_agent.AIAgent, "__init__", _capture_init), \
         patch("threading.Thread", _SyncThread), \
         patch.object(
             run_agent.AIAgent,
             "_current_main_runtime",
             lambda self: {},
             create=False,
         ):
        agent._spawn_background_review(
            messages_snapshot=[],
            review_memory=True,
            review_skills=False,
        )

    return captured


def test_background_review_agent_inherits_reasoning_config():
    """A configured reasoning_config on the parent must reach the review
    agent's constructor — otherwise the fork falls back to medium effort."""
    parent_reasoning = {"effort": "xhigh", "summary": "detailed"}
    captured = _spawn_and_capture(parent_reasoning)

    assert "reasoning_config" in captured, (
        "AIAgent.__init__ was called but reasoning_config was not passed — "
        "regression of #18871 (review fork drops parent reasoning config)"
    )
    assert captured["reasoning_config"] == parent_reasoning


def test_background_review_agent_inherits_none_reasoning_config():
    """When the parent has no reasoning_config (None), the review agent must
    receive None too — not be silently bound to a default. Pre-fix the kwarg
    was missing entirely; post-fix it is present and equal to None."""
    captured = _spawn_and_capture(None)
    assert "reasoning_config" in captured
    assert captured["reasoning_config"] is None
