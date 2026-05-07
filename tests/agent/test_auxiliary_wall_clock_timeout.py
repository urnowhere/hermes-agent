"""Regression tests for auxiliary LLM wall-clock timeout enforcement.

Auxiliary calls are used by hot-path features such as context compression.  A
provider SDK timeout alone is not sufficient: a streaming call can block the
interactive reply path until the user interrupts the whole session.  These tests
cover the provider-independent wrapper used by OpenAI-compatible, Codex, and
Anthropic auxiliary clients.
"""

from __future__ import annotations

import asyncio
import threading
import time
from types import SimpleNamespace

import pytest

from agent.auxiliary_client import (
    _async_create_with_wall_clock_timeout,
    _create_with_wall_clock_timeout,
    _deadline_from_timeout,
)


class _BlockingCompletions:
    def __init__(self, closed: threading.Event):
        self.closed = closed
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        # Simulate an SDK stream that would otherwise keep the reply path stuck.
        self.closed.wait(timeout=5.0)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="late"))]
        )


class _FakeSyncClient:
    def __init__(self):
        self.closed = threading.Event()
        self.chat = SimpleNamespace(completions=_BlockingCompletions(self.closed))

    def close(self):
        self.closed.set()


class _ImmediateCompletions:
    def create(self, **kwargs):
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))]
        )


class _FakeImmediateClient:
    def __init__(self):
        self.closed = False
        self.chat = SimpleNamespace(completions=_ImmediateCompletions())

    def close(self):
        self.closed = True


@pytest.mark.parametrize("task", ["compression", "openai-compatible", "anthropic-compatible", "openai-codex"])
def test_sync_wall_clock_timeout_closes_provider_client(task):
    client = _FakeSyncClient()
    started = time.monotonic()

    with pytest.raises(TimeoutError, match="wall-clock timeout"):
        _create_with_wall_clock_timeout(
            client,
            {"model": "m", "messages": [], "timeout": 0.05},
            _deadline_from_timeout(0.05),
            task,
        )

    assert time.monotonic() - started < 0.5
    assert client.closed.is_set(), "timed-out auxiliary client must be closed"


def test_sync_wall_clock_timeout_preserves_successful_response():
    client = _FakeImmediateClient()

    response = _create_with_wall_clock_timeout(
        client,
        {"model": "m", "messages": [], "timeout": 1.0},
        _deadline_from_timeout(1.0),
        "compression",
    )

    assert response.choices[0].message.content == "ok"
    assert client.closed is False


class _AsyncBlockingCompletions:
    def __init__(self):
        self.started = asyncio.Event()

    async def create(self, **kwargs):
        self.started.set()
        await asyncio.sleep(5.0)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="late"))]
        )


class _FakeAsyncClient:
    def __init__(self):
        self.closed = False
        self.chat = SimpleNamespace(completions=_AsyncBlockingCompletions())

    async def aclose(self):
        self.closed = True


@pytest.mark.asyncio
async def test_async_wall_clock_timeout_closes_provider_client():
    client = _FakeAsyncClient()
    started = time.monotonic()

    with pytest.raises(TimeoutError, match="wall-clock timeout"):
        await _async_create_with_wall_clock_timeout(
            client,
            {"model": "m", "messages": [], "timeout": 0.05},
            _deadline_from_timeout(0.05),
            "compression",
        )

    assert time.monotonic() - started < 0.5
    assert client.closed is True
