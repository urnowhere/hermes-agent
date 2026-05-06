"""Regression test for #18742.

Kimi / Moonshot models routed through an aggregator base URL (synthetic.new,
OpenRouter, Together, …) must still trigger the Kimi-specific output
budget defaults — otherwise the agent emits empty responses on long agentic
prompts because the aggregator's tiny default ``max_tokens`` is consumed
entirely by Kimi's hidden reasoning before any visible token is produced.

The fix wires ``is_moonshot_model`` (which already recognizes aggregator-
prefixed slugs) into the ``_is_kimi`` runtime detection in
``run_agent.AIAgent._build_api_kwargs`` so detection is no longer
base-URL-only. This test pins that wiring.
"""

import inspect

from agent.moonshot_schema import is_moonshot_model


# is_moonshot_model has its own positive/negative tests in
# tests/agent/test_moonshot_schema.py; the cases below are the *exact* slugs
# the bug report calls out, asserted as a regression baseline so a future
# change to is_moonshot_model that drops them will surface here too.
def test_aggregator_prefixed_kimi_slugs_are_recognized_as_moonshot():
    assert is_moonshot_model("hf:moonshotai/Kimi-K2.5") is True
    assert is_moonshot_model("nous/moonshotai/kimi-k2.5") is True
    assert is_moonshot_model("openrouter/moonshotai/kimi-k2.5") is True
    # Bare slugs the existing flag path already covered should stay True.
    assert is_moonshot_model("kimi-k2.5") is True
    # Non-Moonshot model on an aggregator must still be False.
    assert is_moonshot_model("openai/gpt-5") is False


def test_build_api_kwargs_consults_is_moonshot_model_for_kimi_detection():
    """The base-URL-only ``_is_kimi`` check missed every aggregator that
    routes to Moonshot inference (#18742). The fix adds
    ``is_moonshot_model(self.model)`` to the ``_is_kimi`` OR chain.

    This is a source-level guard rather than a full integration test
    because ``_build_api_kwargs`` is a 500+-line method whose other
    branches require extensive agent state to exercise — the relevant
    change is a single OR clause that we can verify is present.
    """
    import run_agent

    src = inspect.getsource(run_agent.AIAgent._build_api_kwargs)
    # Either the import alias or the imported name itself must appear inside
    # the function body — we don't pin a single spelling.
    assert "is_moonshot_model" in src or "_is_moonshot" in src, (
        "regression #18742: _build_api_kwargs no longer references "
        "is_moonshot_model — Kimi/Moonshot models routed through aggregator "
        "base URLs will silently lose the max_tokens=32000 / "
        "reasoning_effort=medium defaults and emit empty responses."
    )
