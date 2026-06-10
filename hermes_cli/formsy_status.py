"""Small display helpers for FormSy tool-result status projection."""

from __future__ import annotations

import json
from typing import Any


def formsy_statuses_from_tool_result(
    name: str,
    args: dict[str, Any] | None,
    result: str,
) -> list[tuple[str, str]]:
    statuses = _context_statuses(name, args or {}, result)
    finish_gate = _finish_gate_status(name, result)
    if finish_gate:
        statuses.append(finish_gate)
    return statuses


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


def _finish_gate_status(name: str, result: str) -> tuple[str, str] | None:
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
    return "formsy.finish_gate", "\n".join(lines)
