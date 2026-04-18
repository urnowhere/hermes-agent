import logging
from aiohttp import web

logger = logging.getLogger("mcp_api")

async def handle_get_mcp_servers(request: web.Request) -> web.Response:
    """List all configured MCP servers and their connection status."""
    try:
        from tools.mcp_tool import get_mcp_status
        status_list = get_mcp_status()
        return web.json_response({"servers": status_list})
    except Exception as e:
        logger.error(f"Failed to fetch MCP servers: {e}")
        return web.json_response({"servers": []})

async def handle_reload_mcp_servers(request: web.Request) -> web.Response:
    """Shutdown and reload MCP servers from config."""
    try:
        from tools.mcp_tool import shutdown_mcp_servers, discover_mcp_tools, _servers, _lock
        logger.info("Reloading MCP servers from GUI request...")

        old_servers = set()
        if _lock:
            with _lock:
                old_servers = set(_servers.keys())

        # Disconnect all existing servers
        shutdown_mcp_servers()

        # Reconnect using updated config
        new_tools = discover_mcp_tools()

        new_servers = set()
        if _lock:
            with _lock:
                new_servers = set(_servers.keys())

        return web.json_response({
            "success": True, 
            "tools_count": len(new_tools), 
            "servers_count": len(new_servers),
            "added": list(new_servers - old_servers),
            "removed": list(old_servers - new_servers),
            "reconnected": list(new_servers & old_servers)
        })
    except Exception as e:
        logger.error(f"Failed to reload MCP servers: {e}")
        return web.json_response({"success": False, "error": str(e)}, status=500)

def register_mcp_api_routes(app: web.Application) -> None:
    app.router.add_get("/api/gui/mcp/servers", handle_get_mcp_servers)
    app.router.add_post("/api/gui/mcp/reload", handle_reload_mcp_servers)
