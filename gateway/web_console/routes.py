"""Route handlers for the Hermes Web Console backend skeleton."""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

from aiohttp import web

from .api import register_web_console_api_routes
from .sse import SseMessage, stream_sse
from .state import get_web_console_state
from . import static as web_console_static

ADAPTER_APP_KEY = web.AppKey("hermes_web_console_adapter", object)


async def handle_gui_health(request: web.Request) -> web.Response:
    """Return a simple health payload for the GUI backend."""
    return web.json_response(
        {
            "status": "ok",
            "service": "gui-backend",
            "product": "hermes-web-console",
        }
    )


async def handle_gui_meta(request: web.Request) -> web.Response:
    """Return minimal metadata for the GUI backend."""
    adapter = request.app.get(ADAPTER_APP_KEY)
    adapter_name = getattr(adapter, "name", None)
    return web.json_response(
        {
            "product": "hermes-web-console",
            "api_base_path": "/api/gui",
            "app_base_path": "/app/",
            "adapter": adapter_name,
        }
    )


async def handle_app_root(request: web.Request) -> web.Response:
    """Serve the web console SPA shell."""
    return web.Response(
        text=web_console_static.get_web_console_app_html(),
        content_type="text/html",
    )


async def handle_app_redirect(request: web.Request) -> web.StreamResponse:
    """Redirect /app to the canonical trailing-slash SPA mount."""
    raise web.HTTPFound("/app/")


async def handle_app_manifest(request: web.Request) -> web.Response:
    """Serve the PWA manifest for the mounted GUI app."""
    manifest = web_console_static.get_web_console_manifest_json()
    if manifest is None:
        raise web.HTTPNotFound(text="manifest.json not found")
    return web.Response(text=manifest, content_type="application/manifest+json")


async def handle_app_service_worker(request: web.Request) -> web.Response:
    """Serve the service worker for the mounted GUI app."""
    service_worker = web_console_static.get_web_console_service_worker()
    if service_worker is None:
        raise web.HTTPNotFound(text="sw.js not found")
    return web.Response(text=service_worker, content_type="application/javascript")


async def handle_app_asset(request: web.Request) -> web.StreamResponse:
    """Serve built frontend assets from web_console/dist/assets."""
    rel_path = request.match_info["tail"]
    asset_path = web_console_static.get_web_console_dist_dir() / "assets" / rel_path
    if not asset_path.is_file():
        raise web.HTTPNotFound(text=f"asset not found: {rel_path}")
    return web.FileResponse(asset_path)


async def handle_app_icon(request: web.Request) -> web.StreamResponse:
    """Serve static PWA icons from the frontend source tree."""
    filename = request.match_info["filename"]
    icon_path = web_console_static.get_web_console_frontend_root() / "icons" / filename
    if not icon_path.is_file():
        raise web.HTTPNotFound(text=f"icon not found: {filename}")
    return web.FileResponse(icon_path)


async def _event_stream_generator(request: web.Request, session_id: str):
    state = get_web_console_state()
    queue = await state.event_bus.subscribe(session_id)
    try:
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=15.0)
            except asyncio.TimeoutError:
                yield SseMessage(data={"ping": time.time()})
                continue
            payload = {
                "type": event.type,
                "session_id": event.session_id,
                "run_id": event.run_id,
                "payload": event.payload,
                "ts": event.ts,
            }
            yield SseMessage(data=payload)
    finally:
        await state.event_bus.unsubscribe(session_id, queue)


async def handle_session_event_stream(request: web.Request) -> web.StreamResponse:
    """GET /api/gui/stream/session/{session_id} — SSE event stream for a session."""
    session_id = request.match_info["session_id"]
    return await stream_sse(request, _event_stream_generator(request, session_id))


def register_web_console_routes(app: web.Application, adapter: Any = None) -> None:
    """Register the Hermes Web Console routes on an aiohttp application."""
    app[ADAPTER_APP_KEY] = adapter
    app.router.add_get("/api/gui/health", handle_gui_health)
    app.router.add_get("/api/gui/meta", handle_gui_meta)
    app.router.add_get(
        "/api/gui/stream/session/{session_id}", handle_session_event_stream
    )
    register_web_console_api_routes(app)
    app.router.add_get("/app", handle_app_redirect)
    app.router.add_get("/app/", handle_app_root)
    app.router.add_get("/app/index.html", handle_app_root)
    app.router.add_get("/app/manifest.json", handle_app_manifest)
    app.router.add_get("/app/sw.js", handle_app_service_worker)
    app.router.add_get("/app/icons/{filename}", handle_app_icon)
    app.router.add_get("/app/assets/{tail:.*}", handle_app_asset)
    app.router.add_get("/app/{tail:.*}", handle_app_root)
