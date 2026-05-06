"""Read-only governance context helpers for hot-path prompt compaction.

This module deliberately treats governance source files as immutable input:
- rules.source.json remains canonical full authority on disk.
- the always-on prompt receives a compact compiled core derived from memory_line
  fields, not full canonical_body text.
- each user turn gets deterministic exact source retrieval based on action
  classification, with at least the truth/claim-verification baseline fetched.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Iterable, Mapping, Sequence

from hermes_cli.config import get_hermes_home

logger = logging.getLogger(__name__)

GOVERNANCE_INDEX_START = "═══ COMPRESSED INDEX — OPERATIONAL FORM"
GOVERNANCE_INDEX_END = "# END MEMORY.md COMPRESSED FORM"

DEFAULT_RULE_SOURCE = Path("sovereign-mind/governance/rules.source.json")
FALLBACK_RULE_SOURCE = Path("sovereign-mind/governance/rules.source.json")

# Small always-on skeleton. Exact rule bodies are fetched deterministically per
# turn/action below; keep this compact to avoid recreating full memory injection.
CORE_RULE_IDS = (
    "ABS-1",
    "ABS-2",
    "ABS-3",
    "ABS-6",
    "ABS-7",
    "ABS-10",
    "ABS-13",
    "ABS-15",
    "ABS-21",
    "ABS-23",
    "ABS-25",
    "ABS-26",
    "ABS-34",
    "ABS-37",
    "DIKTAT-4",
    "DIKTAT-7-REFINED",
    "DIKTAT-13",
    "DIKTAT-18",
    "DIKTAT-20",
    "DIKTAT-21",
    "DIKTAT-26",
    "DIKTAT-30",
)

# Always fetch exact truth/claim/privacy/action baselines, then add class-specific
# rules. This satisfies deterministic retrieval for every turn without dumping
# all canonical bodies into the hot path.
ALWAYS_FETCH_RULE_IDS = ("ABS-2", "ABS-37")

TRIGGER_RULES: tuple[tuple[str, tuple[str, ...], tuple[str, ...]], ...] = (
    ("write", ("write", "patch", "edit", "modify", "create", "save", "commit", "apply"), ("ABS-1", "ABS-10", "DIKTAT-30")),
    ("delete", ("delete", "remove", "prune", "drop", "clean"), ("ABS-5", "ABS-25", "ABS-26", "STD-5")),
    ("install", ("install", "upgrade", "pip", "npm", "brew", "cargo", "go get", "docker pull"), ("ABS-15", "STD-16")),
    ("credential", ("credential", "token", "password", "secret", "keychain", "oauth", "2fa", "sudo"), ("ABS-7", "ABS-34")),
    ("external", ("http", "https", "github", "api", "send", "post", "email", "publish", "upload", "download"), ("ABS-6", "ABS-34", "DIKTAT-8")),
    ("governance", ("governance", "rule", "agents.md", "memory.md", "business.md", "rules.source", "memory_structure"), ("ABS-14", "ABS-18", "ABS-27", "DIKTAT-23", "DIKTAT-24")),
    ("hermes-runtime", ("hermes", "gateway", "plugin", "hook", "agent", "model", "config", "provider", "update"), ("ABS-19", "ABS-30", "DIKTAT-29", "DIKTAT-30")),
    ("visual", ("screen", "visual", "screenshot", "ui", "browser", "look", "see"), ("ABS-24", "ABS-32")),
    ("verification", ("verify", "verified", "done", "ready", "running", "live", "working"), ("ABS-33", "ABS-37")),
)

_WORD_RE = re.compile(r"[a-z0-9_.:/-]+")


@dataclass(frozen=True)
class GovernanceRule:
    id: str
    title: str
    tier: str
    status: str
    enforcement_mode: str
    memory_line: str
    canonical_body: str
    canonical_body_sha256: str
    source_file: str
    source_line: int | None


@dataclass(frozen=True)
class GovernanceSource:
    path: Path
    sha256: str
    source_hash: str
    version: str
    generated_at: str
    rules: Mapping[str, GovernanceRule]


def _resolve_hermes_home(hermes_home: Path | str | None = None) -> Path:
    if hermes_home is None:
        return get_hermes_home()
    return Path(hermes_home).expanduser().resolve(strict=False)


def _rule_source_path(hermes_home: Path | str | None = None) -> Path:
    home = _resolve_hermes_home(hermes_home)
    return home / DEFAULT_RULE_SOURCE


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@lru_cache(maxsize=8)
def _load_source_cached(path_str: str, mtime_ns: int, size: int) -> GovernanceSource:
    del mtime_ns, size  # cache key only
    path = Path(path_str)
    raw = path.read_text(encoding="utf-8")
    payload = json.loads(raw)
    rules: dict[str, GovernanceRule] = {}
    for item in payload.get("rules", []):
        rule_id = str(item.get("id", "")).strip()
        if not rule_id:
            continue
        rules[rule_id] = GovernanceRule(
            id=rule_id,
            title=str(item.get("title", "")).strip(),
            tier=str(item.get("tier", "")).strip(),
            status=str(item.get("status", "")).strip(),
            enforcement_mode=str(item.get("enforcement_mode", "")).strip(),
            memory_line=str(item.get("memory_line", "")).strip(),
            canonical_body=str(item.get("canonical_body", "")).strip(),
            canonical_body_sha256=str(item.get("canonical_body_sha256", "")).strip(),
            source_file=str(item.get("source_file", "")).strip(),
            source_line=item.get("source_line") if isinstance(item.get("source_line"), int) else None,
        )
    return GovernanceSource(
        path=path,
        sha256=_sha256_text(raw),
        source_hash=str(payload.get("source_hash", "")).strip(),
        version=str(payload.get("version", "")).strip(),
        generated_at=str(payload.get("generated_at", "")).strip(),
        rules=rules,
    )


def load_governance_source(hermes_home: Path | str | None = None) -> GovernanceSource | None:
    path = _rule_source_path(hermes_home)
    try:
        stat = path.stat()
    except OSError:
        logger.warning("governance_context source_missing path=%s", path)
        return None
    start = time.perf_counter()
    try:
        source = _load_source_cached(str(path), stat.st_mtime_ns, stat.st_size)
    except Exception as exc:
        logger.warning("governance_context source_load_failed path=%s error=%s", path, exc)
        return None
    elapsed_ms = (time.perf_counter() - start) * 1000.0
    logger.info(
        "governance_context source_load_ms=%.2f source_bytes=%s rule_count=%s sha256=%s",
        elapsed_ms,
        stat.st_size,
        len(source.rules),
        source.sha256[:12],
    )
    return source


def _existing_rules(source: GovernanceSource, ids: Iterable[str]) -> list[GovernanceRule]:
    seen: set[str] = set()
    out: list[GovernanceRule] = []
    for rule_id in ids:
        if rule_id in seen:
            continue
        rule = source.rules.get(rule_id)
        if rule is None:
            continue
        seen.add(rule_id)
        out.append(rule)
    return out


def build_compiled_governance_core(hermes_home: Path | str | None = None) -> str:
    source = load_governance_source(hermes_home)
    if source is None:
        return (
            "COMPILED GOVERNANCE CORE\n"
            "Status: unavailable; exact source fetch failed. Default to conservative governance behavior and use tools before claims."
        )

    lines = [
        "COMPILED GOVERNANCE CORE — compact always-on packet",
        "Canonical full authority remains on disk; do not modify governance source files from this packet.",
        f"rules.source.json sha256: {source.sha256}",
        f"source_hash: {source.source_hash or 'unknown'}",
        f"version: {source.version or 'unknown'} generated_at: {source.generated_at or 'unknown'}",
        "Exact retrieval is mandatory and deterministic per turn/action; this packet is not a substitute for source fetch on risky actions.",
        "Core rules:",
    ]
    for rule in _existing_rules(source, CORE_RULE_IDS):
        summary = rule.memory_line or f"{rule.id}: {rule.title}"
        lines.append(f"- {summary}")
    return "\n".join(lines)


def _tokens(text: str) -> set[str]:
    return set(_WORD_RE.findall(text.lower()))


def classify_governance_triggers(action_text: str) -> list[tuple[str, tuple[str, ...]]]:
    lowered = action_text.lower()
    tokens = _tokens(action_text)
    matches: list[tuple[str, tuple[str, ...]]] = [("always", ALWAYS_FETCH_RULE_IDS)]
    for trigger, needles, rule_ids in TRIGGER_RULES:
        hit = False
        for needle in needles:
            n = needle.lower()
            if " " in n or "." in n or "/" in n or ":" in n:
                hit = n in lowered
            else:
                hit = n in tokens
            if hit:
                break
        if hit:
            matches.append((trigger, rule_ids))
    return matches


def build_governance_retrieval_context(action_text: str, hermes_home: Path | str | None = None) -> str:
    source = load_governance_source(hermes_home)
    if source is None:
        return (
            "EXACT GOVERNANCE SOURCE FETCH\n"
            "Status: unavailable; source could not be read. Treat this as a conservative governance failure and avoid risky action until source is readable."
        )

    trigger_hits = classify_governance_triggers(action_text)
    ordered_ids: list[str] = []
    trigger_by_rule: dict[str, list[str]] = {}
    for trigger, rule_ids in trigger_hits:
        for rule_id in rule_ids:
            if rule_id not in ordered_ids:
                ordered_ids.append(rule_id)
            trigger_by_rule.setdefault(rule_id, []).append(trigger)

    rules = _existing_rules(source, ordered_ids)
    lines = [
        "EXACT GOVERNANCE SOURCE FETCH — deterministic per-turn/action retrieval",
        f"rules.source.json sha256: {source.sha256}",
        "Fetched exact rule bodies:",
    ]
    if not rules:
        lines.append("- No matching rules found in source; default to conservative behavior and verify before acting.")
        return "\n".join(lines)

    for rule in rules:
        triggers = ",".join(trigger_by_rule.get(rule.id, ["unknown"]))
        body_hash = _sha256_text(rule.canonical_body) if rule.canonical_body else ""
        hash_status = "unknown"
        if rule.canonical_body_sha256:
            hash_status = "ok" if body_hash == rule.canonical_body_sha256 else "mismatch"
        lines.extend(
            [
                f"\n## {rule.id}: {rule.title}",
                f"trigger: {triggers}",
                f"tier: {rule.tier}; status: {rule.status}; enforcement: {rule.enforcement_mode}",
                f"source: {rule.source_file}:{rule.source_line or '?'}; body_sha256_status: {hash_status}",
                rule.canonical_body or rule.memory_line or "[empty rule body]",
            ]
        )
    return "\n".join(lines)


def compact_governance_memory_block(memory_prompt: str, hermes_home: Path | str | None = None) -> str:
    """Replace the large compressed governance index inside MEMORY prompt text.

    Non-governance durable memory entries outside the compressed index are kept.
    If the expected markers are absent, return the prompt unchanged.
    """
    start = memory_prompt.find(GOVERNANCE_INDEX_START)
    if start < 0:
        return memory_prompt
    end = memory_prompt.find(GOVERNANCE_INDEX_END, start)
    if end < 0:
        return memory_prompt
    line_end = memory_prompt.find("\n", end)
    if line_end < 0:
        line_end = len(memory_prompt)
    else:
        line_end += 1
    core = build_compiled_governance_core(hermes_home)
    return memory_prompt[:start] + core + "\n" + memory_prompt[line_end:]


def is_governance_source_required(hermes_home: Path | str | None = None) -> bool:
    """Return True when a local structured governance source is expected.

    Default upstream installs do not ship ``sovereign-mind/governance``. In that
    case governance retrieval stays a no-op and must not block tool execution.
    If the governance directory exists, or an operator explicitly opts in via
    ``HERMES_GOVERNANCE_SOURCE_REQUIRED``, source-read failures become
    fail-closed so a corrupted/missing structured source cannot be silently
    bypassed.
    """
    env = str(os.environ.get("HERMES_GOVERNANCE_SOURCE_REQUIRED", "")).strip().lower()
    if env in {"1", "true", "yes", "on"}:
        return True
    source_path = _rule_source_path(hermes_home)
    return source_path.exists() or source_path.parent.exists()


def is_governance_source_available(hermes_home: Path | str | None = None) -> bool:
    return load_governance_source(hermes_home) is not None


__all__ = [
    "build_compiled_governance_core",
    "build_governance_retrieval_context",
    "classify_governance_triggers",
    "compact_governance_memory_block",
    "is_governance_source_available",
    "is_governance_source_required",
    "load_governance_source",
]
