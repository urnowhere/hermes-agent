"""Workspace and process API routes for the Hermes Web Console backend."""

from __future__ import annotations

import json
from typing import Any

from aiohttp import web

from gateway.web_console.services.workspace_service import WorkspaceService

WORKSPACE_SERVICE_APP_KEY = web.AppKey("hermes_web_console_workspace_service", WorkspaceService)


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


def _parse_int(value: str | None, *, field_name: str, minimum: int | None = None) -> int:
    try:
        parsed = int(value) if value is not None else 0
    except (TypeError, ValueError) as exc:
        raise ValueError(f"The '{field_name}' field must be an integer.") from exc
    if minimum is not None and parsed < minimum:
        raise ValueError(f"The '{field_name}' field must be >= {minimum}.")
    return parsed


def _parse_bool(value: str | None) -> bool:
    if value is None:
        return False
    return value.lower() in {"1", "true", "yes", "on"}


def _get_workspace_service(request: web.Request) -> WorkspaceService:
    service = request.app.get(WORKSPACE_SERVICE_APP_KEY)
    if service is None:
        service = WorkspaceService()
        request.app[WORKSPACE_SERVICE_APP_KEY] = service
    return service


async def handle_workspace_tree(request: web.Request) -> web.Response:
    service = _get_workspace_service(request)
    try:
        depth = _parse_int(request.query.get("depth", "2"), field_name="depth", minimum=0)
        result = service.get_tree(
            path=request.query.get("path"),
            depth=depth,
            include_hidden=_parse_bool(request.query.get("include_hidden")),
        )
    except ValueError as exc:
        return _json_error(status=400, code="invalid_path", message=str(exc))
    except FileNotFoundError as exc:
        return _json_error(status=404, code="path_not_found", message=str(exc))
    return web.json_response({"ok": True, **result})


async def handle_workspace_file(request: web.Request) -> web.Response:
    service = _get_workspace_service(request)
    path = request.query.get("path")
    if not path:
        return _json_error(status=400, code="missing_path", message="The 'path' query parameter is required.")
    try:
        offset = _parse_int(request.query.get("offset", "1"), field_name="offset", minimum=1)
        limit = _parse_int(request.query.get("limit", "500"), field_name="limit", minimum=1)
        result = service.get_file(path=path, offset=offset, limit=limit)
    except ValueError as exc:
        return _json_error(status=400, code="invalid_path", message=str(exc))
    except FileNotFoundError as exc:
        return _json_error(status=404, code="path_not_found", message=str(exc))
    return web.json_response({"ok": True, **result})


async def handle_workspace_search(request: web.Request) -> web.Response:
    service = _get_workspace_service(request)
    query = request.query.get("query") or request.query.get("q")
    if not query:
        return _json_error(status=400, code="missing_query", message="The 'query' parameter is required.")
    try:
        limit = _parse_int(request.query.get("limit", "50"), field_name="limit", minimum=1)
        result = service.search_workspace(
            query=query,
            path=request.query.get("path"),
            limit=limit,
            include_hidden=_parse_bool(request.query.get("include_hidden")),
            regex=_parse_bool(request.query.get("regex")),
        )
    except ValueError as exc:
        return _json_error(status=400, code="invalid_search", message=str(exc))
    except FileNotFoundError as exc:
        return _json_error(status=404, code="path_not_found", message=str(exc))
    return web.json_response({"ok": True, **result})


async def handle_workspace_diff(request: web.Request) -> web.Response:
    service = _get_workspace_service(request)
    checkpoint_id = request.query.get("checkpoint_id") or request.query.get("checkpoint")
    if not checkpoint_id:
        return _json_error(status=400, code="missing_checkpoint_id", message="The 'checkpoint_id' parameter is required.")
    try:
        result = service.diff_checkpoint(checkpoint_id=checkpoint_id, path=request.query.get("path"))
    except ValueError as exc:
        return _json_error(status=400, code="invalid_checkpoint", message=str(exc))
    except FileNotFoundError as exc:
        return _json_error(status=404, code="checkpoint_not_found", message=str(exc))
    return web.json_response({"ok": True, **result})


async def handle_workspace_checkpoints(request: web.Request) -> web.Response:
    service = _get_workspace_service(request)
    try:
        result = service.list_checkpoints(path=request.query.get("path"))
    except ValueError as exc:
        return _json_error(status=400, code="invalid_path", message=str(exc))
    except FileNotFoundError as exc:
        return _json_error(status=404, code="path_not_found", message=str(exc))
    return web.json_response({"ok": True, **result})


