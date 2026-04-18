"""Chat API routes for the Hermes Web Console backend."""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
import uuid
from typing import Any

from aiohttp import web

from gateway.web_console.api.approvals import HUMAN_SERVICE_APP_KEY
from gateway.web_console.services.chat_service import ChatService
from gateway.web_console.state import create_web_console_state, get_web_console_state

CHAT_SERVICE_APP_KEY = web.AppKey("hermes_web_console_chat_service", ChatService)
CHAT_STATE_APP_KEY = web.AppKey("hermes_web_console_chat_state", object)
CHAT_TASKS_APP_KEY = web.AppKey("hermes_web_console_chat_tasks", set)
CHAT_CLEANUP_REGISTERED_APP_KEY = web.AppKey("hermes_web_console_chat_cleanup_registered", bool)


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


def _get_chat_service(request: web.Request) -> ChatService:
    service = request.app.get(CHAT_SERVICE_APP_KEY)
    if service is None:
        state = request.app.get(CHAT_STATE_APP_KEY)
        if state is None:
            state = create_web_console_state()
            request.app[CHAT_STATE_APP_KEY] = state
        human_service = request.app.get(HUMAN_SERVICE_APP_KEY)
        service = ChatService(state=state, human_service=human_service)
        request.app[CHAT_SERVICE_APP_KEY] = service
    return service


def _get_chat_tasks(app: web.Application) -> set[asyncio.Task[Any]]:
    tasks = app.get(CHAT_TASKS_APP_KEY)
    if tasks is None:
        tasks = set()
        app[CHAT_TASKS_APP_KEY] = tasks
    return tasks


async def _read_json_body(request: web.Request) -> dict[str, Any] | None:
    try:
        data = await request.json()
    except (json.JSONDecodeError, ValueError, TypeError):
        return None
    if not isinstance(data, dict):
        return None
    return data


def _build_run_metadata(
    *,
    session_id: str,
    run_id: str,
    prompt: str,
    conversation_history: list[dict[str, Any]] | None,
    ephemeral_system_prompt: str | None,
    runtime_context: dict[str, Any] | None,
    status: str,
    source_run_id: str | None = None,
) -> dict[str, Any]:
    timestamp = time.time()
    
    # Try to resolve the active model configurations to populate Run panel metadata
    active_model = "unknown"
    active_provider = "unknown"
    try:
        from gateway.run import _resolve_gateway_model, _resolve_runtime_agent_kwargs
        active_model = _resolve_gateway_model()
        active_provider = _resolve_runtime_agent_kwargs().get("provider", "unknown")
    except Exception:
        pass

    metadata: dict[str, Any] = {
        "session_id": session_id,
        "run_id": run_id,
        "prompt": prompt,
        "status": status,
        "model": active_model,
        "provider": active_provider,
        "created_at": timestamp,
        "updated_at": timestamp,
        "observed": True,
    }
    if conversation_history:
        metadata["conversation_history"] = list(conversation_history)
    if ephemeral_system_prompt is not None:
        metadata["ephemeral_system_prompt"] = ephemeral_system_prompt
    if runtime_context:
        metadata["runtime_context"] = dict(runtime_context)
    if source_run_id is not None:
        metadata["source_run_id"] = source_run_id
    return metadata


async def _execute_tracked_run(
    *,
    service: ChatService,
    session_id: str,
    run_id: str,
    prompt: str,
    conversation_history: list[dict[str, Any]] | None,
    ephemeral_system_prompt: str | None,
    runtime_context: dict[str, Any] | None,
) -> None:
    state = service.state

    try:
        result = await service.run_chat(
            prompt=prompt,
            session_id=session_id,
            conversation_history=conversation_history,
            ephemeral_system_prompt=ephemeral_system_prompt,
            run_id=run_id,
            runtime_context=runtime_context,
        )
    except asyncio.CancelledError:
        state.update_run(run_id, status="cancelled", updated_at=time.time())
        raise
    except Exception as exc:
        state.update_run(
            run_id,
            status="failed",
            updated_at=time.time(),
            error=str(exc),
            error_type=type(exc).__name__,
        )
        return

    update: dict[str, Any] = {
        "updated_at": time.time(),
    }
    if result.get("failed"):
        update.update(
            {
                "status": "failed",
                "error": result.get("error") or "Run failed",
            }
        )
    else:
        update.update(
            {
                "status": "completed" if result.get("completed", True) else "running",
                "completed": result.get("completed", True),
                "final_response": ChatService._extract_assistant_text(dict(result or {})),
                "prompt_tokens": result.get("prompt_tokens"),
                "completion_tokens": result.get("completion_tokens"),
                "total_tokens": result.get("total_tokens"),
            }
        )
    state.update_run(run_id, **update)


