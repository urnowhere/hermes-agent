"""
Timezone-aware clock for Hermes.

Provides a single ``now()`` helper that returns a timezone-aware datetime
based on the user's configured IANA timezone (e.g. ``Asia/Kolkata``).

Resolution order:
  1. ``HERMES_TIMEZONE`` environment variable
  2. ``timezone`` key in ``~/.hermes/config.yaml``
  3. Falls back to the server's local time (``datetime.now().astimezone()``)

Invalid timezone values log a warning and fall back safely — Hermes never
crashes due to a bad timezone string.
"""

import logging
import os
from datetime import datetime
from hermes_constants import get_config_path
from typing import Any, Optional

logger = logging.getLogger(__name__)

try:
    from zoneinfo import ZoneInfo
except ImportError:
    # Python 3.8 fallback (shouldn't be needed — Hermes requires 3.9+)
    from backports.zoneinfo import ZoneInfo  # type: ignore[no-redef]

# Cached state — resolved once, reused on every call.
# Call reset_cache() to force re-resolution (e.g. after config changes).
_cached_tz: Optional[ZoneInfo] = None
_cached_tz_name: Optional[str] = None
_cache_resolved: bool = False


def _resolve_timezone_name() -> str:
    """Read the configured IANA timezone string (or empty string).

    This does file I/O when falling through to config.yaml, so callers
    should cache the result rather than calling on every ``now()``.
    """
    # 1. Environment variable (highest priority — set by Supervisor, etc.)
    tz_env = os.getenv("HERMES_TIMEZONE", "").strip()
    if tz_env:
        return tz_env

    # 2. config.yaml ``timezone`` key
    try:
        import yaml
        config_path = get_config_path()
        if config_path.exists():
            with open(config_path) as f:
                cfg = yaml.safe_load(f) or {}
            tz_cfg = cfg.get("timezone", "")
            if isinstance(tz_cfg, str) and tz_cfg.strip():
                return tz_cfg.strip()
    except Exception:
        pass

    return ""


def _get_zoneinfo(name: str) -> Optional[ZoneInfo]:
    """Validate and return a ZoneInfo, or None if invalid."""
    if not name:
        return None
    try:
        return ZoneInfo(name)
    except (KeyError, Exception) as exc:
        logger.warning(
            "Invalid timezone '%s': %s. Falling back to server local time.",
            name, exc,
        )
        return None


def get_timezone() -> Optional[ZoneInfo]:
    """Return the user's configured ZoneInfo, or None (meaning server-local).

    Resolved once and cached. Call ``reset_cache()`` after config changes.
    """
    global _cached_tz, _cached_tz_name, _cache_resolved
    if not _cache_resolved:
        _cached_tz_name = _resolve_timezone_name()
        _cached_tz = _get_zoneinfo(_cached_tz_name)
        _cache_resolved = True
    return _cached_tz


def now() -> datetime:
    """
    Return the current time as a timezone-aware datetime.

    If a valid timezone is configured, returns wall-clock time in that zone.
    Otherwise returns the server's local time (via ``astimezone()``).
    """
    tz = get_timezone()
    if tz is not None:
        return datetime.now(tz)
    # No timezone configured — use server-local (still tz-aware)
    return datetime.now().astimezone()


_CURRENT_TIME_NOTE_PREFIX = "[System note: Current time is "


def format_current_time_note(current: Optional[datetime] = None) -> str:
    """Return the standard ephemeral current-time note for LLM turns.

    The note is intentionally shaped as a user-message system note instead of
    a dynamic system-prompt fragment. That keeps Hermes' cached system prompt
    stable while still giving the model fresh wall-clock awareness each turn.
    """
    current = current or now()
    formatted = current.strftime("%A, %B %d, %Y %I:%M %p")
    tz_label = current.strftime("%Z").strip()
    if tz_label:
        formatted = f"{formatted} {tz_label}"
    return f"{_CURRENT_TIME_NOTE_PREFIX}{formatted}.]"


def _content_has_current_time_note(content: Any) -> bool:
    """Return True when a user-message content value already starts with the note."""
    if isinstance(content, str):
        return content.lstrip().startswith(_CURRENT_TIME_NOTE_PREFIX)
    if isinstance(content, list):
        for part in content:
            if not isinstance(part, dict):
                continue
            if part.get("type") == "text" and isinstance(part.get("text"), str):
                return part["text"].lstrip().startswith(_CURRENT_TIME_NOTE_PREFIX)
            # Only inspect the leading text part. If the first meaningful part
            # is an image, no note has been injected yet.
            if part.get("type"):
                return False
    return False


def prepend_current_time_context(content: Any) -> Any:
    """Prepend the current-time note to API-facing user-message content.

    Supports plain string turns and OpenAI-style multimodal content lists. The
    input list is never mutated, so callers can persist the clean user message
    separately from the API-facing variant.
    """
    if _content_has_current_time_note(content):
        return content

    note = format_current_time_note()
    if isinstance(content, str):
        return f"{note}\n\n{content}"

    if isinstance(content, list):
        return [{"type": "text", "text": note}] + [
            dict(part) if isinstance(part, dict) else part
            for part in content
        ]

    return content


