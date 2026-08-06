"""Small display helpers for FormSy tool-result status projection."""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import quote


def formsy_statuses_from_tool_result(
    name: str,
    args: dict[str, Any] | None,
    result: str,
    *,
    fs_console_base_url: str = "",
    fallback_code_plan_url: str = "",
) -> list[tuple[str, str]]:
    statuses = _context_statuses(name, args or {}, result)
    finish_gate = _finish_gate_status(
        name,
        result,
        fs_console_base_url=fs_console_base_url,
        fallback_code_plan_url=fallback_code_plan_url,
    )
    if finish_gate:
        statuses.append(finish_gate)
    return statuses


def formsy_code_plan_url_from_tool_result(
    name: str,
    result: str,
    *,
    fs_console_base_url: str = "",
) -> str:
    if name not in {"context_search", "formsy_verify_completion"}:
        return ""
    data = _parse_result(result)
    if not data:
        return ""
    return _code_plan_url(data, fs_console_base_url)


def formsy_tool_status_cards_from_tool_result(
    name: str,
    args: dict[str, Any] | None,
    result: str,
    *,
    fs_console_base_url: str = "",
    fallback_code_plan_url: str = "",
    tool_call_id: str = "",
) -> list[dict[str, Any]]:
    """Project FormSy tool results into generic ToolStatusCard dictionaries."""
    code_plan_url = formsy_code_plan_url_from_tool_result(
        name,
        result,
        fs_console_base_url=fs_console_base_url,
    ) or str(fallback_code_plan_url or "").strip()
    cards: list[dict[str, Any]] = []
    for kind, text in formsy_statuses_from_tool_result(
        name,
        args or {},
        result,
        fs_console_base_url=fs_console_base_url,
        fallback_code_plan_url=fallback_code_plan_url,
    ):
        card = _status_tuple_to_card(kind, text, code_plan_url, tool_call_id)
        if card:
            cards.append(card)
    return cards


def _parse_result(result: str) -> dict[str, Any] | None:
    text = str(result or "").strip()
    if not text:
        return None
    json_text = text.split("\n\n## FormSy Constraint Protocol", 1)[0].strip()
    try:
        data = json.loads(json_text)
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def _first_target(data: dict[str, Any]) -> str:
    for key in (
        "accepted_targets",
        "bundle_must_edit",
        "bundle_primary_files",
        "direct_match_files",
    ):
        value = data.get(key)
        if isinstance(value, list) and value:
            target = str(value[0] or "").strip()
            if target:
                return target
    recipes = data.get("verified_solution_recipes")
    if isinstance(recipes, list) and recipes:
        recipe = recipes[0]
        if isinstance(recipe, dict):
            for key in ("primary_edit_files", "changed_files", "accepted_targets"):
                value = recipe.get(key)
                if isinstance(value, list) and value:
                    target = str(value[0] or "").strip()
                    if target:
                        return target
    return ""


def _context_statuses(name: str, args: dict[str, Any], result: str) -> list[tuple[str, str]]:
    if name != "context_search":
        return []
    data = _parse_result(result)
    if not data or data.get("ok") is False:
        return []
    coverage = str(data.get("coverage") or "").strip().lower()
    if coverage in {"poor", "missing", "none", "empty"}:
        return []

    statuses: list[tuple[str, str]] = []
    target = _first_target(data)
    query = " ".join(str(data.get("query") or args.get("query") or "").split())
    lines = ["[FormSy] Context Pack ready"]
    if query:
        lines.append(f"Task: {query[:96]}")
    if target:
        lines.append(f"Primary target: {target}")
    memory_status = str(data.get("memory_status") or "").strip()
    if memory_status:
        lines.append(f"Memory: {memory_status}")
    if target:
        statuses.append(("formsy.context_ready", "\n".join(lines)))

    recipes = data.get("verified_solution_recipes")
    if isinstance(recipes, list) and recipes:
        recipe_lines = ["[FormSy] Verified recipe available"]
        if target:
            recipe_lines.append(f"Primary target: {target}")
        statuses.append(("formsy.verified_recipe", "\n".join(recipe_lines)))
    return statuses


