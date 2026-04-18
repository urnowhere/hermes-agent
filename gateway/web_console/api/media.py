"""Media API routes for the Hermes Web Console backend."""

from __future__ import annotations

import importlib.util
import json
import uuid
from pathlib import Path
from types import ModuleType
from typing import Any

from aiohttp import web

from hermes_cli.config import ensure_hermes_home, get_hermes_home


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


def _load_tool_module(module_name: str, file_name: str) -> ModuleType:
    module_path = Path(__file__).resolve().parents[3] / "tools" / file_name
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load tool module from {module_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _media_upload_dir() -> Path:
    ensure_hermes_home()
    target = get_hermes_home() / "uploads" / "web_console"
    target.mkdir(parents=True, exist_ok=True)
    return target


async def handle_media_upload(request: web.Request) -> web.Response:
    if not request.content_type.startswith("multipart/"):
        return _json_error(status=400, code="invalid_upload", message="Upload requests must use multipart/form-data.")
    reader = await request.multipart()
    part = await reader.next()
    if part is None or part.name != "file":
        return _json_error(status=400, code="missing_file", message="The upload must include a 'file' field.")

    filename = part.filename or f"upload-{uuid.uuid4().hex}"
    safe_name = Path(filename).name or f"upload-{uuid.uuid4().hex}"
    destination = _media_upload_dir() / safe_name
    if destination.exists():
        destination = _media_upload_dir() / f"{destination.stem}-{uuid.uuid4().hex[:8]}{destination.suffix}"

    size = 0
    with destination.open("wb") as handle:
        while True:
            chunk = await part.read_chunk()
            if not chunk:
                break
            size += len(chunk)
            handle.write(chunk)

    return web.json_response(
        {
            "ok": True,
            "media": {
                "file_path": str(destination),
                "filename": destination.name,
                "content_type": part.headers.get("Content-Type"),
                "size": size,
            },
        }
    )


async def handle_media_transcribe(request: web.Request) -> web.Response:
    data = await _read_json_body(request)
    if data is None:
        return _json_error(status=400, code="invalid_json", message="Request body must be a valid JSON object.")

    file_path = data.get("file_path")
    if not isinstance(file_path, str) or not file_path.strip():
        return _json_error(status=400, code="invalid_file_path", message="The 'file_path' field must be a non-empty string.")

    transcription_tools = _load_tool_module("hermes_web_console_transcription_tools", "transcription_tools.py")
    result = transcription_tools.transcribe_audio(file_path.strip(), model=data.get("model"))
    status = 200 if result.get("success") else 400
    return web.json_response({"ok": bool(result.get("success")), "transcription": result}, status=status)


async def handle_media_tts(request: web.Request) -> web.Response:
    data = await _read_json_body(request)
    if data is None:
        return _json_error(status=400, code="invalid_json", message="Request body must be a valid JSON object.")

    text = data.get("text")
    if not isinstance(text, str) or not text.strip():
        return _json_error(status=400, code="invalid_text", message="The 'text' field must be a non-empty string.")

    output_path = data.get("output_path")
    if output_path is not None and not isinstance(output_path, str):
        return _json_error(status=400, code="invalid_output_path", message="The 'output_path' field must be a string when provided.")

    tts_tool = _load_tool_module("hermes_web_console_tts_tool", "tts_tool.py")
    raw_result = tts_tool.text_to_speech_tool(text=text, output_path=output_path)
    try:
        result = json.loads(raw_result)
    except (TypeError, ValueError):
        result = {"success": False, "error": "TTS returned an invalid payload.", "raw_result": raw_result}
    status = 200 if result.get("success") else 400
    return web.json_response({"ok": bool(result.get("success")), "tts": result}, status=status)


async def handle_media_transcribe_upload(request: web.Request) -> web.Response:
    return _json_error(status=501, code="not_implemented", message="Use /api/gui/media/upload followed by /api/gui/media/transcribe.")


def register_media_api_routes(app: web.Application) -> None:
    app.router.add_post("/api/gui/media/upload", handle_media_upload)
    app.router.add_post("/api/gui/media/transcribe", handle_media_transcribe)
    app.router.add_post("/api/gui/media/tts", handle_media_tts)
