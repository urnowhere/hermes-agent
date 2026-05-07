"""Durable roster of child bots managed by the Telegram Fleet manager.

Schema is YAML-on-disk at ``~/.hermes/telegram_fleet.yaml``.  Tokens are
treated as secrets — file is created with mode 0600, never logged in full,
and atomically written via :func:`utils.atomic_yaml_write`.

A ``ChildBot`` is the smallest unit of state we keep per bot:

* ``username`` and ``bot_id`` (Telegram identity)
* ``token`` (Bot API token from ``getManagedBotToken``) — secret
* ``persona`` (system-prompt addendum injected into the agent's
  identity for that bot's sessions)
* ``model`` / ``profile`` / ``toolset`` overrides (optional)
* ``status`` — one of ``pending`` / ``active`` / ``decommissioned``
* ``rate_limit_per_min`` and ``daily_budget_usd`` guardrails
* ``created_at`` / ``last_rotated_at`` (UTC ISO-8601)
* ``nonce`` (set when ``status=pending``, used to correlate the
  spawn deep-link with the eventual ``managed_bot`` update)
* ``parent_chat_filter`` — optional list of chat IDs the child is
  scoped to (defaults to all chats)

Pending entries are spawn requests where the user hasn't yet confirmed
the deep link.  They expire after :data:`PENDING_TTL_SECONDS` and are
pruned on every load.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from hermes_constants import get_hermes_home
from utils import atomic_yaml_write

logger = logging.getLogger(__name__)

ROSTER_FILENAME = "telegram_fleet.yaml"
SCHEMA_VERSION = 1
PENDING_TTL_SECONDS = 30 * 60  # 30 minutes — generous buffer for slow user taps

VALID_STATUSES = frozenset({"pending", "active", "decommissioned"})


class RosterError(Exception):
    """Raised when the roster file is malformed or a write fails."""


@dataclass
class ChildBot:
    """A single child bot owned by the fleet manager."""

    username: str
    persona: str = ""
    bot_id: Optional[int] = None
    token: Optional[str] = None
    model: Optional[str] = None
    profile: Optional[str] = None
    toolset: Optional[List[str]] = None
    status: str = "pending"
    rate_limit_per_min: int = 30
    daily_budget_usd: Optional[float] = None
    nonce: Optional[str] = None
    parent_chat_filter: Optional[List[str]] = None
    created_at: str = ""
    last_rotated_at: Optional[str] = None
    notes: str = ""

    def __post_init__(self) -> None:
        if self.status not in VALID_STATUSES:
            raise RosterError(
                f"invalid status {self.status!r} for {self.username!r} "
                f"(expected one of {sorted(VALID_STATUSES)})"
            )
        if not self.username:
            raise RosterError("ChildBot.username must be non-empty")
        if not self.created_at:
            self.created_at = _now_iso()

    def is_active(self) -> bool:
        return self.status == "active" and bool(self.token)

    def to_dict(self, *, include_token: bool = True) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "username": self.username,
            "persona": self.persona,
            "status": self.status,
            "rate_limit_per_min": self.rate_limit_per_min,
            "created_at": self.created_at,
        }
        if self.bot_id is not None:
            out["bot_id"] = self.bot_id
        if include_token and self.token:
            out["token"] = self.token
        if self.model:
            out["model"] = self.model
        if self.profile:
            out["profile"] = self.profile
        if self.toolset:
            out["toolset"] = list(self.toolset)
        if self.daily_budget_usd is not None:
            out["daily_budget_usd"] = self.daily_budget_usd
        if self.nonce:
            out["nonce"] = self.nonce
        if self.parent_chat_filter:
            out["parent_chat_filter"] = list(self.parent_chat_filter)
        if self.last_rotated_at:
            out["last_rotated_at"] = self.last_rotated_at
        if self.notes:
            out["notes"] = self.notes
        return out

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ChildBot":
        if not isinstance(data, dict):
            raise RosterError(f"child entry must be a mapping, got {type(data).__name__}")
        try:
            return cls(
                username=str(data["username"]),
                persona=str(data.get("persona", "")),
                bot_id=_coerce_optional_int(data.get("bot_id")),
                token=_coerce_optional_str(data.get("token")),
                model=_coerce_optional_str(data.get("model")),
                profile=_coerce_optional_str(data.get("profile")),
                toolset=_coerce_optional_str_list(data.get("toolset")),
                status=str(data.get("status", "pending")),
                rate_limit_per_min=int(data.get("rate_limit_per_min", 30)),
                daily_budget_usd=_coerce_optional_float(data.get("daily_budget_usd")),
                nonce=_coerce_optional_str(data.get("nonce")),
                parent_chat_filter=_coerce_optional_str_list(data.get("parent_chat_filter")),
                created_at=str(data.get("created_at", "")),
                last_rotated_at=_coerce_optional_str(data.get("last_rotated_at")),
                notes=str(data.get("notes", "")),
            )
        except KeyError as e:
            raise RosterError(f"missing required field: {e}") from e
        except (TypeError, ValueError) as e:
            raise RosterError(f"invalid child entry: {e}") from e


@dataclass
class FleetRoster:
    """Top-level roster wrapping schema + manager metadata + child list."""

    schema_version: int = SCHEMA_VERSION
    manager_bot_username: str = ""
    children: List[ChildBot] = field(default_factory=list)
    max_size: int = 16
    spawn_enabled: bool = True
    updated_at: str = ""

    def __post_init__(self) -> None:
        if self.schema_version != SCHEMA_VERSION:
            raise RosterError(
                f"unsupported schema_version {self.schema_version} "
                f"(this build supports v{SCHEMA_VERSION})"
            )
        if not self.updated_at:
            self.updated_at = _now_iso()

    # ── Lookups ────────────────────────────────────────────────────────

    def find(self, username: str) -> Optional[ChildBot]:
        username = (username or "").lstrip("@").lower()
        for child in self.children:
            if child.username.lower() == username:
                return child
        return None

    def find_by_nonce(self, nonce: str) -> Optional[ChildBot]:
        if not nonce:
            return None
        for child in self.children:
            if child.nonce == nonce:
                return child
        return None

    def active_children(self) -> List[ChildBot]:
        return [c for c in self.children if c.is_active()]

    def pending_children(self) -> List[ChildBot]:
        return [c for c in self.children if c.status == "pending"]

    # ── Mutations ──────────────────────────────────────────────────────

    def upsert(self, child: ChildBot) -> None:
        existing = self.find(child.username)
        if existing is not None:
            self.children.remove(existing)
        self.children.append(child)
        self.updated_at = _now_iso()

    def remove(self, username: str) -> bool:
        existing = self.find(username)
        if existing is None:
            return False
        self.children.remove(existing)
        self.updated_at = _now_iso()
        return True

    # ── (de)serialisation ─────────────────────────────────────────────

    def to_dict(self, *, include_tokens: bool = True) -> Dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "manager_bot_username": self.manager_bot_username,
            "max_size": self.max_size,
            "spawn_enabled": self.spawn_enabled,
            "updated_at": self.updated_at,
            "children": [
                c.to_dict(include_token=include_tokens) for c in self.children
            ],
        }

    @classmethod
    def from_dict(cls, data: Optional[Dict[str, Any]]) -> "FleetRoster":
        if not data:
            return cls()
        if not isinstance(data, dict):
            raise RosterError(f"roster must be a mapping, got {type(data).__name__}")
        children_raw = data.get("children") or []
        if not isinstance(children_raw, list):
            raise RosterError("'children' must be a list")
        return cls(
            schema_version=int(data.get("schema_version", SCHEMA_VERSION)),
            manager_bot_username=str(data.get("manager_bot_username", "")),
            max_size=int(data.get("max_size", 16)),
            spawn_enabled=bool(data.get("spawn_enabled", True)),
            updated_at=str(data.get("updated_at", "")),
            children=[ChildBot.from_dict(c) for c in children_raw],
        )

    def prune_expired_pending(
        self, *, ttl_seconds: int = PENDING_TTL_SECONDS, now: Optional[float] = None
    ) -> int:
        """Drop ``pending`` entries older than *ttl_seconds*.  Returns count removed."""
        now = time.time() if now is None else now
        cutoff_iso = datetime.fromtimestamp(now - ttl_seconds, tz=timezone.utc).isoformat()
        before = len(self.children)
        self.children = [
            c for c in self.children
            if c.status != "pending" or (c.created_at or "") >= cutoff_iso
        ]
        removed = before - len(self.children)
        if removed:
            self.updated_at = _now_iso()
        return removed


# ── Module-level IO ─────────────────────────────────────────────────────


def get_roster_path() -> Path:
    return get_hermes_home() / ROSTER_FILENAME


def load_roster(*, path: Optional[Path] = None) -> FleetRoster:
    """Read the roster from disk.  Returns an empty roster when missing."""
    p = path or get_roster_path()
    if not p.exists():
        return FleetRoster()
    try:
        raw = yaml.safe_load(p.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise RosterError(f"could not parse {p}: {e}") from e
    return FleetRoster.from_dict(raw)


def save_roster(roster: FleetRoster, *, path: Optional[Path] = None) -> None:
    """Persist the roster atomically with mode 0600 (tokens are secret)."""
    p = path or get_roster_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    roster.updated_at = _now_iso()
    try:
        atomic_yaml_write(p, roster.to_dict(include_tokens=True))
    except Exception as e:
        raise RosterError(f"could not write {p}: {e}") from e
    try:
        os.chmod(p, 0o600)
    except OSError as e:  # pragma: no cover - non-POSIX or permission edge
        logger.debug("could not chmod 0600 on %s: %s", p, e)


# ── helpers ─────────────────────────────────────────────────────────────


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _coerce_optional_str(v: Any) -> Optional[str]:
    if v is None or v == "":
        return None
    return str(v)


def _coerce_optional_int(v: Any) -> Optional[int]:
    if v is None or v == "":
        return None
    return int(v)


def _coerce_optional_float(v: Any) -> Optional[float]:
    if v is None or v == "":
        return None
    return float(v)


def _coerce_optional_str_list(v: Any) -> Optional[List[str]]:
    if v is None:
        return None
    if isinstance(v, str):
        return [v]
    if isinstance(v, list):
        out = [str(x).strip() for x in v if str(x).strip()]
        return out or None
    raise RosterError(f"expected list of strings, got {type(v).__name__}")
