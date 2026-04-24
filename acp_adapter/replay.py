"""Translate a persisted Hermes conversation history into ACP session updates.

The ACP spec requires ``session/load`` to stream the prior conversation back to
the client via ``session_update`` notifications before resolving, so editors
such as Zed can reconstruct the thread after a reconnect. The live-prompt
callbacks in :mod:`acp_adapter.events` and the tool-call builders in
:mod:`acp_adapter.tools` already know how to translate each event type into an
ACP payload; this module reuses those helpers against stored messages.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Iterable, List, Mapping

import acp

from .tools import build_tool_complete, build_tool_start, make_tool_call_id

logger = logging.getLogger(__name__)


def _parse_tool_arguments(raw: Any) -> dict:
    """Normalize persisted OpenAI-style tool arguments into a plain dict."""
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw:
        try:
            parsed = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return {"raw": raw}
        if isinstance(parsed, dict):
            return parsed
        return {"raw": parsed}
    return {}


def build_replay_notifications(history: Iterable[Mapping[str, Any]]) -> List[Any]:
    """Return ACP session_update notifications mirroring a persisted history.

    The returned list preserves message order and reuses the same builder
    helpers the live event callbacks use, so replayed updates are
    indistinguishable from fresh events on the wire:

    * ``user`` messages → :func:`acp.update_user_message_text`
    * ``assistant`` reasoning → :func:`acp.update_agent_thought_text`
    * ``assistant`` text → :func:`acp.update_agent_message_text`
    * ``assistant`` tool_calls → :func:`build_tool_start` (paired with the
      matching ``tool`` result via :func:`build_tool_complete`)

    Entries with no recoverable payload are skipped rather than aborting the
    replay — the goal is best-effort reconstruction, not strict fidelity.
    """
    updates: List[Any] = []

    for msg in history:
        if not isinstance(msg, Mapping):
            continue
        role = msg.get("role")
        content = msg.get("content")
        text = str(content).strip() if content else ""

        if role == "user":
            if text:
                updates.append(acp.update_user_message_text(text))
            continue

        if role == "assistant":
            reasoning = msg.get("reasoning") or msg.get("reasoning_content")
            if reasoning:
                reasoning_text = str(reasoning).strip()
                if reasoning_text:
                    updates.append(acp.update_agent_thought_text(reasoning_text))
            if text:
                updates.append(acp.update_agent_message_text(text))

            tool_calls = msg.get("tool_calls") or []
            if isinstance(tool_calls, list):
                for call in tool_calls:
                    if not isinstance(call, Mapping):
                        continue
                    fn = call.get("function") or {}
                    if not isinstance(fn, Mapping):
                        fn = {}
                    name = str(fn.get("name") or call.get("name") or "").strip()
                    if not name:
                        continue
                    tc_id = str(call.get("id") or "").strip() or make_tool_call_id()
                    args = _parse_tool_arguments(fn.get("arguments") or call.get("arguments"))
                    updates.append(build_tool_start(tc_id, name, args))
            continue

        if role == "tool":
            tc_id = str(msg.get("tool_call_id") or "").strip()
            if not tc_id:
                continue
            tool_name = str(msg.get("tool_name") or msg.get("name") or "").strip() or "tool"
            updates.append(build_tool_complete(tc_id, tool_name, result=text or None))
            continue

    return updates
