"""Tests for tools.teams_meetings."""

from __future__ import annotations

from pathlib import Path

import pytest

from tools.microsoft_graph_client import MicrosoftGraphAPIError
from tools.teams_meetings import (
    TeamsMeetingArtifactNotFoundError,
    download_recording_artifact,
    enrich_meeting_with_call_record,
    fetch_preferred_transcript_text,
    list_recording_artifacts,
    resolve_meeting_reference,
    select_preferred_transcript,
)
from tools.teams_pipeline_models import (
    GraphSubscription,
    MeetingArtifact,
    TeamsMeetingPipelineJob,
    TeamsMeetingRef,
    TeamsMeetingSummaryPayload,
)


class FakeGraphClient:
    def __init__(self) -> None:
        self.get_json_responses: dict[tuple[str, tuple[tuple[str, str], ...]], object] = {}
        self.collect_responses: dict[str, list[dict]] = {}
        self.download_payloads: dict[str, bytes] = {}
        self.download_calls: list[tuple[str, str]] = []

    async def get_json(self, path: str, *, params=None, headers=None):
        key = (path, tuple(sorted((params or {}).items())))
        response = self.get_json_responses.get(key)
        if isinstance(response, Exception):
            raise response
        if response is None:
            raise AssertionError(f"Unexpected get_json call: {key!r}")
        return response

    async def collect_paginated(self, path: str, *, params=None, headers=None):
        response = self.collect_responses.get(path)
        if isinstance(response, Exception):
            raise response
        if response is None:
            raise AssertionError(f"Unexpected collect_paginated call: {path!r}")
        return response

    async def download_to_file(self, path: str, destination: str | Path, *, headers=None, chunk_size=65536):
        payload = self.download_payloads.get(path)
        if isinstance(payload, Exception):
            raise payload
        if payload is None:
            raise AssertionError(f"Unexpected download_to_file call: {path!r}")
        target = Path(destination)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)
        self.download_calls.append((path, str(target)))
        return {
            "path": str(target),
            "size_bytes": len(payload),
            "content_type": "text/vtt" if target.suffix == ".vtt" else "video/mp4",
        }


@pytest.mark.anyio
class TestTeamsMeetingModels:
    async def test_pipeline_models_round_trip(self):
        meeting_ref = TeamsMeetingRef(meeting_id="meeting-1", join_web_url="https://teams.microsoft.com/l/meetup-join/1")
        artifact = MeetingArtifact(
            artifact_type="transcript",
            artifact_id="tx-1",
            display_name="meeting.vtt",
            created_at="2026-05-03T18:00:00Z",
        )
        summary = TeamsMeetingSummaryPayload(
            meeting_ref=meeting_ref,
            title="Weekly Sync",
            transcript_text="hello",
            source_artifacts=[artifact],
        )
        job = TeamsMeetingPipelineJob(
            job_id="job-1",
            event_id="event-1",
            source_event_type="graph.notification",
            dedupe_key="dedupe-1",
            status="pending",
            meeting_ref=meeting_ref,
            summary_payload=summary,
        )
        subscription = GraphSubscription.from_dict(
            {
                "id": "sub-1",
                "resource": "communications/onlineMeetings",
                "changeType": "created",
                "notificationUrl": "https://example.com/hook",
                "expirationDateTime": "2026-05-04T00:00:00Z",
            }
        )

        assert TeamsMeetingPipelineJob.from_dict(job.to_dict()).to_dict()["job_id"] == "job-1"
        assert TeamsMeetingSummaryPayload.from_dict(summary.to_dict()).source_artifacts[0].artifact_id == "tx-1"
        assert subscription.to_dict()["resource"] == "communications/onlineMeetings"