def _start_tracked_run(
    request: web.Request,
    *,
    service: ChatService,
    session_id: str,
    prompt: str,
    conversation_history: list[dict[str, Any]] | None,
    ephemeral_system_prompt: str | None,
    runtime_context: dict[str, Any] | None,
    source_run_id: str | None = None,
) -> tuple[str, dict[str, Any]]:
    run_id = str(uuid.uuid4())
    metadata = _build_run_metadata(
        session_id=session_id,
        run_id=run_id,
        prompt=prompt,
        conversation_history=conversation_history,
        ephemeral_system_prompt=ephemeral_system_prompt,
        runtime_context=runtime_context,
        status="started",
        source_run_id=source_run_id,
    )
    service.state.record_run(run_id, metadata)

    task = asyncio.create_task(
        _execute_tracked_run(
            service=service,
            session_id=session_id,
            run_id=run_id,
            prompt=prompt,
            conversation_history=conversation_history,
            ephemeral_system_prompt=ephemeral_system_prompt,
            runtime_context=runtime_context,
        )
    )
    tasks = _get_chat_tasks(request.app)
    tasks.add(task)
    task.add_done_callback(tasks.discard)
    return run_id, metadata


async def handle_chat_send(request: web.Request) -> web.Response:
    """Start a chat run and immediately return tracked run metadata."""
    data = await _read_json_body(request)
    if data is None:
        return _json_error(
            status=400,
            code="invalid_json",
            message="Request body must be a valid JSON object.",
        )

    prompt = data.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        return _json_error(
            status=400,
            code="invalid_prompt",
            message="The 'prompt' field must be a non-empty string.",
        )

    session_id = data.get("session_id")
    if session_id is None:
        session_id = str(uuid.uuid4())
    elif not isinstance(session_id, str) or not session_id:
        return _json_error(
            status=400,
            code="invalid_session_id",
            message="The 'session_id' field must be a non-empty string when provided.",
        )
    conversation_history = data.get("conversation_history")
    if conversation_history is not None and not isinstance(conversation_history, list):
        return _json_error(
            status=400,
            code="invalid_conversation_history",
            message="The 'conversation_history' field must be a list when provided.",
        )
    if conversation_history is None:
        try:
            from hermes_state import SessionDB
            db = SessionDB()
            conversation_history = db.get_messages_as_conversation(session_id)
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning("Failed to load history from DB: %s", e)
            conversation_history = []

    runtime_context = data.get("runtime_context")
    if runtime_context is not None and not isinstance(runtime_context, dict):
        return _json_error(
            status=400,
            code="invalid_runtime_context",
            message="The 'runtime_context' field must be an object when provided.",
        )

    ephemeral_system_prompt = data.get("ephemeral_system_prompt")
    if ephemeral_system_prompt is not None and not isinstance(ephemeral_system_prompt, str):
        return _json_error(
            status=400,
            code="invalid_ephemeral_system_prompt",
            message="The 'ephemeral_system_prompt' field must be a string when provided.",
        )

    service = _get_chat_service(request)
    run_id, _ = _start_tracked_run(
        request,
        service=service,
        session_id=session_id,
        prompt=prompt,
        conversation_history=conversation_history,
        ephemeral_system_prompt=ephemeral_system_prompt,
        runtime_context=runtime_context,
    )
    return web.json_response(
        {
            "ok": True,
            "session_id": session_id,
            "run_id": run_id,
            "status": "started",
        }
    )


async def handle_chat_background(request: web.Request) -> web.Response:
    """Start a detached chat run in a new ephemeral session."""
    data = await _read_json_body(request)
    if data is None:
        return _json_error(status=400, code="invalid_json", message="Request body must be a valid JSON object.")

    prompt = data.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        return _json_error(status=400, code="invalid_prompt", message="The 'prompt' field must be a non-empty string.")

    # Always generate a new session ID for background runs so they don't pollute the caller's active session
    bg_session_id = str(uuid.uuid4())
    
    runtime_context = data.get("runtime_context", {})
    runtime_context["is_background"] = True
    ephemeral_system_prompt = data.get("ephemeral_system_prompt")

    service = _get_chat_service(request)
    # Note: We do NOT pass conversation_history. Background tasks execute freshly.
    run_id, _ = _start_tracked_run(
        request,
        service=service,
        session_id=bg_session_id,
        prompt=prompt,
        conversation_history=[],
        ephemeral_system_prompt=ephemeral_system_prompt,
        runtime_context=runtime_context,
    )
    return web.json_response(
        {
            "ok": True,
            "session_id": bg_session_id,
            "run_id": run_id,
            "status": "started",
        }
    )


