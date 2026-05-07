"""Agent-facing tools for the zoom_meeting plugin."""

from __future__ import annotations

import json
import os
from typing import Any, Dict, Optional

from plugins.zoom_meeting.client import ZoomClient, ZoomClientError, ZoomCredentials
from plugins.zoom_meeting.store import ZoomMeetingStore


def _json(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False)


def _ok(**payload: Any) -> str:
    return _json({"success": True, **payload})


def _err(message: str, **payload: Any) -> str:
    return _json({"success": False, "error": message, **payload})


def check_zoom_meeting_requirements() -> bool:
    try:
        import requests  # noqa: F401
    except ImportError:
        return False
    return True


def _store() -> ZoomMeetingStore:
    return ZoomMeetingStore()


def _client_from_env() -> Optional[ZoomClient]:
    account_id = os.getenv("ZOOM_ACCOUNT_ID", "").strip()
    client_id = os.getenv("ZOOM_CLIENT_ID", "").strip()
    client_secret = os.getenv("ZOOM_CLIENT_SECRET", "").strip()
    if not (account_id and client_id and client_secret):
        return None
    return ZoomClient(ZoomCredentials(account_id=account_id, client_id=client_id, client_secret=client_secret))


ZOOM_MEETING_WATCH_SCHEMA: Dict[str, Any] = {
    "name": "zoom_meeting_watch",
    "description": (
        "Initialize or refresh local state for a Zoom meeting using account-scoped REST metadata. "
        "This creates a durable meeting workspace under ~/.hermes/cache/zoom/meetings/<meeting_id>/ "
        "so later webhook/RTMS events can accumulate transcript and status data there."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "meeting_id": {"type": "string", "description": "Zoom meeting ID."},
            "fetch_recordings": {
                "type": "boolean",
                "description": "Also fetch recording metadata for past meetings if the account can access it.",
            },
        },
        "required": ["meeting_id"],
        "additionalProperties": False,
    },
}

ZOOM_MEETING_STATUS_SCHEMA: Dict[str, Any] = {
    "name": "zoom_meeting_status",
    "description": "Return local state for a watched Zoom meeting, including transcript and event counts.",
    "parameters": {
        "type": "object",
        "properties": {
            "meeting_id": {"type": "string"},
        },
        "required": ["meeting_id"],
        "additionalProperties": False,
    },
}

ZOOM_MEETING_TRANSCRIPT_SCHEMA: Dict[str, Any] = {
    "name": "zoom_meeting_transcript",
    "description": "Read the locally accumulated transcript for a Zoom meeting.",
    "parameters": {
        "type": "object",
        "properties": {
            "meeting_id": {"type": "string"},
            "last": {"type": "integer", "minimum": 1},
        },
        "required": ["meeting_id"],
        "additionalProperties": False,
    },
}

ZOOM_MEETING_EVENTS_SCHEMA: Dict[str, Any] = {
    "name": "zoom_meeting_events",
    "description": "Read normalized Zoom meeting events captured by the webhook server.",
    "parameters": {
        "type": "object",
        "properties": {
            "meeting_id": {"type": "string"},
            "last": {"type": "integer", "minimum": 1},
        },
        "required": ["meeting_id"],
        "additionalProperties": False,
    },
}

ZOOM_MEETING_SUMMARY_SCHEMA: Dict[str, Any] = {
    "name": "zoom_meeting_summary",
    "description": (
        "Render a deterministic markdown summary from the locally captured Zoom meeting state, "
        "events, and transcript excerpt."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "meeting_id": {"type": "string"},
        },
        "required": ["meeting_id"],
        "additionalProperties": False,
    },
}

ZOOM_MEETING_ACTION_ITEMS_SCHEMA: Dict[str, Any] = {
    "name": "zoom_meeting_action_items",
    "description": (
        "Extract deterministic action items, decisions, and open questions from the locally captured "
        "Zoom meeting transcript."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "meeting_id": {"type": "string"},
        },
        "required": ["meeting_id"],
        "additionalProperties": False,
    },
}

ZOOM_MEETING_ARTIFACTS_SCHEMA: Dict[str, Any] = {
    "name": "zoom_meeting_artifacts",
    "description": (
        "Export a local artifact bundle for a watched Zoom meeting. "
        "Formats: markdown (human-friendly report) or json (state + transcript + events)."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "meeting_id": {"type": "string"},
            "format": {"type": "string", "enum": ["markdown", "json"]},
            "output_path": {"type": "string"},
        },
        "required": ["meeting_id"],
        "additionalProperties": False,
    },
}


