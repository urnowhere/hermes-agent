"""Global Systems Office checks for long-term Hermes coherence.

This module is intentionally read-only.  Local fixes should stay fast; these
helpers identify when accumulated local changes are crossing system boundaries
and need a decision record, broader verification, or later architecture review.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Iterable, Sequence


_SEVERITY_RANK = {"ok": 0, "info": 1, "warning": 2, "critical": 3}
_DECISION_RECORD_PREFIXES = (
    "docs/decisions/",
    "docs/adr/",
    "docs/adrs/",
    "adr/",
    "adrs/",
    "decisions/",
)

_COMPONENTS: tuple[dict, ...] = (
    {
        "name": "agent-runtime",
        "owner": "Global Systems Office / Agent runtime",
        "paths": ["run_agent.py", "model_tools.py", "toolsets.py", "agent/"],
        "invariant": "Conversation loop, tool orchestration, model routing, memory/context behavior.",
    },
    {
        "name": "messaging-gateway",
        "owner": "Global Systems Office / Gateway",
        "paths": ["gateway/"],
        "invariant": "Telegram/Discord/etc delivery, session dispatch, platform safety, service control.",
    },
    {
        "name": "configuration",
        "owner": "Global Systems Office / Config",
        "paths": ["hermes_cli/config.py", "hermes_cli/main.py", "hermes_cli/model_switch.py", "hermes_cli/governance.py", "hermes_constants.py"],
        "invariant": "User config, profile-aware paths, model/provider entry points, CLI command surface.",
    },
    {
        "name": "tool-plane",
        "owner": "Global Systems Office / Tools",
        "paths": ["tools/", "plugins/", "model_tools.py", "toolsets.py"],
        "invariant": "Tool schemas, tool dispatch, toolset boundaries, plugin/tool blast radius.",
    },
    {
        "name": "scheduler",
        "owner": "Global Systems Office / Async runtime",
        "paths": ["cron/", "task_lanes.py", "gateway/task_control.py", "gateway/worker_runtime.py"],
        "invariant": "Cron jobs, background work, queue/task control, durable async execution.",
    },
    {
        "name": "knowledge-and-evals",
        "owner": "Global Systems Office / Knowledge & evals",
        "paths": ["skills/", "optional-skills/", "tests/", "docs/", "website/"],
        "invariant": "Reusable playbooks, documentation, regression coverage, eval/quality gates.",
    },
    {
        "name": "tests",
        "owner": "Global Systems Office / Verification",
        "paths": ["tests/"],
        "invariant": "Regression proof for changes that affect runtime or user contracts.",
    },
)

_ADR_TEMPLATE = """# ADR: <short decision title>

## Status
Proposed | Accepted | Superseded

## Context
What changed, which system boundary is touched, and why local fixes are no longer enough.

## Decision
The smallest durable rule or architecture choice we are making.

## Consequences
Positive and negative trade-offs, operational impact, and rollback path.

## Verification
Tests, smoke checks, evals, or monitoring that prove the decision is safe.

