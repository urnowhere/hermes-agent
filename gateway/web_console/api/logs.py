"""Logs API routes for the Hermes Web Console backend."""

from __future__ import annotations

from typing import Any

from aiohttp import web

from gateway.web_console.services.log_service import LogService

LOG_SERVICE_APP_KEY = web.AppKey("hermes_web_console_log_service", LogService)


def _json_error(*, status: int, code: str, message: str, **extra: Any) -> web.Response:
    payload: dict[str, Any] = {"ok": False, "error": {"code": code, "message": message}}
    payload["error"].update(extra)
    return web.json_response(payload, status=status)


def _get_log_service(request: web.Request) -> LogService:
    service = request.app.get(LOG_SERVICE_APP_KEY)
    if service is None:
        service = LogService()
        request.app[LOG_SERVICE_APP_KEY] = service
    return service


async def handle_get_logs(request: web.Request) -> web.Response:
    file_name = request.query.get("file") or None
    limit_raw = request.query.get("limit", "200")
    try:
        limit = int(limit_raw)
    except (TypeError, ValueError):
        return _json_error(status=400, code="invalid_limit", message="The 'limit' field must be an integer.")
    if limit < 0:
        return _json_error(status=400, code="invalid_limit", message="The 'limit' field must be >= 0.")

    service = _get_log_service(request)
    try:
        logs = service.get_logs(file_name=file_name, limit=limit)
    except FileNotFoundError:
        return _json_error(status=404, code="log_not_found", message="The requested log file was not found.", file=file_name)
    return web.json_response({"ok": True, "logs": logs})


def register_logs_api_routes(app: web.Application) -> None:
    if app.get(LOG_SERVICE_APP_KEY) is None:
        app[LOG_SERVICE_APP_KEY] = LogService()

    app.router.add_get("/api/gui/logs", handle_get_logs)