async def handle_chat_get_backgrounds(request: web.Request) -> web.Response:
    """Return all tracked runs that are marked as background jobs."""
    service = _get_chat_service(request)
    runs = list(service.state.runs.values())
    bg_runs = [r for r in runs if r.get("runtime_context", {}).get("is_background")]
    bg_runs.reverse()  # Newest first
    return web.json_response({"ok": True, "background_runs": bg_runs})


async def handle_chat_btw(request: web.Request) -> web.Response:
    """Start a quick-ask chat run that does not update session memory."""
    data = await _read_json_body(request)
    if data is None:
        return _json_error(status=400, code="invalid_json", message="Request body must be a valid JSON object.")

    prompt = data.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        return _json_error(status=400, code="invalid_prompt", message="The 'prompt' field must be a non-empty string.")

    session_id = data.get("session_id")
    if session_id is None:
        session_id = str(uuid.uuid4())
    elif not isinstance(session_id, str) or not session_id:
        return _json_error(status=400, code="invalid_session_id", message="The 'session_id' field must be a non-empty string when provided.")

    conversation_history = data.get("conversation_history")
    if conversation_history is not None and not isinstance(conversation_history, list):
        return _json_error(
            status=400,
            code="invalid_conversation_history",
            message="The 'conversation_history' field must be a list when provided.",
        )
    if conversation_history is None:
        try:
            from hermes_state import SessionDB
            db = SessionDB()
            conversation_history = db.get_messages_as_conversation(session_id)
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning("Failed to load history from DB: %s", e)
            conversation_history = []
    
    runtime_context = data.get("runtime_context", {})
    runtime_context["quick_ask"] = True

    ephemeral_system_prompt = data.get("ephemeral_system_prompt")

    service = _get_chat_service(request)
    run_id, _ = _start_tracked_run(
        request,
        service=service,
        session_id=session_id,
        prompt=prompt,
        conversation_history=conversation_history,
        ephemeral_system_prompt=ephemeral_system_prompt,
        runtime_context=runtime_context,
    )
    return web.json_response(
        {
            "ok": True,
            "session_id": session_id,
            "run_id": run_id,
            "status": "started_btw",
        }
    )


async def handle_chat_stop(request: web.Request) -> web.Response:
    """Return stop capability metadata for a known run."""
    data = await _read_json_body(request)
    if data is None:
        return _json_error(
            status=400,
            code="invalid_json",
            message="Request body must be a valid JSON object.",
        )

    run_id = data.get("run_id")
    if not isinstance(run_id, str) or not run_id:
        return _json_error(
            status=400,
            code="invalid_run_id",
            message="The 'run_id' field must be a non-empty string.",
        )

    service = _get_chat_service(request)
    run = service.state.get_run(run_id)
    if run is None:
        return _json_error(
            status=404,
            code="run_not_found",
            message="No tracked run was found for the provided run_id.",
            run_id=run_id,
        )

    return web.json_response(
        {
            "ok": True,
            "supported": False,
            "action": "stop",
            "run_id": run_id,
            "session_id": run["session_id"],
            "status": run.get("status"),
            "stop_requested": False,
        }
    )


async def handle_chat_retry(request: web.Request) -> web.Response:
    """Retry a previously observed run by reusing its tracked input metadata."""
    data = await _read_json_body(request)
    if data is None:
        return _json_error(
            status=400,
            code="invalid_json",
            message="Request body must be a valid JSON object.",
        )

    run_id = data.get("run_id")
    if not isinstance(run_id, str) or not run_id:
        return _json_error(
            status=400,
            code="invalid_run_id",
            message="The 'run_id' field must be a non-empty string.",
        )

    service = _get_chat_service(request)
    existing_run = service.state.get_run(run_id)
    if existing_run is None:
        return _json_error(
            status=404,
            code="run_not_found",
            message="No tracked run was found for the provided run_id.",
            run_id=run_id,
        )

    new_run_id, _ = _start_tracked_run(
        request,
        service=service,
        session_id=existing_run["session_id"],
        prompt=existing_run["prompt"],
        conversation_history=existing_run.get("conversation_history"),
        ephemeral_system_prompt=existing_run.get("ephemeral_system_prompt"),
        runtime_context=existing_run.get("runtime_context"),
        source_run_id=run_id,
    )
    return web.json_response(
        {
            "ok": True,
            "session_id": existing_run["session_id"],
            "run_id": new_run_id,
            "retried_from_run_id": run_id,
            "status": "started",
        }
    )


