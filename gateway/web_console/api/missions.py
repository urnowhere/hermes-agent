"""GUI backend API routes for Missions Kanban Board."""

import json
from pathlib import Path

from aiohttp import web
from hermes_constants import get_hermes_home

def get_missions_file_path() -> Path:
    return get_hermes_home() / "missions.json"

async def handle_get_missions(request: web.Request) -> web.Response:
    """Retrieve missions.json content."""
    missions_file = get_missions_file_path()
    
    if not missions_file.exists():
        return web.json_response({"ok": True, "columns": None})
        
    try:
        content = missions_file.read_text(encoding="utf-8")
        data = json.loads(content)
        return web.json_response({"ok": True, "columns": data})
    except Exception as e:
        return web.json_response({"ok": False, "error": str(e)}, status=500)

async def handle_update_missions(request: web.Request) -> web.Response:
    """Overwrite missions.json content entirely."""
    try:
        data = await request.json()
        columns = data.get("columns", [])
    except json.JSONDecodeError:
        return web.json_response({"ok": False, "error": "Invalid JSON"}, status=400)
        
    missions_file = get_missions_file_path()
    try:
        missions_file.parent.mkdir(parents=True, exist_ok=True)
        missions_file.write_text(json.dumps(columns, indent=2), encoding="utf-8")
        return web.json_response({"ok": True})
    except Exception as e:
        return web.json_response({"ok": False, "error": str(e)}, status=500)

def register_missions_api_routes(app: web.Application) -> None:
    app.router.add_get("/api/gui/missions", handle_get_missions)
    app.router.add_post("/api/gui/missions", handle_update_missions)