@pytest.mark.anyio
class TestTeamsMeetingResolution:
    async def test_resolve_meeting_by_join_url(self):
        client = FakeGraphClient()
        join_url = "https://teams.microsoft.com/l/meetup-join/19%3ameeting_abc"
        client.get_json_responses[
            (
                "/communications/onlineMeetings",
                (("$filter", f"JoinWebUrl eq '{join_url}'"),),
            )
        ] = {
            "value": [
                {
                    "id": "meeting-123",
                    "joinWebUrl": join_url,
                    "calendarEventId": "event-9",
                    "organizer": {"identity": {"user": {"id": "user-1"}}},
                    "chatInfo": {"threadId": "thread-7"},
                    "subject": "Design Review",
                }
            ]
        }

        meeting_ref = await resolve_meeting_reference(client, join_web_url=join_url, tenant_id="tenant-1")

        assert meeting_ref.meeting_id == "meeting-123"
        assert meeting_ref.organizer_user_id == "user-1"
        assert meeting_ref.thread_id == "thread-7"
        assert meeting_ref.tenant_id == "tenant-1"

    async def test_transcript_first_resolution_prefers_completed_candidate_and_downloads_text(self):
        client = FakeGraphClient()
        meeting_ref = TeamsMeetingRef(meeting_id="meeting-123")
        client.collect_responses["/communications/onlineMeetings/meeting-123/transcripts"] = [
            {
                "id": "tx-old",
                "displayName": "draft.vtt",
                "status": "inProgress",
                "createdDateTime": "2026-05-03T18:00:00Z",
            },
            {
                "id": "tx-best",
                "displayName": "final.vtt",
                "status": "completed",
                "createdDateTime": "2026-05-03T18:05:00Z",
            },
        ]
        client.download_payloads[
            "/communications/onlineMeetings/meeting-123/transcripts/tx-best/content"
        ] = b"WEBVTT\n\nhello world"

        artifact, text = await fetch_preferred_transcript_text(client, meeting_ref)

        assert artifact is not None
        assert artifact.artifact_id == "tx-best"
        assert text == "WEBVTT\n\nhello world"
        assert client.download_calls[0][0].endswith("/tx-best/content")

    async def test_multi_candidate_transcript_selection_is_deterministic(self):
        chosen = select_preferred_transcript(
            [
                MeetingArtifact(
                    artifact_type="transcript",
                    artifact_id="tx-1",
                    display_name="one.vtt",
                    created_at="2026-05-03T18:00:00Z",
                    metadata={"status": "completed"},
                ),
                MeetingArtifact(
                    artifact_type="transcript",
                    artifact_id="tx-2",
                    display_name="two.vtt",
                    created_at="2026-05-03T18:05:00Z",
                    metadata={"status": "completed"},
                ),
            ]
        )

        assert chosen is not None
        assert chosen.artifact_id == "tx-2"

    async def test_empty_transcript_degrades_gracefully(self):
        client = FakeGraphClient()
        meeting_ref = TeamsMeetingRef(meeting_id="meeting-123")
        client.collect_responses["/communications/onlineMeetings/meeting-123/transcripts"] = [
            {"id": "tx-empty", "displayName": "empty.vtt", "status": "completed"}
        ]
        client.download_payloads[
            "/communications/onlineMeetings/meeting-123/transcripts/tx-empty/content"
        ] = b"   "

        artifact, text = await fetch_preferred_transcript_text(client, meeting_ref)

        assert artifact is None
        assert text is None

    async def test_recording_metadata_and_download(self, tmp_path: Path):
        client = FakeGraphClient()
        meeting_ref = TeamsMeetingRef(meeting_id="meeting-123")
        client.collect_responses["/communications/onlineMeetings/meeting-123/recordings"] = [
            {
                "id": "rec-1",
                "name": "recording.mp4",
                "contentType": "video/mp4",
                "size": 42,
                "@microsoft.graph.downloadUrl": "https://files.example/recording.mp4",
                "webUrl": "https://sharepoint.example/recording",
            }
        ]
        client.download_payloads["https://files.example/recording.mp4"] = b"recording-bytes"

        recordings = await list_recording_artifacts(client, meeting_ref)
        result = await download_recording_artifact(client, meeting_ref, recordings[0], tmp_path / "recording.mp4")

        assert recordings[0].display_name == "recording.mp4"
        assert recordings[0].source_url == "https://sharepoint.example/recording"
        assert result["size_bytes"] == len(b"recording-bytes")
        assert (tmp_path / "recording.mp4").read_bytes() == b"recording-bytes"

    async def test_call_record_permission_error_degrades_gracefully(self):
        client = FakeGraphClient()
        client.get_json_responses[
            ("/communications/callRecords/call-1", ())
        ] = MicrosoftGraphAPIError(
            403,
            "GET",
            "https://graph.microsoft.com/v1.0/communications/callRecords/call-1",
            "Forbidden",
        )
        meeting_ref = TeamsMeetingRef(meeting_id="meeting-123", metadata={"call_record_id": "call-1"})

        artifact = await enrich_meeting_with_call_record(client, meeting_ref)

        assert artifact is None

    async def test_empty_transcript_download_raises_at_low_level(self):
        client = FakeGraphClient()
        meeting_ref = TeamsMeetingRef(meeting_id="meeting-123")
        transcript = MeetingArtifact(artifact_type="transcript", artifact_id="tx-1", display_name="t.vtt")
        client.download_payloads["/communications/onlineMeetings/meeting-123/transcripts/tx-1/content"] = b""

        with pytest.raises(TeamsMeetingArtifactNotFoundError):
            from tools.teams_meetings import download_transcript_text

            await download_transcript_text(client, meeting_ref, transcript)
