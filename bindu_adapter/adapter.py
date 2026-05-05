"""Hermes ``AIAgent`` wrapped as a Bindu A2A handler.

Bindu's contract is simple: ``handler(messages) -> str``. Bindu replays the
full conversation history on every call; the handler only needs to feed the
newest user message into Hermes. Keeping a single long-lived ``AIAgent`` per
process lets the provider's prompt cache stay valid across turns — Bindu is
the source of truth for history, Hermes owns the live model state for caching.

Safety tiers control which Hermes toolsets the A2A-exposed agent can reach.
Default is ``read``: web search + extract only. ``full`` exposes everything
(terminal, filesystem, code exec, browser). NEVER combine ``full`` with a
public tunnel.
"""

from __future__ import annotations

import os
from typing import Any

from run_agent import AIAgent

# Toolset tiers. ``None`` means "no restriction" (full tier — all toolsets enabled).
# Keep this in sync with Hermes' toolset names in ``agent/toolsets/``.
TIERS: dict[str, list[str] | None] = {
    "read": ["web"],
    "sandbox": ["web", "file", "moa"],
    "full": None,
}

_agent: AIAgent | None = None


def get_agent() -> AIAgent:
    """Lazily create one shared ``AIAgent`` per process.

    Created on first call, reused for every subsequent A2A request in this
    process. Swap out the knobs below with env vars in ``entry.py`` rather
    than editing this function.
    """
    global _agent
    if _agent is None:
        tier = os.getenv("HERMES_BINDU_TIER", "read")
        _agent = AIAgent(
            model=os.getenv("HERMES_BINDU_MODEL", "anthropic/claude-3.5-haiku"),
            max_iterations=int(os.getenv("HERMES_BINDU_MAX_ITERATIONS", "30")),
            enabled_toolsets=TIERS.get(tier, TIERS["read"]),
            quiet_mode=True,
            platform="bindu",
            save_trajectories=False,
            skip_memory=True,
            persist_session=False,
        )
    return _agent


def _last_user_text(messages: list[dict[str, Any]]) -> str:
    """Pull the newest user message's text, tolerating the A2A parts shape.

    A2A messages arrive with ``content`` either as a plain string or as a
    list of ``{"kind": "text", "text": "..."}`` parts. We accept both and
    concatenate text parts with newlines.
    """
    for m in reversed(messages):
        if m.get("role") != "user":
            continue
        content = m.get("content", "")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            return "\n".join(
                p.get("text", "")
                for p in content
                if isinstance(p, dict) and p.get("kind") == "text"
            )
    return ""


def handler(messages: list[dict[str, Any]]) -> str:
    """Bindu handler contract: (messages) -> response string.

    Bindu wraps the return value in a DID-signed artifact and delivers it
    back to the caller via the A2A protocol.
    """
    text = _last_user_text(messages)
    if not text.strip():
        return "Empty message."
    return get_agent().chat(text)
