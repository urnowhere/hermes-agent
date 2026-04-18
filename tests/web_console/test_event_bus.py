"""Tests for the Hermes Web Console event bus and SSE helpers."""

from __future__ import annotations

import asyncio

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from gateway.web_console.event_bus import GuiEvent, GuiEventBus
from gateway.web_console.sse import SseMessage, format_sse_message, format_sse_ping, stream_sse


class TestGuiEventBus:
    @pytest.mark.asyncio
    async def test_subscribe_publish_preserves_order(self):
        bus = GuiEventBus()
        queue = await bus.subscribe("session-1")

        first = GuiEvent(type="run.started", session_id="session-1", run_id="run-1", payload={"step": 1})
        second = GuiEvent(type="run.output", session_id="session-1", run_id="run-1", payload={"step": 2})
        third = GuiEvent(type="run.finished", session_id="session-1", run_id="run-1", payload={"step": 3})

        await bus.publish("session-1", first)
        await bus.publish("session-1", second)
        await bus.publish("session-1", third)

        assert await queue.get() is first
        assert await queue.get() is second
        assert await queue.get() is third

    @pytest.mark.asyncio
    async def test_unsubscribe_disconnect_stops_delivery(self):
        bus = GuiEventBus()
        queue = await bus.subscribe("session-1")

        await bus.unsubscribe("session-1", queue)
        await bus.publish(
            "session-1",
            GuiEvent(type="run.output", session_id="session-1", run_id="run-1", payload={"text": "hello"}),
        )

        assert await bus.subscriber_count("session-1") == 0
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(queue.get(), timeout=0.05)

    @pytest.mark.asyncio
    async def test_concurrent_publish_calls_preserve_order(self):
        bus = GuiEventBus()
        queue = await bus.subscribe("session-1")

        events = [
            GuiEvent(type="run.output", session_id="session-1", run_id="run-1", payload={"index": idx})
            for idx in range(10)
        ]

        await asyncio.gather(*(bus.publish("session-1", event) for event in events))

        received = [await queue.get() for _ in events]
        assert [event.payload["index"] for event in received] == list(range(10))

    @pytest.mark.asyncio
    async def test_channels_are_separate(self):
        bus = GuiEventBus()
        queue_a = await bus.subscribe("session-a")
        queue_b = await bus.subscribe("session-b")

        event_a = GuiEvent(type="run.output", session_id="session-a", run_id="run-a", payload={"msg": "a"})
        event_b = GuiEvent(type="run.output", session_id="session-b", run_id="run-b", payload={"msg": "b"})

        await bus.publish("session-a", event_a)
        await bus.publish("session-b", event_b)

        assert await queue_a.get() is event_a
        assert await queue_b.get() is event_b

        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(queue_a.get(), timeout=0.05)
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(queue_b.get(), timeout=0.05)


class TestSseHelpers:
    def test_format_sse_message_includes_event_and_json_payload(self):
        payload = {"session_id": "session-1", "value": 7}

        encoded = format_sse_message(
            SseMessage(data=payload, event="gui.event", id="evt-1", retry=5000)
        )

        assert encoded == (
            b"event: gui.event\n"
            b"id: evt-1\n"
            b"retry: 5000\n"
            b'data: {"session_id":"session-1","value":7}\n\n'
        )

    def test_format_sse_ping_emits_comment_frame(self):
        assert format_sse_ping() == b": ping\n\n"
        assert format_sse_ping("keepalive") == b": keepalive\n\n"

    @pytest.mark.asyncio
    async def test_stream_sse_writes_event_frames_and_headers(self):
        async def event_source():
            yield SseMessage(data={"hello": "world"}, event="gui.event", id="evt-1")

        async def handler(request: web.Request) -> web.StreamResponse:
            return await stream_sse(request, event_source(), keepalive_interval=0.1)

        app = web.Application()
        app.router.add_get("/stream", handler)

        async with TestClient(TestServer(app)) as cli:
            resp = await cli.get("/stream")
            assert resp.status == 200
            assert resp.headers["Content-Type"].startswith("text/event-stream")
            text = await resp.text()
            assert "event: gui.event" in text
            assert "id: evt-1" in text
            assert 'data: {"hello":"world"}' in text

    @pytest.mark.asyncio
    async def test_stream_sse_emits_keepalive_ping_before_next_event(self):
        async def event_source():
            await asyncio.sleep(0.02)
            yield SseMessage(data={"step": 1}, event="gui.event")

        async def handler(request: web.Request) -> web.StreamResponse:
            return await stream_sse(request, event_source(), keepalive_interval=0.005, ping_comment="keepalive")

        app = web.Application()
        app.router.add_get("/stream", handler)

        async with TestClient(TestServer(app)) as cli:
            resp = await cli.get("/stream")
            text = await resp.text()
            assert ": keepalive" in text
            assert 'data: {"step":1}' in text
