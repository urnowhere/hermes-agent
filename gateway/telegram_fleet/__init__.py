"""Telegram Fleet — orchestrate a swarm of Telegram bots through a manager bot.

Built on top of Telegram Bot API 9.5/9.6 (March-April 2026) ``Managed Bots``,
which lets one "manager bot" create and own N child bots via the
``getManagedBotToken`` / ``replaceManagedBotToken`` endpoints and the
``managed_bot`` update type.

This package adds:

* :mod:`gateway.telegram_fleet.roster` — durable YAML roster of child bots
  (token, persona, model, toolset overrides) at ``~/.hermes/telegram_fleet.yaml``.
* :mod:`gateway.telegram_fleet.api` — thin ``httpx``-based client for the new
  Bot API endpoints (PTB 22.7 doesn't expose them yet).
* :mod:`gateway.telegram_fleet.audit` — append-only audit log of fleet events.
* :mod:`gateway.telegram_fleet.guardrails` — fleet-size cap, per-child rate
  limit, spawn-approval gate.
* :mod:`gateway.telegram_fleet.coordinator` — the orchestrator the gateway
  and tools talk to.

Tools live in :mod:`tools.telegram_fleet_tool` and let the agent itself spawn,
delegate-to, rotate, and decommission fleet members.
"""

from gateway.telegram_fleet.roster import (
    ChildBot,
    FleetRoster,
    RosterError,
    load_roster,
    save_roster,
)
from gateway.telegram_fleet.audit import audit_event
from gateway.telegram_fleet.guardrails import (
    FleetGuardrailError,
    SpawnApprovalRequired,
    check_can_spawn,
    check_rate_limit,
)
from gateway.telegram_fleet.coordinator import (
    FleetApprovalRequired,
    FleetCoordinator,
    get_coordinator,
)

__all__ = [
    "ChildBot",
    "FleetRoster",
    "RosterError",
    "load_roster",
    "save_roster",
    "audit_event",
    "FleetGuardrailError",
    "SpawnApprovalRequired",
    "check_can_spawn",
    "check_rate_limit",
    "FleetApprovalRequired",
    "FleetCoordinator",
    "get_coordinator",
]
