from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from gateway.config import GatewayConfig, Platform, PlatformConfig
from gateway.platforms.base import MessageEvent, MessageType
from gateway.session import SessionEntry, SessionSource, build_session_key


BASE_TS = datetime(2026, 4, 22, 22, 0, 0)


def _make_source() -> SessionSource:
    return SessionSource(
        platform=Platform.DISCORD,
        user_id="u1",
        chat_id="c1",
        user_name="tester",
        chat_type="dm",
        thread_id="t1",
    )


def _make_event(
    text: str,
    *,
    message_id: str | None,
    timestamp: datetime = BASE_TS,
    reply_to_message_id: str | None = None,
) -> MessageEvent:
    return MessageEvent(
        text=text,
        source=_make_source(),
        message_id=message_id,
        timestamp=timestamp,
        reply_to_message_id=reply_to_message_id,
        message_type=MessageType.TEXT,
    )


def _make_runner():
    from gateway.run import GatewayRunner

    runner = object.__new__(GatewayRunner)
    runner.config = GatewayConfig(
        platforms={Platform.DISCORD: PlatformConfig(enabled=True, token="***")}
    )
    adapter = MagicMock()
    adapter.send = AsyncMock()
    runner.adapters = {Platform.DISCORD: adapter}
    runner._voice_mode = {}
    runner.hooks = SimpleNamespace(emit=AsyncMock(), loaded_hooks=False)
    runner._session_model_overrides = {}
    runner._pending_model_notes = {}
    runner._background_tasks = set()

    session_key = build_session_key(_make_source())
    session_entry = SessionEntry(
        session_key=session_key,
        session_id="sess-1",
        created_at=datetime.now(),
        updated_at=datetime.now(),
        platform=Platform.DISCORD,
        chat_type="dm",
    )
    runner.session_store = MagicMock()
    runner.session_store.get_or_create_session.return_value = session_entry
    runner.session_store._entries = {session_key: session_entry}
    runner.session_store._generate_session_key.return_value = session_key
    runner._running_agents = {}
    runner._running_agents_ts = {}
    runner._pending_messages = {}
    runner._pending_approvals = {}
    runner._session_db = None
    runner._agent_cache_lock = None
    runner._is_user_authorized = lambda _source: True
    runner._duplicate_inbound_ttl_seconds = 5.0
    runner._recent_inbound_events = {}
    runner._duplicate_inbound_drops = 0
    return runner


def test_duplicate_inbound_event_same_message_id_second_sighting_is_duplicate(monkeypatch):
    runner = _make_runner()
    monkeypatch.setattr("gateway.run.time.time", lambda: 100.0)

    event = _make_event("hello", message_id="m1")

    assert runner._is_duplicate_inbound_event(event) is False
    assert runner._is_duplicate_inbound_event(event) is True
    assert runner._duplicate_inbound_drops == 1


def test_duplicate_inbound_event_same_semantics_different_message_ids_is_duplicate(monkeypatch):
    runner = _make_runner()
    monkeypatch.setattr("gateway.run.time.time", lambda: 100.0)

    first = _make_event("hello", message_id="m1")
    second = _make_event("hello", message_id="m2")

    assert runner._is_duplicate_inbound_event(first) is False
    assert runner._is_duplicate_inbound_event(second) is True
    assert runner._duplicate_inbound_drops == 1


def test_duplicate_inbound_event_different_text_is_not_duplicate(monkeypatch):
    runner = _make_runner()
    monkeypatch.setattr("gateway.run.time.time", lambda: 100.0)

    first = _make_event("hello", message_id="m1")
    second = _make_event("different text", message_id="m2")

    assert runner._is_duplicate_inbound_event(first) is False
    assert runner._is_duplicate_inbound_event(second) is False
    assert runner._duplicate_inbound_drops == 0


@pytest.mark.asyncio
async def test_handle_message_drops_duplicate_before_agent_pipeline(monkeypatch):
    runner = _make_runner()
    monkeypatch.setattr("gateway.run.time.time", lambda: 100.0)

    calls = []

    async def fake_handle(event, source, quick_key, generation):
        calls.append((event.text, quick_key, generation))
        return "processed"

    runner._begin_session_run_generation = lambda _quick_key: 1
    runner._release_running_agent_state = lambda _quick_key: None
    runner._invalidate_session_run_generation = lambda *_args, **_kwargs: None
    runner._interrupt_and_clear_session = AsyncMock()
    runner._handle_message_with_agent = fake_handle

    event = _make_event("hello", message_id="m1")

    first = await runner._handle_message(event)
    second = await runner._handle_message(event)

    assert first == "processed"
    assert second is None
    assert calls == [("hello", build_session_key(_make_source()), 1)]
    assert runner._duplicate_inbound_drops == 1
