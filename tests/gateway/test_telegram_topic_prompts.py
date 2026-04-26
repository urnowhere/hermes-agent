"""Tests for resolve_topic_prompt: per-topic prompt lookup in telegram group_topics config."""

import sys
import types
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest


# ── Fake telegram module tree so importing the adapter module works ──
# Mirrors the pattern in test_telegram_thread_fallback.py. The full
# import surface of gateway/platforms/telegram.py must be satisfied —
# `from telegram.constants import ParseMode, ChatType` will explode at
# import time if either name is missing from the fake.
_fake_telegram = types.ModuleType("telegram")
_fake_telegram.Update = object
_fake_telegram.Bot = object
_fake_telegram.Message = object
_fake_telegram.InlineKeyboardButton = object
_fake_telegram.InlineKeyboardMarkup = object


class _FakeNetworkError(Exception):
    pass


class _FakeBadRequest(_FakeNetworkError):
    pass


class _FakeTimedOut(_FakeNetworkError):
    pass


class _FakeRetryAfter(Exception):
    def __init__(self, seconds):
        super().__init__(f"Retry after {seconds}")
        self.retry_after = seconds


_fake_telegram_error = types.ModuleType("telegram.error")
_fake_telegram_error.NetworkError = _FakeNetworkError
_fake_telegram_error.BadRequest = _FakeBadRequest
_fake_telegram_error.TimedOut = _FakeTimedOut
_fake_telegram_error.RetryAfter = _FakeRetryAfter
_fake_telegram_error.TelegramError = Exception
_fake_telegram.error = _fake_telegram_error

_fake_telegram_constants = types.ModuleType("telegram.constants")
_fake_telegram_constants.ParseMode = SimpleNamespace(MARKDOWN_V2="MarkdownV2", HTML="HTML")
_fake_telegram_constants.ChatType = SimpleNamespace(
    GROUP="group",
    SUPERGROUP="supergroup",
    CHANNEL="channel",
    PRIVATE="private",
)
_fake_telegram_constants.ReactionEmoji = type("ReactionEmoji", (), {})
_fake_telegram.constants = _fake_telegram_constants

_fake_telegram_ext = types.ModuleType("telegram.ext")
_fake_telegram_ext.Application = object
_fake_telegram_ext.ApplicationBuilder = object
_fake_telegram_ext.CommandHandler = object
_fake_telegram_ext.CallbackQueryHandler = object
_fake_telegram_ext.MessageHandler = object
_fake_telegram_ext.ContextTypes = SimpleNamespace(DEFAULT_TYPE=object)
_fake_telegram_ext.filters = object

_fake_telegram_request = types.ModuleType("telegram.request")
_fake_telegram_request.HTTPXRequest = object

sys.modules.setdefault("telegram", _fake_telegram)
sys.modules.setdefault("telegram.constants", _fake_telegram_constants)
sys.modules.setdefault("telegram.error", _fake_telegram_error)
sys.modules.setdefault("telegram.ext", _fake_telegram_ext)
sys.modules.setdefault("telegram.request", _fake_telegram_request)


from gateway.platforms.telegram import resolve_topic_prompt  # noqa: E402

# Note: if `from gateway.platforms.telegram import resolve_topic_prompt`
# raises an ImportError mentioning a name other than `resolve_topic_prompt`
# (e.g. `cannot import name 'X' from 'telegram.constants'`), telegram.py's
# import surface has grown — extend the fake module tree above to match.


class TestResolveTopicPrompt:
    def test_returns_prompt_for_matching_chat_and_thread(self):
        extra = {
            "group_topics": [
                {
                    "chat_id": "-1003742888118",
                    "topics": [
                        {"name": "ops", "thread_id": "5", "prompt": "Ops prompt"},
                    ],
                },
            ]
        }
        assert resolve_topic_prompt(extra, "-1003742888118", "5") == "Ops prompt"
