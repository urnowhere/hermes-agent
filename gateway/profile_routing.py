"""
Profile-based routing for the gateway.

Allows a single gateway instance to route specific channels/threads to
different Hermes profiles, each with their own model, tools, memory, and
persona configuration.

Configuration example (config.yaml)::

    gateway:
      profile_routes:
        - name: trader
          platform: discord
          chat_id: "1467470389465583668"
          profile: trader
          enabled: true

Matching priority (most specific first):
  1. platform + chat_id + thread_id  (exact thread route)
  2. platform + chat_id             (channel route)
  3. platform                       (platform-wide default — rare)
  4. No match → use default "main" profile
"""

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .session import SessionSource
from .config import Platform

logger = logging.getLogger(__name__)


@dataclass
class ProfileRoute:
    """A single routing rule that maps a message source to a Hermes profile."""

    name: str  # Human-readable route name (e.g. "trader")
    platform: Platform  # Which platform this route applies to
    profile: str  # Profile name (e.g. "trader") — must exist in ~/.hermes/profiles/
    enabled: bool = True

    # Matching criteria (all optional — more specific = higher priority)
    chat_id: Optional[str] = None  # Match specific channel/group
    thread_id: Optional[str] = None  # Match specific thread within chat_id
    user_id: Optional[str] = None  # Match specific user (rarely used)

    # Profile-specific overrides (optional — fall back to profile's config.yaml)
    model: Optional[str] = None
    provider: Optional[str] = None

    @classmethod
    def from_dict(cls, data: Dict) -> "ProfileRoute":
        """Parse a route from a config dict entry."""
        platform_str = data.get("platform", "")
        try:
            platform = Platform(platform_str)
        except ValueError:
            logger.warning("ProfileRoute '%s': unknown platform '%s', skipping", data.get("name", "?"), platform_str)
            raise

        return cls(
            name=data.get("name", "unnamed"),
            platform=platform,
            profile=data["profile"],  # required
            enabled=data.get("enabled", True),
            chat_id=data.get("chat_id"),
            thread_id=data.get("thread_id"),
            user_id=data.get("user_id"),
            model=data.get("model"),
            provider=data.get("provider"),
        )

    @property
    def specificity(self) -> int:
        """Return a specificity score for priority sorting (higher = more specific)."""
        score = 0
        if self.thread_id:
            score += 4
        if self.chat_id:
            score += 2
        if self.user_id:
            score += 1
        return score


def parse_profile_routes(raw: List[Dict]) -> List[ProfileRoute]:
    """Parse a list of raw route dicts into ProfileRoute objects.

    Silently skips invalid entries and logs warnings.
    """
    routes: List[ProfileRoute] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        if not entry.get("profile"):
            logger.warning("ProfileRoute missing 'profile' field, skipping: %s", entry.get("name", "?"))
            continue
        try:
            route = ProfileRoute.from_dict(entry)
            if route.enabled:
                routes.append(route)
        except (ValueError, KeyError):
            continue

    # Sort by specificity descending — most specific routes match first
    routes.sort(key=lambda r: r.specificity, reverse=True)
    return routes


def match_profile_route(
    source: SessionSource,
    routes: List[ProfileRoute],
) -> Optional[ProfileRoute]:
    """Find the best matching route for a message source.

    Returns the first matching route (most specific first) or None for default.
    """
    for route in routes:
        if not route.enabled:
            continue
        # Platform must always match
        if route.platform != source.platform:
            continue
        # If thread_id specified, both chat_id and thread_id must match
        if route.thread_id:
            if source.thread_id != route.thread_id:
                continue
            if route.chat_id and source.chat_id != route.chat_id:
                continue
            return route
        # If chat_id specified (no thread_id), chat_id must match
        if route.chat_id:
            if source.chat_id != route.chat_id:
                continue
            return route
        # If only user_id specified
        if route.user_id:
            if source.user_id != route.user_id:
                continue
            return route
        # Platform-only match (catch-all for this platform)
        return route

    return None
