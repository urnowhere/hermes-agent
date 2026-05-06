"""Operator-facing tools for the Microsoft Teams meeting pipeline."""

from __future__ import annotations

from typing import Any

from hermes_constants import get_hermes_home
from tools.microsoft_graph_auth import GraphCredentials, MicrosoftGraphConfigError, MicrosoftGraphTokenProvider
from tools.microsoft_graph_client import MicrosoftGraphClient
from tools.registry import registry, tool_error, tool_result
from tools.teams_meetings import (
    enrich_meeting_with_call_record,
    fetch_preferred_transcript_text,
    list_recording_artifacts,
    resolve_meeting_reference,
)
from tools.teams_pipeline import TeamsMeetingPipeline
from tools.teams_pipeline_store import TeamsPipelineStore


DEFAULT_TEAMS_PIPELINE_STORE = get_hermes_home() / "teams_pipeline_store.json"


def _check_graph_requirements() -> bool:
    return GraphCredentials.from_env(required=False) is not None


def _parse_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _resolve_store_path(args: dict[str, Any]) -> str:
    return str(args.get("store_path") or DEFAULT_TEAMS_PIPELINE_STORE)


def _build_store(args: dict[str, Any]) -> TeamsPipelineStore:
    return TeamsPipelineStore(_resolve_store_path(args))


def _build_graph_client() -> MicrosoftGraphClient:
    provider = MicrosoftGraphTokenProvider.from_env()
    return MicrosoftGraphClient(provider)


def _compact_job_view(job: dict[str, Any]) -> dict[str, Any]:
    summary = dict(job.get("summary_payload") or {})
    transcript_text = summary.pop("transcript_text", None)
    if transcript_text:
        summary["transcript_preview"] = str(transcript_text)[:240]
    return {
        "job_id": job.get("job_id"),
        "event_id": job.get("event_id"),
        "status": job.get("status"),
        "retry_count": job.get("retry_count", 0),
        "updated_at": job.get("updated_at"),
        "meeting_ref": job.get("meeting_ref"),
        "selected_artifact_strategy": job.get("selected_artifact_strategy"),
        "error_info": job.get("error_info") or {},
        "summary_payload": summary or None,
    }


async def _teams_pipeline_list_jobs(args: dict[str, Any], **_kwargs: Any) -> str:
    try:
        store = _build_store(args)
        limit = max(1, min(_parse_int(args.get("limit"), 20), 100))
        status_filter = str(args.get("status") or "").strip().lower()
        jobs = list(store.list_jobs().values())
        jobs.sort(key=lambda item: str((item or {}).get("updated_at") or ""), reverse=True)
        if status_filter:
            jobs = [job for job in jobs if str(job.get("status") or "").lower() == status_filter]
        compact_jobs = [_compact_job_view(job) for job in jobs[:limit]]
        return tool_result(
            success=True,
            count=len(compact_jobs),
            store_path=_resolve_store_path(args),
            jobs=compact_jobs,
        )
    except Exception as exc:
        return tool_error(f"Failed to list Teams pipeline jobs: {exc}")


async def _teams_pipeline_replay_job(args: dict[str, Any], **_kwargs: Any) -> str:
    job_id = str(args.get("job_id") or "").strip()
    if not job_id:
        return tool_error("job_id is required")

    try:
        store = _build_store(args)
        existing = store.get_job(job_id)
        if not existing:
            return tool_error(f"Unknown Teams pipeline job: {job_id}")
        pipeline = TeamsMeetingPipeline(
            graph_client=_build_graph_client(),
            store=store,
            config=args.get("pipeline_config") or {},
        )
        replayed = await pipeline.run_job(job_id)
        return tool_result(
            success=True,
            replayed=True,
            job=_compact_job_view(replayed.to_dict()),
        )
    except MicrosoftGraphConfigError as exc:
        return tool_error(str(exc))
    except Exception as exc:
        return tool_error(f"Failed to replay Teams pipeline job: {exc}")


