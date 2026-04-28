# ABOUTME: Steel (steel.dev) cloud browser provider for headless browser sessions.
# ABOUTME: Delegates session lifecycle + command dispatch to the Steel CLI.
"""Steel cloud browser provider.

Browser-automation sessions go through the Steel CLI (`steel browser ...`),
which speaks Steel's CDP dialect natively. agent-browser cannot connect to
Steel's WS endpoint directly, so the Steel CLI is required for the CDP path
(``cloud_provider: steel``). The standalone ``steel_scrape`` tool is
unaffected — it hits Steel's HTTP scrape API directly and needs no CLI.
"""

import json
import logging
import os
import shutil
import subprocess
import uuid
from typing import Dict, Optional

import requests

from tools.browser_providers.base import CloudBrowserProvider

logger = logging.getLogger(__name__)

_DEFAULT_BASE_URL = "https://api.steel.dev"

_STEEL_CLI_INSTALL_HINT = (
    "Steel CLI not found on PATH. The Steel browser provider requires the "
    "Steel CLI for browser automation. Install it with one of:\n"
    "  curl -fsS https://setup.steel.dev | sh\n"
    "  npm install -g @steel-dev/cli\n"
    "(The standalone steel_scrape tool works without the CLI — only "
    "browser_navigate / browser_click / browser_snapshot via "
    "cloud_provider=steel need it.)"
)


def _find_steel_cli() -> Optional[str]:
    """Return absolute path to the ``steel`` CLI, or None if not on PATH."""
    return shutil.which("steel")


class SteelProvider(CloudBrowserProvider):
    """Steel (https://steel.dev) cloud browser backend.

    Provides headless browser sessions with optional proxy rotation,
    CAPTCHA solving, and configurable timeouts.  Activated when
    ``STEEL_API_KEY`` is set and ``config["browser"]["cloud_provider"]``
    is ``"steel"``.

    Environment Variables:
    - STEEL_API_KEY: API key (required)
    - STEEL_BASE_URL: Override base URL for self-hosted instances
    - STEEL_USE_PROXY: Enable residential proxy (default: disabled)
    - STEEL_SOLVE_CAPTCHA: Enable CAPTCHA solving (default: disabled)
    - STEEL_SESSION_TIMEOUT: Session timeout in milliseconds
    """

    def provider_name(self) -> str:
        return "Steel"

    def is_configured(self) -> bool:
        return bool(os.environ.get("STEEL_API_KEY"))

    # ------------------------------------------------------------------
    # Internal helpers (kept for steel_scrape, which uses Steel's HTTP API)
    # ------------------------------------------------------------------

    def _base_url(self) -> str:
        return os.environ.get("STEEL_BASE_URL", _DEFAULT_BASE_URL).rstrip("/")

    def _headers(self) -> Dict[str, str]:
        api_key = os.environ.get("STEEL_API_KEY")
        if not api_key:
            raise ValueError(
                "STEEL_API_KEY environment variable is required. "
                "Get your key at https://steel.dev"
            )
        return {
            "Content-Type": "application/json",
            "Steel-Api-Key": api_key,
        }

    def _run_cli(self, args: list, timeout: float) -> Dict[str, object]:
        """Run a steel CLI subcommand and return the parsed JSON envelope."""
        cli = _find_steel_cli()
        if not cli:
            raise RuntimeError(_STEEL_CLI_INSTALL_HINT)

        try:
            result = subprocess.run(
                [cli, *args, "--json"],
                capture_output=True,
                text=True,
                timeout=timeout,
                env=os.environ.copy(),
            )
        except subprocess.TimeoutExpired:
            raise RuntimeError(
                f"steel CLI timed out after {timeout}s: {' '.join(args)}"
            )

        stdout = (result.stdout or "").strip()
        stderr = (result.stderr or "").strip()
        if result.returncode != 0:
            raise RuntimeError(
                f"steel CLI failed (exit {result.returncode}): {stderr or stdout or '<no output>'}"
            )

        try:
            payload = json.loads(stdout)
        except json.JSONDecodeError:
            raise RuntimeError(
                f"steel CLI returned non-JSON output: {stdout[:200]}"
            )
        if not isinstance(payload, dict):
            raise RuntimeError(f"steel CLI returned unexpected JSON shape: {payload!r}")
        return payload

    # ------------------------------------------------------------------
    # Session lifecycle
    # ------------------------------------------------------------------

    def create_session(self, task_id: str) -> Dict[str, object]:
        session_name = f"hermes_{task_id}_{uuid.uuid4().hex[:8]}"

        cli_args = ["browser", "start", "--session", session_name]
        if os.environ.get("STEEL_USE_PROXY", "").lower() in ("1", "true", "yes"):
            cli_args += ["--proxy", "true"]
        if os.environ.get("STEEL_SOLVE_CAPTCHA", "").lower() in ("1", "true", "yes"):
            cli_args.append("--session-solve-captcha")

        timeout_ms = os.environ.get("STEEL_SESSION_TIMEOUT")
        if timeout_ms:
            try:
                cli_args += ["--session-timeout", str(int(timeout_ms))]
            except ValueError:
                logger.warning("Invalid STEEL_SESSION_TIMEOUT value: %s", timeout_ms)

        payload = self._run_cli(cli_args, timeout=30)
        if not payload.get("success"):
            raise RuntimeError(f"steel browser start failed: {payload}")

        data = payload.get("data") or {}
        if not isinstance(data, dict):
            raise RuntimeError(f"steel browser start: unexpected data shape: {data!r}")

        cloud_session_id = str(data.get("id") or "")
        live_url = str(data.get("liveUrl") or "")

        features: Dict[str, object] = {"steel": True}
        if os.environ.get("STEEL_USE_PROXY", "").lower() in ("1", "true", "yes"):
            features["proxy"] = True
        if os.environ.get("STEEL_SOLVE_CAPTCHA", "").lower() in ("1", "true", "yes"):
            features["captcha_solving"] = True

        if live_url:
            logger.info("Steel session %s — live viewer: %s", session_name, live_url)
        else:
            logger.info("Created Steel session %s", session_name)

        # ``bb_session_id`` is the legacy key the rest of browser_tool.py uses
        # for cleanup. With steel CLI mode we key sessions by name (which the
        # CLI maps to the cloud session internally), so bb_session_id holds
        # the session name — close_session will pass it back through.
        result: Dict[str, object] = {
            "session_name": session_name,
            "bb_session_id": session_name,
            "uses_steel_cli": True,
            "features": features,
        }
        if cloud_session_id:
            result["steel_cloud_session_id"] = cloud_session_id
        if live_url:
            result["session_viewer_url"] = live_url
        return result

    def close_session(self, session_id: str) -> bool:
        # ``session_id`` here is the session name (see create_session for why).
        try:
            payload = self._run_cli(
                ["browser", "stop", "--session", session_id], timeout=10
            )
        except Exception as e:
            logger.warning("Failed to close Steel session %s: %s", session_id, e)
            return False
        return bool(payload.get("success"))

    def emergency_cleanup(self, session_id: str) -> None:
        try:
            self.close_session(session_id)
        except Exception as e:
            logger.debug(
                "Emergency cleanup failed for Steel session %s: %s", session_id, e
            )
