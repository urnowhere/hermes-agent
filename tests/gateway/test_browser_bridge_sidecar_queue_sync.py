"""Regression tests for sidecar queued-turn sync on slash-command turns."""

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import gateway.run as gateway_run
from gateway.config import Platform
from gateway.run import GatewayRunner
from gateway.session import SessionSource


def _make_runner():
    runner = object.__new__(GatewayRunner)
    runner._browser_bridge_progress = {}
    runner._browser_bridge_tasks = {}
    runner._browser_bridge_pending_interrupts = set()
    runner._running_agents = {}
    runner.session_store = MagicMock()
    runner._extract_browser_bridge_image_attachments = MagicMock(return_value=([], []))
    runner._get_browser_bridge_session_snapshot = MagicMock(
        return_value={"progress": {"running": False}, "messages": []}
    )
    return runner


def _make_source():
    return SessionSource(
        platform=Platform.LOCAL,
        chat_id="browser-bridge:test",
        chat_type="dm",
        user_id="local-user",
        user_name="Hermes Sidecar",
        chat_name="Hermes Sidecar",
    )


@pytest.mark.asyncio
async def test_sidecar_slash_turn_persists_transcript_when_handler_does_not():
    runner = _make_runner()
    runner._handle_message = AsyncMock(return_value="🌐 Browser connected to live CDP.")
    runner.session_store.get_or_create_session.return_value = SimpleNamespace(
        session_key="browser-bridge:test",
        session_id="session-1",
    )
    runner.session_store.load_transcript.side_effect = [[], []]

    await runner._handle_browser_bridge_send(
        payload={"message": "/browser connect ws://localhost:9222"},
        source=_make_source(),
        async_mode=False,
    )

    assert runner.session_store.append_to_transcript.call_count == 2
    user_call = runner.session_store.append_to_transcript.call_args_list[0]
    assistant_call = runner.session_store.append_to_transcript.call_args_list[1]
    assert user_call.args[0] == "session-1"
    assert user_call.args[1]["role"] == "user"
    assert user_call.args[1]["content"].startswith("/browser connect")
    assert assistant_call.args[0] == "session-1"
    assert assistant_call.args[1]["role"] == "assistant"
    assert "connected to live CDP" in assistant_call.args[1]["content"]


@pytest.mark.asyncio
async def test_sidecar_slash_turn_skips_manual_persist_when_handler_already_updated():
    runner = _make_runner()
    runner._handle_message = AsyncMock(return_value="done")
    runner.session_store.get_or_create_session.return_value = SimpleNamespace(
        session_key="browser-bridge:test",
        session_id="session-2",
    )
    runner.session_store.load_transcript.side_effect = [
        [],
        [{"role": "user", "content": "/status"}],
    ]

    await runner._handle_browser_bridge_send(
        payload={"message": "/status"},
        source=_make_source(),
        async_mode=False,
    )

    runner.session_store.append_to_transcript.assert_not_called()


@pytest.mark.asyncio
async def test_sidecar_sync_turn_timeout_cleans_progress_and_task(monkeypatch):
    runner = _make_runner()

    async def _slow_handle(_event):
        await asyncio.sleep(0.2)
        return "done"

    runner._handle_message = AsyncMock(side_effect=_slow_handle)
    runner.session_store.get_or_create_session.return_value = SimpleNamespace(
        session_key="browser-bridge:test",
        session_id="session-3",
    )
    runner.session_store.load_transcript.return_value = []
    monkeypatch.setattr(gateway_run, "_BROWSER_SIDECAR_SYNC_TIMEOUT_SECONDS", 0.01)

    with pytest.raises(TimeoutError, match="Sidecar turn exceeded"):
        await runner._handle_browser_bridge_send(
            payload={"message": "analyze this page"},
            source=_make_source(),
            async_mode=False,
        )

    progress = runner._browser_bridge_progress["browser-bridge:test"]
    assert progress["running"] is False
    assert progress["detail"] == "Sidecar turn timed out."
    assert "cancelled" in progress["error"]
    assert "browser-bridge:test" not in runner._browser_bridge_tasks


