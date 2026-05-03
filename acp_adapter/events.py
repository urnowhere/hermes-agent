"""Callback factories for bridging AIAgent events to ACP notifications.

Each factory returns a callable with the signature that AIAgent expects
for its callbacks. Internally, the callbacks push ACP session updates
to the client via ``conn.session_update()`` using
``asyncio.run_coroutine_threadsafe()`` (since AIAgent runs in a worker
thread while the event loop lives on the main thread).
"""

import asyncio
import json
import logging
from collections import deque
from typing import Any, Callable, Deque, Dict

import acp

from .tools import (
    build_tool_complete,
    build_tool_start,
    make_tool_call_id,
)

logger = logging.getLogger(__name__)


def _send_update(
    conn: acp.Client,
    session_id: str,
    loop: asyncio.AbstractEventLoop,
    update: Any,
) -> None:
    """Fire-and-forget an ACP session update from a worker thread."""
    try:
        future = asyncio.run_coroutine_threadsafe(
            conn.session_update(session_id, update), loop
        )
        future.result(timeout=5)
    except Exception:
        logger.debug("Failed to send ACP update", exc_info=True)


# ------------------------------------------------------------------
# Tool progress callback
# ------------------------------------------------------------------

def make_tool_progress_cb(
    conn: acp.Client,
    session_id: str,
    loop: asyncio.AbstractEventLoop,
    tool_call_ids: Dict[str, Deque[str]],
    tool_call_meta: Dict[str, Dict[str, Any]],
) -> Callable:
    """Create a ``tool_progress_callback`` for AIAgent.

    Signature expected by AIAgent::

        tool_progress_callback(event_type: str, name: str, preview: str, args: dict, **kwargs)

    Emits ``ToolCallStart`` for ``tool.started`` events and tracks IDs in a FIFO
    queue per tool name so duplicate/parallel same-name calls still complete
    against the correct ACP tool call.  Other event types (``tool.completed``,
    ``reasoning.available``) are silently ignored.
    """

    def _tool_progress(event_type: str, name: str = None, preview: str = None, args: Any = None, **kwargs) -> None:
        # Only emit ACP ToolCallStart for tool.started; ignore other event types
        if event_type != "tool.started":
            return
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except (json.JSONDecodeError, TypeError):
                args = {"raw": args}
        if not isinstance(args, dict):
            args = {}

        tc_id = make_tool_call_id()
        queue = tool_call_ids.get(name)
        if queue is None:
            queue = deque()
            tool_call_ids[name] = queue
        elif isinstance(queue, str):
            queue = deque([queue])
            tool_call_ids[name] = queue
        queue.append(tc_id)

        snapshot = None
        if name in {"write_file", "patch", "skill_manage"}:
            try:
                from agent.display import capture_local_edit_snapshot

                snapshot = capture_local_edit_snapshot(name, args)
            except Exception:
                logger.debug("Failed to capture ACP edit snapshot for %s", name, exc_info=True)
        tool_call_meta[tc_id] = {"args": args, "snapshot": snapshot}

        update = build_tool_start(tc_id, name, args)
        _send_update(conn, session_id, loop, update)

    return _tool_progress


# ------------------------------------------------------------------
# Thinking callback
# ------------------------------------------------------------------

def make_thinking_cb(
    conn: acp.Client,
    session_id: str,
    loop: asyncio.AbstractEventLoop,
) -> Callable:
    """Create a ``thinking_callback`` for AIAgent."""

    def _thinking(text: str) -> None:
        if not text:
            return
        update = acp.update_agent_thought_text(text)
        _send_update(conn, session_id, loop, update)

    return _thinking


# ------------------------------------------------------------------
# Step callback
# ------------------------------------------------------------------

def make_step_cb(
    conn: acp.Client,
    session_id: str,
    loop: asyncio.AbstractEventLoop,
    tool_call_ids: Dict[str, Deque[str]],
    tool_call_meta: Dict[str, Dict[str, Any]],
) -> Callable:
    """Create a ``step_callback`` for AIAgent.

    Signature expected by AIAgent::

        step_callback(api_call_count: int, prev_tools: list)
    """

    def _step(api_call_count: int, prev_tools: Any = None) -> None:
        if prev_tools and isinstance(prev_tools, list):
            for tool_info in prev_tools:
                tool_name = None
                result = None
                function_args = None

                if isinstance(tool_info, dict):
                    tool_name = tool_info.get("name") or tool_info.get("function_name")
                    result = tool_info.get("result") or tool_info.get("output")
                    function_args = tool_info.get("arguments") or tool_info.get("args")
                elif isinstance(tool_info, str):
                    tool_name = tool_info

                queue = tool_call_ids.get(tool_name or "")
                if isinstance(queue, str):
                    queue = deque([queue])
                    tool_call_ids[tool_name] = queue
                if tool_name and queue:
                    tc_id = queue.popleft()
                    meta = tool_call_meta.pop(tc_id, {})
                    update = build_tool_complete(
                        tc_id,
                        tool_name,
                        result=str(result) if result is not None else None,
                        function_args=function_args or meta.get("args"),
                        snapshot=meta.get("snapshot"),
                    )
                    _send_update(conn, session_id, loop, update)
                    if not queue:
                        tool_call_ids.pop(tool_name, None)

    return _step


# ------------------------------------------------------------------
# Agent message callback
# ------------------------------------------------------------------

def make_streaming_cbs(
    conn: acp.Client,
    session_id: str,
    loop: asyncio.AbstractEventLoop,
    batch_ms: int = 500,
) -> tuple[Callable, Callable, Callable]:
    """Create streaming callbacks with a shared ordered queue.

    Thinking and text chunks arrive in model-defined order. We batch them
    together every batch_ms, preserving the original sequence. Returns
    (thinking_cb, message_cb, flush_fn).
    """
    import threading, time
    import asyncio

    # (type, text, timestamp) — type: "think" or "text"
    queue: list[tuple[str, str, float]] = []
    queue_lock = threading.Lock()
    timer: threading.Timer | None = None

    def _flush() -> None:
        nonlocal timer
        with queue_lock:
            if not queue:
                timer = None
                return
            # Take all pending, preserve order
            batch = queue[:]
            queue.clear()
            timer = None
        # Build and send all events in order
        for kind, text, _ts in batch:
            if kind == "think":
                update = acp.update_agent_thought_text(text)
            else:
                update = acp.update_agent_message_text(text)
            _send_update(conn, session_id, loop, update)

    def _start_timer() -> None:
        nonlocal timer
        if timer is not None:
            return
        timer = threading.Timer(batch_ms / 1000.0, _flush)
        timer.daemon = True
        timer.start()

    def _enqueue(kind: str, text: str) -> None:
        if not text:
            return
        with queue_lock:
            queue.append((kind, text, time.monotonic()))
            _start_timer()

    def thinking_cb(text: str) -> None:
        _enqueue("think", text)

    def message_cb(text: str) -> None:
        _enqueue("text", text)

    async def flush_fn() -> None:
        nonlocal timer
        with queue_lock:
            if timer:
                timer.cancel()
                timer = None
            if not queue:
                return
            batch = queue[:]
            queue.clear()
        # Send directly (no _send_update here — we're in async, await for ordering)
        for kind, text, _ts in batch:
            if kind == "think":
                update = acp.update_agent_thought_text(text)
            else:
                update = acp.update_agent_message_text(text)
            await conn.session_update(session_id, update)

    return thinking_cb, message_cb, flush_fn