def _finish_gate_status(
    name: str,
    result: str,
    *,
    fs_console_base_url: str = "",
    fallback_code_plan_url: str = "",
) -> tuple[str, str] | None:
    if name != "formsy_verify_completion":
        return None
    data = _parse_result(result)
    if not data:
        return None
    protocol = data.get("protocol")
    audit = data.get("completion_audit")
    decision = str(
        data.get("decision")
        or (audit.get("gate_decision") if isinstance(audit, dict) else "")
        or (protocol.get("gate_decision") if isinstance(protocol, dict) else "")
        or ""
    ).strip()
    if not decision:
        return None
    accepted = decision.upper() in {"ACCEPT_DONE", "ACCEPT_DONE_WITH_OVERRIDE"}
    title = "[FormSy Finish Gate] Accepted" if accepted else "[FormSy Finish Gate] Needs validation"
    lines = [title, f"Decision: {decision}"]
    if isinstance(protocol, dict):
        summary = str(protocol.get("summary") or "").strip()
        if summary:
            lines.append(f"Evidence: {summary}" if accepted else f"Reason: {summary}")
    code_plan_url = _code_plan_url(data, fs_console_base_url) or str(fallback_code_plan_url or "").strip()
    if code_plan_url:
        lines.append(f"Task Workflow: {code_plan_url}")
    return "formsy.finish_gate", "\n".join(lines)


def _status_tuple_to_card(
    kind: str,
    text: str,
    code_plan_url: str,
    tool_call_id: str,
) -> dict[str, Any] | None:
    lines = [line for line in str(text or "").splitlines() if line.strip()]
    if not lines:
        return None
    link = {"label": "Task Workflow", "url": code_plan_url} if code_plan_url else None
    if kind == "formsy.finish_gate":
        if lines and lines[0].startswith("[FormSy Finish Gate]"):
            lines = lines[1:]
        decision_line = next((line for line in lines if line.startswith("Decision:")), "")
        accepted = "ACCEPT_DONE" in decision_line.upper()
        card: dict[str, Any] = {
            "source": "formsy",
            "kind": "finish_gate",
            "title": "FormSy Finish Gate",
            "body": lines,
            "severity": "success" if accepted else "warning",
            "dedupe_key": f"formsy:finish_gate:{tool_call_id or code_plan_url or decision_line}",
            "group_key": "formsy:finish_gate",
            "replace_policy": "latest",
        }
        if link:
            card["link"] = link
        return card
    if kind in {"formsy.context_ready", "formsy.verified_recipe"}:
        card = {
            "source": "formsy",
            "kind": "verified_recipe" if kind == "formsy.verified_recipe" else "context_ready",
            "title": "FormSy Context",
            "body": lines,
            "severity": "info",
            "dedupe_key": f"{kind}:{tool_call_id or code_plan_url or hash(text)}",
            "group_key": "formsy:context",
            "replace_policy": "append",
        }
        if link:
            card["link"] = link
        return card
    return None


def _code_plan_url(data: dict[str, Any], fs_console_base_url: str) -> str:
    base_url = str(fs_console_base_url or "").strip().rstrip("/")
    code_plan_id = _code_plan_id(data)
    if code_plan_id and base_url:
        return f"{base_url}/code-plans/{quote(code_plan_id, safe='')}"
    return _embedded_code_plan_url(data)


def _code_plan_id(data: dict[str, Any]) -> str:
    for value in (
        data.get("code_plan_id"),
        data.get("codePlanId"),
        data.get("plan_id"),
        data.get("planId"),
    ):
        text = str(value or "").strip()
        if text:
            return text
    for key in ("protocol", "completion_audit", "fs_console", "code_plan", "guidance", "code_plan_review"):
        nested = data.get(key)
        if isinstance(nested, dict):
            text = _code_plan_id(nested)
            if text:
                return text
    return ""


def _embedded_code_plan_url(data: dict[str, Any]) -> str:
    for value in (data.get("url"), data.get("code_plan_url"), data.get("codePlanUrl")):
        text = str(value or "").strip()
        if text:
            return text
    for key in ("fs_console", "code_plan", "guidance", "code_plan_review"):
        nested = data.get(key)
        if isinstance(nested, dict):
            text = _embedded_code_plan_url(nested)
            if text:
                return text
    return ""
