"""Cron API routes for the Hermes Web Console backend."""

from __future__ import annotations

import json
from typing import Any

from aiohttp import web

from gateway.web_console.services.cron_service import CronService

CRON_SERVICE_APP_KEY = web.AppKey("hermes_web_console_cron_service", CronService)


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


def _get_cron_service(request: web.Request) -> CronService:
    service = request.app.get(CRON_SERVICE_APP_KEY)
    if service is None:
        service = CronService()
        request.app[CRON_SERVICE_APP_KEY] = service
    return service


def _parse_non_negative_int(value: str, *, field_name: str) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        raise ValueError(f"The '{field_name}' field must be an integer.")
    if parsed < 0:
        raise ValueError(f"The '{field_name}' field must be >= 0.")
    return parsed


def _job_not_found(job_id: str) -> web.Response:
    return _json_error(
        status=404,
        code="job_not_found",
        message="No cron job was found for the provided job_id.",
        job_id=job_id,
    )


async def handle_list_jobs(request: web.Request) -> web.Response:
    service = _get_cron_service(request)
    include_disabled = request.query.get("include_disabled", "true").lower() not in {"0", "false", "no"}
    payload = service.list_jobs(include_disabled=include_disabled)
    return web.json_response({"ok": True, **payload})


async def handle_create_job(request: web.Request) -> web.Response:
    data = await _read_json_body(request)
    if data is None:
        return _json_error(status=400, code="invalid_json", message="Request body must be a valid JSON object.")

    service = _get_cron_service(request)
    try:
        job = service.create_job(data)
    except ValueError as exc:
        return _json_error(status=400, code="invalid_job", message=str(exc))
    except Exception as exc:
        return _json_error(status=500, code="job_create_failed", message=str(exc))
    return web.json_response({"ok": True, "job": job})


async def handle_get_job(request: web.Request) -> web.Response:
    service = _get_cron_service(request)
    job_id = request.match_info["job_id"]
    job = service.get_job(job_id)
    if job is None:
        return _job_not_found(job_id)
    return web.json_response({"ok": True, "job": job})


async def handle_update_job(request: web.Request) -> web.Response:
    data = await _read_json_body(request)
    if data is None:
        return _json_error(status=400, code="invalid_json", message="Request body must be a valid JSON object.")

    service = _get_cron_service(request)
    job_id = request.match_info["job_id"]
    try:
        job = service.update_job(job_id, data)
    except ValueError as exc:
        return _json_error(status=400, code="invalid_job", message=str(exc), job_id=job_id)
    except Exception as exc:
        return _json_error(status=500, code="job_update_failed", message=str(exc), job_id=job_id)
    if job is None:
        return _job_not_found(job_id)
    return web.json_response({"ok": True, "job": job})


async def handle_run_job(request: web.Request) -> web.Response:
    service = _get_cron_service(request)
    job_id = request.match_info["job_id"]
    job = service.run_job(job_id)
    if job is None:
        return _job_not_found(job_id)
    return web.json_response({"ok": True, "job": job, "queued": True})


async def handle_pause_job(request: web.Request) -> web.Response:
    data = await _read_json_body(request)
    if request.can_read_body and request.content_length not in (None, 0) and data is None:
        return _json_error(status=400, code="invalid_json", message="Request body must be a valid JSON object.")

    reason = None if data is None else data.get("reason")
    service = _get_cron_service(request)
    job_id = request.match_info["job_id"]
    try:
        job = service.pause_job(job_id, reason=reason)
    except ValueError as exc:
        return _json_error(status=400, code="invalid_job", message=str(exc), job_id=job_id)
    except Exception as exc:
        return _json_error(status=500, code="job_pause_failed", message=str(exc), job_id=job_id)
    if job is None:
        return _job_not_found(job_id)
    return web.json_response({"ok": True, "job": job})


async def handle_resume_job(request: web.Request) -> web.Response:
    service = _get_cron_service(request)
    job_id = request.match_info["job_id"]
    job = service.resume_job(job_id)
    if job is None:
        return _job_not_found(job_id)
    return web.json_response({"ok": True, "job": job})


async def handle_delete_job(request: web.Request) -> web.Response:
    service = _get_cron_service(request)
    job_id = request.match_info["job_id"]
    deleted = service.delete_job(job_id)
    if not deleted:
        return _job_not_found(job_id)
    return web.json_response({"ok": True, "job_id": job_id, "deleted": True})


async def handle_job_history(request: web.Request) -> web.Response:
    service = _get_cron_service(request)
    job_id = request.match_info["job_id"]
    try:
        limit = _parse_non_negative_int(request.query.get("limit", "20"), field_name="limit")
    except ValueError as exc:
        return _json_error(status=400, code="invalid_pagination", message=str(exc))

    try:
        payload = service.get_job_history(job_id, limit=limit)
    except ValueError as exc:
        return _json_error(status=400, code="invalid_pagination", message=str(exc), job_id=job_id)
    except Exception as exc:
        return _json_error(status=500, code="job_history_failed", message=str(exc), job_id=job_id)
    if payload is None:
        return _job_not_found(job_id)
    return web.json_response({"ok": True, **payload})


def register_cron_api_routes(app: web.Application) -> None:
    if app.get(CRON_SERVICE_APP_KEY) is None:
        app[CRON_SERVICE_APP_KEY] = CronService()

    app.router.add_get("/api/gui/cron/jobs", handle_list_jobs)
    app.router.add_post("/api/gui/cron/jobs", handle_create_job)
    app.router.add_get("/api/gui/cron/jobs/{job_id}", handle_get_job)
    app.router.add_patch("/api/gui/cron/jobs/{job_id}", handle_update_job)
    app.router.add_post("/api/gui/cron/jobs/{job_id}/run", handle_run_job)
    app.router.add_post("/api/gui/cron/jobs/{job_id}/pause", handle_pause_job)
    app.router.add_post("/api/gui/cron/jobs/{job_id}/resume", handle_resume_job)
    app.router.add_delete("/api/gui/cron/jobs/{job_id}", handle_delete_job)
    app.router.add_get("/api/gui/cron/jobs/{job_id}/history", handle_job_history)
