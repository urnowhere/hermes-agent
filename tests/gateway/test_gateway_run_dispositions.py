import logging
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from gateway.config import GatewayConfig, Platform, PlatformConfig
from gateway.platforms.base import MessageEvent, MessageType
from gateway.session import SessionEntry, SessionSource, build_session_key


def _make_source() -> SessionSource:
    return SessionSource(
        platform=Platform.TELEGRAM,
        user_id="u1",
        chat_id="c1",
        user_name="tester",
        chat_type="dm",
    )


def _make_event(text: str, message_type=MessageType.TEXT) -> MessageEvent:
    return MessageEvent(
        text=text,
        message_type=message_type,
        source=_make_source(),
        message_id="m1",
    )


def _make_runner(session_entry: SessionEntry):
    from gateway.run import GatewayRunner

    runner = object.__new__(GatewayRunner)
    runner.config = GatewayConfig(
        platforms={Platform.TELEGRAM: PlatformConfig(enabled=True, token="***")}
    )
    adapter = MagicMock()
    adapter.send = AsyncMock()
    adapter._pending_messages = {}
    runner.adapters = {Platform.TELEGRAM: adapter}
    runner._voice_mode = {}
    runner.hooks = SimpleNamespace(emit=AsyncMock(), loaded_hooks=False)
    runner.session_store = MagicMock()
    runner.session_store.get_or_create_session.return_value = session_entry
    runner.session_store.load_transcript.return_value = []
    runner.session_store.has_any_sessions.return_value = True
    runner.session_store.append_to_transcript = MagicMock()
    runner.session_store.rewrite_transcript = MagicMock()
    runner.session_store.update_session = MagicMock()
    runner._running_agents = {}
    runner._running_agents_ts = {}
    runner._pending_messages = {}
    runner._pending_approvals = {}
    runner._session_db = None
    runner._reasoning_config = None
    runner._provider_routing = {}
    runner._fallback_model = None
    runner._show_reasoning = False
    runner._is_user_authorized = lambda _source: True
    runner._set_session_env = lambda _context: None
    runner._should_send_voice_reply = lambda *_args, **_kwargs: False
    runner._send_voice_reply = AsyncMock()
    runner._capture_gateway_honcho_if_configured = lambda *args, **kwargs: None
    runner._emit_gateway_run_progress = AsyncMock()
    runner._MAX_INTERRUPT_DEPTH = 3
    runner.pairing_store = MagicMock()
    runner.pairing_store._is_rate_limited = MagicMock(return_value=False)
    runner.pairing_store.generate_code = MagicMock(return_value="ABC123")
    return runner


@pytest.mark.asyncio
async def test_logs_disposition_when_runner_queues_photo_during_active_run(caplog):
    session_entry = SessionEntry(
        session_key=build_session_key(_make_source()),
        session_id="sess-1",
        created_at=datetime.now(),
        updated_at=datetime.now(),
        platform=Platform.TELEGRAM,
        chat_type="dm",
    )
    runner = _make_runner(session_entry)
    runner._running_agents[build_session_key(_make_source())] = MagicMock()

    with caplog.at_level(logging.INFO, logger="gateway.run"):
        result = await runner._handle_message(_make_event("photo", message_type=MessageType.PHOTO))

    assert result is None
    assert any(
        "gateway_disposition" in r.message and "queued_without_interrupt" in r.message
        for r in caplog.records
    )


@pytest.mark.asyncio
async def test_logs_disposition_when_runner_interrupts_active_run(caplog):
    session_entry = SessionEntry(
        session_key=build_session_key(_make_source()),
        session_id="sess-1",
        created_at=datetime.now(),
        updated_at=datetime.now(),
        platform=Platform.TELEGRAM,
        chat_type="dm",
    )
    runner = _make_runner(session_entry)
    running_agent = MagicMock()
    runner._running_agents[build_session_key(_make_source())] = running_agent

    with caplog.at_level(logging.INFO, logger="gateway.run"):
        result = await runner._handle_message(_make_event("new message"))

    assert result is None
    running_agent.interrupt.assert_called_once_with("new message")
    assert any(
        "gateway_disposition" in r.message and "interrupt_requested" in r.message
        for r in caplog.records
    )


@pytest.mark.asyncio
async def test_logs_disposition_when_unauthorized_dm_is_rate_limited(caplog):
    session_entry = SessionEntry(
        session_key=build_session_key(_make_source()),
        session_id="sess-1",
        created_at=datetime.now(),
        updated_at=datetime.now(),
        platform=Platform.TELEGRAM,
        chat_type="dm",
    )
    runner = _make_runner(session_entry)
    runner._is_user_authorized = lambda _source: False
    runner.pairing_store._is_rate_limited.return_value = True

    with caplog.at_level(logging.INFO, logger="gateway.run"):
        result = await runner._handle_message(_make_event("hello"))

    assert result is None
    assert any(
        "gateway_disposition" in r.message and "unauthorized_rate_limited" in r.message
        for r in caplog.records
    )
