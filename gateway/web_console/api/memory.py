"""GUI backend API routes for Memory and session search."""

from __future__ import annotations

import json
from typing import Any

from aiohttp import web

from gateway.web_console.services.memory_service import MemoryService

MEMORY_SERVICE_APP_KEY = web.AppKey("hermes_web_console_memory_service", MemoryService)


def _json_error(*, status: int, code: str, message: str, **extra: Any) -> web.Response:
    payload: dict[str, Any] = {"ok": False, "error": {"code": code, "message": message}}
    payload["error"].update(extra)
    return web.json_response(payload, status=status)


def _get_memory_service(request: web.Request) -> MemoryService:
    service = request.app.get(MEMORY_SERVICE_APP_KEY)
    if service is None:
        service = MemoryService()
        request.app[MEMORY_SERVICE_APP_KEY] = service
    return service


async def handle_get_memory(request: web.Request) -> web.Response:
    """GET /api/gui/memory — return structured memory entries."""
    service = _get_memory_service(request)
    try:
        payload = service.get_memory(target="memory")
    except Exception as exc:
        return _json_error(status=500, code="memory_error", message=str(exc))
    return web.json_response({"ok": True, "memory": payload})


async def handle_get_user_profile(request: web.Request) -> web.Response:
    """GET /api/gui/user-profile — return structured user profile entries."""
    service = _get_memory_service(request)
    try:
        payload = service.get_memory(target="user")
    except Exception as exc:
        return _json_error(status=500, code="memory_error", message=str(exc))
    return web.json_response({"ok": True, "user_profile": payload})


async def handle_add_memory(request: web.Request) -> web.Response:
    """POST /api/gui/memory — add an entry to memory or user profile."""
    try:
        data = await request.json()
    except (json.JSONDecodeError, ValueError, TypeError):
        return _json_error(status=400, code="invalid_json", message="Request body must be valid JSON.")

    if not isinstance(data, dict):
        return _json_error(status=400, code="invalid_json", message="Request body must be a JSON object.")

    target = data.get("target", "memory")
    content = data.get("content")

    service = _get_memory_service(request)
    try:
        result = service.mutate_memory(action="add", target=target, content=content)
    except PermissionError as exc:
        return _json_error(status=403, code="memory_disabled", message=str(exc))
    except ValueError as exc:
        return _json_error(status=400, code="invalid_target", message=str(exc))
    except Exception as exc:
        return _json_error(status=500, code="memory_error", message=str(exc))

    return web.json_response({"ok": True, "memory": result})


async def handle_update_memory(request: web.Request) -> web.Response:
    """PATCH /api/gui/memory — replace an entry in memory or user profile."""
    try:
        data = await request.json()
    except (json.JSONDecodeError, ValueError, TypeError):
        return _json_error(status=400, code="invalid_json", message="Request body must be valid JSON.")

    if not isinstance(data, dict):
        return _json_error(status=400, code="invalid_json", message="Request body must be a JSON object.")

    target = data.get("target", "memory")
    content = data.get("content")
    old_text = data.get("old_text")

    service = _get_memory_service(request)
    try:
        result = service.mutate_memory(action="replace", target=target, content=content, old_text=old_text)
    except PermissionError as exc:
        return _json_error(status=403, code="memory_disabled", message=str(exc))
    except ValueError as exc:
        return _json_error(status=400, code="invalid_target", message=str(exc))
    except Exception as exc:
        return _json_error(status=500, code="memory_error", message=str(exc))

    if not result.get("success"):
        extra: dict[str, Any] = {}
        if "matches" in result:
            extra["matches"] = result["matches"]
        return _json_error(
            status=400,
            code="memory_update_failed",
            message=result.get("error", "Update failed."),
            **extra,
        )

    return web.json_response({"ok": True, "memory": result})


async def handle_delete_memory(request: web.Request) -> web.Response:
    """DELETE /api/gui/memory — remove an entry from memory or user profile."""
    try:
        data = await request.json()
    except (json.JSONDecodeError, ValueError, TypeError):
        return _json_error(status=400, code="invalid_json", message="Request body must be valid JSON.")

    if not isinstance(data, dict):
        return _json_error(status=400, code="invalid_json", message="Request body must be a JSON object.")

    target = data.get("target", "memory")
    old_text = data.get("old_text")

    service = _get_memory_service(request)
    try:
        result = service.mutate_memory(action="remove", target=target, old_text=old_text)
    except PermissionError as exc:
        return _json_error(status=403, code="memory_disabled", message=str(exc))
    except ValueError as exc:
        return _json_error(status=400, code="invalid_target", message=str(exc))
    except Exception as exc:
        return _json_error(status=500, code="memory_error", message=str(exc))

    return web.json_response({"ok": True, "memory": result})


async def handle_session_search(request: web.Request) -> web.Response:
    """GET /api/gui/session-search — search past sessions."""
    query = request.query.get("query")
    if not query or not query.strip():
        return _json_error(status=400, code="missing_query", message="The 'query' parameter is required.")

    role_filter = request.query.get("role_filter")
    current_session_id = request.query.get("current_session_id")

    limit_raw = request.query.get("limit")
    limit = 3
    if limit_raw is not None:
        try:
            limit = int(limit_raw)
            if limit < 1:
                raise ValueError()
        except (ValueError, TypeError):
            return _json_error(status=400, code="invalid_search", message="The 'limit' parameter must be a positive integer.")

    service = _get_memory_service(request)
    try:
        result = service.search_sessions(
            query=query.strip(),
            role_filter=role_filter,
            limit=limit,
            current_session_id=current_session_id,
        )
    except RuntimeError as exc:
        return _json_error(status=500, code="search_failed", message=str(exc))
    except Exception as exc:
        return _json_error(status=500, code="search_failed", message=str(exc))

    if not result.get("success", True):
        return _json_error(
            status=503,
            code="search_failed",
            message=result.get("error", "Session search failed."),
        )

    return web.json_response({"ok": True, "search": result})


def register_memory_api_routes(app: web.Application) -> None:
    if app.get(MEMORY_SERVICE_APP_KEY) is None:
        try:
            app[MEMORY_SERVICE_APP_KEY] = MemoryService()
        except Exception:
            # MemoryService may fail to initialize in test environments
            # where the memory tool module is not available.
            pass

    app.router.add_get("/api/gui/memory", handle_get_memory)
    app.router.add_post("/api/gui/memory", handle_add_memory)
    app.router.add_patch("/api/gui/memory", handle_update_memory)
    app.router.add_delete("/api/gui/memory", handle_delete_memory)
    app.router.add_get("/api/gui/user-profile", handle_get_user_profile)
    app.router.add_get("/api/gui/session-search", handle_session_search)
