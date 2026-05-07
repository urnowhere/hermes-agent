"""Auto-update system for Hermes Gateway.

Handles checking for updates, applying them automatically, and notifying
the user after a restart. Fork-aware so it won't auto-update forks.
"""

import asyncio
import json
import logging
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from hermes_constants import get_hermes_home

logger = logging.getLogger(__name__)

# Constants
_UPDATE_MANIFEST_FILE = ".update_manifest.json"
_HOME_CHANNEL_FILE = ".home_channel.json"
_HOURS_SECONDS = {
    "1h": 3600,
    "6h": 21600,
    "12h": 43200,
    "24h": 86400,
    "48h": 172800,
    "72h": 259200,
}

# Module-level reference to gateway runner for notifications
# Set by gateway/run.py after instantiating AutoUpdater
_gateway_runner: Optional[Any] = None


def _get_repo_dir() -> Optional[Path]:
    """Return the active Hermes git checkout, or None if not a git install."""
    hermes_home = get_hermes_home()
    repo_dir = hermes_home / "hermes-agent"
    if not (repo_dir / ".git").exists():
        repo_dir = Path(__file__).parent.parent.resolve()
    return repo_dir if (repo_dir / ".git").exists() else None


def parse_check_interval(interval: str) -> Optional[int]:
    """Parse a check interval string like '1h', '24h', '72h' into seconds.

    Returns None for invalid values.
    """
    return _HOURS_SECONDS.get(interval)