@pytest.mark.asyncio
async def test_browser_bridge_fetch_pdf_text_action(monkeypatch):
    runner = _make_runner()
    monkeypatch.setattr(gateway_run, "fetch_pdf_text", lambda url: f"PDF text from {url}")

    result = await GatewayRunner._handle_browser_bridge_session(
        runner,
        {
            "action": "fetch_pdf_text",
            "url": "https://example.com/report.pdf",
        },
    )

    assert result["pdf_text"] == "PDF text from https://example.com/report.pdf"
    assert result["char_count"] == len(result["pdf_text"])


@pytest.mark.asyncio
async def test_browser_bridge_fetch_pdf_preview_info_action(monkeypatch):
    runner = _make_runner()
    monkeypatch.setattr(gateway_run, "fetch_pdf_page_images", lambda url: [b"img-1", b"img-2"])

    result = await GatewayRunner._handle_browser_bridge_session(
        runner,
        {
            "action": "fetch_pdf_preview_info",
            "url": "https://example.com/report.pdf",
        },
    )

    assert result["image_count"] == 2


@pytest.mark.asyncio
async def test_sidecar_pdf_send_attaches_rendered_page_images(monkeypatch, tmp_path):
    runner = _make_runner()
    runner._handle_message = AsyncMock(return_value="done")
    runner.session_store.get_or_create_session.return_value = SimpleNamespace(
        session_key="browser-bridge:test",
        session_id="session-5",
    )
    runner.session_store.load_transcript.return_value = []
    monkeypatch.setattr(gateway_run, "_hermes_home", tmp_path)
    monkeypatch.setattr(gateway_run, "fetch_pdf_page_images", lambda url: [b"page-one", b"page-two"])

    await runner._handle_browser_bridge_send(
        payload={
            "message": "look at this pdf",
            "pageContext": {
                "url": "https://example.com/report.pdf",
                "contentKind": "pdf-document",
                "metadata": {"pdfUrl": "https://example.com/report.pdf"},
            },
        },
        source=_make_source(),
        async_mode=False,
    )

    event = runner._handle_message.await_args.args[0]
    assert event.message_type == gateway_run.MessageType.PHOTO
    assert len(event.media_urls) == 2
    assert event.media_types == ["image/png", "image/png"]
    for path in event.media_urls:
        assert path.endswith(".png")
        assert tmp_path in gateway_run.Path(path).parents


def test_browser_bridge_snapshot_keeps_running_sidecar_session_interruptable():
    runner = _make_runner()
    runner.session_store.get_or_create_session.return_value = SimpleNamespace(
        session_key="browser-bridge:test",
        session_id="session-4",
    )
    runner.session_store.load_transcript.return_value = [{"role": "user", "content": "hello"}]
    runner._get_browser_bridge_progress_snapshot = MagicMock(
        return_value={"running": True, "detail": "Hermes is thinking..."}
    )

    snapshot = GatewayRunner._get_browser_bridge_session_snapshot(runner, _make_source())

    assert snapshot["progress"]["running"] is True
    assert snapshot["can_send"] is True


def test_browser_bridge_source_normalizes_legacy_extension_label():
    runner = _make_runner()

    source = GatewayRunner._build_browser_bridge_source(runner, "Chrome Extension", "")

    assert source.chat_name == "Hermes Sidecar"
    assert source.user_name == "Hermes Sidecar"


def test_browser_bridge_session_list_normalizes_legacy_extension_label():
    runner = _make_runner()
    legacy_source = SessionSource(
        platform=Platform.LOCAL,
        chat_id="browser-bridge:legacy",
        chat_type="dm",
        user_id="browser-sidecar",
        user_name="Chrome Extension",
        chat_name="Chrome Extension",
    )
    runner.session_store._entries = {
        "browser-bridge:legacy": SimpleNamespace(
            origin=legacy_source,
            updated_at=datetime.now(timezone.utc),
            created_at=datetime.now(timezone.utc),
            session_key="browser-bridge:legacy",
            session_id="legacy-session",
        )
    }
    runner.session_store.load_transcript.return_value = []
    runner._get_browser_bridge_progress_snapshot = MagicMock(return_value={"running": False})

    result = GatewayRunner._list_browser_bridge_sessions(runner)

    assert result["sessions"][0]["browser_label"] == "Hermes Sidecar"


def test_browser_bridge_user_is_authorized_after_bridge_auth():
    runner = _make_runner()

    assert GatewayRunner._is_user_authorized(runner, _make_source()) is True
