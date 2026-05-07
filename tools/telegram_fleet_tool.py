"""telegram_fleet — agent-facing tools for orchestrating a Telegram bot swarm.

Exposes six tools registered under the ``telegram_fleet`` toolset:

* ``telegram_spawn_bot`` — mint a deep link for the user to confirm a new
  child bot.  Returns the link + a short instruction the agent can relay.
* ``telegram_fleet_list`` — current roster status (active / pending /
  decommissioned), token-redacted.
* ``telegram_delegate`` — make a named child bot post a message into a chat.
* ``telegram_rotate_bot_token`` — rotate via ``replaceManagedBotToken``.
* ``telegram_decommission_bot`` — drop a bot from active service.
* ``telegram_orchestrate_swarm`` — the headline primitive: take an objective
  + a list of subtasks, fan them out across the active fleet in parallel,
  optionally stream status to a report chat, return aggregated results.

All tools are read-only against config — they only touch
``~/.hermes/telegram_fleet.yaml`` (atomic writes) and the audit log.
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional

from gateway.telegram_fleet import (
    FleetApprovalRequired,
    FleetGuardrailError,
    RosterError,
    SpawnApprovalRequired,
    get_coordinator,
)
from gateway.telegram_fleet.api import BotApiError
from tools.registry import registry, tool_error, tool_result


# ── Tool: telegram_spawn_bot ───────────────────────────────────────────


def telegram_spawn_bot(
    suggested_username: str,
    persona: str = "",
    display_name: Optional[str] = None,
    model: Optional[str] = None,
    profile: Optional[str] = None,
    toolset: Optional[List[str]] = None,
    rate_limit_per_min: int = 30,
    daily_budget_usd: Optional[float] = None,
    notes: str = "",
    **_: Any,
) -> str:
    """Mint a deep link the user taps to add a new child bot to the fleet."""
    try:
        coord = get_coordinator()
        result = coord.spawn_bot(
            suggested_username=suggested_username,
            persona=persona,
            display_name=display_name,
            model=model,
            profile=profile,
            toolset=toolset,
            rate_limit_per_min=int(rate_limit_per_min or 30),
            daily_budget_usd=daily_budget_usd,
            notes=notes,
        )
    except SpawnApprovalRequired as e:
        return tool_error(str(e), code="spawn_approval_disabled")
    except FleetGuardrailError as e:
        return tool_error(str(e), code="guardrail")
    except (RosterError, BotApiError) as e:
        return tool_error(str(e), code=type(e).__name__)
    return tool_result(
        success=True,
        suggested_username=result.suggested_username,
        deep_link=result.deep_link,
        nonce_preview=result.nonce[:6] + "…",
        instruction=(
            "Share the deep link with the user.  When they tap it and "
            "confirm in Telegram, the new child bot is automatically "
            "registered and joins the fleet within seconds."
        ),
    )


TELEGRAM_SPAWN_BOT_SCHEMA = {
    "type": "function",
    "function": {
        "name": "telegram_spawn_bot",
        "description": (
            "Add a new child bot to the Telegram fleet.  Returns a deep "
            "link the user must tap once in Telegram to confirm creation "
            "(this is required by Telegram's Managed Bots API; no link, no "
            "bot).  Once tapped, the bot is auto-registered with the persona "
            "and overrides you specify and becomes available to "
            "telegram_orchestrate_swarm and telegram_delegate."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "suggested_username": {
                    "type": "string",
                    "description": "Suggested username for the new bot (without @).  Must end in 'bot' per Telegram rules.  Make it unique and descriptive (e.g. 'hermes_research_legal_bot').",
                },
                "persona": {
                    "type": "string",
                    "description": "Free-text persona/role description injected into the bot's system prompt for tasks routed through it.",
                    "default": "",
                },
                "display_name": {
                    "type": "string",
                    "description": "Pretty display name shown in Telegram (defaults to persona/username).",
                },
                "model": {
                    "type": "string",
                    "description": "Optional model override for tasks routed through this bot.",
                },
                "profile": {
                    "type": "string",
                    "description": "Optional Hermes profile name for isolated config/memory.",
                },
                "toolset": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional toolset whitelist for this bot (e.g. ['web', 'file']).",
                },
                "rate_limit_per_min": {
                    "type": "integer",
                    "description": "Per-child outbound message cap.  Default 30/min.",
                    "default": 30,
                },
                "daily_budget_usd": {
                    "type": "number",
                    "description": "Optional daily LLM spend cap for tasks routed through this bot.",
                },
                "notes": {
                    "type": "string",
                    "description": "Free-text notes recorded in the roster.",
                    "default": "",
                },
            },
            "required": ["suggested_username"],
        },
    },
}


# ── Tool: telegram_fleet_list ──────────────────────────────────────────


def telegram_fleet_list(status: Optional[str] = None, **_: Any) -> str:
    """Return current roster (tokens redacted)."""
    try:
        coord = get_coordinator()
        children = coord.list_children(status=status)
    except RosterError as e:
        return tool_error(str(e), code="roster")
    rows = []
    for c in children:
        entry = c.to_dict(include_token=False)
        if c.token:
            entry["has_token"] = True
        rows.append(entry)
    return tool_result(
        success=True,
        manager_bot_username=coord.roster.manager_bot_username,
        max_size=coord.roster.max_size,
        children=rows,
    )


TELEGRAM_FLEET_LIST_SCHEMA = {
    "type": "function",
    "function": {
        "name": "telegram_fleet_list",
        "description": "List all bots in the Telegram fleet (tokens redacted).  Useful before orchestrating to see who's available.",
        "parameters": {
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "enum": ["active", "pending", "decommissioned"],
                    "description": "Filter by status.  Omit to list all.",
                },
            },
        },
    },
}


# ── Tool: telegram_delegate ────────────────────────────────────────────


def telegram_delegate(
    target_bot: str,
    chat_id: str,
    text: str,
    reply_to: Optional[int] = None,
    **_: Any,
) -> str:
    """Have the named child bot post *text* into *chat_id*."""
    try:
        coord = get_coordinator()
        result = coord.delegate_message(
            target_username=target_bot,
            chat_id=str(chat_id),
            text=text,
            reply_to=reply_to,
        )
    except FleetGuardrailError as e:
        return tool_error(str(e), code="guardrail")
    except BotApiError as e:
        return tool_error(str(e), code="bot_api")
    return tool_result(
        success=True,
        target_bot=target_bot.lstrip("@"),
        message_id=(result or {}).get("message_id"),
        chat_id=str(chat_id),
    )


TELEGRAM_DELEGATE_SCHEMA = {
    "type": "function",
    "function": {
        "name": "telegram_delegate",
        "description": "Make a specific child bot post a message into a Telegram chat.  Use to relay sub-results from the swarm back into a report channel.",
        "parameters": {
            "type": "object",
            "properties": {
                "target_bot": {
                    "type": "string",
                    "description": "Username of the child bot (with or without @).",
                },
                "chat_id": {
                    "type": "string",
                    "description": "Telegram chat ID (numeric, can be negative for groups).",
                },
                "text": {
                    "type": "string",
                    "description": "Message text.",
                },
                "reply_to": {
                    "type": "integer",
                    "description": "Optional message_id to reply to.",
                },
            },
            "required": ["target_bot", "chat_id", "text"],
        },
    },
}


# ── Tool: telegram_rotate_bot_token ────────────────────────────────────


def telegram_rotate_bot_token(target_bot: str, **_: Any) -> str:
    try:
        coord = get_coordinator()
        child = coord.rotate_token(target_bot)
    except FleetGuardrailError as e:
        return tool_error(str(e), code="guardrail")
    except BotApiError as e:
        return tool_error(str(e), code="bot_api")
    return tool_result(
        success=True,
        target_bot=child.username,
        last_rotated_at=child.last_rotated_at,
    )


TELEGRAM_ROTATE_TOKEN_SCHEMA = {
    "type": "function",
    "function": {
        "name": "telegram_rotate_bot_token",
        "description": "Rotate the API token for a fleet member via replaceManagedBotToken.  Use after a suspected token compromise.",
        "parameters": {
            "type": "object",
            "properties": {
                "target_bot": {
                    "type": "string",
                    "description": "Username of the child bot to rotate.",
                },
            },
            "required": ["target_bot"],
        },
    },
}


# ── Tool: telegram_decommission_bot ────────────────────────────────────


def telegram_decommission_bot(target_bot: str, **_: Any) -> str:
    try:
        coord = get_coordinator()
        removed = coord.decommission(target_bot)
    except RosterError as e:
        return tool_error(str(e), code="roster")
    if not removed:
        return tool_error(f"@{target_bot.lstrip('@')} is not in the roster", code="not_found")
    return tool_result(success=True, target_bot=target_bot.lstrip("@"))


TELEGRAM_DECOMMISSION_SCHEMA = {
    "type": "function",
    "function": {
        "name": "telegram_decommission_bot",
        "description": "Mark a fleet member decommissioned.  Token is zeroed; entry stays for audit.",
        "parameters": {
            "type": "object",
            "properties": {
                "target_bot": {
                    "type": "string",
                    "description": "Username of the child bot to retire.",
                },
            },
            "required": ["target_bot"],
        },
    },
}


# ── Tool: telegram_orchestrate_swarm ──────────────────────────────────


def telegram_orchestrate_swarm(
    objective: str,
    subtasks: List[Dict[str, Any]],
    report_chat_id: Optional[str] = None,
    max_parallel: int = 8,
    per_task_timeout_s: float = 600.0,
    user_approved: bool = False,
    parent_agent: Any = None,
    **_: Any,
) -> str:
    try:
        coord = get_coordinator()
        result = coord.orchestrate_swarm(
            objective=objective,
            subtasks=subtasks,
            report_chat_id=str(report_chat_id) if report_chat_id else None,
            max_parallel=int(max_parallel or 8),
            per_task_timeout_s=float(per_task_timeout_s or 600.0),
            user_approved=bool(user_approved),
            parent_agent=parent_agent,
        )
    except FleetApprovalRequired as e:
        # Hermes-style approval-required response.  The agent is expected to
        # surface the plan via `clarify` (multiple-choice or open-ended), get
        # consent, then re-call this tool with ``user_approved=true``.
        return json.dumps(
            {
                "status": "approval_required",
                "code": "approval_required",
                "message": str(e),
                "plan": e.to_plan_dict(),
                "instruction": (
                    "Surface the plan to the user via the `clarify` tool "
                    "(or in a normal reply) and confirm they want this run.  "
                    "Once they say yes, re-call telegram_orchestrate_swarm "
                    "with the same arguments plus user_approved=true.  If "
                    "they want to change the plan, edit subtasks and "
                    "re-call without user_approved.  To bypass approval "
                    "entirely, the user can pin every subtask to a "
                    "specific bot_username."
                ),
            },
            ensure_ascii=False,
        )
    except FleetGuardrailError as e:
        return tool_error(str(e), code="guardrail")
    return json.dumps({"success": True, **result}, ensure_ascii=False)


TELEGRAM_ORCHESTRATE_SWARM_SCHEMA = {
    "type": "function",
    "function": {
        "name": "telegram_orchestrate_swarm",
        "description": (
            "Fan a task across the connected Telegram bot swarm.  Each "
            "subtask runs as a NAMED child bot that posts updates out "
            "into chats — this is the visible variant, treat it as an "
            "outbound action.\n\n"
            "APPROVAL FLOW (mirrors `terminal_tool` force=True pattern):\n"
            "1. First call WITHOUT user_approved returns "
            "`status: approval_required` with a structured plan.\n"
            "2. Surface the plan to the user via `clarify` (or in your "
            "reply) and let them confirm / adjust / cancel.\n"
            "3. On confirmation, re-call with user_approved=true.\n"
            "Bypass: if every subtask pins a specific bot_username, the "
            "user is treated as having already requested by name and the "
            "approval gate is skipped.  Operators can disable the gate "
            "globally via `telegram_fleet.auto_approve: true` in "
            "config.yaml.\n\n"
            "USE THIS over `hermes_swarm` ONLY when the user explicitly "
            "wants visible Telegram bot activity ('I want to watch them "
            "work', 'send updates from each agent', or they referenced "
            "fleet bots by name).  Default to `hermes_swarm` for "
            "invisible in-process fan-out."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "objective": {
                    "type": "string",
                    "description": "The user's overall goal.  Included in each sub-agent's context for grounding.",
                },
                "subtasks": {
                    "type": "array",
                    "description": "Sub-tasks to fan out.  Each has at minimum a 'goal'.  Optional fields: 'persona' (override the bot's default persona for this task), 'bot_username' (pin to a specific fleet member; otherwise round-robin), 'context' (extra grounding), 'toolsets' (override allowed toolsets).",
                    "items": {
                        "type": "object",
                        "properties": {
                            "goal": {"type": "string"},
                            "persona": {"type": "string"},
                            "bot_username": {"type": "string"},
                            "context": {"type": "string"},
                            "toolsets": {"type": "array", "items": {"type": "string"}},
                        },
                        "required": ["goal"],
                    },
                },
                "report_chat_id": {
                    "type": "string",
                    "description": "Optional Telegram chat where each fleet member will post live status updates as itself.  Lets the operator watch the swarm work.",
                },
                "max_parallel": {
                    "type": "integer",
                    "description": "Max sub-tasks to run concurrently.  Default 8.",
                    "default": 8,
                },
                "per_task_timeout_s": {
                    "type": "number",
                    "description": "Per-sub-task timeout in seconds.  Default 600 (10 minutes).",
                    "default": 600,
                },
                "user_approved": {
                    "type": "boolean",
                    "default": False,
                    "description": (
                        "Set to true ONLY after the user has explicitly "
                        "confirmed (via `clarify` or in their reply) that "
                        "they want this fleet run to proceed.  Without "
                        "approval AND without every subtask pinning a "
                        "specific bot_username, the tool returns "
                        "`status: approval_required` with the proposed "
                        "plan instead of running."
                    ),
                },
            },
            "required": ["objective", "subtasks"],
        },
    },
}


# ── Toolset availability check ─────────────────────────────────────────


def _check_telegram_fleet_available() -> bool:
    """Toolset is available when the manager-bot token env var is set.

    API-calling tools (delegate, rotate, spawn, swarm) all require the token
    at call time.  Reporting True when only the roster file exists would make
    the tools appear available while every call fails with "no manager token".
    """
    return bool(os.getenv("TELEGRAM_FLEET_MANAGER_TOKEN"))


def _check_fleet_read_available() -> bool:
    """telegram_fleet_list only reads the roster — no API token required."""
    if os.getenv("TELEGRAM_FLEET_MANAGER_TOKEN"):
        return True
    try:
        from gateway.telegram_fleet.roster import load_roster

        return bool(load_roster().manager_bot_username)
    except Exception:
        return False


# ── Registration ───────────────────────────────────────────────────────

_TOOLSET = "telegram_fleet"

registry.register(
    name="telegram_spawn_bot",
    toolset=_TOOLSET,
    schema=TELEGRAM_SPAWN_BOT_SCHEMA,
    handler=lambda args, **kw: telegram_spawn_bot(**args, **kw),
    check_fn=_check_telegram_fleet_available,
    requires_env=["TELEGRAM_FLEET_MANAGER_TOKEN"],
    emoji="🤖",
)
registry.register(
    name="telegram_fleet_list",
    toolset=_TOOLSET,
    schema=TELEGRAM_FLEET_LIST_SCHEMA,
    handler=lambda args, **kw: telegram_fleet_list(**args, **kw),
    check_fn=_check_fleet_read_available,
    emoji="📋",
)
registry.register(
    name="telegram_delegate",
    toolset=_TOOLSET,
    schema=TELEGRAM_DELEGATE_SCHEMA,
    handler=lambda args, **kw: telegram_delegate(**args, **kw),
    check_fn=_check_telegram_fleet_available,
    emoji="📡",
)
registry.register(
    name="telegram_rotate_bot_token",
    toolset=_TOOLSET,
    schema=TELEGRAM_ROTATE_TOKEN_SCHEMA,
    handler=lambda args, **kw: telegram_rotate_bot_token(**args, **kw),
    check_fn=_check_telegram_fleet_available,
    emoji="🔄",
)
registry.register(
    name="telegram_decommission_bot",
    toolset=_TOOLSET,
    schema=TELEGRAM_DECOMMISSION_SCHEMA,
    handler=lambda args, **kw: telegram_decommission_bot(**args, **kw),
    check_fn=_check_telegram_fleet_available,
    emoji="🗑️",
)
registry.register(
    name="telegram_orchestrate_swarm",
    toolset=_TOOLSET,
    schema=TELEGRAM_ORCHESTRATE_SWARM_SCHEMA,
    handler=lambda args, **kw: telegram_orchestrate_swarm(**args, parent_agent=kw.get("parent_agent")),
    check_fn=_check_telegram_fleet_available,
    emoji="🌊",
)
