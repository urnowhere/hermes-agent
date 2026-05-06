from __future__ import annotations

import re
from typing import Any, Dict
from urllib.parse import urlsplit, urlunsplit

from agent.redact import redact_sensitive_text

_REASONING_KEY_RE = re.compile(r"reasoning|thinking|thought|chain_of_thought|cot", re.IGNORECASE)


def force_redact_text(text: Any) -> str:
    if text is None:
        return ""
    redacted = redact_sensitive_text(str(text), force=True)
    # Defense in depth for generic bearer-ish values and private blocks.
    redacted = re.sub(r"(?i)(Bearer\s+)[A-Za-z0-9._=\-]{12,}", r"\1***", redacted)
    redacted = re.sub(
        r"-----BEGIN[A-Z ]*PRIVATE KEY-----[\s\S]*?-----END[A-Z ]*PRIVATE KEY-----",
        "[REDACTED PRIVATE KEY]",
        redacted,
    )
    return redacted


def drop_reasoning_fields(value: Any) -> Any:
    if isinstance(value, dict):
        clean = {}
        for key, item in value.items():
            if _REASONING_KEY_RE.search(str(key)):
                continue
            clean[key] = drop_reasoning_fields(item)
        return clean
    if isinstance(value, list):
        return [drop_reasoning_fields(item) for item in value]
    if isinstance(value, tuple):
        return tuple(drop_reasoning_fields(item) for item in value)
    return value


def summarize_tool_event(event: Dict[str, Any]) -> Dict[str, Any]:
    tool_name = force_redact_text(event.get("tool_name") or event.get("name") or "unknown")
    summary: Dict[str, Any] = {
        "role": "tool",
        "tool_name": tool_name,
        "timestamp": event.get("timestamp"),
    }
    if event.get("tool_call_id"):
        summary["tool_call_id"] = force_redact_text(event.get("tool_call_id"))
    if event.get("status"):
        summary["status"] = force_redact_text(event.get("status"))
    return summary


def sanitize_media(media: Any) -> Dict[str, Any]:
    if isinstance(media, dict):
        url = str(media.get("url") or media.get("href") or "")
        mime = media.get("mime") or media.get("content_type")
    else:
        url = str(media or "")
        mime = None
    split = urlsplit(url)
    safe_url = urlunsplit((split.scheme, split.netloc, split.path, "", "")) if split.scheme and split.netloc else force_redact_text(url)
    out: Dict[str, Any] = {"url": safe_url}
    if split.netloc:
        out["host"] = split.hostname or split.netloc
    if split.path:
        out["path_suffix"] = split.path.rsplit("/", 1)[-1]
    if mime:
        out["mime"] = force_redact_text(mime)
    return out
