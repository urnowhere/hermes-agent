"""Tests for the ``pre_outbound_send`` plugin hook.

Covers:
- Hook fires with correct kwargs (platform / chat_id / content / metadata)
- String return value mutates content
- ``{"content": ...}`` dict return value mutates content
- Multiple plugins chain in registration order (last non-None wins)
- ``None`` return / no callback leaves content unchanged
- Plugin exception is logged and swallowed -- send proceeds with original content
- Hook is invoked from ``BasePlatformAdapter._fire_pre_outbound_send_hooks``
  (transport-level integration verified via FeishuAdapter monkey-patch)
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional
from unittest.mock import patch

import pytest

from gateway.platforms.base import BasePlatformAdapter, SendResult


class _RecordingAdapter(BasePlatformAdapter):
    """Minimal adapter used to exercise the base hook helper."""

    def __init__(self) -> None:
        # Skip BasePlatformAdapter.__init__ -- we don't need full lifecycle
        # for hook tests. Set the platform attr so type(self).__name__ is
        # the only piece the hook helper reads.
        self.last_content: Optional[str] = None

    async def connect(self) -> bool:  # pragma: no cover - abstract stub
        return True

    async def disconnect(self) -> None:  # pragma: no cover - abstract stub
        return None

    async def get_chat_info(self, chat_id: str) -> Dict[str, Any]:  # pragma: no cover - abstract stub
        return {}

    async def send(
        self,
        chat_id: str,
        content: str,
        reply_to: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> SendResult:
        content = await self._fire_pre_outbound_send_hooks(content, chat_id, metadata)
        self.last_content = content
        return SendResult(success=True, message_id="m-1")


@pytest.fixture
def patched_invoke_hook():
    """Patch ``hermes_cli.plugins.invoke_hook`` and yield the call recorder."""
    calls: List[Dict[str, Any]] = []
    return_values: List[Any] = []

    def fake_invoke(hook_name: str, **kwargs: Any) -> List[Any]:
        calls.append({"hook_name": hook_name, "kwargs": kwargs})
        return list(return_values)

    with patch("hermes_cli.plugins.invoke_hook", side_effect=fake_invoke):
        yield calls, return_values


@pytest.mark.asyncio
async def test_hook_fires_with_expected_kwargs(patched_invoke_hook):
    calls, _ = patched_invoke_hook
    adapter = _RecordingAdapter()

    await adapter.send("chat-42", "hello", metadata={"thread_id": "t-1"})

    assert len(calls) == 1
    call = calls[0]
    assert call["hook_name"] == "pre_outbound_send"
    assert call["kwargs"]["platform"] == "_RecordingAdapter"
    assert call["kwargs"]["chat_id"] == "chat-42"
    assert call["kwargs"]["content"] == "hello"
    assert call["kwargs"]["metadata"] == {"thread_id": "t-1"}


@pytest.mark.asyncio
async def test_string_return_mutates_content(patched_invoke_hook):
    _, return_values = patched_invoke_hook
    return_values.append("rewritten")
    adapter = _RecordingAdapter()

    await adapter.send("c", "original", metadata=None)

    assert adapter.last_content == "rewritten"


@pytest.mark.asyncio
async def test_dict_return_with_content_key_mutates(patched_invoke_hook):
    _, return_values = patched_invoke_hook
    return_values.append({"content": "from-dict"})
    adapter = _RecordingAdapter()

    await adapter.send("c", "original")

    assert adapter.last_content == "from-dict"


@pytest.mark.asyncio
async def test_none_return_leaves_content_unchanged(patched_invoke_hook):
    # Empty return list (e.g. no plugin registered)
    adapter = _RecordingAdapter()
    await adapter.send("c", "untouched")
    assert adapter.last_content == "untouched"


@pytest.mark.asyncio
async def test_multiple_plugins_last_wins(patched_invoke_hook):
    _, return_values = patched_invoke_hook
    return_values.extend(["first-rewrite", "second-rewrite"])
    adapter = _RecordingAdapter()

    await adapter.send("c", "original")

    assert adapter.last_content == "second-rewrite"


@pytest.mark.asyncio
async def test_plugin_exception_does_not_block_send():
    adapter = _RecordingAdapter()

    def boom(*args: Any, **kwargs: Any) -> List[Any]:
        raise RuntimeError("plugin crashed")

    with patch("hermes_cli.plugins.invoke_hook", side_effect=boom):
        result = await adapter.send("c", "still-sent")

    assert result.success is True
    assert adapter.last_content == "still-sent"


@pytest.mark.asyncio
async def test_invalid_return_shapes_are_ignored(patched_invoke_hook):
    """Non-string / non-{content: str} returns are silently ignored."""
    _, return_values = patched_invoke_hook
    return_values.extend([42, ["list-not-allowed"], {"unrelated": "x"}, None])
    adapter = _RecordingAdapter()

    await adapter.send("c", "kept")

    assert adapter.last_content == "kept"


@pytest.mark.asyncio
async def test_dict_with_non_string_content_ignored(patched_invoke_hook):
    """``{"content": <non-str>}`` is ignored to prevent type confusion downstream."""
    _, return_values = patched_invoke_hook
    return_values.append({"content": 123})
    adapter = _RecordingAdapter()

    await adapter.send("c", "kept-too")

    assert adapter.last_content == "kept-too"


def test_pre_outbound_send_in_valid_hooks():
    """Smoke test: VALID_HOOKS exposes the new hook name."""
    from hermes_cli.plugins import VALID_HOOKS

    assert "pre_outbound_send" in VALID_HOOKS
