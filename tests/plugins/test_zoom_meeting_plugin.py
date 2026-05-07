"""Tests for the zoom_meeting plugin primitives."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import plugins.zoom_meeting as plugin
from plugins.zoom_meeting.server import AIOHTTP_AVAILABLE, ZoomWebhookServer
from plugins.zoom_meeting.store import (
    ZoomMeetingStore,
    compute_webhook_validation_token,
    extract_meeting_identity,
    normalize_event,
)
from plugins.zoom_meeting.tools import (
    handle_zoom_meeting_action_items,
    handle_zoom_meeting_artifacts,
    handle_zoom_meeting_status,
    handle_zoom_meeting_summary,
    handle_zoom_meeting_transcript,
    handle_zoom_meeting_watch,
)

import pytest


def test_compute_webhook_validation_token_is_stable():
    token = compute_webhook_validation_token("secret", "plain-token")
    assert len(token) == 64
    assert token == compute_webhook_validation_token("secret", "plain-token")


def test_normalize_event_extracts_identity_and_transcript_entries():
    payload = {
        "event": "meeting.rtms_transcript",
        "payload": {
            "object": {
                "id": "12345",
                "uuid": "uuid-1",
                "topic": "Quarterly Review",
                "segments": [
                    {"speaker_name": "Dilek", "transcript": "Welcome everyone", "timestamp": "2026-05-06T10:00:00Z"},
                    {"speaker_name": "Alex", "transcript": "Status update follows", "timestamp": "2026-05-06T10:00:04Z"},
                ],
            }
        },
    }
    identity = extract_meeting_identity(payload)
    assert identity["meeting_id"] == "12345"
    assert identity["meeting_uuid"] == "uuid-1"
    assert identity["topic"] == "Quarterly Review"

    normalized = normalize_event(payload)
    assert normalized["kind"] == "transcript"
    assert len(normalized["transcript_entries"]) == 2
    assert normalized["transcript_entries"][0]["speaker"] == "Dilek"


def test_store_ingest_event_updates_state_and_transcript(tmp_path):
    store = ZoomMeetingStore(root=tmp_path / "zoom")
    payload = {
        "event": "meeting.started",
        "payload": {
            "object": {
                "id": "2468",
                "topic": "Infra Sync",
                "speaker_name": "Dilek",
                "transcript": "Kickoff and agenda review",
            }
        },
    }
    normalized = store.ingest_event(payload)
    assert normalized["meeting_id"] == "2468"

    state = store.load_state("2468")
    assert state["status"] in ("started", "active")
    assert state["event_count"] == 1
    assert state["transcript_lines"] == 1

    transcript = store.read_transcript("2468")
    assert "Kickoff and agenda review" in transcript


def test_tool_handlers_use_local_store_and_export_artifacts(tmp_path, monkeypatch):
    monkeypatch.setattr("plugins.zoom_meeting.tools._store", lambda: ZoomMeetingStore(root=tmp_path / "zoom"))
    monkeypatch.setattr(
        "plugins.zoom_meeting.tools._client_from_env",
        lambda: None,
    )

    watch = json.loads(handle_zoom_meeting_watch({"meeting_id": "777"}))
    assert watch["success"] is True
    assert "warning" in watch

    store = ZoomMeetingStore(root=tmp_path / "zoom")
    store.ingest_event(
        {
            "event": "meeting.rtms_transcript",
            "payload": {
                "object": {
                    "id": "777",
                    "topic": "Strategy",
                    "caption": "We should expand in Q3",
                    "speaker": "Dilek",
                }
            },
        }
    )

    status = json.loads(handle_zoom_meeting_status({"meeting_id": "777"}))
    assert status["success"] is True
    assert status["state"]["event_count"] >= 1

    transcript = json.loads(handle_zoom_meeting_transcript({"meeting_id": "777"}))
    assert transcript["success"] is True
    assert "expand in Q3" in transcript["transcript"]

    summary = json.loads(handle_zoom_meeting_summary({"meeting_id": "777"}))
    assert summary["success"] is True
    assert "# Zoom Meeting Summary" in summary["summary"]
    assert "## Action Items" in summary["summary"]
    assert Path(summary["path"]).is_file()

    action_items = json.loads(handle_zoom_meeting_action_items({"meeting_id": "777"}))
    assert action_items["success"] is True
    assert action_items["action_items"]

    artifact = json.loads(handle_zoom_meeting_artifacts({"meeting_id": "777", "format": "json"}))
    assert artifact["success"] is True
    exported = json.loads(Path(artifact["path"]).read_text(encoding="utf-8"))
    assert exported["state"]["meeting_id"] == "777"
    assert exported["analysis"]["action_items"]


def test_register_wires_tools_and_cli():
    calls = {"tools": [], "cli": []}

    class _Ctx:
        def register_tool(self, **kw):
            calls["tools"].append(kw["name"])

        def register_cli_command(self, **kw):
            calls["cli"].append(kw["name"])

    plugin.register(_Ctx())

    assert set(calls["tools"]) == {
        "zoom_meeting_watch",
        "zoom_meeting_status",
        "zoom_meeting_transcript",
        "zoom_meeting_events",
        "zoom_meeting_summary",
        "zoom_meeting_action_items",
        "zoom_meeting_artifacts",
    }
    assert calls["cli"] == ["zoom"]


@pytest.mark.skipif(not AIOHTTP_AVAILABLE, reason="aiohttp not installed")
def test_webhook_server_validates_endpoint_and_ingests_events(tmp_path):
    class _Request:
        def __init__(self, payload: dict, *, headers: dict[str, str]):
            self._body = json.dumps(payload).encode("utf-8")
            self.headers = headers

        async def read(self) -> bytes:
            return self._body

    async def _run() -> None:
        store = ZoomMeetingStore(root=tmp_path / "zoom")
        server = ZoomWebhookServer(store, secret_token="zoom-secret")

        validation_payload = {"event": "endpoint.url_validation", "payload": {"plainToken": "abc123"}}
        timestamp = "1700000000"
        validation_sig = compute_webhook_validation_token(
            "zoom-secret",
            f"v0:{timestamp}:{json.dumps(validation_payload)}",
        )
        validation_request = _Request(
            validation_payload,
            headers={
                "x-zm-request-timestamp": timestamp,
                "x-zm-signature": f"v0={validation_sig}",
            },
        )
        response = await server._handle_webhook(validation_request)
        assert response.status == 200
        body = json.loads(response.text)
        assert body["plainToken"] == "abc123"
        assert body["encryptedToken"] == compute_webhook_validation_token("zoom-secret", "abc123")

        event_payload = {
            "event": "meeting.rtms_transcript",
            "payload": {
                "object": {
                    "id": "4242",
                    "topic": "Roadmap",
                    "speaker_name": "Dilek",
                    "transcript": "We should prepare the launch brief",
                }
            },
        }
        event_sig = compute_webhook_validation_token(
            "zoom-secret",
            f"v0:{timestamp}:{json.dumps(event_payload)}",
        )
        event_request = _Request(
            event_payload,
            headers={
                "x-zm-request-timestamp": timestamp,
                "x-zm-signature": f"v0={event_sig}",
            },
        )
        response = await server._handle_webhook(event_request)
        assert response.status == 200
        body = json.loads(response.text)
        assert body["ok"] is True
        assert body["meeting_id"] == "4242"

        transcript = store.read_transcript("4242")
        assert "launch brief" in transcript

    asyncio.run(_run())