## Follow-ups
Debt items, catalog updates, playbook/skill updates, or postmortem links.
"""


def _normalize_path(path: str | Path) -> str:
    text = str(path).replace("\\", "/").strip()
    while text.startswith("./"):
        text = text[2:]
    return text


def _matches_prefix(path: str, prefixes: Sequence[str]) -> bool:
    return any(path == prefix.rstrip("/") or path.startswith(prefix) for prefix in prefixes)


def component_catalog() -> list[dict]:
    """Return the static v0 system catalog for governance checks."""
    return [dict(component) for component in _COMPONENTS]


def classify_paths(paths: Iterable[str | Path]) -> dict[str, list[str]]:
    """Map changed paths to Global Systems Office component names."""
    normalized = sorted({_normalize_path(path) for path in paths if str(path).strip()})
    classified: dict[str, list[str]] = {}
    for path in normalized:
        matched = False
        for component in _COMPONENTS:
            if _matches_prefix(path, component["paths"]):
                classified.setdefault(component["name"], []).append(path)
                matched = True
        if not matched:
            classified.setdefault("uncategorized", []).append(path)
    return classified


def _run_git(root: Path, args: Sequence[str]) -> tuple[int, str]:
    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=root,
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return 1, ""
    return completed.returncode, completed.stdout.strip()


def _git_changed_paths(root: Path) -> tuple[list[str], list[str], str]:
    code, git_root = _run_git(root, ["rev-parse", "--show-toplevel"])
    if code != 0 or not git_root:
        return [], [], "not-a-git-repository"

    repo_root = Path(git_root)
    tracked_code, tracked_out = _run_git(repo_root, ["diff", "--name-only", "HEAD"])
    untracked_code, untracked_out = _run_git(
        repo_root, ["ls-files", "--others", "--exclude-standard"]
    )
    if tracked_code != 0 or untracked_code != 0:
        return [], [], "git-error"
    tracked = [line for line in tracked_out.splitlines() if line.strip()]
    untracked = [line for line in untracked_out.splitlines() if line.strip()]
    return tracked, untracked, "ok"


def _git_status_line(root: Path) -> str:
    code, out = _run_git(root, ["status", "--short", "--branch"])
    if code != 0:
        return ""
    return out.splitlines()[0] if out else ""


def _has_decision_record(paths: Iterable[str]) -> bool:
    for path in paths:
        normalized = _normalize_path(path).lower()
        if _matches_prefix(normalized, _DECISION_RECORD_PREFIXES):
            return True
        name = Path(normalized).name
        if name.startswith("adr-") or "decision" in name:
            return True
    return False


def _is_production_path(path: str) -> bool:
    return not (
        path.startswith("tests/")
        or path.startswith("docs/")
        or path.startswith("website/")
        or path.startswith("skills/")
        or path.startswith("optional-skills/")
        or path.startswith(".hermes/plans/")
    )


def _summary_severity(findings: list[dict]) -> str:
    if not findings:
        return "ok"
    return max((finding["severity"] for finding in findings), key=lambda item: _SEVERITY_RANK[item])


def build_report(
    root: str | Path | None = None,
    paths: Iterable[str | Path] | None = None,
) -> dict:
    """Build a read-only governance report for a worktree or explicit path list."""
    root_path = Path(root or ".").resolve()
    git_status = "explicit-paths"
    untracked_paths: list[str] = []

    if paths is None:
        tracked_paths, untracked_paths, git_status = _git_changed_paths(root_path)
        changed_paths = sorted({_normalize_path(p) for p in (*tracked_paths, *untracked_paths)})
    else:
        changed_paths = sorted({_normalize_path(path) for path in paths if str(path).strip()})

    classified = classify_paths(changed_paths)
    production_paths = [path for path in changed_paths if _is_production_path(path)]
    production_components = sorted(
        name
        for name, component_paths in classified.items()
        if name not in {"tests", "knowledge-and-evals", "uncategorized"}
        and any(_is_production_path(path) for path in component_paths)
    )
    has_tests = any(path.startswith("tests/") for path in changed_paths)
    has_decision_record = _has_decision_record(changed_paths)

    findings: list[dict] = []
    if git_status not in {"ok", "explicit-paths"}:
        findings.append(
            {
                "id": "git-state-unavailable",
                "severity": "warning",
                "message": f"Could not inspect git state ({git_status}); report is incomplete.",
            }
        )

    if len(changed_paths) >= 25:
        findings.append(
            {
                "id": "large-change-surface",
                "severity": "warning",
                "message": f"{len(changed_paths)} changed paths detected; consider splitting or adding a release/readiness review.",
            }
        )

    requires_decision_record = len(production_components) >= 2 and not has_decision_record
    if requires_decision_record:
        findings.append(
            {
                "id": "decision-record-needed",
                "severity": "warning",
                "message": "Cross-boundary production changes need a lightweight ADR/RFC before they become invisible architecture drift.",
                "components": production_components,
            }
        )

    if production_paths and not has_tests:
        findings.append(
            {
                "id": "verification-missing",
                "severity": "warning",
                "message": "Production paths changed without test paths in the same surface; record a verification command or add regression coverage.",
            }
        )

    if untracked_paths:
        findings.append(
            {
                "id": "untracked-work",
                "severity": "info",
                "message": f"{len(untracked_paths)} untracked paths detected; decide whether they belong in the changeset or should be ignored/archived.",
            }
        )

    status_line = _git_status_line(root_path) if paths is None else ""
    if "behind" in status_line:
        findings.append(
            {
                "id": "branch-behind-upstream",
                "severity": "info",
                "message": "Branch is behind its upstream; rebase before PR or release readiness checks.",
            }
        )

    recommended_actions: list[str] = []
    if requires_decision_record:
        recommended_actions.append("Add a mini ADR with context, decision, consequences, verification, and rollback.")
    if production_paths and not has_tests:
        recommended_actions.append("Run or add focused regression tests for the touched runtime surface.")
    if len(production_components) >= 2:
        recommended_actions.append("Route the change through the Global Systems Office weekly drift review.")
    if not recommended_actions:
        recommended_actions.append("No governance escalation needed; keep local delivery moving.")

    return {
        "title": "Global Systems Office governance report",
        "root": str(root_path),
        "git_status": git_status,
        "status_line": status_line,
        "changed_paths": changed_paths,
        "untracked_paths": untracked_paths,
        "classified_paths": classified,
        "touched_components": sorted(classified),
        "production_components": production_components,
        "requires_decision_record": requires_decision_record,
        "findings": findings,
        "recommended_actions": recommended_actions,
        "summary": {
            "changed_path_count": len(changed_paths),
            "component_count": len(production_components),
            "severity": _summary_severity(findings),
        },
    }


def format_report(report: dict) -> str:
    """Format a governance report for terminal output."""
    lines = [
        "Global Systems Office",
        f"Severity: {report['summary']['severity']}",
        f"Changed paths: {report['summary']['changed_path_count']}",
    ]
    if report.get("status_line"):
        lines.append(f"Git: {report['status_line']}")

    components = report.get("production_components") or []
    if components:
        lines.append("Components: " + ", ".join(components))
    else:
        lines.append("Components: none requiring governance escalation")

    lines.append("")
    lines.append("Findings:")
    if report.get("findings"):
        for finding in report["findings"]:
            lines.append(f"- {finding['severity']}: {finding['id']} — {finding['message']}")
    else:
        lines.append("- ok: no governance findings")

    lines.append("")
    lines.append("Recommended actions:")
    for action in report.get("recommended_actions", []):
        lines.append(f"- {action}")

    classified = report.get("classified_paths") or {}
    if classified:
        lines.append("")
        lines.append("Touched map:")
        for component in sorted(classified):
            paths = classified[component]
            preview = ", ".join(paths[:4])
            if len(paths) > 4:
                preview += f", +{len(paths) - 4} more"
            lines.append(f"- {component}: {preview}")

    return "\n".join(lines)


def _print_catalog(as_json: bool) -> int:
    catalog = component_catalog()
    if as_json:
        print(json.dumps(catalog, ensure_ascii=False, indent=2))
    else:
        print("Global Systems Office catalog")
        for component in catalog:
            print(f"- {component['name']}: {component['invariant']}")
            print(f"  Owner: {component['owner']}")
            print(f"  Paths: {', '.join(component['paths'])}")
    return 0


def _exit_code_for(report: dict, fail_on: str | None) -> int:
    if not fail_on:
        return 0
    severity = report["summary"]["severity"]
    threshold = _SEVERITY_RANK.get(fail_on, 999)
    return 1 if _SEVERITY_RANK.get(severity, 0) >= threshold else 0


def governance_command(args) -> int:
    """Entry point for `hermes governance` / `hermes gso`."""
    subcommand = getattr(args, "governance_command", None) or "check"

    if subcommand == "catalog":
        return _print_catalog(bool(getattr(args, "json", False)))

    if subcommand == "adr-template":
        print(_ADR_TEMPLATE.rstrip())
        return 0

    if subcommand == "check":
        report = build_report(
            root=getattr(args, "root", None),
            paths=getattr(args, "paths", None),
        )
        if getattr(args, "json", False):
            print(json.dumps(report, ensure_ascii=False, indent=2))
        else:
            print(format_report(report))
        return _exit_code_for(report, getattr(args, "fail_on", None))

    print(f"Unknown governance command: {subcommand}")
    return 1