async def handle_workspace_rollback(request: web.Request) -> web.Response:
    data = await _read_json_body(request)
    if data is None:
        return _json_error(status=400, code="invalid_json", message="Request body must be a valid JSON object.")
    checkpoint_id = data.get("checkpoint_id") or data.get("checkpoint")
    if not isinstance(checkpoint_id, str) or not checkpoint_id:
        return _json_error(status=400, code="missing_checkpoint_id", message="The 'checkpoint_id' field must be a non-empty string.")

    service = _get_workspace_service(request)
    try:
        result = service.rollback(
            checkpoint_id=checkpoint_id,
            path=data.get("path"),
            file_path=data.get("file_path"),
        )
    except ValueError as exc:
        return _json_error(status=400, code="invalid_rollback", message=str(exc))
    except FileNotFoundError as exc:
        return _json_error(status=404, code="checkpoint_not_found", message=str(exc))
    except RuntimeError as exc:
        return _json_error(status=500, code="rollback_failed", message=str(exc))
    return web.json_response({"ok": True, "result": result})


async def handle_list_processes(request: web.Request) -> web.Response:
    service = _get_workspace_service(request)
    return web.json_response({"ok": True, **service.list_processes()})


async def handle_process_log(request: web.Request) -> web.Response:
    service = _get_workspace_service(request)
    process_id = request.match_info["process_id"]
    try:
        offset = _parse_int(request.query.get("offset", "0"), field_name="offset", minimum=0)
        limit = _parse_int(request.query.get("limit", "200"), field_name="limit", minimum=1)
        result = service.get_process_log(process_id, offset=offset, limit=limit)
    except ValueError as exc:
        return _json_error(status=400, code="invalid_pagination", message=str(exc))
    except FileNotFoundError as exc:
        return _json_error(status=404, code="process_not_found", message=str(exc), process_id=process_id)
    return web.json_response({"ok": True, **result})


async def handle_kill_process(request: web.Request) -> web.Response:
    service = _get_workspace_service(request)
    process_id = request.match_info["process_id"]
    try:
        result = service.kill_process(process_id)
    except FileNotFoundError as exc:
        return _json_error(status=404, code="process_not_found", message=str(exc), process_id=process_id)
    except RuntimeError as exc:
        return _json_error(status=500, code="process_kill_failed", message=str(exc), process_id=process_id)
    return web.json_response({"ok": True, "result": result})


async def handle_workspace_file_save(request: web.Request) -> web.Response:
    data = await _read_json_body(request)
    if data is None:
        return _json_error(status=400, code="invalid_json", message="Request body must be a valid JSON object.")
    path = data.get("path")
    content = data.get("content")
    if not isinstance(path, str) or not path:
        return _json_error(status=400, code="missing_path", message="The 'path' field must be a non-empty string.")
    if not isinstance(content, str):
        return _json_error(status=400, code="missing_content", message="The 'content' field must be a string.")

    service = _get_workspace_service(request)
    try:
        result = service.save_file(path=path, content=content)
    except ValueError as exc:
        return _json_error(status=400, code="invalid_path", message=str(exc))
    except OSError as exc:
        return _json_error(status=500, code="write_failed", message=str(exc))
    return web.json_response({"ok": True, **result})


async def handle_workspace_exec(request: web.Request) -> web.Response:
    data = await _read_json_body(request)
    if data is None:
        return _json_error(status=400, code="invalid_json", message="Request body must be a valid JSON object.")
    command = data.get("command")
    if not isinstance(command, str) or not command.strip():
        return _json_error(status=400, code="missing_command", message="The 'command' field must be a non-empty string.")

    service = _get_workspace_service(request)
    try:
        result = service.execute_sync(command)
    except ValueError as exc:
        return _json_error(status=400, code="invalid_command", message=str(exc))
    except RuntimeError as exc:
        return _json_error(status=500, code="execution_failed", message=str(exc))
    return web.json_response({"ok": True, **result})


def register_workspace_api_routes(app: web.Application) -> None:
    if app.get(WORKSPACE_SERVICE_APP_KEY) is None:
        app[WORKSPACE_SERVICE_APP_KEY] = WorkspaceService()

    app.router.add_get("/api/gui/workspace/tree", handle_workspace_tree)
    app.router.add_get("/api/gui/workspace/file", handle_workspace_file)
    app.router.add_post("/api/gui/workspace/file/save", handle_workspace_file_save)
    app.router.add_get("/api/gui/workspace/search", handle_workspace_search)
    app.router.add_get("/api/gui/workspace/diff", handle_workspace_diff)
    app.router.add_get("/api/gui/workspace/checkpoints", handle_workspace_checkpoints)
    app.router.add_post("/api/gui/workspace/rollback", handle_workspace_rollback)
    app.router.add_post("/api/gui/workspace/exec", handle_workspace_exec)
    app.router.add_get("/api/gui/processes", handle_list_processes)
    app.router.add_get("/api/gui/processes/{process_id}/log", handle_process_log)
    app.router.add_post("/api/gui/processes/{process_id}/kill", handle_kill_process)

