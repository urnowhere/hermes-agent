"""Version info API route for the Hermes Web Console backend."""

from __future__ import annotations

import logging
import aiohttp
from aiohttp import web

logger = logging.getLogger("version_api")

async def handle_get_version(request: web.Request) -> web.Response:
    try:
        from hermes_cli import __version__, __release_date__
    except ImportError:
        __version__ = "unknown"
        __release_date__ = "unknown"

    return web.json_response({
        "ok": True,
        "version": __version__,
        "release_date": __release_date__,
    })

async def handle_check_update(request: web.Request) -> web.Response:
    """Check PyPI for the latest version of hermes-agent."""
    try:
        from hermes_cli import __version__
    except ImportError:
        __version__ = "0.0.0"

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get("https://pypi.org/pypi/hermes-agent/json", timeout=5) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    latest_version = data["info"]["version"]
                    
                    # Basic comparison assuming semantic versioning like 0.6.1 vs 0.6.0
                    # For simplicity, we just check if it's different.
                    # Normally we'd use packaging.version.parse, but standard string comparison
                    # or exact matching is often enough to say "update available" if __version__ != latest
                    has_update = __version__ != "unknown" and latest_version != __version__
                    
                    # Attempt to fetch changelog or description for the new version
                    releases = data.get("releases", {})
                    latest_release_info = releases.get(latest_version, [])
                    changelog = ""
                    if latest_release_info:
                        # Extract some basic info, like if there's a Github release description
                        # Actually PyPI JSON doesn't contain the markdown changelog for that release easily.
                        # We'll just provide the project url if needed.
                        pass
                    
                    return web.json_response({
                        "ok": True,
                        "current_version": __version__,
                        "latest_version": latest_version,
                        "has_update": has_update,
                        "project_url": data["info"]["project_urls"].get("Homepage", "https://pypi.org/project/hermes-agent/")
                    })
                return web.json_response({
                    "ok": False,
                    "error": f"Failed to fetch PyPI data: {resp.status}"
                }, status=502)
    except Exception as e:
        logger.error(f"Failed to check for updates: {e}")
        return web.json_response({"ok": False, "error": str(e)}, status=500)


def register_version_api_routes(app: web.Application) -> None:
    app.router.add_get("/api/gui/version", handle_get_version)
    app.router.add_get("/api/gui/version/check", handle_check_update)
