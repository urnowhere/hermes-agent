"""Tests for Teams meeting pipeline operator tools."""

from __future__ import annotations

import json

import pytest

from tools.teams_pipeline_models import MeetingArtifact, TeamsMeetingPipelineJob, TeamsMeetingRef
from tools.teams_pipeline_operator_tools import (
    _teams_pipeline_dry_run_fetch,
    _teams_pipeline_list_jobs,
    _teams_pipeline_replay_job,
)
from tools.teams_pipeline_store import TeamsPipelineStore


@pytest.mark.anyio
async def test_list_jobs_returns_recent_compact_view(tmp_path):
    store = TeamsPipelineStore(tmp_path / "teams-store.json")
    store.upsert_job(
        "job-1",
        {
            "event_id": "evt-1",
            "source_event_type": "updated",
            "dedupe_key": "evt-1",
            "status": "failed",
            "updated_at": "2026-05-03T19:31:00Z",
            "summary_payload": {"transcript_text": "alpha " * 80},
        },
    )
    store.upsert_job(
        "job-2",
        {
            "event_id": "evt-2",
            "source_event_type": "updated",
            "dedupe_key": "evt-2",
            "status": "completed",
            "updated_at": "2026-05-03T19:32:00Z",
        },
    )

    result = json.loads(
        await _teams_pipeline_list_jobs(
            {"store_path": str(tmp_path / "teams-store.json"), "status": "completed"}
        )
    )

    assert result["success"] is True
    assert result["count"] == 1
    assert result["jobs"][0]["job_id"] == "job-2"


@pytest.mark.anyio
async def test_replay_job_uses_pipeline_runtime(tmp_path, monkeypatch):
    store = TeamsPipelineStore(tmp_path / "teams-store.json")
    stored = store.upsert_job(
        "job-1",
        {
            "event_id": "evt-1",
            "source_event_type": "updated",
            "dedupe_key": "evt-1",
            "status": "failed",
            "meeting_ref": {"meeting_id": "meeting-1"},
        },
    )

    class FakePipeline:
        def __init__(self, *, graph_client, store, config):
            self.graph_client = graph_client
            self.store = store
            self.config = config

        async def run_job(self, job_id):
            payload = dict(stored)
            payload["job_id"] = job_id
            payload["status"] = "completed"
            return TeamsMeetingPipelineJob.from_dict(payload)

    monkeypatch.setattr(
        "tools.teams_pipeline_operator_tools._build_graph_client",
        lambda: object(),
    )
    monkeypatch.setattr(
        "tools.teams_pipeline_operator_tools.TeamsMeetingPipeline",
        FakePipeline,
    )

    result = json.loads(
        await _teams_pipeline_replay_job(
            {"store_path": str(tmp_path / "teams-store.json"), "job_id": "job-1"}
        )
    )

    assert result["success"] is True
    assert result["replayed"] is True
    assert result["job"]["status"] == "completed"


@pytest.mark.anyio
async def test_dry_run_fetch_returns_transcript_and_recording_metadata(monkeypatch):
    async def _resolve(client, *, meeting_id=None, join_web_url=None, tenant_id=None):
        return TeamsMeetingRef(meeting_id=meeting_id or "meeting-1", tenant_id=tenant_id)

    async def _transcript(client, meeting_ref):
        return (
            MeetingArtifact(artifact_type="transcript", artifact_id="tx-1", display_name="meeting.vtt"),
            "Action: Send notes.\nDecision: Proceed.",
        )

    async def _recordings(client, meeting_ref):
        return [
            MeetingArtifact(
                artifact_type="recording",
                artifact_id="rec-1",
                display_name="meeting.mp4",
            )
        ]

    async def _call_record(client, meeting_ref, *, call_record_id=None):
        return MeetingArtifact(artifact_type="call_record", artifact_id="call-1")

    monkeypatch.setattr(
        "tools.teams_pipeline_operator_tools._build_graph_client",
        lambda: object(),
    )
    monkeypatch.setattr("tools.teams_pipeline_operator_tools.resolve_meeting_reference", _resolve)
    monkeypatch.setattr("tools.teams_pipeline_operator_tools.fetch_preferred_transcript_text", _transcript)
    monkeypatch.setattr("tools.teams_pipeline_operator_tools.list_recording_artifacts", _recordings)
    monkeypatch.setattr("tools.teams_pipeline_operator_tools.enrich_meeting_with_call_record", _call_record)

    result = json.loads(await _teams_pipeline_dry_run_fetch({"meeting_id": "meeting-1"}))

    assert result["success"] is True
    assert result["meeting_ref"]["meeting_id"] == "meeting-1"
    assert result["transcript_available"] is True
    assert result["recording_count"] == 1
    assert result["call_record"]["artifact_id"] == "call-1"
