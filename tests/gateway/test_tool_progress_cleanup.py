import asyncio
import queue

import pytest

from gateway.run import (
    _delete_tool_progress_message,
    _delete_tool_progress_messages,
    _finish_tool_progress_task,
    _reset_tool_progress_state,
    _resolve_tool_progress_cleanup,
)


class _DeleteAdapter:
    def __init__(self):
        self.deleted = []

    async def delete_message(self, chat_id: str, message_id: str) -> bool:
        self.deleted.append((chat_id, message_id))
        return True


class _NoDeleteAdapter:
    pass


def test_tool_progress_cleanup_prefers_platform_override():
    config = {
        "display": {
            "tool_progress_cleanup": False,
            "platforms": {
                "telegram": {"tool_progress_cleanup": True},
            },
        }
    }

    assert _resolve_tool_progress_cleanup(config, "telegram") is True


def test_tool_progress_cleanup_defaults_to_disabled_for_invalid_values():
    config = {"display": {"platforms": {"telegram": {"tool_progress_cleanup": "junk"}}}}

    assert _resolve_tool_progress_cleanup(config, "telegram") is False


@pytest.mark.parametrize("mode", ["new", "all", "verbose"])
@pytest.mark.parametrize("single_message", [False, True])
def test_tool_progress_single_message_controls_reset_independent_of_progress_mode(mode, single_message):
    progress_lines = [f"{mode}-first", f"{mode}-second"]
    last_progress_msg = [f"{mode}-second"]
    repeat_count = [3]

    reset = _reset_tool_progress_state(
        progress_lines,
        last_progress_msg,
        repeat_count,
        single_message=single_message,
    )

    assert reset is (not single_message), mode
    if single_message:
        assert progress_lines == [f"{mode}-first", f"{mode}-second"]
        assert last_progress_msg == [f"{mode}-second"]
        assert repeat_count == [3]
    else:
        assert progress_lines == []
        assert last_progress_msg == [None]
        assert repeat_count == [0]


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["new", "all", "verbose"])
async def test_tool_progress_cleanup_deletes_progress_messages_independent_of_progress_mode(mode):
    adapter = _DeleteAdapter()

    deleted = await _delete_tool_progress_messages(
        adapter,
        "chat-1",
        [f"{mode}-msg-1", f"{mode}-msg-2"],
        enabled=True,
    )

    assert deleted == 2
    assert adapter.deleted == [("chat-1", f"{mode}-msg-1"), ("chat-1", f"{mode}-msg-2")]


@pytest.mark.asyncio
async def test_delete_tool_progress_message_deletes_when_enabled():
    adapter = _DeleteAdapter()

    deleted = await _delete_tool_progress_message(adapter, "chat-1", "msg-1", enabled=True)

    assert deleted is True
    assert adapter.deleted == [("chat-1", "msg-1")]


@pytest.mark.asyncio
async def test_delete_tool_progress_message_noops_when_disabled_or_missing_id():
    adapter = _DeleteAdapter()

    assert await _delete_tool_progress_message(adapter, "chat-1", "msg-1", enabled=False) is False
    assert await _delete_tool_progress_message(adapter, "chat-1", None, enabled=True) is False
    assert adapter.deleted == []


@pytest.mark.asyncio
async def test_delete_tool_progress_message_silently_degrades_without_delete_support():
    adapter = _NoDeleteAdapter()

    assert await _delete_tool_progress_message(adapter, "chat-1", "msg-1", enabled=True) is False


@pytest.mark.asyncio
async def test_delete_tool_progress_messages_deletes_every_distinct_progress_bubble():
    adapter = _DeleteAdapter()

    deleted = await _delete_tool_progress_messages(
        adapter,
        "chat-1",
        ["msg-1", "msg-2", "msg-1", None],
        enabled=True,
    )

    assert deleted == 2
    assert adapter.deleted == [("chat-1", "msg-1"), ("chat-1", "msg-2")]


@pytest.mark.asyncio
async def test_finish_tool_progress_task_sends_finish_sentinel_before_cancelling():
    progress_queue = queue.Queue()
    finished = asyncio.Event()

    async def worker():
        while True:
            try:
                item = progress_queue.get_nowait()
            except queue.Empty:
                await asyncio.sleep(0.01)
                continue
            if isinstance(item, tuple) and item[0] == "__finish__":
                finished.set()
                return

    task = asyncio.create_task(worker())

    await _finish_tool_progress_task(task, progress_queue, timeout=1.0)

    assert finished.is_set()
    assert task.done()


@pytest.mark.asyncio
async def test_finish_tool_progress_task_preserves_outer_cancellation():
    progress_queue = queue.Queue()

    async def worker():
        await asyncio.sleep(10)

    task = asyncio.create_task(worker())
    finisher = asyncio.create_task(_finish_tool_progress_task(task, progress_queue, timeout=10.0))
    await asyncio.sleep(0)

    finisher.cancel()

    with pytest.raises(asyncio.CancelledError):
        await finisher
    assert task.cancelled()