async def _teams_pipeline_dry_run_fetch(args: dict[str, Any], **_kwargs: Any) -> str:
    meeting_id = str(args.get("meeting_id") or "").strip() or None
    join_web_url = str(args.get("join_web_url") or "").strip() or None
    tenant_id = str(args.get("tenant_id") or "").strip() or None
    call_record_id = str(args.get("call_record_id") or "").strip() or None
    if not meeting_id and not join_web_url:
        return tool_error("meeting_id or join_web_url is required")

    try:
        client = _build_graph_client()
        meeting_ref = await resolve_meeting_reference(
            client,
            meeting_id=meeting_id,
            join_web_url=join_web_url,
            tenant_id=tenant_id,
        )
        transcript_artifact, transcript_text = await fetch_preferred_transcript_text(client, meeting_ref)
        recordings = await list_recording_artifacts(client, meeting_ref)
        call_record = await enrich_meeting_with_call_record(
            client,
            meeting_ref,
            call_record_id=call_record_id,
        )
        return tool_result(
            success=True,
            meeting_ref=meeting_ref.to_dict(),
            transcript_available=bool(transcript_artifact and transcript_text),
            transcript_artifact=transcript_artifact.to_dict() if transcript_artifact else None,
            transcript_preview=(transcript_text or "")[:240] or None,
            recording_count=len(recordings),
            recordings=[recording.to_dict() for recording in recordings[:5]],
            call_record=call_record.to_dict() if call_record else None,
        )
    except MicrosoftGraphConfigError as exc:
        return tool_error(str(exc))
    except Exception as exc:
        return tool_error(f"Failed to dry-run Teams artifact fetch: {exc}")


TEAMS_PIPELINE_LIST_JOBS_SCHEMA = {
    "name": "teams_pipeline_list_jobs",
    "description": "List recent Microsoft Teams meeting pipeline jobs from the durable store.",
    "parameters": {
        "type": "object",
        "properties": {
            "limit": {"type": "integer"},
            "status": {"type": "string"},
            "store_path": {"type": "string"},
        },
        "required": [],
    },
}

TEAMS_PIPELINE_REPLAY_JOB_SCHEMA = {
    "name": "teams_pipeline_replay_job",
    "description": "Replay a stored Microsoft Teams meeting pipeline job by job_id.",
    "parameters": {
        "type": "object",
        "properties": {
            "job_id": {"type": "string"},
            "store_path": {"type": "string"},
            "pipeline_config": {"type": "object"},
        },
        "required": ["job_id"],
    },
}

TEAMS_PIPELINE_DRY_RUN_FETCH_SCHEMA = {
    "name": "teams_pipeline_dry_run_fetch",
    "description": "Dry-run Microsoft Graph meeting artifact resolution without writing sinks.",
    "parameters": {
        "type": "object",
        "properties": {
            "meeting_id": {"type": "string"},
            "join_web_url": {"type": "string"},
            "tenant_id": {"type": "string"},
            "call_record_id": {"type": "string"},
        },
        "required": [],
    },
}


registry.register(
    name="teams_pipeline_list_jobs",
    toolset="microsoft_graph",
    schema=TEAMS_PIPELINE_LIST_JOBS_SCHEMA,
    handler=_teams_pipeline_list_jobs,
    check_fn=_check_graph_requirements,
    is_async=True,
    emoji="📋",
)

registry.register(
    name="teams_pipeline_replay_job",
    toolset="microsoft_graph",
    schema=TEAMS_PIPELINE_REPLAY_JOB_SCHEMA,
    handler=_teams_pipeline_replay_job,
    check_fn=_check_graph_requirements,
    is_async=True,
    emoji="🔁",
)

registry.register(
    name="teams_pipeline_dry_run_fetch",
    toolset="microsoft_graph",
    schema=TEAMS_PIPELINE_DRY_RUN_FETCH_SCHEMA,
    handler=_teams_pipeline_dry_run_fetch,
    check_fn=_check_graph_requirements,
    is_async=True,
    emoji="🧪",
)
