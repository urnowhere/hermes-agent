from aiohttp import web
from hermes_cli.plugins import get_plugin_manager, discover_plugins

async def handle_get_plugins(request: web.Request) -> web.Response:
    # Ensure plugins are discovered
    discover_plugins()
    
    manager = get_plugin_manager()
    plugins = manager.list_plugins()
    return web.json_response({"ok": True, "plugins": plugins})


def register_plugins_api_routes(app: web.Application) -> None:
    app.router.add_get("/api/gui/plugins", handle_get_plugins)
