from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, Optional

from .sanitize import drop_reasoning_fields, force_redact_text

SCHEMA_VERSION = 1
REDACTION_VERSION = "forced-v1"


@dataclass(frozen=True)
class TranscriptMetadata:
    platform: str
    source_type: str
    session_key_hash: str
    session_id_hash: str
    started_at: str
    finalized_at: str
    schema_version: int = SCHEMA_VERSION
    redaction_version: str = REDACTION_VERSION


@dataclass(frozen=True)
class TranscriptEvent:
    role: str
    content: str = ""
    timestamp: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    tool_name: Optional[str] = None


def _format_metadata(metadata: Dict[str, Any]) -> str:
    clean = drop_reasoning_fields(metadata or {})
    if not clean:
        return ""
    return force_redact_text(json.dumps(clean, sort_keys=True, default=str))


def render_transcript(meta: TranscriptMetadata, events: Iterable[TranscriptEvent]) -> str:
    header = [
        f"schema_version: {meta.schema_version}",
        f"redaction_version: {meta.redaction_version}",
        f"platform: {force_redact_text(meta.platform)}",
        f"source_type: {force_redact_text(meta.source_type)}",
        f"session_key_hash: {force_redact_text(meta.session_key_hash)}",
        f"session_id_hash: {force_redact_text(meta.session_id_hash)}",
        f"started_at: {force_redact_text(meta.started_at)}",
        f"finalized_at: {force_redact_text(meta.finalized_at)}",
        "",
    ]
    lines = header
    for event in events:
        role = (event.role or "event").upper()
        if event.tool_name:
            role = f"TOOL {force_redact_text(event.tool_name)}"
        stamp = force_redact_text(event.timestamp or "unknown-time")
        content = force_redact_text(event.content or "")
        lines.append(f"[{stamp}] {role}:")
        if content:
            lines.append(content)
        meta_text = _format_metadata(event.metadata)
        if meta_text:
            lines.append(f"metadata: {meta_text}")
        lines.append("")
    lines.append("END_SESSION")
    return "\n".join(lines) + "\n"
