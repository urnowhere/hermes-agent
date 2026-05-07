"""Append-only audit log for Telegram fleet events.

Lives at ``~/.hermes/telegram_fleet_audit.jsonl``.  One JSON object per line.
Tokens are never recorded — only the bot username and the action taken.
The file is created with mode 0600.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from hermes_constants import get_hermes_home

logger = logging.getLogger(__name__)

AUDIT_FILENAME = "telegram_fleet_audit.jsonl"


def get_audit_path() -> Path:
    return get_hermes_home() / AUDIT_FILENAME


def audit_event(action: str, **fields: Any) -> None:
    """Append a single event to the audit log.

    *action* is a short verb like ``spawn_requested``, ``spawn_confirmed``,
    ``token_rotated``, ``decommissioned``, ``swarm_started``,
    ``swarm_completed``.  Tokens, raw nonces, and any large payload should
    NOT be passed in — only redacted/structural metadata.
    """
    record: Dict[str, Any] = {
        "ts": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "action": str(action),
    }
    for key, value in fields.items():
        if key == "token" or key.endswith("_token"):
            # Defense in depth — never write tokens to the audit log.
            value = _redact(str(value))
        record[key] = _stringify_safely(value)

    path = get_audit_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        existed = path.exists()
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
        if not existed:
            try:
                os.chmod(path, 0o600)
            except OSError as e:  # pragma: no cover
                logger.debug("could not chmod 0600 on %s: %s", path, e)
    except OSError as e:
        logger.warning("could not write fleet audit event %s: %s", action, e)


def _redact(value: str) -> str:
    if not value:
        return ""
    if len(value) <= 8:
        return "***"
    return f"{value[:4]}…{value[-4:]}"


def _stringify_safely(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, (list, tuple)):
        return [_stringify_safely(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _stringify_safely(v) for k, v in value.items()}
    return str(value)


def read_recent_events(limit: int = 50, *, path: Optional[Path] = None) -> list[Dict[str, Any]]:
    """Read up to *limit* most recent events.  Test/CLI helper.

    Reads the whole file (audit logs are small) and returns the tail.
    """
    p = path or get_audit_path()
    if not p.exists():
        return []
    out: list[Dict[str, Any]] = []
    try:
        with p.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except OSError:
        return []
    return out[-limit:] if limit > 0 else out
