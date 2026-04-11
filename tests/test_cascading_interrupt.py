import logging
import threading
import time
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import httpx
import pytest

from tools.interrupt import set_interrupt


def _make_agent():
    from run_agent import AIAgent

    with (
        patch("run_agent.get_tool_definitions", return_value=[]),
        patch("run_agent.check_toolset_requirements", return_value={}),
        patch("run_agent.OpenAI"),
    ):
        agent = AIAgent(
            model="test/model",
            api_key="test-key",
            quiet_mode=True,
            skip_context_files=True,
            skip_memory=True,
        )
    agent.api_mode = "chat_completions"
    agent._emit_status = MagicMock()
    agent._replace_primary_openai_client = MagicMock(return_value=True)
    return agent


def _make_stream_chunk(
    content=None, finish_reason=None, model="test-model", usage=None
):
    delta = SimpleNamespace(
        content=content,
        tool_calls=None,
        reasoning_content=None,
        reasoning=None,
    )
    choice = SimpleNamespace(index=0, delta=delta, finish_reason=finish_reason)
    return SimpleNamespace(choices=[choice], model=model, usage=usage)


def _make_empty_chunk(model="test-model", usage=None):
    return SimpleNamespace(choices=[], model=model, usage=usage)


@pytest.fixture(autouse=True)
def _reset_interrupt_state():
    set_interrupt(False)
    yield
    set_interrupt(False)


def _run_in_thread(target):
    holder = {}

    def _wrapped():
        try:
            holder["result"] = target()
        except BaseException as exc:  # noqa: BLE001
            holder["error"] = exc

    thread = threading.Thread(target=_wrapped, daemon=True)
    thread.start()
    return thread, holder


def test_interrupt_during_stream_does_not_retry():
    agent = _make_agent()
    started = threading.Event()
    closed = threading.Event()
    release = threading.Event()
    mock_client = MagicMock()

    def create(**kwargs):
        started.set()
        assert kwargs["stream"] is True
        release.wait(timeout=2)
        raise httpx.RemoteProtocolError("interrupt-induced close")

    mock_client.chat.completions.create.side_effect = create
    agent._create_request_openai_client = MagicMock(return_value=mock_client)

    def close_request(client, *, reason):
        if reason == "stream_interrupt_abort":
            closed.set()

    agent._close_request_openai_client = MagicMock(side_effect=close_request)

    start = time.monotonic()
    thread, holder = _run_in_thread(
        lambda: agent._interruptible_streaming_api_call(
            {"model": "test", "messages": []}
        )
    )

    assert started.wait(timeout=1)
    agent.interrupt("stop")
    assert closed.wait(timeout=1)
    release.set()
    thread.join(timeout=2)
    elapsed = time.monotonic() - start

    assert not thread.is_alive()
    assert isinstance(holder.get("error"), InterruptedError)
    assert mock_client.chat.completions.create.call_count == 1
    assert not any(
        "Reconnecting" in call.args[0] for call in agent._emit_status.call_args_list
    )
    assert elapsed < 2


def test_cached_agent_after_interrupt_second_turn_clean():
    agent = _make_agent()
    first_started = threading.Event()
    first_closed = threading.Event()
    first_release = threading.Event()
    mock_client = MagicMock()

    def create(**kwargs):
        if mock_client.chat.completions.create.call_count == 0:
            raise AssertionError("unexpected mock state")
        if mock_client.chat.completions.create.call_count == 1:
            first_started.set()
            first_release.wait(timeout=2)
            raise httpx.RemoteProtocolError("interrupt-induced close")
        return iter(
            [
                _make_stream_chunk(content="second turn", finish_reason="stop"),
                _make_empty_chunk(),
            ]
        )

    mock_client.chat.completions.create.side_effect = create
    agent._create_request_openai_client = MagicMock(return_value=mock_client)

    def close_request(client, *, reason):
        if reason == "stream_interrupt_abort":
            first_closed.set()

    agent._close_request_openai_client = MagicMock(side_effect=close_request)

    start = time.monotonic()
    thread, holder = _run_in_thread(
        lambda: agent._interruptible_streaming_api_call(
            {"model": "test", "messages": []}
        )
    )
    assert first_started.wait(timeout=1)
    agent.interrupt("stop")
    assert first_closed.wait(timeout=1)
    first_release.set()
    thread.join(timeout=2)

    assert isinstance(holder.get("error"), InterruptedError)

    agent.clear_interrupt()
    second = agent._interruptible_streaming_api_call({"model": "test", "messages": []})
    elapsed = time.monotonic() - start

    assert second.choices[0].message.content == "second turn"
    assert elapsed < 3
    assert not any(
        "Reconnecting" in call.args[0] for call in agent._emit_status.call_args_list
    )


