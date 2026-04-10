import asyncio
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from gateway.config import Platform, PlatformConfig
from gateway.platforms.base import BasePlatformAdapter, MessageEvent, MessageType, SendResult
from gateway.session import SessionSource, build_session_key


class _StubAdapter(BasePlatformAdapter):
    def __init__(self):
        super().__init__(PlatformConfig(enabled=True, token="***"), Platform.TELEGRAM)
        self._send_results = []

    async def connect(self) -> bool:
        return True

    async def disconnect(self) -> None:
        self._mark_disconnected()

    async def send(self, chat_id, content, reply_to=None, metadata=None):
        if self._send_results:
            return self._send_results.pop(0)
        return SendResult(success=True, message_id="msg-1")

    async def get_chat_info(self, chat_id):
        return {"id": chat_id, "type": "dm"}


def _make_source(chat_id="chat-1", user_id="user-1"):
    return SessionSource(
        platform=Platform.TELEGRAM,
        chat_id=chat_id,
        user_id=user_id,
        user_name="tester",
        chat_type="dm",
    )


def _make_event(text="hello", message_type=MessageType.TEXT):
    return MessageEvent(
        text=text,
        message_type=message_type,
        source=_make_source(),
        message_id="m1",
    )


@pytest.mark.asyncio
async def test_logs_disposition_when_photo_is_queued_without_interrupt(caplog):
    adapter = _StubAdapter()
    adapter.set_message_handler(AsyncMock(return_value="ok"))
    session_key = build_session_key(_make_source())
    adapter._active_sessions[session_key] = asyncio.Event()

    with caplog.at_level(logging.INFO, logger="gateway.platforms.base"):
        await adapter.handle_message(_make_event(message_type=MessageType.PHOTO))

    assert any(
        "gateway_disposition" in r.message and "queued_without_interrupt" in r.message
        for r in caplog.records
    )


@pytest.mark.asyncio
async def test_logs_disposition_when_handler_returns_empty(caplog):
    adapter = _StubAdapter()
    adapter.set_message_handler(AsyncMock(return_value=None))

    with caplog.at_level(logging.INFO, logger="gateway.platforms.base"):
        await adapter.handle_message(_make_event())
        await asyncio.sleep(0.3)

    assert any(
        "gateway_disposition" in r.message and "handler_empty_response" in r.message
        for r in caplog.records
    )


@pytest.mark.asyncio
async def test_logs_disposition_when_delivery_retries_exhausted(caplog):
    adapter = _StubAdapter()
    network_err = SendResult(success=False, error="httpx.ConnectError: host unreachable")
    adapter._send_results = [network_err, network_err, network_err, SendResult(success=True)]

    with patch("asyncio.sleep", new_callable=AsyncMock):
        with caplog.at_level(logging.INFO, logger="gateway.platforms.base"):
            await adapter._send_with_retry("chat1", "hello", max_retries=2, base_delay=0)

    assert any(
        "gateway_disposition" in r.message and "delivery_failed_retries_exhausted" in r.message
        for r in caplog.records
    )