async def handle_chat_undo(request: web.Request) -> web.Response:
    """Return structured undo capability metadata."""
    data = await _read_json_body(request)
    if data is None:
        return _json_error(
            status=400,
            code="invalid_json",
            message="Request body must be a valid JSON object.",
        )

    session_id = data.get("session_id")
    run_id = data.get("run_id")
    if session_id is not None and not isinstance(session_id, str):
        return _json_error(
            status=400,
            code="invalid_session_id",
            message="The 'session_id' field must be a string when provided.",
        )
    if run_id is not None and not isinstance(run_id, str):
        return _json_error(
            status=400,
            code="invalid_run_id",
            message="The 'run_id' field must be a string when provided.",
        )

    return web.json_response(
        {
            "ok": True,
            "supported": False,
            "action": "undo",
            "session_id": session_id,
            "run_id": run_id,
            "status": "unavailable",
        }
    )


async def handle_chat_run(request: web.Request) -> web.Response:
    """Return tracked metadata for a previously observed run."""
    run_id = request.match_info["run_id"]
    service = _get_chat_service(request)
    run = service.state.get_run(run_id)
    if run is None:
        return _json_error(
            status=404,
            code="run_not_found",
            message="No tracked run was found for the provided run_id.",
            run_id=run_id,
        )

    return web.json_response(
        {
            "ok": True,
            "run": run,
        }
    )


async def handle_chat_compress(request: web.Request) -> web.Response:
    """Manually trigger context compression for a session."""
    data = await _read_json_body(request)
    if data is None:
        return _json_error(status=400, code="invalid_json", message="Request body must be a valid JSON object.")

    session_id = data.get("session_id")
    if not isinstance(session_id, str) or not session_id:
        return _json_error(status=400, code="invalid_session_id", message="The 'session_id' field must be a non-empty string.")

    focus_topic = data.get("focus_topic")  # Optional guided compression topic

    from run_agent import AIAgent
    
    # Initialize agent for context loading
    # Run in executor to not block async loop if it's slow
    def _do_compress():
        try:
            from hermes_state import SessionDB
            session_db = SessionDB()
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning("SessionDB unavailable: %s", e)
            session_db = None

        agent = AIAgent(session_id=session_id, session_db=session_db)
        if len(agent.messages) < agent.context_compressor.protect_first_n + agent.context_compressor.protect_last_n + 1:
            return {"ok": True, "compressed": False, "reason": "not_enough_messages"}
            
        orig_len = len(agent.messages)
        compress_kwargs = {}
        if focus_topic:
            compress_kwargs["focus_topic"] = focus_topic
        agent.messages = agent.context_compressor.compress(agent.messages, **compress_kwargs)
        
        if len(agent.messages) < orig_len:
            agent._flush_messages_to_session_db()
            return {"ok": True, "compressed": True, "original_length": orig_len, "new_length": len(agent.messages), "focus_topic": focus_topic or None}
        else:
            return {"ok": True, "compressed": False, "reason": "no_reduction"}

    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(None, _do_compress)
    return web.json_response(result)


async def _cleanup_chat_tasks(app: web.Application) -> None:
    tasks = list(_get_chat_tasks(app))
    for task in tasks:
        task.cancel()
    for task in tasks:
        with contextlib.suppress(asyncio.CancelledError):
            await task


def register_chat_api_routes(app: web.Application) -> None:
    """Register web-console chat API routes on an aiohttp application."""
    _get_chat_tasks(app)
    if app.get(CHAT_STATE_APP_KEY) is None:
        app[CHAT_STATE_APP_KEY] = create_web_console_state()
    if app.get(CHAT_SERVICE_APP_KEY) is None:
        app[CHAT_SERVICE_APP_KEY] = ChatService(
            state=app[CHAT_STATE_APP_KEY],
            human_service=app.get(HUMAN_SERVICE_APP_KEY),
        )
    if not app.get(CHAT_CLEANUP_REGISTERED_APP_KEY):
        app.on_cleanup.append(_cleanup_chat_tasks)
        app[CHAT_CLEANUP_REGISTERED_APP_KEY] = True

    app.router.add_post("/api/gui/chat/send", handle_chat_send)
    app.router.add_post("/api/gui/chat/background", handle_chat_background)
    app.router.add_post("/api/gui/chat/btw", handle_chat_btw)
    app.router.add_post("/api/gui/chat/compress", handle_chat_compress)
    app.router.add_get("/api/gui/chat/backgrounds", handle_chat_get_backgrounds)
    app.router.add_post("/api/gui/chat/stop", handle_chat_stop)
    app.router.add_post("/api/gui/chat/retry", handle_chat_retry)
    app.router.add_post("/api/gui/chat/undo", handle_chat_undo)
    app.router.add_get("/api/gui/chat/run/{run_id}", handle_chat_run)
