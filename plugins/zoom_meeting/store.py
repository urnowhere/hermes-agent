"""Persistent state, event normalization, and artifact export for Zoom meetings."""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from hermes_constants import get_hermes_home


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _slugify(value: str) -> str:
    value = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip())
    return value.strip("-") or "unknown"


def _coerce_text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _parse_transcript_line(line: str) -> Dict[str, str]:
    text = line.strip()
    if not text:
        return {"speaker": "", "text": ""}

    if text.startswith("[") and "] " in text:
        text = text.split("] ", 1)[1].strip()

    if ": " in text:
        speaker, message = text.split(": ", 1)
        return {"speaker": speaker.strip(), "text": message.strip()}

    return {"speaker": "", "text": text}


def _deep_get(obj: Any, *keys: str) -> Any:
    current = obj
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def compute_webhook_validation_token(secret: str, plain_token: str) -> str:
    return hmac.new(secret.encode("utf-8"), plain_token.encode("utf-8"), hashlib.sha256).hexdigest()


def _find_first(obj: Any, candidate_keys: Iterable[str]) -> Any:
    stack = [obj]
    keyset = {k.lower() for k in candidate_keys}
    while stack:
        current = stack.pop()
        if isinstance(current, dict):
            for key, value in current.items():
                if key.lower() in keyset and value not in (None, ""):
                    return value
                stack.append(value)
        elif isinstance(current, list):
            stack.extend(reversed(current))
    return None


def extract_meeting_identity(payload: Dict[str, Any]) -> Dict[str, str]:
    obj = _deep_get(payload, "payload", "object") or payload.get("object") or payload
    meeting_id = _find_first(obj, ("id", "meeting_id", "meetingId", "meetingNumber"))
    meeting_uuid = _find_first(obj, ("uuid", "meeting_uuid", "meetingUUID"))
    topic = _find_first(obj, ("topic", "meeting_topic", "title"))
    return {
        "meeting_id": str(meeting_id or meeting_uuid or "unknown"),
        "meeting_uuid": str(meeting_uuid or meeting_id or ""),
        "topic": str(topic or ""),
    }


def _extract_transcript_entries(payload: Dict[str, Any]) -> List[Dict[str, str]]:
    event_name = _coerce_text(payload.get("event") or payload.get("event_type") or payload.get("type")).lower()
    want_loose_match = any(token in event_name for token in ("transcript", "caption", "rtms", "utterance"))
    results: List[Dict[str, str]] = []

    def visit(node: Any, path: tuple[str, ...]) -> None:
        if isinstance(node, dict):
            lowered = {str(k).lower(): v for k, v in node.items()}
            path_joined = ".".join(path).lower()
            text = ""
            key_used = ""
            for key in ("transcript", "caption", "utterance", "content", "text", "message"):
                value = lowered.get(key)
                value_text = _coerce_text(value)
                if value_text:
                    text = value_text
                    key_used = key
                    break

            speaker = ""
            for key in ("speaker", "speaker_name", "participant_name", "display_name", "user_name", "name"):
                value_text = _coerce_text(lowered.get(key))
                if value_text:
                    speaker = value_text
                    break

            ts = ""
            for key in ("timestamp", "time", "start_time", "ts"):
                value_text = _coerce_text(lowered.get(key))
                if value_text:
                    ts = value_text
                    break

            if text:
                relevant_path = any(token in path_joined for token in ("transcript", "caption", "utterance", "chat"))
                transcriptish_key = key_used in {"transcript", "caption", "utterance"}
                if transcriptish_key or relevant_path or want_loose_match:
                    results.append({"speaker": speaker, "text": text, "timestamp": ts})

            for key, value in node.items():
                visit(value, path + (str(key),))
        elif isinstance(node, list):
            for idx, value in enumerate(node):
                visit(value, path + (str(idx),))

    visit(payload, ())

    deduped: List[Dict[str, str]] = []
    seen = set()
    for item in results:
        key = (item["speaker"], item["text"], item["timestamp"])
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


def normalize_event(payload: Dict[str, Any]) -> Dict[str, Any]:
    identity = extract_meeting_identity(payload)
    event_name = _coerce_text(payload.get("event") or payload.get("event_type") or payload.get("type")) or "unknown"
    transcript_entries = _extract_transcript_entries(payload)
    lifecycle = "lifecycle" if any(token in event_name.lower() for token in ("meeting.", "recording.", "participant.")) else "data"
    return {
        "received_at": _utcnow_iso(),
        "event": event_name,
        "kind": "transcript" if transcript_entries else lifecycle,
        "meeting_id": identity["meeting_id"],
        "meeting_uuid": identity["meeting_uuid"],
        "topic": identity["topic"],
        "transcript_entries": transcript_entries,
        "payload": payload,
    }


