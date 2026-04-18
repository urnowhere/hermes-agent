"""Human approval and clarification API routes for the Hermes Web Console."""

from __future__ import annotations

import json
from typing import Any

from aiohttp import web

from gateway.web_console.services.approval_service import ApprovalService

HUMAN_SERVICE_APP_KEY = web.AppKey("hermes_web_console_human_service", ApprovalService)


def _json_error(*, status: int, code: str, message: str, **extra: Any) -> web.Response:
    payload: dict[str, Any] = {"ok": False, "error": {"code": code, "message": message}}
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


def _get_human_service(request: web.Request) -> ApprovalService:
    return request.app[HUMAN_SERVICE_APP_KEY]


async def handle_list_pending(request: web.Request) -> web.Response:
    service = _get_human_service(request)
    return web.json_response({"ok": True, "pending": service.list_pending()})


async def handle_approve(request: web.Request) -> web.Response:
    data = await _read_json_body(request)
    if data is None:
        return _json_error(status=400, code="invalid_json", message="Request body must be a valid JSON object.")
    request_id = data.get("request_id")
    decision = data.get("decision") or data.get("scope") or "once"
    if not isinstance(request_id, str) or not request_id:
        return _json_error(status=400, code="invalid_request_id", message="The 'request_id' field must be a non-empty string.")
    if decision not in {"once", "session", "always"}:
        return _json_error(status=400, code="invalid_decision", message="The approval decision must be one of: once, session, always.")

    service = _get_human_service(request)
    resolved = service.resolve_approval(request_id, decision)
    if resolved is None:
        return _json_error(status=404, code="request_not_found", message="No pending approval request was found.", request_id=request_id)
    return web.json_response({"ok": True, "request": resolved})


async def handle_deny(request: web.Request) -> web.Response:
    data = await _read_json_body(request)
    if data is None:
        return _json_error(status=400, code="invalid_json", message="Request body must be a valid JSON object.")
    request_id = data.get("request_id")
    if not isinstance(request_id, str) or not request_id:
        return _json_error(status=400, code="invalid_request_id", message="The 'request_id' field must be a non-empty string.")

    service = _get_human_service(request)
    resolved = service.deny_request(request_id)
    if resolved is None:
        return _json_error(status=404, code="request_not_found", message="No pending human request was found.", request_id=request_id)
    return web.json_response({"ok": True, "request": resolved})


async def handle_clarify(request: web.Request) -> web.Response:
    data = await _read_json_body(request)
    if data is None:
        return _json_error(status=400, code="invalid_json", message="Request body must be a valid JSON object.")
    request_id = data.get("request_id")
    response = data.get("response")
    if not isinstance(request_id, str) or not request_id:
        return _json_error(status=400, code="invalid_request_id", message="The 'request_id' field must be a non-empty string.")
    if not isinstance(response, str):
        return _json_error(status=400, code="invalid_response", message="The 'response' field must be a string.")

    service = _get_human_service(request)
    resolved = service.resolve_clarification(request_id, response)
    if resolved is None:
        return _json_error(status=404, code="request_not_found", message="No pending clarification request was found.", request_id=request_id)
    return web.json_response({"ok": True, "request": resolved})


async def handle_get_yolo(request: web.Request) -> web.Response:
    from tools.approval import is_session_yolo_enabled

    session_id = request.query.get("session_id", "")
    if not session_id:
        return _json_error(status=400, code="invalid_session_id", message="The 'session_id' query parameter is required.")
    return web.json_response({"ok": True, "session_id": session_id, "enabled": is_session_yolo_enabled(session_id)})


async def handle_set_yolo(request: web.Request) -> web.Response:
    from tools.approval import disable_session_yolo, enable_session_yolo, is_session_yolo_enabled

    data = await _read_json_body(request)
    if data is None:
        return _json_error(status=400, code="invalid_json", message="Request body must be a valid JSON object.")

    session_id = data.get("session_id")
    enabled = data.get("enabled")
    if not isinstance(session_id, str) or not session_id:
        return _json_error(status=400, code="invalid_session_id", message="The 'session_id' field must be a non-empty string.")
    if not isinstance(enabled, bool):
        return _json_error(status=400, code="invalid_enabled", message="The 'enabled' field must be a boolean.")

    if enabled:
        enable_session_yolo(session_id)
    else:
        disable_session_yolo(session_id)

    return web.json_response({"ok": True, "session_id": session_id, "enabled": is_session_yolo_enabled(session_id)})


def register_approval_api_routes(app: web.Application) -> None:
    if app.get(HUMAN_SERVICE_APP_KEY) is None:
        app[HUMAN_SERVICE_APP_KEY] = ApprovalService()

    from gateway.web_console.api.chat import CHAT_SERVICE_APP_KEY

    existing_chat_service = app.get(CHAT_SERVICE_APP_KEY)
    if existing_chat_service is not None:
        existing_chat_service.human_service = app[HUMAN_SERVICE_APP_KEY]

    app.router.add_get("/api/gui/human/pending", handle_list_pending)
    app.router.add_post("/api/gui/human/approve", handle_approve)
    app.router.add_post("/api/gui/human/deny", handle_deny)
    app.router.add_post("/api/gui/human/clarify", handle_clarify)
    app.router.add_get("/api/gui/human/yolo", handle_get_yolo)
    app.router.add_post("/api/gui/human/yolo", handle_set_yolo)
