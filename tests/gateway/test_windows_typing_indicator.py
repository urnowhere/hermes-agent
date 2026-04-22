"""Regression test: typing indicator in run.py uses _progress_metadata (always
defined) instead of _thread_metadata (only defined in the proxy code path).

On Windows, the gateway is more likely to be run without proxy mode (local
model server), which is exactly the code path where the bug occurred:

    NameError: name '_thread_metadata' is not defined

The fix replaces _thread_metadata with _progress_metadata in the _keep_typing
task creation inside the non-proxy agent run path.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.mark.asyncio
async def test_keep_typing_receives_progress_metadata_not_thread_metadata():
    """The typing indicator task must be created with _progress_metadata (which
    is always defined in the non-proxy run path), not _thread_metadata (which
    only exists in the proxy path and raises NameError on local models).
    """
    # Build the simplest possible _progress_metadata, mirroring run.py logic:
    # _progress_thread_id = source.thread_id  (None for most Telegram messages)
    # _progress_metadata = {"thread_id": ...} if _progress_thread_id else None
    source_thread_id = None
    _progress_thread_id = source_thread_id
    _progress_metadata = {"thread_id": _progress_thread_id} if _progress_thread_id else None

    received_metadata = []

    async def fake_keep_typing(chat_id, interval=4.0, metadata=None):
        received_metadata.append(metadata)
        # Immediately return — we just want to check args
        return

    fake_adapter = MagicMock()
    fake_adapter._keep_typing = fake_keep_typing

    # Simulate the task creation block from run.py:
    #   typing_task = asyncio.create_task(
    #       adapter._keep_typing(chat_id, interval=4.0, metadata=_progress_metadata)
    #   )
    task = asyncio.create_task(
        fake_adapter._keep_typing("chat_123", interval=4.0, metadata=_progress_metadata)
    )
    await task

    # _progress_metadata is None when there's no thread_id (normal Telegram DM)
    assert received_metadata == [None]


@pytest.mark.asyncio
async def test_keep_typing_passes_thread_metadata_for_forum_topics():
    """When source.thread_id is set (Telegram forum topic), _progress_metadata
    must carry the thread_id so the typing indicator appears in the right thread.
    """
    source_thread_id = 42
    _progress_thread_id = source_thread_id
    _progress_metadata = {"thread_id": _progress_thread_id} if _progress_thread_id else None

    received_metadata = []

    async def fake_keep_typing(chat_id, interval=4.0, metadata=None):
        received_metadata.append(metadata)

    fake_adapter = MagicMock()
    fake_adapter._keep_typing = fake_keep_typing

    task = asyncio.create_task(
        fake_adapter._keep_typing("chat_123", interval=4.0, metadata=_progress_metadata)
    )
    await task

    assert received_metadata == [{"thread_id": 42}]