class ZoomMeetingStore:
    def __init__(self, root: Optional[Path] = None):
        base = root or (Path(get_hermes_home()) / "cache" / "zoom" / "meetings")
        self.root = Path(base)
        self.root.mkdir(parents=True, exist_ok=True)

    def meeting_dir(self, meeting_id: str) -> Path:
        path = self.root / _slugify(meeting_id)
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _state_path(self, meeting_id: str) -> Path:
        return self.meeting_dir(meeting_id) / "state.json"

    def _events_path(self, meeting_id: str) -> Path:
        return self.meeting_dir(meeting_id) / "events.jsonl"

    def _transcript_path(self, meeting_id: str) -> Path:
        return self.meeting_dir(meeting_id) / "transcript.txt"

    def _summary_path(self, meeting_id: str) -> Path:
        return self.meeting_dir(meeting_id) / "summary.md"

    def load_state(self, meeting_id: str) -> Dict[str, Any]:
        path = self._state_path(meeting_id)
        if not path.is_file():
            return {}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def save_state(self, meeting_id: str, state: Dict[str, Any]) -> Dict[str, Any]:
        path = self._state_path(meeting_id)
        path.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")
        return state

    def ensure_meeting(self, meeting_id: str, metadata: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        state = self.load_state(meeting_id)
        if not state:
            state = {
                "meeting_id": meeting_id,
                "meeting_uuid": "",
                "topic": "",
                "status": "initialized",
                "created_at": _utcnow_iso(),
                "updated_at": _utcnow_iso(),
                "transcript_lines": 0,
                "event_count": 0,
                "participants": [],
            }
        if metadata:
            for key in ("uuid", "id", "topic", "join_url", "start_time", "duration", "timezone", "host_id", "agenda"):
                value = metadata.get(key)
                if value not in (None, ""):
                    state_key = "meeting_uuid" if key == "uuid" else ("meeting_id" if key == "id" else key)
                    state[state_key] = value
            state["status"] = state.get("status") or "watched"
        state["updated_at"] = _utcnow_iso()
        return self.save_state(meeting_id, state)

    def ingest_event(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        normalized = normalize_event(payload)
        meeting_id = str(normalized["meeting_id"] or "unknown")
        state = self.ensure_meeting(meeting_id, metadata={"id": meeting_id, "uuid": normalized.get("meeting_uuid"), "topic": normalized.get("topic")})

        with self._events_path(meeting_id).open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(normalized, ensure_ascii=False) + "\n")

        transcript_entries = normalized["transcript_entries"]
        if transcript_entries:
            with self._transcript_path(meeting_id).open("a", encoding="utf-8") as fh:
                for entry in transcript_entries:
                    speaker = entry["speaker"] or "Unknown"
                    text = entry["text"]
                    ts = f"[{entry['timestamp']}] " if entry["timestamp"] else ""
                    fh.write(f"{ts}{speaker}: {text}\n")

        participants = set(state.get("participants") or [])
        payload_obj = _deep_get(payload, "payload", "object") or payload.get("object") or {}
        candidate_name = _find_first(payload_obj, ("participant_name", "display_name", "user_name", "name"))
        if isinstance(candidate_name, str) and candidate_name.strip():
            participants.add(candidate_name.strip())

        state["updated_at"] = _utcnow_iso()
        state["last_event"] = normalized["event"]
        state["last_event_at"] = normalized["received_at"]
        state["event_count"] = int(state.get("event_count") or 0) + 1
        state["transcript_lines"] = int(state.get("transcript_lines") or 0) + len(transcript_entries)
        state["participants"] = sorted(participants)

        event_lower = normalized["event"].lower()
        if "meeting.started" in event_lower:
            state["status"] = "started"
        elif "meeting.ended" in event_lower:
            state["status"] = "ended"
            state["ended_at"] = normalized["received_at"]
        elif transcript_entries and state.get("status") in ("initialized", "watched"):
            state["status"] = "active"

        self.save_state(meeting_id, state)
        return normalized

    def read_transcript(self, meeting_id: str, last: Optional[int] = None) -> str:
        path = self._transcript_path(meeting_id)
        if not path.is_file():
            return ""
        lines = path.read_text(encoding="utf-8").splitlines()
        if isinstance(last, int) and last > 0:
            lines = lines[-last:]
        return "\n".join(lines)

    def read_events(self, meeting_id: str, last: Optional[int] = None) -> List[Dict[str, Any]]:
        path = self._events_path(meeting_id)
        if not path.is_file():
            return []
        lines = path.read_text(encoding="utf-8").splitlines()
        if isinstance(last, int) and last > 0:
            lines = lines[-last:]
        result: List[Dict[str, Any]] = []
        for line in lines:
            try:
                result.append(json.loads(line))
            except Exception:
                continue
        return result

    def analyze_meeting(self, meeting_id: str) -> Dict[str, Any]:
        transcript = self.read_transcript(meeting_id)
        action_items: List[Dict[str, str]] = []
        decisions: List[Dict[str, str]] = []
        questions: List[Dict[str, str]] = []
        seen_actions = set()
        seen_decisions = set()
        seen_questions = set()

        action_tokens = (
            "action item",
            "todo",
            "follow up",
            "follow-up",
            "next step",
            "need to",
            "needs to",
            "should ",
            "please ",
            "will ",
        )
        decision_tokens = (
            "decided",
            "decision",
            "agreed",
            "approved",
            "chosen",
            "we'll go with",
            "we will go with",
        )
        question_tokens = ("who ", "what ", "when ", "where ", "why ", "how ", "can ", "should ", "do we ", "does ")

        for raw_line in transcript.splitlines():
            parsed = _parse_transcript_line(raw_line)
            speaker = parsed["speaker"]
            text = parsed["text"]
            lowered = text.lower()
            if not text:
                continue

            if any(token in lowered for token in action_tokens):
                key = (speaker, text)
                if key not in seen_actions:
                    seen_actions.add(key)
                    action_items.append(
                        {
                            "owner": speaker,
                            "text": text,
                            "source": raw_line,
                        }
                    )

            if any(token in lowered for token in decision_tokens):
                key = (speaker, text)
                if key not in seen_decisions:
                    seen_decisions.add(key)
                    decisions.append(
                        {
                            "speaker": speaker,
                            "text": text,
                            "source": raw_line,
                        }
                    )

            if text.endswith("?") or lowered.startswith(question_tokens):
                key = (speaker, text)
                if key not in seen_questions:
                    seen_questions.add(key)
                    questions.append(
                        {
                            "speaker": speaker,
                            "text": text,
                            "source": raw_line,
                        }
                    )

        return {
            "action_items": action_items[:12],
            "decisions": decisions[:12],
            "questions": questions[:12],
        }

    def render_markdown_summary(self, meeting_id: str) -> str:
        state = self.load_state(meeting_id)
        transcript = self.read_transcript(meeting_id)
        events = self.read_events(meeting_id, last=10)
        analysis = self.analyze_meeting(meeting_id)
        lines = transcript.splitlines()
        excerpt = lines[:8]
        tail = lines[-8:] if len(lines) > 8 else []
        if tail and excerpt != tail:
            excerpt = excerpt + ["...", *tail]

        md = [
            f"# Zoom Meeting Summary — {state.get('topic') or meeting_id}",
            "",
            f"- Meeting ID: `{state.get('meeting_id') or meeting_id}`",
            f"- UUID: `{state.get('meeting_uuid') or ''}`",
            f"- Status: `{state.get('status') or 'unknown'}`",
            f"- Transcript lines: `{state.get('transcript_lines') or 0}`",
            f"- Event count: `{state.get('event_count') or 0}`",
        ]
        if state.get("participants"):
            md.append(f"- Participants seen: {', '.join(state['participants'])}")
        if state.get("start_time"):
            md.append(f"- Scheduled start: `{state['start_time']}`")
        if state.get("ended_at"):
            md.append(f"- Ended at: `{state['ended_at']}`")
        md.extend(["", "## Recent Event Feed", ""])
        if events:
            for event in events:
                md.append(f"- `{event.get('received_at')}` `{event.get('event')}` ({event.get('kind')})")
        else:
            md.append("- No events captured yet.")

        md.extend(["", "## Action Items", ""])
        if analysis["action_items"]:
            for item in analysis["action_items"]:
                owner = item["owner"] or "Unassigned"
                md.append(f"- **{owner}**: {item['text']}")
        else:
            md.append("- No action-item signals detected yet.")

        md.extend(["", "## Decisions", ""])
        if analysis["decisions"]:
            for item in analysis["decisions"]:
                speaker = item["speaker"] or "Unknown"
                md.append(f"- **{speaker}**: {item['text']}")
        else:
            md.append("- No decision signals detected yet.")

        md.extend(["", "## Open Questions", ""])
        if analysis["questions"]:
            for item in analysis["questions"]:
                speaker = item["speaker"] or "Unknown"
                md.append(f"- **{speaker}**: {item['text']}")
        else:
            md.append("- No question signals detected yet.")

        md.extend(["", "## Transcript Excerpt", ""])
        if excerpt:
            md.append("```text")
            md.extend(excerpt)
            md.append("```")
        else:
            md.append("No transcript captured yet.")
        return "\n".join(md) + "\n"

    def write_summary(self, meeting_id: str) -> Path:
        summary = self.render_markdown_summary(meeting_id)
        path = self._summary_path(meeting_id)
        path.write_text(summary, encoding="utf-8")
        return path

    def export_artifacts(self, meeting_id: str, *, fmt: str = "markdown", output_path: Optional[str] = None) -> Path:
        fmt = (fmt or "markdown").strip().lower()
        state = self.load_state(meeting_id)
        transcript = self.read_transcript(meeting_id)
        events = self.read_events(meeting_id)
        if output_path:
            path = Path(output_path).expanduser()
        else:
            suffix = "md" if fmt == "markdown" else "json"
            path = self.meeting_dir(meeting_id) / f"artifact.{suffix}"
        path.parent.mkdir(parents=True, exist_ok=True)

        if fmt == "json":
            payload = {
                "state": state,
                "transcript": transcript,
                "events": events,
                "analysis": self.analyze_meeting(meeting_id),
            }
            path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        else:
            path.write_text(self.render_markdown_summary(meeting_id), encoding="utf-8")
        return path
