"""Sessions API routes for the Hermes Web Console backend."""

from __future__ import annotations

import json
from typing import Any

from aiohttp import web

from gateway.web_console.services.session_service import SessionService

SESSIONS_SERVICE_APP_KEY = web.AppKey("hermes_web_console_sessions_service", SessionService)


def _json_error(*, status: int, code: str, message: str, **extra: Any) -> web.Response:
    payload: dict[str, Any] = {
        "ok": False,
        "error": {
            "code": code,
            "message": message,
        },
    }
    payload["error"].update(extra)
    return web.json_response(payload, status=status)


async def _read_json_body(request: web.Request) -> dict[str, Any] | None:
    try:
        data = await request.json()
    except (json.JSONDecodeError, ValueError, TypeError):
        return None
    if not isinstance(data, dict):
        return None
    return data


def _get_session_service(request: web.Request) -> SessionService:
    return request.app[SESSIONS_SERVICE_APP_KEY]


def _parse_non_negative_int(value: str, *, field_name: str) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        raise ValueError(f"The '{field_name}' field must be an integer.")
    if parsed < 0:
        raise ValueError(f"The '{field_name}' field must be >= 0.")
    return parsed


async def handle_list_sessions(request: web.Request) -> web.Response:
    service = _get_session_service(request)
    source = request.query.get("source") or None
    try:
        limit = _parse_non_negative_int(request.query.get("limit", "20"), field_name="limit")
        offset = _parse_non_negative_int(request.query.get("offset", "0"), field_name="offset")
    except ValueError as exc:
        return _json_error(status=400, code="invalid_pagination", message=str(exc))
    sessions = service.list_sessions(source=source, limit=limit, offset=offset)
    return web.json_response({"ok": True, "sessions": sessions})


async def handle_get_session(request: web.Request) -> web.Response:
    service = _get_session_service(request)
    session_id = request.match_info["session_id"]
    session = service.get_session_detail(session_id)
    if session is None:
        return _json_error(status=404, code="session_not_found", message="No session was found for the provided session_id.", session_id=session_id)
    return web.json_response({"ok": True, "session": session})


async def handle_get_transcript(request: web.Request) -> web.Response:
    service = _get_session_service(request)
    session_id = request.match_info["session_id"]
    transcript = service.get_transcript(session_id)
    if transcript is None:
        return _json_error(status=404, code="session_not_found", message="No session was found for the provided session_id.", session_id=session_id)
    return web.json_response({"ok": True, **transcript})


async def handle_set_title(request: web.Request) -> web.Response:
    data = await _read_json_body(request)
    if data is None:
        return _json_error(status=400, code="invalid_json", message="Request body must be a valid JSON object.")
    title = data.get("title")
    if not isinstance(title, str):
        return _json_error(status=400, code="invalid_title", message="The 'title' field must be a string.")

    service = _get_session_service(request)
    session_id = request.match_info["session_id"]
    try:
        result = service.set_title(session_id, title)
    except ValueError as exc:
        return _json_error(status=400, code="invalid_title", message=str(exc))
    if result is None:
        return _json_error(status=404, code="session_not_found", message="No session was found for the provided session_id.", session_id=session_id)
    return web.json_response({"ok": True, **result})


async def handle_resume_session(request: web.Request) -> web.Response:
    service = _get_session_service(request)
    session_id = request.match_info["session_id"]
    result = service.resume_session(session_id)
    if result is None:
        return _json_error(status=404, code="session_not_found", message="No session was found for the provided session_id.", session_id=session_id)
    return web.json_response({"ok": True, **result})


async def handle_delete_session(request: web.Request) -> web.Response:
    service = _get_session_service(request)
    session_id = request.match_info["session_id"]
    deleted = service.delete_session(session_id)
    if not deleted:
        return _json_error(status=404, code="session_not_found", message="No session was found for the provided session_id.", session_id=session_id)
    return web.json_response({"ok": True, "session_id": session_id, "deleted": True})


