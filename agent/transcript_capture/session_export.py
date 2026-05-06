from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from .config import TranscriptCaptureConfig
from .formatting import TranscriptEvent, TranscriptMetadata, render_transcript
from .sanitize import force_redact_text, summarize_tool_event
from .writer import TranscriptWriter, stable_short_hash


def _iso_from_timestamp(value: Any) -> str:
    if value is None:
        return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(float(value), timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    return force_redact_text(str(value))


@dataclass(frozen=True)
class SessionFinalizeEntry:
    session_id: str
    session_key: str
    platform: str
    source_type: str = "gateway"
    chat_id: Optional[str] = None


class SessionTranscriptExporter:
    def __init__(self, session_store: Any, config: TranscriptCaptureConfig):
        self.session_store = session_store
        self.config = config
        self.writer = TranscriptWriter(config)

    def _message_to_events(self, message: Dict[str, Any]) -> List[TranscriptEvent]:
        role = message.get("role")
        timestamp = _iso_from_timestamp(message.get("timestamp"))
        if role in {"user", "assistant"}:
            return [TranscriptEvent(role=role, content=force_redact_text(message.get("content") or ""), timestamp=timestamp)]
        if role == "tool" or message.get("tool_name"):
            summary = summarize_tool_event(message)
            return [TranscriptEvent(role="tool", content="Tool event summary only; raw args/results omitted.", timestamp=timestamp, tool_name=summary.get("tool_name"), metadata={k: v for k, v in summary.items() if k not in {"role", "tool_name", "timestamp"}})]
        return []

    def export_finalized(self, entry: SessionFinalizeEntry) -> Path:
        session = self.session_store.get_session(entry.session_id) or {}
        messages = self.session_store.get_messages(entry.session_id)
        started_at = _iso_from_timestamp(session.get("started_at") or (messages[0].get("timestamp") if messages else None))
        finalized_at = _iso_from_timestamp(session.get("ended_at"))
        source = force_redact_text(entry.source_type or session.get("source") or "unknown")
        platform = force_redact_text(entry.platform or session.get("source") or "unknown")
        events: List[TranscriptEvent] = []
        for message in messages:
            events.extend(self._message_to_events(message))
        meta = TranscriptMetadata(
            platform=platform,
            source_type=source,
            session_key_hash=stable_short_hash(entry.session_key),
            session_id_hash=stable_short_hash(entry.session_id),
            started_at=started_at,
            finalized_at=finalized_at,
        )
        body = render_transcript(meta, events)
        date_prefix = started_at[:10] if len(started_at) >= 10 else datetime.now(timezone.utc).strftime("%Y-%m-%d")
        return self.writer.publish(date_prefix, platform, entry.session_key, entry.session_id, body)
