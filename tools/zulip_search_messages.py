"""Zulip message search tool — fetch and search message history.

Provides a single flexible tool that wraps Zulip's ``/messages`` API.
The agent can search by stream+topic, full-text query, message anchor,
and paginate through results — all through one interface.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def _check_zulip_search_requirements() -> bool:
    """Check that the zulip_search_messages tool is usable.

    The tool is available on Zulip sessions (gateway context) or when
    Zulip credentials are explicitly configured.  Follows the same
    pattern as ``_check_send_message`` in ``send_message_tool.py``.
    """
    # 1. Session-context check (gateway-side: agent knows the platform).
    try:
        from gateway.session_context import get_session_env
        platform = get_session_env("HERMES_SESSION_PLATFORM", "")
        if platform == "zulip":
            return True
    except Exception:
        pass

    # 2. Gateway running check (same as send_message).
    try:
        from gateway.status import is_gateway_running
        if not is_gateway_running():
            return False
    except Exception:
        pass

    # 3. Explicit env-var check (CLI/standalone usage).
    if (
        os.getenv("ZULIP_SITE_URL")
        and os.getenv("ZULIP_BOT_EMAIL")
        and os.getenv("ZULIP_API_KEY")
    ):
        return True

    return False


def zulip_search_messages(
    stream: Optional[str] = None,
    topic: Optional[str] = None,
    query: Optional[str] = None,
    anchor: Optional[str] = None,
    num_before: int = 20,
    num_after: int = 0,
    *,
    task_id: Optional[str] = None,
) -> str:
    """Search Zulip message history.

    Fetches messages from a Zulip organization using the bot's credentials.
    Supports narrowing by stream, topic, full-text search, and pagination
    via message ID anchors.

    Args:
        stream: Stream name to narrow to (e.g. ``"general"``). Optional.
        topic: Topic name to narrow to (e.g. ``"database"``). Optional.
        query: Full-text search using Zulip's search syntax.
               Supports operators like ``sender:alice@example.com``,
               ``has:link``, ``is:starred``, ``near:<id>``, etc. Optional.
        anchor: Message ID to anchor around, or ``"newest"`` / ``"oldest"``.
                Defaults to ``"newest"`` (most recent messages).
        num_before: Number of messages to fetch before the anchor. Default 20.
        num_after: Number of messages to fetch after the anchor. Default 0.
        task_id: Internal task ID (injected by framework).

    Returns:
        A JSON string with search results including messages and pagination
        info (the oldest message ID for continued pagination).

    **Common usage patterns:**

    - Recent context: ``stream="general", topic="database", anchor="newest", num_before=20``
    - Around a specific message: ``anchor="<msg_id>", num_before=5, num_after=5``
    - Text search: ``stream="general", query="postgresql"``
    - Find by sender: ``query="sender:alice@example.com"``
    - Older page: ``stream="general", topic="db", anchor="<oldest_id>", num_before=20``
    """
    try:
        import zulip
    except ImportError:
        return json.dumps({"error": "zulip package not installed"})

    site_url = os.getenv("ZULIP_SITE_URL", "").rstrip("/")
    bot_email = os.getenv("ZULIP_BOT_EMAIL", "")
    api_key = os.getenv("ZULIP_API_KEY", "")

    if not site_url or not bot_email or not api_key:
        return json.dumps({
            "error": "Zulip credentials not configured. "
                     "Set ZULIP_SITE_URL, ZULIP_BOT_EMAIL, and ZULIP_API_KEY."
        })

    # Build the narrow filter.
    narrow: List[List[str]] = []
    if stream:
        narrow.append(["stream", stream])
    if topic:
        narrow.append(["topic", topic])
    if query:
        narrow.append(["search", query])

    # Resolve anchor.
    anchor_value: Any = anchor if anchor else "newest"

    client = zulip.Client(site=site_url, email=bot_email, api_key=api_key)
    try:
        result = client.get_messages({
            "anchor": anchor_value,
            "num_before": num_before,
            "num_after": num_after,
            "narrow": narrow or None,
            "apply_markdown": False,
        })
    except Exception as exc:
        logger.warning("Zulip search failed: %s", exc)
        return json.dumps({"error": f"Zulip API error: {exc}"})

    if result.get("result") != "success":
        return json.dumps({
            "error": result.get("msg", "Unknown Zulip error"),
        })

    messages = result.get("messages", [])
    if not messages:
        return json.dumps({
            "messages": [],
            "count": 0,
            "found_newest": result.get("found_newest", True),
            "found_oldest": result.get("found_oldest", True),
            "note": "No messages matched the search criteria.",
        })

    # Format messages for readability.
    formatted: List[Dict[str, Any]] = []
    for msg in messages:
        formatted.append({
            "id": msg.get("id"),
            "sender": msg.get("sender_full_name") or msg.get("sender_email", "?"),
            "timestamp": msg.get("timestamp", 0),
            "content": (msg.get("content") or "").strip(),
            "is_bot": msg.get("sender_email") == bot_email,
        })

    # Pagination cues.
    oldest_id = None
    newest_id = None
    if formatted:
        oldest_id = min(m["id"] for m in formatted if m["id"])
        newest_id = max(m["id"] for m in formatted if m["id"])

    return json.dumps({
        "messages": formatted,
        "count": len(formatted),
        "requested_before": num_before,
        "requested_after": num_after,
        "oldest_message_id": oldest_id,
        "newest_message_id": newest_id,
        "found_oldest": result.get("found_oldest", False),
        "found_newest": result.get("found_newest", False),
        "pagination_hint": (
            f"To get older messages, call again with "
            f"anchor={oldest_id}, num_before={num_before}, num_after=0. "
            f"To get newer messages, call with "
            f"anchor={newest_id}, num_before=0, num_after={num_after or 20}."
        ) if formatted else "",
    })


# --- Registry ---
from tools.registry import registry

_ZULIP_SEARCH_SCHEMA = {
    "name": "zulip_search_messages",
    "description": (
        "Search Zulip message history. Fetches messages from streams, "
        "topics, or by full-text search. Supports pagination via "
        "message ID anchors. Use this to get context about what was "
        "discussed before your @mention, to search for specific "
        "information in past conversations, or to find messages "
        "by a specific sender."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "stream": {
                "type": "string",
                "description": (
                    "Stream name to narrow search to. "
                    "Example: 'general', 'engineering', 'announce'."
                ),
            },
            "topic": {
                "type": "string",
                "description": (
                    "Topic name within the stream. "
                    "Example: 'database', 'onboarding'."
                ),
            },
            "query": {
                "type": "string",
                "description": (
                    "Full-text search using Zulip's search syntax. "
                    "Supports operators like "
                    "sender:alice@example.com, has:link, is:starred, "
                    "near:12345, pm-with:alice@example.com. "
                    "Combine with stream/topic for focused search."
                ),
            },
            "anchor": {
                "type": "string",
                "description": (
                    "Message ID to anchor pagination around, or "
                    "'newest' (most recent) or 'oldest'. "
                    "Default: 'newest'. For pagination, use the "
                    "'oldest_message_id' from a previous response."
                ),
            },
            "num_before": {
                "type": "integer",
                "description": (
                    "Number of messages to fetch before the anchor. "
                    "Default: 20. Max: 5000."
                ),
                "default": 20,
            },
            "num_after": {
                "type": "integer",
                "description": (
                    "Number of messages to fetch after the anchor. "
                    "Default: 0. Set to >0 to see context after a "
                    "specific message (e.g., 5 messages after a reply)."
                ),
                "default": 0,
            },
        },
        "required": [],
    },
}

registry.register(
    name="zulip_search_messages",
    toolset="zulip-history",
    schema=_ZULIP_SEARCH_SCHEMA,
    handler=lambda args, **kw: zulip_search_messages(
        stream=args.get("stream"),
        topic=args.get("topic"),
        query=args.get("query"),
        anchor=args.get("anchor"),
        num_before=args.get("num_before", 20),
        num_after=args.get("num_after", 0),
        task_id=kw.get("task_id"),
    ),
    check_fn=_check_zulip_search_requirements,
    requires_env=["ZULIP_SITE_URL", "ZULIP_BOT_EMAIL", "ZULIP_API_KEY"],
    emoji="🔍",
)