async def handle_export_session(request: web.Request) -> web.Response:
    service = _get_session_service(request)
    session_id = request.match_info["session_id"]
    export_format = request.query.get("format", "md").lower()
    
    resolved = service.db.resolve_session_id(session_id) or session_id
    exported = service.db.export_session(resolved)
    if not exported:
        return _json_error(status=404, code="session_not_found", message="No session was found for the provided session_id.", session_id=session_id)

    if export_format == "json":
        # export JSON as an attachment
        def default_serializer(obj: Any) -> Any:
            try:
                return json.dumps(obj)
            except Exception:
                return str(obj)
                
        response_text = json.dumps(exported, default=default_serializer, indent=2)
        response = web.Response(text=response_text, content_type="application/json")
        response.headers["Content-Disposition"] = f'attachment; filename="session_{resolved}.json"'
        return response

    # Format as Markdown
    title = exported.get("title") or "Hermes Session"
    lines = [
        f"# {title}",
        f"**Session ID:** `{resolved}`",
        f"**Model:** `{exported.get('model', 'unknown')}`",
        f"**Started At:** {exported.get('started_at', 'unknown')}",
        ""
    ]
    
    for msg in exported.get("messages", []):
        role = str(msg.get("role", "unknown")).capitalize()
        content = msg.get("content") or ""
        
        if role == "System":
            lines.append("## System")
            lines.append(f"> {content}")
            lines.append("")
        elif role == "User":
            lines.append("## User")
            lines.append(content)
            lines.append("")
        elif role == "Assistant":
            lines.append("## Assistant")
            if content:
                lines.append(content)
                lines.append("")
            
            tool_calls = msg.get("tool_calls")
            if tool_calls:
                for tc in tool_calls:
                    fn = tc.get("function", {})
                    fn_name = fn.get("name", "unknown")
                    args = fn.get("arguments", "{}")
                    try:
                        args_formatted = json.dumps(json.loads(args), indent=2)
                    except Exception:
                        args_formatted = str(args)
                    lines.append(f"**🔧 Tool Call:** `{fn_name}`")
                    lines.append(f"```json\n{args_formatted}\n```")
                    lines.append("")
        elif role == "Tool":
            tool_name = msg.get("tool_name") or msg.get("name") or "unknown"
            lines.append(f"## Tool Result: `{tool_name}`")
            lines.append(f"```\n{content}\n```")
            lines.append("")

    md_content = "\n".join(lines)
    
    if export_format == "txt":
        response = web.Response(text=md_content, content_type="text/plain")
        response.headers["Content-Disposition"] = f'attachment; filename="session_{resolved}.txt"'
        return response

    # Default md
    response = web.Response(text=md_content, content_type="text/markdown")
    response.headers["Content-Disposition"] = f'attachment; filename="session_{resolved}.md"'
    return response


async def handle_branch_session(request: web.Request) -> web.Response:
    """POST /api/gui/sessions/{session_id}/branch — fork a session at a message index."""
    service = _get_session_service(request)
    session_id = request.match_info["session_id"]

    data = await _read_json_body(request)
    at_message_index: int | None = None
    if data and "at_message_index" in data:
        try:
            at_message_index = int(data["at_message_index"])
        except (TypeError, ValueError):
            return _json_error(
                status=400,
                code="invalid_index",
                message="The 'at_message_index' field must be an integer.",
            )

    result = service.branch_session(session_id, at_message_index=at_message_index)
    if result is None:
        return _json_error(
            status=404,
            code="session_not_found",
            message="No session was found for the provided session_id.",
            session_id=session_id,
        )
    return web.json_response({"ok": True, **result})


async def handle_session_search(request: web.Request) -> web.Response:
    """GET /api/gui/session-search?q=... — FTS5 full-text search across sessions."""
    query = request.query.get("q", "").strip()
    if not query:
        return web.json_response({"ok": True, "search": {"results": []}})

    service = _get_session_service(request)
    try:
        results = service.db.search_messages(query, limit=20)
        formatted = []
        for r in results:
            formatted.append({
                "session_id": r.get("session_id", ""),
                "session_title": r.get("session_title") or r.get("session_id", "")[:12],
                "snippet": r.get("snippet") or r.get("content", "")[:120],
                "role": r.get("role", ""),
            })
        return web.json_response({"ok": True, "search": {"results": formatted}})
    except Exception as exc:
        return _json_error(
            status=500,
            code="search_error",
            message=f"Search failed: {exc}",
        )


def register_sessions_api_routes(app: web.Application) -> None:
    if app.get(SESSIONS_SERVICE_APP_KEY) is None:
        app[SESSIONS_SERVICE_APP_KEY] = SessionService()

    app.router.add_get("/api/gui/sessions", handle_list_sessions)
    app.router.add_get("/api/gui/session-search", handle_session_search)
    app.router.add_get("/api/gui/sessions/{session_id}", handle_get_session)
    app.router.add_get("/api/gui/sessions/{session_id}/transcript", handle_get_transcript)
    app.router.add_get("/api/gui/sessions/{session_id}/export", handle_export_session)
    app.router.add_post("/api/gui/sessions/{session_id}/title", handle_set_title)
    app.router.add_post("/api/gui/sessions/{session_id}/resume", handle_resume_session)
    app.router.add_post("/api/gui/sessions/{session_id}/branch", handle_branch_session)
    app.router.add_delete("/api/gui/sessions/{session_id}", handle_delete_session)

