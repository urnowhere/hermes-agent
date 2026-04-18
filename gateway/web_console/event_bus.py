"""In-process GUI event bus primitives for the Hermes Web Console."""

from __future__ import annotations

import asyncio
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, DefaultDict


@dataclass(slots=True)
class GuiEvent:
    """A single event emitted for consumption by the web console."""

    type: str
    session_id: str
    run_id: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)
    ts: float = field(default_factory=time.time)


class GuiEventBus:
    """A lightweight in-process pub/sub bus namespaced by channel."""

    def __init__(self) -> None:
        self._subscribers: DefaultDict[str, set[asyncio.Queue[GuiEvent]]] = defaultdict(set)
        self._lock = asyncio.Lock()

    async def subscribe(self, channel: str) -> asyncio.Queue[GuiEvent]:
        """Create and register a subscriber queue for a channel."""
        queue: asyncio.Queue[GuiEvent] = asyncio.Queue()
        async with self._lock:
            self._subscribers[channel].add(queue)
        return queue

    async def unsubscribe(self, channel: str, queue: asyncio.Queue[GuiEvent]) -> None:
        """Remove a subscriber queue from a channel if it is still registered."""
        async with self._lock:
            subscribers = self._subscribers.get(channel)
            if not subscribers:
                return
            subscribers.discard(queue)
            if not subscribers:
                self._subscribers.pop(channel, None)

    async def publish(self, channel: str, event: GuiEvent) -> None:
        """Publish an event to all current subscribers for a channel."""
        async with self._lock:
            subscribers = list(self._subscribers.get(channel, ()))
            print(f"DEBUG: Publishing {event.type} to channel {channel}. Subscribers: {len(subscribers)}", flush=True)
            for queue in subscribers:
                queue.put_nowait(event)

    async def subscriber_count(self, channel: str) -> int:
        """Return the current number of subscribers for a channel."""
        async with self._lock:
            return len(self._subscribers.get(channel, ()))