class AutoUpdater:
    """Handles automatic update checks and application.

    Configuration options:
        enabled: Whether auto-update is enabled
        mode: "notify" (just tell user) or "apply" (automatically apply updates)
        check_interval: How often to check for updates (e.g., "24h")
        grace_period_seconds: Minimum time between auto-updates (prevents rapid restarts)
    """

    def __init__(self, config: Dict[str, Any]):
        auto_cfg = config.get("auto_update", {})
        self.enabled = bool(auto_cfg.get("enabled", False))
        self.mode = str(auto_cfg.get("mode", "notify"))
        interval_str = str(auto_cfg.get("check_interval", "24h"))
        self.check_interval_seconds = parse_check_interval(interval_str) or 86400
        self.grace_period_seconds = int(auto_cfg.get("grace_period_seconds", 300))
        self._last_apply_ts: Optional[float] = None

    def check_for_updates(self) -> Dict[str, Any]:
        """Check if updates are available.

        Returns dict with:
            available: bool indicating if updates exist
            version: str of new version (git describe --tags --always)
            commits: int number of commits behind
        """
        result = {
            "available": False,
            "version": None,
            "commits": 0,
        }

        repo_dir = _get_repo_dir()
        if not repo_dir:
            return result

        # Fork check first
        if self._is_fork():
            logger.debug("Skipping update check: fork detected")
            return result

        # Do a fresh fetch (bypassing cache for accuracy)
        try:
            subprocess.run(
                ["git", "fetch", "origin", "--quiet"],
                capture_output=True, timeout=15,
                cwd=str(repo_dir),
            )
        except Exception as e:
            logger.debug("git fetch failed: %s", e)
            return result

        # Get current version
        current = self._get_current_version()
        if current:
            result["version"] = current

        # Count commits behind
        try:
            rev_result = subprocess.run(
                ["git", "rev-list", "--count", "HEAD..origin/main"],
                capture_output=True, text=True, timeout=5,
                cwd=str(repo_dir),
            )
            if rev_result.returncode == 0:
                commits = int(rev_result.stdout.strip())
                result["commits"] = commits
                result["available"] = commits > 0
        except Exception as e:
            logger.debug("git rev-list failed: %s", e)

        return result

    def _get_current_version(self) -> Optional[str]:
        """Get the current git version as a short tag+hash string."""
        repo_dir = _get_repo_dir()
        if not repo_dir:
            return None
        try:
            result = subprocess.run(
                ["git", "describe", "--tags", "--always"],
                capture_output=True, text=True, timeout=5,
                cwd=str(repo_dir),
            )
            if result.returncode == 0:
                return result.stdout.strip()
        except Exception:
            pass
        return None

    def _is_fork(self) -> bool:
        """Check if this is a fork (not the official NousResearch repo)."""
        # Import from hermes_cli.main if available
        try:
            from hermes_cli.main import _is_fork as check_fork
        except ImportError:
            # Fallback inline implementation
            def check_fork(origin_url: Optional[str]) -> bool:
                if not origin_url:
                    return False
                normalized = origin_url.rstrip("/")
                if normalized.endswith(".git"):
                    normalized = normalized[:-4]
                official_urls = [
                    "https://github.com/NousResearch/hermes-agent",
                    "git@github.com:NousResearch/hermes-agent",
                ]
                for official in official_urls:
                    off_norm = official.rstrip("/")
                    if off_norm.endswith(".git"):
                        off_norm = off_norm[:-4]
                    if normalized == off_norm:
                        return False
                return True

        repo_dir = _get_repo_dir()
        if not repo_dir:
            return False

        try:
            result = subprocess.run(
                ["git", "remote", "get-url", "origin"],
                capture_output=True, text=True, timeout=5,
                cwd=str(repo_dir),
            )
            if result.returncode == 0:
                origin_url = result.stdout.strip()
                return check_fork(origin_url)
        except Exception:
            pass
        return False

    def should_apply(self) -> bool:
        """Determine if an update should be automatically applied.

        Returns False if:
        - Auto-update is disabled
        - Mode is "notify" (only notify, don't apply)
        - Running on a fork
        - No updates available
        - Grace period hasn't passed since last apply
        """
        if not self.enabled:
            return False
        if self.mode != "apply":
            return False
        if self._is_fork():
            return False

        # Check for updates
        update_info = self.check_for_updates()
        if not update_info["available"]:
            return False

        # Check grace period
        if self._last_apply_ts is not None:
            elapsed = datetime.now(timezone.utc).timestamp() - self._last_apply_ts
            if elapsed < self.grace_period_seconds:
                logger.debug(
                    "Grace period active: %.0fs elapsed, need %ds",
                    elapsed, self.grace_period_seconds,
                )
                return False

        return True

    def apply_update(self) -> bool:
        """Apply the update via git pull and schedule restart notification.

        Returns True if the update was initiated, False otherwise.
        Writes .update_manifest.json for post-restart notification.
        """
        repo_dir = _get_repo_dir()
        if not repo_dir:
            logger.error("Cannot apply update: no git repo found")
            return False

        # Ensure we have updates to apply
        update_info = self.check_for_updates()
        if not update_info["available"]:
            logger.info("No updates available to apply")
            return False

        try:
            # Fetch latest refs
            subprocess.run(
                ["git", "fetch", "origin", "--quiet"],
                capture_output=True, timeout=15,
                cwd=str(repo_dir),
            )

            # Pull changes
            result = subprocess.run(
                ["git", "pull", "--ff-only", "origin", "main"],
                capture_output=True, timeout=60,
                cwd=str(repo_dir),
            )
            if result.returncode != 0:
                logger.error("git pull failed: %s", result.stderr.decode())
                return False

        except subprocess.TimeoutExpired:
            logger.error("git pull timed out")
            return False
        except Exception as e:
            logger.error("git pull failed: %s", e)
            return False

        # Write manifest for post-update notification
        manifest_path = get_hermes_home() / _UPDATE_MANIFEST_FILE
        manifest = {
            "version": update_info["version"],
            "commits": update_info["commits"],
            "applied_at": datetime.now(timezone.utc).isoformat(),
            "home_channel": self._load_home_channel(),
        }
        try:
            manifest_path.write_text(json.dumps(manifest, indent=2))
        except Exception as e:
            logger.warning("Failed to write update manifest: %s", e)

        # Record apply timestamp
        self._last_apply_ts = datetime.now(timezone.utc).timestamp()

        logger.info("Update applied successfully, restarting...")
        # Restart the gateway
        os.execv(sys.executable, [sys.executable, "-m", "gateway.run"])

        return True

    def _load_home_channel(self) -> Optional[Dict[str, str]]:
        """Load the saved home channel for notifications."""
        path = get_hermes_home() / _HOME_CHANNEL_FILE
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text())
        except Exception:
            return None

    def _save_home_channel(self, channel: Dict[str, str]) -> None:
        """Save the home channel for post-update notifications."""
        path = get_hermes_home() / _HOME_CHANNEL_FILE
        try:
            path.write_text(json.dumps(channel, indent=2))
        except Exception as e:
            logger.warning("Failed to save home channel: %s", e)

    def update_home_channel(self, platform: str, chat_id: str) -> None:
        """Update the home channel (called when user sends first message)."""
        channel = {"platform": platform, "chat_id": chat_id}
        self._save_home_channel(channel)

    async def check_post_update_notification(self) -> bool:
        """Check for a post-update manifest and notify the user.

        Returns True if a notification was sent, False otherwise.
        Deletes the manifest after sending.
        """
        global _gateway_runner

        manifest_path = get_hermes_home() / _UPDATE_MANIFEST_FILE
        if not manifest_path.exists():
            return False

        try:
            manifest = json.loads(manifest_path.read_text())
        except Exception:
            manifest_path.unlink(missing_ok=True)
            return False

        # Get the home channel
        home_channel = manifest.get("home_channel")
        if not home_channel:
            manifest_path.unlink(missing_ok=True)
            return False

        platform_str = home_channel.get("platform")
        chat_id = home_channel.get("chat_id")
        if not platform_str or not chat_id:
            manifest_path.unlink(missing_ok=True)
            return False

        version = manifest.get("version", "unknown")
        commits = manifest.get("commits", 0)

        message = (
            f"✅ Auto-update complete! Now running v{version} "
            f"({commits} commit{'s' if commits != 1 else ''} behind)."
        )

        await self._notify_user(message, home_channel)

        # Clean up manifest
        manifest_path.unlink(missing_ok=True)
        return True

    async def _notify_user(self, message: str, channel: Dict[str, str]) -> None:
        """Send a notification message to the user."""
        global _gateway_runner

        if _gateway_runner is None:
            logger.debug("No gateway runner available for notification")
            return

        platform_str = channel.get("platform")
        chat_id = channel.get("chat_id")
        if not platform_str or not chat_id:
            return

        try:
            from gateway.config import Platform
            platform = Platform(platform_str)
            adapter = _gateway_runner.adapters.get(platform)
            if adapter:
                await adapter.send_message(chat_id, message)
        except Exception as e:
            logger.warning("Failed to send notification: %s", e)
