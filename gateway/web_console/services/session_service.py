"""Session data access helpers for the Hermes Web Console."""

from __future__ import annotations

import json
from typing import Any

from hermes_state import SessionDB


class SessionService:
    """Thin wrapper around Hermes session storage for GUI/API use."""

    def __init__(self, db: SessionDB | None = None) -> None:
        self.db = db or SessionDB()

    @staticmethod
    def _token_summary(session: dict[str, Any]) -> dict[str, Any]:
        return {
            "input": session.get("input_tokens", 0) or 0,
            "output": session.get("output_tokens", 0) or 0,
            "total": (session.get("input_tokens", 0) or 0) + (session.get("output_tokens", 0) or 0),
            "cache_read": session.get("cache_read_tokens", 0) or 0,
            "cache_write": session.get("cache_write_tokens", 0) or 0,
            "reasoning": session.get("reasoning_tokens", 0) or 0,
        }

    @staticmethod
    def _session_summary(session: dict[str, Any]) -> dict[str, Any]:
        preview = session.get("preview", "") or ""
        return {
            "session_id": session["id"],
            "title": session.get("title"),
            "last_active": session.get("last_active") or session.get("started_at"),
            "source": session.get("source"),
            "workspace": None,
            "model": session.get("model"),
            "token_summary": SessionService._token_summary(session),
            "parent_session_id": session.get("parent_session_id"),
            "has_tools": bool(session.get("tool_call_count", 0)),
            "has_attachments": False,
            "preview": preview,
            "message_count": session.get("message_count", 0) or 0,
        }

    @staticmethod
    def _parse_json_maybe(value: Any) -> Any:
        if not isinstance(value, str):
            return value
        try:
            return json.loads(value)
        except (json.JSONDecodeError, TypeError):
            return value

    @staticmethod
    def _transcript_item(message: dict[str, Any]) -> dict[str, Any]:
        tool_calls = message.get("tool_calls")
        has_tools = bool(tool_calls)
        if message.get("role") == "tool":
            item_type = "tool_result"
        elif has_tools:
            item_type = "assistant_tool_call"
        else:
            item_type = f"{message.get('role', 'unknown')}_message"
        return {
            "id": message.get("id"),
            "type": item_type,
            "role": message.get("role"),
            "content": message.get("content"),
            "timestamp": message.get("timestamp"),
            "tool_name": message.get("tool_name"),
            "tool_call_id": message.get("tool_call_id"),
            "tool_calls": tool_calls,
            "finish_reason": message.get("finish_reason"),
        }

    def list_sessions(self, *, source: str | None = None, limit: int = 20, offset: int = 0) -> list[dict[str, Any]]:
        sessions = self.db.list_sessions_rich(source=source, limit=limit, offset=offset)
        return [self._session_summary(session) for session in sessions]

    def get_session_detail(self, session_id: str) -> dict[str, Any] | None:
        resolved = self.db.resolve_session_id(session_id) or session_id
        exported = self.db.export_session(resolved)
        if not exported:
            return None
        summary = self._session_summary(exported)
        messages = exported.get("messages", [])
        summary.update(
            {
                "started_at": exported.get("started_at"),
                "ended_at": exported.get("ended_at"),
                "end_reason": exported.get("end_reason"),
                "system_prompt": exported.get("system_prompt"),
                "metadata": {
                    "user_id": exported.get("user_id"),
                    "model_config": SessionService._parse_json_maybe(exported.get("model_config")),
                    "billing_provider": exported.get("billing_provider"),
                    "billing_base_url": exported.get("billing_base_url"),
                    "billing_mode": exported.get("billing_mode"),
                    "estimated_cost_usd": exported.get("estimated_cost_usd"),
                    "actual_cost_usd": exported.get("actual_cost_usd"),
                    "cost_status": exported.get("cost_status"),
                    "cost_source": exported.get("cost_source"),
                    "pricing_version": exported.get("pricing_version"),
                },
                "recap": {
                    "message_count": len(messages),
                    "preview": summary.get("preview", ""),
                    "last_role": messages[-1].get("role") if messages else None,
                },
            }
        )
        return summary

    def get_transcript(self, session_id: str) -> dict[str, Any] | None:
        resolved = self.db.resolve_session_id(session_id) or session_id
        session = self.db.get_session(resolved)
        if not session:
            return None
        messages = self.db.get_messages(resolved)
        return {
            "session_id": resolved,
            "items": [self._transcript_item(message) for message in messages],
        }

    def set_title(self, session_id: str, title: str) -> dict[str, Any] | None:
        resolved = self.db.resolve_session_id(session_id) or session_id
        if not self.db.set_session_title(resolved, title):
            return None
        session = self.db.get_session(resolved)
        return {
            "session_id": resolved,
            "title": session.get("title") if session else self.db.get_session_title(resolved),
        }

    def resume_session(self, session_id: str) -> dict[str, Any] | None:
        resolved = self.db.resolve_session_id(session_id) or session_id
        detail = self.get_session_detail(resolved)
        if detail is None:
            return None
        conversation = self.db.get_messages_as_conversation(resolved)
        return {
            "session_id": detail["session_id"],
            "status": "resumed",
            "resume_supported": True,
            "title": detail.get("title"),
            "conversation_history": conversation,
            "session": detail,
        }

    def branch_session(self, session_id: str, at_message_index: int | None = None) -> dict[str, Any] | None:
        """Create a new session branched from an existing one.

        Copies messages up to ``at_message_index`` (inclusive) into a fresh
        session.  If *at_message_index* is None the entire history is copied.
        The new session's ``parent_session_id`` is set to the source session.
        """
        import time
        import uuid

        resolved = self.db.resolve_session_id(session_id) or session_id
        source = self.db.get_session(resolved)
        if not source:
            return None

        messages = self.db.get_messages(resolved)
        if at_message_index is not None:
            messages = messages[: at_message_index + 1]

        # Generate a new session id
        ts = time.strftime("%Y%m%d_%H%M%S")
        branch_id = f"{ts}_branch_{uuid.uuid4().hex[:6]}"

        # Create the branched session
        self.db.create_session(
            session_id=branch_id,
            source=source.get("source", "web_console"),
            model=source.get("model", ""),
            system_prompt=source.get("system_prompt", ""),
            parent_session_id=resolved,
        )

        # Set a descriptive title
        source_title = source.get("title") or resolved
        branch_title = f"Branch of {source_title}"
        try:
            self.db.set_session_title(branch_id, branch_title)
        except ValueError:
            # Title conflict — append a suffix
            branch_title = f"Branch of {source_title} ({branch_id[:8]})"
            try:
                self.db.set_session_title(branch_id, branch_title)
            except ValueError:
                pass  # Give up on title, keep the default

        # Copy messages into the new session
        for msg in messages:
            self.db.append_message(
                session_id=branch_id,
                role=msg.get("role", "user"),
                content=msg.get("content", ""),
                tool_calls=msg.get("tool_calls"),
                tool_call_id=msg.get("tool_call_id"),
                tool_name=msg.get("tool_name"),
            )

        return {
            "session_id": branch_id,
            "parent_session_id": resolved,
            "title": branch_title,
            "message_count": len(messages),
        }

    def delete_session(self, session_id: str) -> bool:
        resolved = self.db.resolve_session_id(session_id) or session_id
        return self.db.delete_session(resolved)
