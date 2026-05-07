import pytest
from unittest.mock import AsyncMock

from gateway.config import GatewayConfig, Platform, PlatformConfig
from gateway.platforms.base import MessageEvent, MessageType
from gateway.run import GatewayRunner
from gateway.session import SessionSource


def _make_adapter():
    from gateway.platforms.whatsapp import WhatsAppAdapter

    adapter = object.__new__(WhatsAppAdapter)
    adapter.platform = Platform.WHATSAPP
    adapter.config = PlatformConfig(enabled=True, extra={})
    adapter._message_handler = AsyncMock()
    adapter._dm_policy = "open"
    adapter._allow_from = set()
    adapter._group_policy = "open"
    adapter._group_allow_from = set()
    adapter._mention_patterns = []
    return adapter


def _make_runner() -> GatewayRunner:
    runner = object.__new__(GatewayRunner)
    runner.config = GatewayConfig(
        platforms={
            Platform.WHATSAPP: PlatformConfig(enabled=True, extra={}),
        },
    )
    runner.adapters = {}
    runner._model = "openai/gpt-4.1-mini"
    runner._base_url = None
    return runner


@pytest.mark.asyncio
async def test_whatsapp_build_message_event_populates_reply_context_from_bridge_payload():
    adapter = _make_adapter()

    event = await adapter._build_message_event({
        "messageId": "current-msg",
        "chatId": "85294049323@s.whatsapp.net",
        "senderId": "85294049323@s.whatsapp.net",
        "senderName": "TK",
        "chatName": "TK",
        "isGroup": False,
        "body": "我要你改嘅係personal.",
        "hasMedia": False,
        "mentionedIds": [],
        "botIds": [],
        "quotedMessageId": "quoted-msg",
        "quotedText": "我想去返linkedin page 改動先。",
    })

    assert event is not None
    assert event.message_type is MessageType.TEXT
    assert event.message_id == "current-msg"
    assert event.reply_to_message_id == "quoted-msg"
    assert event.reply_to_text == "我想去返linkedin page 改動先。"


@pytest.mark.asyncio
async def test_prepare_inbound_message_text_injects_whatsapp_reply_context():
    runner = _make_runner()
    source = SessionSource(
        platform=Platform.WHATSAPP,
        chat_id="85294049323@s.whatsapp.net",
        chat_name="TK",
        chat_type="dm",
        user_name="TK",
    )
    event = MessageEvent(
        text="我要你改嘅係personal.",
        message_type=MessageType.TEXT,
        source=source,
        message_id="current-msg",
        reply_to_message_id="quoted-msg",
        reply_to_text="我想去返linkedin page 改動先。",
    )

    result = await runner._prepare_inbound_message_text(
        event=event,
        source=source,
        history=[],
    )

    assert result == '[Replying to: "我想去返linkedin page 改動先。"]\n\n我要你改嘅係personal.'
