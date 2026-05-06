"""Hermetic integration tests for the Teams meeting pipeline."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from gateway.platforms.teams import TeamsSummarySender
from tools.teams_pipeline import NotionWriter, TeamsMeetingPipeline
from tools.teams_pipeline_models import MeetingArtifact, TeamsMeetingRef, TeamsMeetingSummaryPayload
from tools.teams_pipeline_store import TeamsPipelineStore


class FakeGraphClient:
    pass


async def _resolve_meeting(client, *, meeting_id=None, join_web_url=None, tenant_id=None):
    return TeamsMeetingRef(
        meeting_id=str(meeting_id or "meeting-1"),
        tenant_id=tenant_id,
        metadata={"subject": "Weekly Sync", "participants": [{"displayName": "Ada"}]},
    )


async def _no_call_record(*args, **kwargs):
    return None


@pytest.mark.anyio
async def test_notification_to_transcript_and_teams_delivery(tmp_path, monkeypatch):
    from tools import teams_pipeline as pipeline_module

    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"id": "teams-msg-1"}, request=request)

    async def _fetch_transcript(client, meeting_ref):
        return (
            MeetingArtifact(artifact_type="transcript", artifact_id="tx-1", display_name="meeting.vtt"),
            "Decision: Launch on Tuesday.\nAction: Ada sends rollout notes.",
        )

    monkeypatch.setattr(pipeline_module, "resolve_meeting_reference", _resolve_meeting)
    monkeypatch.setattr(pipeline_module, "fetch_preferred_transcript_text", _fetch_transcript)
    monkeypatch.setattr(pipeline_module, "enrich_meeting_with_call_record", _no_call_record)

    store = TeamsPipelineStore(tmp_path / "teams-store.json")
    pipeline = TeamsMeetingPipeline(
        graph_client=FakeGraphClient(),
        store=store,
        config={
            "transcript_min_chars": 20,
            "teams_delivery": {
                "enabled": True,
                "delivery_mode": "incoming_webhook",
                "incoming_webhook_url": "https://outlook.office.com/webhook/abc",
                "channel_id": "channel-1",
            },
        },
        teams_sender=TeamsSummarySender(transport=httpx.MockTransport(handler)),
    )

    job = await pipeline.run_notification(
        {
            "id": "notif-1",
            "changeType": "updated",
            "resource": "communications/onlineMeetings/meeting-123",
            "resourceData": {"id": "meeting-123"},
        }
    )

    assert job.status == "completed"
    assert job.selected_artifact_strategy == "transcript_first"
    sink_record = store.get_sink_record("teams:meeting-123")
    assert sink_record is not None
    assert sink_record["message_id"] == "teams-msg-1"
    assert len(requests) == 1
    assert b"Weekly Sync" in requests[0].content


@pytest.mark.anyio
async def test_notification_to_recording_fallback_and_notion_write(tmp_path, monkeypatch):
    from tools import teams_pipeline as pipeline_module

    requests: list[httpx.Request] = []

    def notion_handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"id": "page-1", "url": "https://notion.so/page-1"}, request=request)

    async def _no_transcript(client, meeting_ref):
        return None, None

    async def _recordings(client, meeting_ref):
        return [
            MeetingArtifact(
                artifact_type="recording",
                artifact_id="rec-1",
                display_name="meeting.mp4",
                download_url="https://files.example/meeting.mp4",
            )
        ]

    async def _download(client, meeting_ref, recording, destination):
        Path(destination).write_bytes(b"video")
        return {"path": str(destination), "size_bytes": 5, "content_type": "video/mp4"}

    async def _prepare_audio(self, recording_path):
        audio_path = recording_path.with_suffix(".wav")
        audio_path.write_bytes(b"audio")
        return audio_path

    def _transcribe(file_path, model):
        return {"success": True, "transcript": "Action: Legal reviews copy.\nRisk: Sign-off pending."}

    async def _summarize(**kwargs):
        return TeamsMeetingSummaryPayload(
            meeting_ref=kwargs["resolved_meeting"],
            title="Weekly Sync",
            transcript_text=kwargs["transcript_text"],
            summary="Fallback summary",
            action_items=["Legal reviews copy."],
            risks=["Sign-off pending."],
            confidence="medium",
            confidence_notes="Generated from recording fallback.",
            source_artifacts=kwargs["artifacts"],
        )

    monkeypatch.setattr(pipeline_module, "resolve_meeting_reference", _resolve_meeting)
    monkeypatch.setattr(pipeline_module, "fetch_preferred_transcript_text", _no_transcript)
    monkeypatch.setattr(pipeline_module, "list_recording_artifacts", _recordings)
    monkeypatch.setattr(pipeline_module, "download_recording_artifact", _download)
    monkeypatch.setattr(pipeline_module.TeamsMeetingPipeline, "_prepare_audio_path", _prepare_audio)
    monkeypatch.setattr(pipeline_module, "enrich_meeting_with_call_record", _no_call_record)

    store = TeamsPipelineStore(tmp_path / "teams-store.json")
    pipeline = TeamsMeetingPipeline(
        graph_client=FakeGraphClient(),
        store=store,
        config={
            "notion": {
                "enabled": True,
                "database_id": "db-1",
                "title_property": "Name",
            }
        },
        transcribe_fn=_transcribe,
        summarize_fn=_summarize,
        notion_writer=NotionWriter(api_key="secret", transport=httpx.MockTransport(notion_handler)),
    )

    job = await pipeline.run_notification(
        {
            "id": "notif-2",
            "changeType": "updated",
            "resource": "communications/onlineMeetings/meeting-456",
            "resourceData": {"id": "meeting-456"},
        }
    )

    assert job.status == "completed"
    assert job.selected_artifact_strategy == "recording_stt_fallback"
    sink_record = store.get_sink_record("notion:meeting-456")
    assert sink_record is not None
    assert sink_record["page_id"] == "page-1"
    assert len(requests) == 1
    assert requests[0].url.path == "/v1/pages"
