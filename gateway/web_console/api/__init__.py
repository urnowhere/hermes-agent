"""API route registration helpers for the Hermes Web Console backend."""

from __future__ import annotations

from aiohttp import web

from .approvals import register_approval_api_routes
from .browser import register_browser_api_routes
from .chat import register_chat_api_routes
from .commands import register_commands_api_routes
from .cron import register_cron_api_routes
from .gateway_admin import register_gateway_admin_api_routes
from .logs import register_logs_api_routes
from .media import register_media_api_routes
from .metrics import register_metrics_api_routes
from .memory import register_memory_api_routes
from .models_api import register_models_api_routes
from .sessions import register_sessions_api_routes
from .settings import register_settings_api_routes
from .skills import register_skills_api_routes
from .tools import register_tools_api_routes
from .version import register_version_api_routes
from .workspace import register_workspace_api_routes
from .profiles import register_profiles_api_routes
from .plugins import register_plugins_api_routes
from .mcp import register_mcp_api_routes
from .usage import register_usage_api_routes
from .missions import register_missions_api_routes
from .credentials import register_credentials_api_routes
from .system import register_system_api_routes


def register_web_console_api_routes(app: web.Application) -> None:
    """Register modular web-console API routes on an aiohttp application."""
    register_approval_api_routes(app)
    register_browser_api_routes(app)
    register_chat_api_routes(app)
    register_commands_api_routes(app)
    register_cron_api_routes(app)
    register_gateway_admin_api_routes(app)
    register_logs_api_routes(app)
    register_media_api_routes(app)
    register_metrics_api_routes(app)
    register_memory_api_routes(app)
    register_models_api_routes(app)
    register_sessions_api_routes(app)
    register_settings_api_routes(app)
    register_skills_api_routes(app)
    register_tools_api_routes(app)
    register_version_api_routes(app)
    register_workspace_api_routes(app)
    register_profiles_api_routes(app)
    register_plugins_api_routes(app)
    register_mcp_api_routes(app)
    register_usage_api_routes(app)
    register_missions_api_routes(app)
    register_credentials_api_routes(app)
    register_system_api_routes(app)


__all__ = [
    "register_web_console_api_routes",
    "register_chat_api_routes",
    "register_browser_api_routes",
    "register_commands_api_routes",
    "register_cron_api_routes",
    "register_gateway_admin_api_routes",
    "register_logs_api_routes",
    "register_media_api_routes",
    "register_metrics_api_routes",
    "register_memory_api_routes",
    "register_models_api_routes",
    "register_sessions_api_routes",
    "register_settings_api_routes",
    "register_skills_api_routes",
    "register_tools_api_routes",
    "register_version_api_routes",
    "register_approval_api_routes",
    "register_workspace_api_routes",
    "register_profiles_api_routes",
    "register_plugins_api_routes",
    "register_mcp_api_routes",
    "register_usage_api_routes",
    "register_missions_api_routes",
    "register_credentials_api_routes",
    "register_system_api_routes",
]
