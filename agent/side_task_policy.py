"""Deterministic side-task policy for auxiliary LLM calls.

The goal is not to disable GPT-5.5 auxiliary work; AK wants GPT-5.5 routes.
The goal is to prevent non-essential side chores from blocking the main reply
path indefinitely. This module is pure policy + timing: no providers, no network.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SideTaskPolicy:
    task: str
    kind: str
    timeout_cap_seconds: float | None
    fail_open: bool


_POLICIES: dict[str, SideTaskPolicy] = {
    # User-visible quality-of-life chores: keep very short and fail open.
    "title_generation": SideTaskPolicy("title_generation", "observer", 4.0, True),
    "skills_hub": SideTaskPolicy("skills_hub", "observer", 8.0, True),
    "session_search": SideTaskPolicy("session_search", "observer", 8.0, True),
    # Compression is more important, but still cannot hold the turn forever.
    "compression": SideTaskPolicy("compression", "transforming", 20.0, True),
    # Web extraction and vision are usually directly user-requested.
    "web_extract": SideTaskPolicy("web_extract", "blocking", None, False),
    "vision": SideTaskPolicy("vision", "blocking", None, False),
    "mcp": SideTaskPolicy("mcp", "blocking", None, False),
}

_DEFAULT_POLICY = SideTaskPolicy("", "blocking", None, False)


def get_side_task_policy(task: str | None) -> SideTaskPolicy:
    key = (task or "").strip().lower()
    if not key:
        return _DEFAULT_POLICY
    return _POLICIES.get(key, SideTaskPolicy(key, "blocking", None, False))


def apply_side_task_timeout_cap(task: str | None, requested_timeout: float) -> float:
    policy = get_side_task_policy(task)
    if policy.timeout_cap_seconds is None:
        return requested_timeout
    return min(float(requested_timeout), float(policy.timeout_cap_seconds))


__all__ = [
    "SideTaskPolicy",
    "apply_side_task_timeout_cap",
    "get_side_task_policy",
]
