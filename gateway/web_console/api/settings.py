"""Settings API routes for the Hermes Web Console backend."""

from __future__ import annotations

import json
from typing import Any

from aiohttp import web

import os
from gateway.web_console.services.settings_service import SettingsService
from hermes_cli.config import save_env_value, OPTIONAL_ENV_VARS

SETTINGS_SERVICE_APP_KEY = web.AppKey("hermes_web_console_settings_service", SettingsService)


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


def _get_settings_service(request: web.Request) -> SettingsService:
    service = request.app.get(SETTINGS_SERVICE_APP_KEY)
    if service is None:
        service = SettingsService()
        request.app[SETTINGS_SERVICE_APP_KEY] = service
    return service


async def handle_get_settings(request: web.Request) -> web.Response:
    service = _get_settings_service(request)
    return web.json_response({"ok": True, "settings": service.get_settings()})


async def handle_patch_settings(request: web.Request) -> web.Response:
    data = await _read_json_body(request)
    if data is None:
        return _json_error(status=400, code="invalid_json", message="Request body must be a valid JSON object.")
    service = _get_settings_service(request)
    try:
        settings = service.update_settings(data)
    except ValueError as exc:
        return _json_error(status=400, code="invalid_patch", message=str(exc))
    return web.json_response({"ok": True, "settings": settings})


async def handle_get_auth_status(request: web.Request) -> web.Response:
    service = _get_settings_service(request)
    return web.json_response({"ok": True, "auth": service.get_auth_status()})


async def handle_patch_auth_keys(request: web.Request) -> web.Response:
    data = await _read_json_body(request)
    if data is None:
        return _json_error(status=400, code="invalid_json", message="Request body must be a valid JSON object.")
        
    for k, v in data.items():
        if isinstance(v, str):
            save_env_value(k, v)
        elif v is None:
            # If they pass null, maybe they want to unset it? 
            # save_env_value supports unsetting if value is empty string, let's pass empty string.
            save_env_value(k, "")

    service = _get_settings_service(request)
    return web.json_response({"ok": True, "auth": service.get_auth_status()})


async def handle_get_auth_schema(request: web.Request) -> web.Response:
    # Build a schema of env vars, categorizing them and providing descriptions
    schema = {}
    for key, meta in OPTIONAL_ENV_VARS.items():
        if meta.get("category") in ("provider", "tool", "messaging") or meta.get("category") is None:
            # Copy meta and add value status
            item = dict(meta)
            val = os.getenv(key, "").strip()
            # If the value is present and is not a placeholder, mask it
            if val and len(val) >= 4 and val.lower() not in ("null", "none", "placeholder", "changeme", "***"):
                item["value"] = "***" + val[-4:] if len(val) >= 8 else "***"
                item["configured"] = True
            else:
                item["value"] = ""
                item["configured"] = False
            schema[key] = item
    return web.json_response({"ok": True, "schema": schema})


def register_settings_api_routes(app: web.Application) -> None:
    if app.get(SETTINGS_SERVICE_APP_KEY) is None:
        app[SETTINGS_SERVICE_APP_KEY] = SettingsService()

    app.router.add_get("/api/gui/settings", handle_get_settings)
    app.router.add_patch("/api/gui/settings", handle_patch_settings)
    app.router.add_get("/api/gui/auth-status", handle_get_auth_status)
    app.router.add_patch("/api/gui/auth/keys", handle_patch_auth_keys)
    app.router.add_get("/api/gui/auth/schema", handle_get_auth_schema)
