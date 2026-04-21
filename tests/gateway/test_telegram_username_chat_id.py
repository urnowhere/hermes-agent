"""Tests for Telegram username-format chat_id support."""

import sys
from unittest.mock import AsyncMock, MagicMock

import pytest

from gateway.config import HomeChannel, Platform, PlatformConfig


def _ensure_telegram_mock():
    if "telegram" in sys.modules and hasattr(sys.modules["telegram"], "__file__"):
        return
    mod = MagicMock()
    mod.ext.ContextTypes.DEFAULT_TYPE = type(None)
    mod.constants.ParseMode.MARKDOWN_V2 = "MarkdownV2"
    mod.constants.ParseMode.MARKDOWN = "Markdown"
    mod.constants.ParseMode.HTML = "HTML"
    mod.constants.ChatType.GROUP = "group"
    mod.constants.ChatType.SUPERGROUP = "supergroup"
    mod.constants.ChatType.CHANNEL = "channel"
    mod.constants.ChatType.PRIVATE = "private"
    for name in ("telegram", "telegram.ext", "telegram.constants", "telegram.request"):
        sys.modules.setdefault(name, mod)


_ensure_telegram_mock()

from gateway.platforms.telegram import TelegramAdapter  # noqa: E402
from gateway.telegram_ids import normalize_telegram_chat_id  # noqa: E402
from gateway.platforms.webhook import WebhookAdapter  # noqa: E402


@pytest.fixture()
def adapter():
    config = PlatformConfig(enabled=True, token="test-token")
    return TelegramAdapter(config)


class TestNormalizeChatId:
    def test_numeric_string_returns_int(self):
        assert normalize_telegram_chat_id("1234567890") == 1234567890

    def test_negative_numeric_supergroup_id(self):
        assert normalize_telegram_chat_id("-1001234567890") == -1001234567890

    def test_whitespace_numeric_string_is_trimmed(self):
        assert normalize_telegram_chat_id("  42  ") == 42

    def test_username_passes_through(self):
        assert normalize_telegram_chat_id("@some_channel") == "@some_channel"

    def test_username_without_at_passes_through(self):
        assert normalize_telegram_chat_id("some_channel") == "some_channel"


class TestTelegramUsernameChatId:
    @pytest.mark.asyncio
    async def test_send_passes_username_chat_id_to_bot_api(self, adapter):
        adapter._bot = MagicMock()
        adapter._bot.get_chat = AsyncMock()
        adapter._bot.send_message = AsyncMock(return_value=MagicMock(message_id=1))
        adapter.truncate_message = lambda content, max_len, **kw: ["hi"]

        result = await adapter.send("@some_channel", "hi")

        assert result.success is True
        adapter._bot.get_chat.assert_not_called()
        call = adapter._bot.send_message.call_args_list[0]
        assert call.kwargs["chat_id"] == "@some_channel"

    @pytest.mark.asyncio
    async def test_send_with_numeric_id_still_passes_int(self, adapter):
        adapter._bot = MagicMock()
        adapter._bot.get_chat = AsyncMock()
        adapter._bot.send_message = AsyncMock(return_value=MagicMock(message_id=1))
        adapter.truncate_message = lambda content, max_len, **kw: ["hi"]

        result = await adapter.send("12345", "hi")

        assert result.success is True
        adapter._bot.get_chat.assert_not_called()
        call = adapter._bot.send_message.call_args_list[0]
        assert call.kwargs["chat_id"] == 12345

    @pytest.mark.asyncio
    async def test_send_username_chat_not_found_is_non_retryable_and_actionable(self, adapter):
        adapter._bot = MagicMock()
        adapter._bot.send_message = AsyncMock(side_effect=RuntimeError("Bad Request: chat not found"))
        adapter.truncate_message = lambda content, max_len, **kw: ["hi"]

        result = await adapter.send("@some_user", "hi")

        assert result.success is False
        assert result.retryable is False
        assert "private DMs need the numeric chat ID" in result.error

    @pytest.mark.asyncio
    async def test_edit_message_accepts_username_chat_id(self, adapter):
        adapter._bot = MagicMock()
        adapter._bot.edit_message_text = AsyncMock(return_value=MagicMock(message_id=1))

        result = await adapter.edit_message("@some_channel", "99", "updated")

        assert result.success is True
        call = adapter._bot.edit_message_text.call_args_list[0]
        assert call.kwargs["chat_id"] == "@some_channel"
        assert call.kwargs["message_id"] == 99

    @pytest.mark.asyncio
    async def test_send_typing_accepts_username_chat_id(self, adapter):
        adapter._bot = MagicMock()
        adapter._bot.send_chat_action = AsyncMock()

        await adapter.send_typing("@some_channel")

        call = adapter._bot.send_chat_action.call_args_list[0]
        assert call.kwargs["chat_id"] == "@some_channel"

    @pytest.mark.asyncio
    async def test_webhook_home_channel_username_reaches_bot_api(self, adapter):
        adapter._bot = MagicMock()
        adapter._bot.get_chat = AsyncMock()
        adapter._bot.send_message = AsyncMock(return_value=MagicMock(message_id=1))
        adapter.truncate_message = lambda content, max_len, **kw: ["webhook delivered"]

        webhook = WebhookAdapter(PlatformConfig(enabled=True, extra={}))
        runner = MagicMock()
        runner.adapters = {Platform.TELEGRAM: adapter}
        runner.config.get_home_channel.return_value = HomeChannel(
            platform=Platform.TELEGRAM,
            chat_id="@some_channel",
            name="Home",
        )
        webhook.gateway_runner = runner

        result = await webhook._deliver_cross_platform(
            "telegram",
            "webhook delivered",
            {"deliver_extra": {}},
        )

        assert result.success is True
        adapter._bot.get_chat.assert_not_called()
        call = adapter._bot.send_message.call_args_list[0]
        assert call.kwargs["chat_id"] == "@some_channel"
