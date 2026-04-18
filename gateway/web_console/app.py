"""App-level helpers for mounting the Hermes Web Console backend."""

from __future__ import annotations

from typing import Any


def maybe_register_web_console(app: Any, adapter: Any = None) -> None:
    """Register the lightweight Hermes Web Console backend on an aiohttp app."""
    from .routes import register_web_console_routes

    register_web_console_routes(app, adapter=adapter)
