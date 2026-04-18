"""Browser API routes for the Hermes Web Console backend."""

from __future__ import annotations

import json
from typing import Any

from aiohttp import web

from gateway.web_console.services.browser_service import BrowserService

BROWSER_SERVICE_APP_KEY = web.AppKey("hermes_web_console_browser_service", BrowserService)


def _json_error(*, status: int, code: str, message: str, **extra: Any) -> web.Response:
    payload: dict[str, Any] = {"ok": False, "error": {"code": code, "message": message}}
    payload["error"].update(extra)
    return web.json_response(payload, status=status)


async def _read_json_body(request: web.Request) -> dict[str, Any] | None:
    if request.content_type == "application/json":
        try:
            data = await request.json()
        except (json.JSONDecodeError, ValueError, TypeError):
            return None
        if not isinstance(data, dict):
            return None
        return data
    return {}


def _get_browser_service(request: web.Request) -> BrowserService:
    service = request.app.get(BROWSER_SERVICE_APP_KEY)
    if service is None:
        service = BrowserService()
        request.app[BROWSER_SERVICE_APP_KEY] = service
    return service


async def handle_browser_status(request: web.Request) -> web.Response:
    service = _get_browser_service(request)
    return web.json_response({"ok": True, "browser": service.get_status()})


async def handle_browser_connect(request: web.Request) -> web.Response:
    data = await _read_json_body(request)
    if data is None:
        return _json_error(status=400, code="invalid_json", message="Request body must be a valid JSON object.")
    service = _get_browser_service(request)
    try:
        browser = service.connect(data.get("cdp_url"))
    except ValueError as exc:
        return _json_error(status=400, code="invalid_cdp_url", message=str(exc))
    return web.json_response({"ok": True, "browser": browser})


async def handle_browser_disconnect(request: web.Request) -> web.Response:
    service = _get_browser_service(request)
    return web.json_response({"ok": True, "browser": service.disconnect()})


def register_browser_api_routes(app: web.Application) -> None:
    app.router.add_get("/api/gui/browser/status", handle_browser_status)
    app.router.add_post("/api/gui/browser/connect", handle_browser_connect)
    app.router.add_post("/api/gui/browser/disconnect", handle_browser_disconnect)