def handle_zoom_meeting_watch(args: Dict[str, Any], **_kw) -> str:
    meeting_id = str(args.get("meeting_id") or "").strip()
    if not meeting_id:
        return _err("meeting_id is required")

    store = _store()
    client = _client_from_env()
    metadata: Dict[str, Any] = {}
    recordings: Dict[str, Any] | None = None
    if client is None:
        state = store.ensure_meeting(meeting_id)
        return _ok(
            meeting_id=meeting_id,
            state=state,
            warning=(
                "Zoom OAuth env vars are not configured. Created the local workspace only; "
                "set ZOOM_ACCOUNT_ID, ZOOM_CLIENT_ID, and ZOOM_CLIENT_SECRET to fetch live metadata."
            ),
        )

    try:
        metadata = client.fetch_meeting(meeting_id)
        if bool(args.get("fetch_recordings", False)):
            try:
                recordings = client.fetch_meeting_recordings(meeting_id)
            except ZoomClientError as exc:
                recordings = {"error": str(exc)}
    except ZoomClientError as exc:
        state = store.ensure_meeting(meeting_id)
        return _err(str(exc), meeting_id=meeting_id, state=state)

    state = store.ensure_meeting(meeting_id, metadata=metadata)
    if recordings:
        state["recordings"] = recordings
        store.save_state(meeting_id, state)
    return _ok(meeting_id=meeting_id, state=state)


def handle_zoom_meeting_status(args: Dict[str, Any], **_kw) -> str:
    meeting_id = str(args.get("meeting_id") or "").strip()
    if not meeting_id:
        return _err("meeting_id is required")
    state = _store().load_state(meeting_id)
    if not state:
        return _err(f"no local Zoom meeting state found for {meeting_id!r}")
    return _ok(meeting_id=meeting_id, state=state)


def handle_zoom_meeting_transcript(args: Dict[str, Any], **_kw) -> str:
    meeting_id = str(args.get("meeting_id") or "").strip()
    if not meeting_id:
        return _err("meeting_id is required")
    last = args.get("last")
    transcript = _store().read_transcript(meeting_id, last=last if isinstance(last, int) else None)
    if not transcript:
        return _err(f"no transcript captured yet for {meeting_id!r}", meeting_id=meeting_id)
    return _ok(meeting_id=meeting_id, transcript=transcript)


def handle_zoom_meeting_events(args: Dict[str, Any], **_kw) -> str:
    meeting_id = str(args.get("meeting_id") or "").strip()
    if not meeting_id:
        return _err("meeting_id is required")
    last = args.get("last")
    events = _store().read_events(meeting_id, last=last if isinstance(last, int) else None)
    return _ok(meeting_id=meeting_id, events=events, count=len(events))


def handle_zoom_meeting_summary(args: Dict[str, Any], **_kw) -> str:
    meeting_id = str(args.get("meeting_id") or "").strip()
    if not meeting_id:
        return _err("meeting_id is required")
    store = _store()
    state = store.load_state(meeting_id)
    if not state:
        return _err(f"no local Zoom meeting state found for {meeting_id!r}")
    summary = store.render_markdown_summary(meeting_id)
    path = store.write_summary(meeting_id)
    return _ok(meeting_id=meeting_id, summary=summary, path=str(path))


def handle_zoom_meeting_action_items(args: Dict[str, Any], **_kw) -> str:
    meeting_id = str(args.get("meeting_id") or "").strip()
    if not meeting_id:
        return _err("meeting_id is required")
    store = _store()
    state = store.load_state(meeting_id)
    if not state:
        return _err(f"no local Zoom meeting state found for {meeting_id!r}")
    analysis = store.analyze_meeting(meeting_id)
    return _ok(meeting_id=meeting_id, **analysis)


def handle_zoom_meeting_artifacts(args: Dict[str, Any], **_kw) -> str:
    meeting_id = str(args.get("meeting_id") or "").strip()
    if not meeting_id:
        return _err("meeting_id is required")
    store = _store()
    state = store.load_state(meeting_id)
    if not state:
        return _err(f"no local Zoom meeting state found for {meeting_id!r}")
    fmt = str(args.get("format") or "markdown")
    output_path = args.get("output_path")
    path = store.export_artifacts(meeting_id, fmt=fmt, output_path=output_path if isinstance(output_path, str) else None)
    return _ok(meeting_id=meeting_id, format=fmt, path=str(path))
