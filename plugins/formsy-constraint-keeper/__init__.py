"""Hermes adapter for FormSy Constraint Keeper.

This module intentionally uses only public plugin hooks and registered tools.
It must not depend on Hermes agent-loop internals.
"""

from __future__ import annotations

import json
from typing import Any

from plugins.formsy.constraint_keeper.coordinator import ConstraintKeeperCoordinator
from plugins.formsy.constraint_keeper.runtime import get_default_coordinator, reset_default_coordinator

_TOOLSET = "plugin_formsy_constraint_keeper"
_coordinator: ConstraintKeeperCoordinator | None = None


def register(ctx: Any) -> None:
    ctx.register_hook("on_session_start", _on_session_start)
    ctx.register_hook("pre_llm_call", _on_pre_llm_call)
    ctx.register_hook("pre_tool_call", _on_pre_tool_call)
    ctx.register_hook("post_tool_call", _on_post_tool_call)
    ctx.register_hook("transform_tool_result", _on_transform_tool_result)
    ctx.register_hook("on_session_end", _on_session_end)
    ctx.register_hook("on_session_reset", _on_session_reset)

    ctx.register_tool(
        name="formsy_constraint_status",
        toolset=_TOOLSET,
        schema=_tool_schema(
            "formsy_constraint_status",
            "Return current FormSy Constraint Keeper status for this task run.",
            {},
        ),
        handler=_tool_status,
        description="Return current FormSy Constraint Keeper status.",
    )
    ctx.register_tool(
        name="formsy_recover",
        toolset=_TOOLSET,
        schema=_tool_schema(
            "formsy_recover",
            "Ask FormSy Constraint Keeper for a recovery protocol.",
            {
                "reason": {
                    "type": "string",
                    "description": "Short reason for requesting recovery.",
                }
            },
        ),
        handler=_tool_recover,
        description="Ask FormSy Constraint Keeper for recovery guidance.",
    )
    ctx.register_tool(
        name="formsy_verify_completion",
        toolset=_TOOLSET,
        schema=_tool_schema(
            "formsy_verify_completion",
            "Ask FormSy Constraint Keeper to verify completion evidence.",
            {},
        ),
        handler=_tool_verify_completion,
        description="Verify completion with FormSy Constraint Keeper.",
    )
    ctx.register_tool(
        name="formsy_request_human_review",
        toolset=_TOOLSET,
        schema=_tool_schema(
            "formsy_request_human_review",
            "Request developer review when Constraint Keeper cannot verify completion automatically.",
            {
                "reason": {
                    "type": "string",
                    "description": "Short reason for requesting human review.",
                }
            },
        ),
        handler=_tool_request_human_review,
        description="Request developer review. This does not grant a trusted override.",
    )


def _on_session_start(session_id: str = "", **kwargs: Any) -> None:
    _get_coordinator().on_session_start(session_id=session_id, **kwargs)


def _on_pre_llm_call(session_id: str = "", user_message: Any = None, **kwargs: Any) -> None:
    coordinator = _get_coordinator()
    if user_message:
        coordinator.on_user_turn(user_message=str(user_message), session_id=session_id, **kwargs)
    return coordinator.pre_llm_call_context(session_id=session_id)


def _on_pre_tool_call(
    tool_name: str = "",
    args: dict[str, Any] | None = None,
    session_id: str = "",
    task_id: str = "",
    **_: Any,
) -> dict[str, str] | None:
    message = _get_coordinator().pre_tool_call_block_message(
        tool_name,
        args or {},
        session_id=session_id,
    )
    if not message:
        return None
    return {"action": "block", "message": message}


def _on_post_tool_call(
    tool_name: str = "",
    args: dict[str, Any] | None = None,
    result: Any = None,
    session_id: str = "",
    task_id: str = "",
    **_: Any,
) -> None:
    _get_coordinator().observe_tool_result(
        tool_name,
        args or {},
        result,
        session_id=session_id,
    )


def _on_transform_tool_result(
    tool_name: str = "",
    args: dict[str, Any] | None = None,
    result: str = "",
    session_id: str = "",
    task_id: str = "",
    **_: Any,
) -> str | None:
    return _get_coordinator().transform_tool_result(
        tool_name,
        args or {},
        result,
        session_id=session_id,
    )


def _on_session_end(**_: Any) -> None:
    try:
        _get_coordinator().flush_pending()
    except Exception:
        return


def _on_session_reset(**_: Any) -> None:
    global _coordinator
    try:
        _get_coordinator().flush_pending()
    except Exception:
        pass
    _coordinator = None
    reset_default_coordinator()


def _tool_status(args: dict[str, Any] | None = None, **kwargs: Any) -> str:
    return _json_result(_get_coordinator().status(session_id=str(kwargs.get("session_id") or "")))


def _tool_recover(args: dict[str, Any] | None = None, **kwargs: Any) -> str:
    args = args if isinstance(args, dict) else {}
    return _json_result(
        _get_coordinator().recover(
            reason=str(args.get("reason") or ""),
            session_id=str(kwargs.get("session_id") or ""),
        )
    )


def _tool_verify_completion(args: dict[str, Any] | None = None, **kwargs: Any) -> str:
    return _json_result(_get_coordinator().verify_completion(session_id=str(kwargs.get("session_id") or "")))


def _tool_request_human_review(args: dict[str, Any] | None = None, **kwargs: Any) -> str:
    args = args if isinstance(args, dict) else {}
    return _json_result({
        "ok": True,
        "requested": True,
        "reason": str(args.get("reason") or ""),
        "note": "Human review requested. This tool does not submit a trusted FormSy override.",
    })


def _get_coordinator() -> ConstraintKeeperCoordinator:
    return _coordinator or get_default_coordinator()


def _tool_schema(name: str, description: str, properties: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": name,
        "description": description,
        "parameters": {
            "type": "object",
            "properties": properties,
        },
    }


def _json_result(payload: Any) -> str:
    return json.dumps(payload if payload is not None else {}, ensure_ascii=False)
