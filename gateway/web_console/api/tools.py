"""Tools management API routes for the Hermes Web Console backend."""

import logging
from aiohttp import web
from tools.registry import registry

logger = logging.getLogger("tools_api")


async def handle_list_tools(request: web.Request) -> web.Response:
    tools_list = []
    for name, tool in registry._tools.items():
        # ToolEntry doesn't have is_available(); check via check_fn
        available = True
        if tool.check_fn:
            try:
                available = bool(tool.check_fn())
            except Exception:
                available = False
        tools_list.append({
            "name": name,
            "toolset": tool.toolset,
            "description": tool.schema.get("description", ""),
            "parameters": tool.schema.get("parameters", {}),
            "requires_env": tool.requires_env,
            "is_available": available,
        })
    return web.json_response({
        "ok": True,
        "tools": tools_list
    })


async def handle_list_toolsets(request: web.Request) -> web.Response:
    """List all toolsets with availability, tool counts, and enabled status."""
    try:
        from hermes_cli.tools_config import CONFIGURABLE_TOOLSETS, _toolset_has_keys
        from hermes_cli.config import load_config
        from toolsets import resolve_toolset

        config = load_config()

        # Get currently enabled toolsets from platform_toolsets (CLI by default)
        platform = request.query.get("platform", "cli")
        platform_toolsets_config = config.get("platform_toolsets", {})
        enabled_ts = platform_toolsets_config.get(platform)

        # If no explicit config, resolve from default toolset
        from hermes_cli.tools_config import _get_platform_tools
        enabled_toolset_keys = _get_platform_tools(config, platform)

        toolsets = []
        for ts_key, ts_label, ts_desc in CONFIGURABLE_TOOLSETS:
            ts_tools = sorted(resolve_toolset(ts_key))
            ts_available = registry.is_toolset_available(ts_key)
            ts_has_keys = _toolset_has_keys(ts_key)
            toolsets.append({
                "key": ts_key,
                "label": ts_label,
                "description": ts_desc,
                "tools": ts_tools,
                "tool_count": len(ts_tools),
                "available": ts_available,
                "has_keys": ts_has_keys,
                "enabled": ts_key in enabled_toolset_keys,
            })

        return web.json_response({"ok": True, "toolsets": toolsets, "platform": platform})
    except Exception as e:
        logger.error(f"Failed to list toolsets: {e}")
        return web.json_response({"ok": False, "error": str(e)}, status=500)


async def handle_toggle_toolset(request: web.Request) -> web.Response:
    """Enable or disable a toolset for a platform."""
    try:
        import json
        data = await request.json()
        ts_key = data.get("toolset")
        enabled = data.get("enabled", True)
        platform = data.get("platform", "cli")

        if not ts_key:
            return web.json_response({"ok": False, "error": "toolset key required"}, status=400)

        from hermes_cli.config import load_config
        from hermes_cli.tools_config import _get_platform_tools, _save_platform_tools

        config = load_config()
        current = _get_platform_tools(config, platform)

        if enabled:
            current.add(ts_key)
        else:
            current.discard(ts_key)

        _save_platform_tools(config, platform, current)

        return web.json_response({
            "ok": True,
            "toolset": ts_key,
            "enabled": enabled,
            "platform": platform,
        })
    except Exception as e:
        logger.error(f"Failed to toggle toolset: {e}")
        return web.json_response({"ok": False, "error": str(e)}, status=500)


def register_tools_api_routes(app: web.Application) -> None:
    app.router.add_get("/api/gui/tools", handle_list_tools)
    app.router.add_get("/api/gui/toolsets", handle_list_toolsets)
    app.router.add_post("/api/gui/toolsets/toggle", handle_toggle_toolset)
