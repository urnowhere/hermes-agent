"""Tests for configurable Discord auto-thread archive duration."""

import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from gateway.config import PlatformConfig


def _ensure_discord_mock():
    """Install a mock discord module when discord.py isn't available."""
    if "discord" in sys.modules and hasattr(sys.modules["discord"], "__file__"):
        return

    discord_mod = MagicMock()
    discord_mod.Intents.default.return_value = MagicMock()
    discord_mod.Client = MagicMock
    discord_mod.File = MagicMock
    discord_mod.DMChannel = type("DMChannel", (), {})
    discord_mod.Thread = type("Thread", (), {})
    discord_mod.ForumChannel = type("ForumChannel", (), {})
    discord_mod.ui = SimpleNamespace(View=object, button=lambda *a, **k: (lambda fn: fn), Button=object)
    discord_mod.ButtonStyle = SimpleNamespace(success=1, primary=2, secondary=2, danger=3, green=1, grey=2, blurple=2, red=3)
    discord_mod.Color = SimpleNamespace(orange=lambda: 1, green=lambda: 2, blue=lambda: 3, red=lambda: 4, purple=lambda: 5)
    discord_mod.Interaction = object
    discord_mod.Embed = MagicMock
    discord_mod.app_commands = SimpleNamespace(
        describe=lambda **kwargs: (lambda fn: fn),
        choices=lambda **kwargs: (lambda fn: fn),
        Choice=lambda **kwargs: SimpleNamespace(**kwargs),
    )

    ext_mod = MagicMock()
    commands_mod = MagicMock()
    commands_mod.Bot = MagicMock
    ext_mod.commands = commands_mod

    sys.modules.setdefault("discord", discord_mod)
    sys.modules.setdefault("discord.ext", ext_mod)
    sys.modules.setdefault("discord.ext.commands", commands_mod)


_ensure_discord_mock()

import gateway.platforms.discord as discord_platform  # noqa: E402
from gateway.platforms.discord import DiscordAdapter  # noqa: E402


@pytest.fixture
def adapter(monkeypatch):
    config = PlatformConfig(enabled=True, token="fake-token")
    adapter = DiscordAdapter(config)
    adapter._client = SimpleNamespace(user=SimpleNamespace(id=999))
    adapter._text_batch_delay_seconds = 0
    adapter.handle_message = AsyncMock()
    return adapter


def test_default_archive_duration_is_1440():
    """Default archive duration should be 1440 minutes."""
    # Re-import to pick up default env
    import importlib
    importlib.reload(discord_platform)
    assert discord_platform._DISCORD_AUTO_THREAD_AUTO_ARCHIVE_DURATION == 1440


@pytest.mark.parametrize("duration", [60, 1440, 4320, 10080])
def test_valid_archive_durations(duration, monkeypatch):
    """Valid Discord archive durations are accepted."""
    monkeypatch.setenv("DISCORD_AUTO_THREAD_AUTO_ARCHIVE_DURATION", str(duration))
    import importlib
    importlib.reload(discord_platform)
    assert discord_platform._DISCORD_AUTO_THREAD_AUTO_ARCHIVE_DURATION == duration


def test_invalid_archive_duration_falls_back_to_1440(caplog, monkeypatch):
    """Invalid archive durations log a warning and fall back to 1440."""
    monkeypatch.setenv("DISCORD_AUTO_THREAD_AUTO_ARCHIVE_DURATION", "300")
    import importlib
    importlib.reload(discord_platform)
    assert discord_platform._DISCORD_AUTO_THREAD_AUTO_ARCHIVE_DURATION == 1440
    assert "is invalid; must be one of" in caplog.text


def test_negative_archive_duration_falls_back_to_1440(caplog, monkeypatch):
    """Negative archive durations log a warning and fall back to 1440."""
    monkeypatch.setenv("DISCORD_AUTO_THREAD_AUTO_ARCHIVE_DURATION", "-1")
    import importlib
    importlib.reload(discord_platform)
    assert discord_platform._DISCORD_AUTO_THREAD_AUTO_ARCHIVE_DURATION == 1440
    assert "is invalid; must be one of" in caplog.text
