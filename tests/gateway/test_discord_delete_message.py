from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from gateway.config import Platform
from gateway.platforms.base import BasePlatformAdapter
from gateway.platforms.discord import DiscordAdapter


class _FakeClient:
    def __init__(self, *, cached_channel=None, fetched_channel=None):
        self.cached_channel = cached_channel
        self.fetched_channel = fetched_channel
        self.fetch_channel = AsyncMock(return_value=fetched_channel)

    def get_channel(self, channel_id: int):
        self.requested_channel_id = channel_id
        return self.cached_channel


@pytest.mark.asyncio
async def test_discord_delete_message_overrides_base_delete():
    assert DiscordAdapter.delete_message is not BasePlatformAdapter.delete_message


@pytest.mark.asyncio
async def test_discord_delete_message_deletes_cached_channel_message():
    msg = SimpleNamespace(delete=AsyncMock())
    channel = SimpleNamespace(fetch_message=AsyncMock(return_value=msg))
    adapter = object.__new__(DiscordAdapter)
    adapter.platform = Platform.DISCORD
    adapter._client = _FakeClient(cached_channel=channel)

    deleted = await adapter.delete_message("123", "456")

    assert deleted is True
    channel.fetch_message.assert_awaited_once_with(456)
    msg.delete.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_discord_delete_message_fetches_channel_when_not_cached():
    msg = SimpleNamespace(delete=AsyncMock())
    channel = SimpleNamespace(fetch_message=AsyncMock(return_value=msg))
    client = _FakeClient(fetched_channel=channel)
    adapter = object.__new__(DiscordAdapter)
    adapter.platform = Platform.DISCORD
    adapter._client = client

    deleted = await adapter.delete_message("123", "456")

    assert deleted is True
    client.fetch_channel.assert_awaited_once_with(123)
    channel.fetch_message.assert_awaited_once_with(456)
    msg.delete.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_discord_delete_message_returns_false_on_missing_client_or_api_error():
    adapter = object.__new__(DiscordAdapter)
    adapter.platform = Platform.DISCORD
    adapter._client = None

    assert await adapter.delete_message("123", "456") is False

    channel = SimpleNamespace(fetch_message=AsyncMock(side_effect=RuntimeError("missing")))
    adapter._client = _FakeClient(cached_channel=channel)

    assert await adapter.delete_message("123", "456") is False
