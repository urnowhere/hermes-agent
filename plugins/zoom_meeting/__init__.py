"""zoom_meeting plugin — webhook/RTMS-first Zoom meeting intelligence.

This first iteration deliberately targets the official-ish surfaces that are
most stable for a self-hosted agent:

- Server-to-server OAuth for account-scoped meeting metadata
- Webhook ingestion for meeting lifecycle events
- RTMS-style event ingestion for transcript/caption fragments
- Durable local artifacts so the agent can reason about meetings after the
  live call ends

It does NOT attempt to impersonate a participant or automate the Zoom UI.
That is a larger second phase and should build on top of the state/artifact
model introduced here rather than replacing it.
"""

from __future__ import annotations

from plugins.zoom_meeting.cli import register_cli as _register_cli
from plugins.zoom_meeting.cli import zoom_command as _zoom_command
from plugins.zoom_meeting.tools import (
    ZOOM_MEETING_ACTION_ITEMS_SCHEMA,
    ZOOM_MEETING_ARTIFACTS_SCHEMA,
    ZOOM_MEETING_EVENTS_SCHEMA,
    ZOOM_MEETING_STATUS_SCHEMA,
    ZOOM_MEETING_SUMMARY_SCHEMA,
    ZOOM_MEETING_TRANSCRIPT_SCHEMA,
    ZOOM_MEETING_WATCH_SCHEMA,
    check_zoom_meeting_requirements,
    handle_zoom_meeting_action_items,
    handle_zoom_meeting_artifacts,
    handle_zoom_meeting_events,
    handle_zoom_meeting_status,
    handle_zoom_meeting_summary,
    handle_zoom_meeting_transcript,
    handle_zoom_meeting_watch,
)

_TOOLS = (
    ("zoom_meeting_watch",      ZOOM_MEETING_WATCH_SCHEMA,      handle_zoom_meeting_watch,      "🎥"),
    ("zoom_meeting_status",     ZOOM_MEETING_STATUS_SCHEMA,     handle_zoom_meeting_status,     "🟢"),
    ("zoom_meeting_transcript", ZOOM_MEETING_TRANSCRIPT_SCHEMA, handle_zoom_meeting_transcript, "📝"),
    ("zoom_meeting_events",     ZOOM_MEETING_EVENTS_SCHEMA,     handle_zoom_meeting_events,     "📡"),
    ("zoom_meeting_summary",    ZOOM_MEETING_SUMMARY_SCHEMA,    handle_zoom_meeting_summary,    "🧠"),
    ("zoom_meeting_action_items", ZOOM_MEETING_ACTION_ITEMS_SCHEMA, handle_zoom_meeting_action_items, "✅"),
    ("zoom_meeting_artifacts",  ZOOM_MEETING_ARTIFACTS_SCHEMA,  handle_zoom_meeting_artifacts,  "📦"),
)


def register(ctx) -> None:
    """Register Zoom meeting tools and CLI surface."""
    for name, schema, handler, emoji in _TOOLS:
        ctx.register_tool(
            name=name,
            toolset="zoom_meeting",
            schema=schema,
            handler=handler,
            check_fn=check_zoom_meeting_requirements,
            emoji=emoji,
        )

    ctx.register_cli_command(
        name="zoom",
        help="Zoom meeting intelligence (watch, serve, ingest, transcript, action-items, artifacts)",
        setup_fn=_register_cli,
        handler_fn=_zoom_command,
        description=(
            "Track Zoom meetings through OAuth metadata fetches plus webhook/RTMS "
            "event ingestion, then export transcript, action-item, and summary artifacts."
        ),
    )
