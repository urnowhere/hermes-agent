"""Helpers for building server-sent event streams for the web console."""

from __future__ import annotations

import asyncio
import contextlib
import json
from dataclasses import dataclass
from typing import Any, AsyncIterable

from aiohttp import web


@dataclass(slots=True)
class SseMessage:
    """An SSE message payload."""

    data: Any
    event: str | None = None
    id: str | None = None
    retry: int | None = None


SSE_HEADERS = {
    "Content-Type": "text/event-stream",
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}


def json_dumps(data: Any) -> str:
    """Serialize data for SSE payload lines."""
    return json.dumps(data, separators=(",", ":"), ensure_ascii=False)


def format_sse_message(message: SseMessage) -> bytes:
    """Encode a single SSE message frame."""
    lines: list[str] = []

    if message.event:
        lines.append(f"event: {message.event}")
    if message.id:
        lines.append(f"id: {message.id}")
    if message.retry is not None:
        lines.append(f"retry: {message.retry}")

    payload = json_dumps(message.data)
    for line in payload.splitlines() or [payload]:
        lines.append(f"data: {line}")

    return ("\n".join(lines) + "\n\n").encode("utf-8")


def format_sse_ping(comment: str = "ping") -> bytes:
    """Encode an SSE keepalive comment frame."""
    return f": {comment}\n\n".encode("utf-8")


async def _safe_sse_write(response: web.StreamResponse, chunk: bytes) -> bool:
    """Write an SSE chunk, returning False if the client disconnected."""
    try:
        await response.write(chunk)
    except (ConnectionResetError, RuntimeError):
        return False
    return True


async def stream_sse(
    request: web.Request,
    events: AsyncIterable[SseMessage],
    *,
    keepalive_interval: float = 15.0,
    ping_comment: str = "ping",
) -> web.StreamResponse:
    """Write an async iterable of SSE messages to the client with keepalives."""
    response = web.StreamResponse(status=200, headers=SSE_HEADERS)
    await response.prepare(request)

    iterator = aiter(events)
    next_message_task: asyncio.Task[SseMessage] | None = None
    while True:
        if next_message_task is None:
            next_message_task = asyncio.create_task(anext(iterator))
        done, _pending = await asyncio.wait({next_message_task}, timeout=keepalive_interval)
        if not done:
            if not await _safe_sse_write(response, format_sse_ping(ping_comment)):
                next_message_task.cancel()
                with contextlib.suppress(asyncio.CancelledError, StopAsyncIteration):
                    await next_message_task
                break
            continue

        try:
            message = next_message_task.result()
        except StopAsyncIteration:
            break
        finally:
            next_message_task = None

        if not await _safe_sse_write(response, format_sse_message(message)):
            break

    with contextlib.suppress(ConnectionResetError, RuntimeError):
        await response.write_eof()

    return response