def test_interrupt_during_non_streaming_does_not_leak_error():
    agent = _make_agent()
    started = threading.Event()
    closed = threading.Event()
    release = threading.Event()
    mock_client = MagicMock()

    def create(**kwargs):
        started.set()
        release.wait(timeout=2)
        raise httpx.RemoteProtocolError("interrupt-induced close")

    mock_client.chat.completions.create.side_effect = create
    agent._create_request_openai_client = MagicMock(return_value=mock_client)

    def close_request(client, *, reason):
        if reason == "interrupt_abort":
            closed.set()

    agent._close_request_openai_client = MagicMock(side_effect=close_request)

    start = time.monotonic()
    thread, holder = _run_in_thread(
        lambda: agent._interruptible_api_call({"model": "test", "messages": []})
    )

    assert started.wait(timeout=1)
    agent.interrupt("stop")
    assert closed.wait(timeout=1)
    release.set()
    thread.join(timeout=2)
    elapsed = time.monotonic() - start

    assert not thread.is_alive()
    assert isinstance(holder.get("error"), InterruptedError)
    assert not isinstance(holder.get("error"), httpx.RemoteProtocolError)
    assert elapsed < 2


def test_logged_as_cancellation_not_reconnect(caplog):
    agent = _make_agent()
    started = threading.Event()
    closed = threading.Event()
    release = threading.Event()
    mock_client = MagicMock()

    def create(**kwargs):
        started.set()
        release.wait(timeout=2)
        raise httpx.RemoteProtocolError("interrupt-induced close")

    mock_client.chat.completions.create.side_effect = create
    agent._create_request_openai_client = MagicMock(return_value=mock_client)

    def close_request(client, *, reason):
        if reason == "stream_interrupt_abort":
            closed.set()

    agent._close_request_openai_client = MagicMock(side_effect=close_request)

    caplog.set_level(logging.DEBUG)
    caplog.set_level(logging.DEBUG, logger="run_agent")
    thread, holder = _run_in_thread(
        lambda: agent._interruptible_streaming_api_call(
            {"model": "test", "messages": []}
        )
    )

    assert started.wait(timeout=1)
    agent.interrupt("stop")
    assert closed.wait(timeout=1)
    release.set()
    thread.join(timeout=2)

    assert isinstance(holder.get("error"), InterruptedError)
    messages = [record.getMessage() for record in caplog.records]
    assert any(
        "not a network bug" in message.lower()
        and ("interrupt" in message.lower() or "cancellation" in message.lower())
        for message in messages
    )
    assert not any(message.startswith("Streaming attempt ") for message in messages)


def test_normal_transient_error_still_retries():
    agent = _make_agent()
    mock_client = MagicMock()
    mock_client.chat.completions.create.side_effect = [
        httpx.RemoteProtocolError("socket closed"),
        iter(
            [
                _make_stream_chunk(content="ok", finish_reason="stop"),
                _make_empty_chunk(),
            ]
        ),
    ]
    agent._create_request_openai_client = MagicMock(return_value=mock_client)
    agent._close_request_openai_client = MagicMock()

    response = agent._interruptible_streaming_api_call(
        {"model": "test", "messages": []}
    )

    assert mock_client.chat.completions.create.call_count == 2
    assert response.choices[0].message.content == "ok"
    assert any(
        "Reconnecting" in call.args[0] for call in agent._emit_status.call_args_list
    )
