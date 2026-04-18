"""Browser service helpers for the Hermes Web Console backend."""

from __future__ import annotations

import socket
from typing import Any, Callable

from hermes_cli.config import save_env_value


class BrowserService:
    """Thin wrapper around Hermes browser connection state."""

    DEFAULT_CDP_URL = "http://localhost:9222"

    def __init__(
        self,
        *,
        active_sessions_getter: Callable[[], dict[str, dict[str, str]]] | None = None,
        cleanup_all_browsers: Callable[[], None] | None = None,
        requirements_checker: Callable[[], bool] | None = None,
        cdp_resolver: Callable[[str], str] | None = None,
        env_writer: Callable[[str, str], None] = save_env_value,
    ) -> None:
        self._active_sessions_getter = active_sessions_getter or (lambda: {})
        self._cleanup_all_browsers = cleanup_all_browsers or (lambda: None)
        self._requirements_checker = requirements_checker or (lambda: True)
        self._cdp_resolver = cdp_resolver or (lambda value: value)
        self._env_writer = env_writer

    def get_status(self) -> dict[str, Any]:
        current = self._current_cdp_url()
        mode = "live_cdp" if current else ("browserbase" if self._has_browserbase_credentials() else "local")
        return {
            "mode": mode,
            "connected": bool(current),
            "cdp_url": current,
            "reachable": self._is_cdp_reachable(current) if current else None,
            "requirements_ok": self._requirements_checker(),
            "active_sessions": self._active_sessions_getter(),
        }

    def connect(self, cdp_url: str | None = None) -> dict[str, Any]:
        target = (cdp_url or self.DEFAULT_CDP_URL).strip()
        if not target:
            raise ValueError("The 'cdp_url' field must be a non-empty string when provided.")
        resolved = self._cdp_resolver(target)
        self._cleanup_all_browsers()
        self._env_writer("BROWSER_CDP_URL", target)
        return {
            **self.get_status(),
            "requested_cdp_url": target,
            "cdp_url": resolved or target,
            "message": "Browser connected to a live Chrome CDP endpoint.",
        }

    def disconnect(self) -> dict[str, Any]:
        self._cleanup_all_browsers()
        self._env_writer("BROWSER_CDP_URL", "")
        status = self.get_status()
        status["message"] = "Browser reverted to the default backend."
        return status

    @staticmethod
    def _has_browserbase_credentials() -> bool:
        import os

        return bool(os.getenv("BROWSERBASE_API_KEY", "").strip())

    @staticmethod
    def _current_cdp_url() -> str:
        import os

        return os.getenv("BROWSER_CDP_URL", "").strip()

    @staticmethod
    def _is_cdp_reachable(cdp_url: str) -> bool:
        try:
            port = int(cdp_url.rsplit(":", 1)[-1].split("/")[0])
        except (TypeError, ValueError, IndexError):
            return False
        host = "127.0.0.1"
        try:
            with socket.create_connection((host, port), timeout=1):
                return True
        except OSError:
            return False
