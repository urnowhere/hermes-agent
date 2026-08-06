"""Generic display-only tool status card projection helpers."""

from __future__ import annotations

from collections import defaultdict, deque
from datetime import datetime, timezone
import logging
import threading
from typing import Any

_MAX_PENDING_TOOL_CALLS = 256
_MAX_BODY_LINES = 6
_MAX_LINE_CHARS = 240
_VALID_SEVERITIES = {"info", "success", "warning", "error"}
_VALID_REPLACE_POLICIES = {"append", "latest"}

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_pending_by_tool_call_id: dict[str, list[dict[str, Any]]] = defaultdict(list)
_pending_order: deque[str] = deque()


def normalize_tool_status_card(
    card: Any,
    *,
    tool_call_id: str = "",
    session_id: str = "",
) -> dict[str, Any] | None:
    """Return a bounded ToolStatusCard dict, or None for invalid input."""
    if not isinstance(card, dict):
        return None

    source = _clean_text(card.get("source"), default="")
    kind = _clean_text(card.get("kind"), default="")
    title = _clean_text(card.get("title"), default="")
    if not source or not kind or not title:
        return None

    normalized: dict[str, Any] = {
        "source": source,
        "kind": kind,
        "title": title,
        "body": _normalize_body(card.get("body")),
        "severity": _normalize_choice(card.get("severity"), _VALID_SEVERITIES, "info"),
        "dedupe_key": _clean_text(
            card.get("dedupe_key"),
            default=f"{tool_call_id}:{source}:{kind}:{title}",
        ),
        "group_key": _clean_text(card.get("group_key"), default=f"{source}:{kind}"),
        "replace_policy": _normalize_choice(
            card.get("replace_policy"),
            _VALID_REPLACE_POLICIES,
            "append",
        ),
        "visibility": _clean_text(card.get("visibility"), default="developer"),
        "tool_call_id": str(tool_call_id or card.get("tool_call_id") or ""),
        "session_id": str(session_id or card.get("session_id") or ""),
    }

    link = _normalize_link(card.get("link"))
    if link:
        normalized["link"] = link

    return normalized


def queue_tool_status_cards(
    cards: Any,
    *,
    tool_call_id: str = "",
    session_id: str = "",
) -> None:
    """Normalize and queue cards for a later CLI/UI-thread drain."""
    normalized_cards = [
        normalized
        for raw in _iter_card_candidates(cards)
        for normalized in [normalize_tool_status_card(
            raw,
            tool_call_id=tool_call_id,
            session_id=session_id,
        )]
        if normalized is not None
    ]
    if not normalized_cards or not tool_call_id:
        return

    with _lock:
        if tool_call_id not in _pending_by_tool_call_id:
            _pending_order.append(tool_call_id)
        _pending_by_tool_call_id[tool_call_id].extend(normalized_cards)
        while len(_pending_order) > _MAX_PENDING_TOOL_CALLS:
            stale_id = _pending_order.popleft()
            _pending_by_tool_call_id.pop(stale_id, None)


def project_and_queue_tool_status_cards(
    *,
    tool_name: str,
    args: dict[str, Any],
    result: str,
    task_id: str,
    session_id: str,
    tool_call_id: str,
    duration_ms: int,
) -> None:
    """Run the display-only projection hook for any completed tool call."""
    try:
        from hermes_cli.plugins import invoke_hook

        cards = invoke_hook(
            "project_tool_status_cards",
            tool_name=tool_name,
            args=args,
            result=result,
            task_id=task_id,
            session_id=session_id,
            tool_call_id=tool_call_id,
            duration_ms=duration_ms,
        )
        queue_tool_status_cards(
            cards,
            tool_call_id=tool_call_id,
            session_id=session_id,
        )
    except Exception:
        logger.debug(
            "Tool status card projection failed for %s",
            tool_name,
            exc_info=True,
        )


def drain_tool_status_cards(tool_call_id: str) -> list[dict[str, Any]]:
    """Return and remove cards queued for *tool_call_id*."""
    key = str(tool_call_id or "")
    if not key:
        return []
    with _lock:
        cards = list(_pending_by_tool_call_id.pop(key, []))
        try:
            _pending_order.remove(key)
        except ValueError:
            pass
    return cards


def display_event_from_card(card: dict[str, Any]) -> dict[str, Any]:
    """Build the compact session/debug event for a ToolStatusCard."""
    event = {
        "version": 1,
        "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "tool_call_id": str(card.get("tool_call_id") or ""),
        "source": str(card.get("source") or ""),
        "kind": str(card.get("kind") or ""),
        "title": str(card.get("title") or ""),
        "severity": str(card.get("severity") or "info"),
        "dedupe_key": str(card.get("dedupe_key") or ""),
        "group_key": str(card.get("group_key") or ""),
        "rendered": bool(card.get("rendered", False)),
    }
    link = _normalize_link(card.get("link"))
    if link:
        event["link"] = link
    return event


def _iter_card_candidates(cards: Any):
    if cards is None:
        return
    if isinstance(cards, dict):
        yield cards
        return
    if isinstance(cards, (list, tuple)):
        for item in cards:
            if isinstance(item, (list, tuple)):
                yield from _iter_card_candidates(item)
            else:
                yield item


def _normalize_body(value: Any) -> list[str]:
    if isinstance(value, (list, tuple)):
        lines = [str(item) for item in value]
    else:
        lines = str(value or "").splitlines()
    normalized = []
    for line in lines:
        text = _clean_text(line, default="")
        if not text:
            continue
        normalized.append(text[:_MAX_LINE_CHARS])
        if len(normalized) >= _MAX_BODY_LINES:
            break
    return normalized


def _normalize_link(value: Any) -> dict[str, str] | None:
    if not isinstance(value, dict):
        return None
    url = _clean_text(value.get("url"), default="")
    if not url:
        return None
    label = _clean_text(value.get("label"), default="Open")
    return {"label": label, "url": url}


def _normalize_choice(value: Any, allowed: set[str], default: str) -> str:
    text = _clean_text(value, default=default).lower()
    return text if text in allowed else default


def _clean_text(value: Any, *, default: str) -> str:
    text = str(value if value is not None else default).strip()
    return " ".join(text.split())
