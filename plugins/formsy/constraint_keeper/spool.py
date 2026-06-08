"""Local FIFO JSONL spool for Constraint Keeper evidence."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

_SAFE_PATH_RE = re.compile(r"[^A-Za-z0-9_.:-]+")


class EvidenceSpool:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).expanduser()

    def append(self, *, task_id: str, run_id: str, event: dict[str, Any]) -> None:
        path = self._path(task_id, run_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"acked": False, "event": event}, ensure_ascii=False) + "\n")

    def pending(self, task_id: str, run_id: str) -> list[dict[str, Any]]:
        records = self._read_records(task_id, run_id)
        events = [
            record.get("event")
            for record in records
            if not record.get("acked") and isinstance(record.get("event"), dict)
        ]
        return sorted(events, key=lambda event: int(event.get("sequence") or 0))

    def mark_acked(self, *, task_id: str, run_id: str, event_id: str) -> None:
        path = self._path(task_id, run_id)
        if not path.exists():
            return
        records = self._read_records(task_id, run_id)
        for record in records:
            event = record.get("event")
            if isinstance(event, dict) and event.get("event_id") == event_id:
                record["acked"] = True
        with path.open("w", encoding="utf-8") as handle:
            for record in records:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    def _read_records(self, task_id: str, run_id: str) -> list[dict[str, Any]]:
        path = self._path(task_id, run_id)
        if not path.exists():
            return []
        records: list[dict[str, Any]] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(record, dict):
                records.append(record)
        return records

    def _path(self, task_id: str, run_id: str) -> Path:
        return self.root / _safe_path_part(task_id) / _safe_path_part(run_id) / "events.jsonl"


def _safe_path_part(value: str) -> str:
    text = _SAFE_PATH_RE.sub("-", str(value or "").strip()).strip("-")
    return text or "default"
