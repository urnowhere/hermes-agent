#!/usr/bin/env python3
"""Profile-backed expert delegation tool.

Runs another Hermes profile in a fresh subprocess and returns its final output
as evidence for the current agent.  This deliberately uses a process boundary:
Hermes profiles are selected before most Hermes modules import, so changing
``HERMES_HOME`` inside the current process is not safe.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, Optional

from tools.registry import registry


DEFAULT_PROFILE_TIMEOUT_SECONDS = 600
DEFAULT_PROFILE_MAX_TIMEOUT_SECONDS = 1800


def _load_delegation_config() -> dict:
    """Load the ``delegation`` config section."""
    try:
        from cli import CLI_CONFIG

        cfg = CLI_CONFIG.get("delegation", {})
        if cfg:
            return cfg
    except Exception:
        pass
    try:
        from hermes_cli.config import load_config

        full = load_config()
        return full.get("delegation", {}) or {}
    except Exception:
        return {}


def _expand_path(value: str) -> Path:
    return Path(os.path.expandvars(os.path.expanduser(value))).resolve()


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [part.strip() for part in value.split(",") if part.strip()]
    if isinstance(value, (list, tuple)):
        return [str(part).strip() for part in value if str(part).strip()]
    return []


def _profile_expert_entry(cfg: dict, profile: str) -> dict:
    experts = cfg.get("profile_experts") or cfg.get("profiles") or {}
    if not isinstance(experts, dict):
        return {}
    entry = experts.get(profile) or {}
    if isinstance(entry, str):
        return {"hermes_home": entry}
    if isinstance(entry, dict):
        return dict(entry)
    return {}


def _resolve_profile_home(profile: str, cfg: Optional[dict] = None) -> Path:
    """Resolve a target expert profile to a HERMES_HOME directory.

    Named Hermes profiles are supported by default.  Custom homes outside
    Hermes' named-profile directory must be explicitly allowlisted in:

        delegation:
          profile_experts:
            reviewer:
              hermes_home: ~/.hermes-reviewer
    """
    cfg = cfg if cfg is not None else _load_delegation_config()
    entry = _profile_expert_entry(cfg, profile)
    configured_home = (
        entry.get("hermes_home")
        or entry.get("home")
        or entry.get("path")
        or entry.get("profile_home")
    )
    if configured_home:
        home = _expand_path(str(configured_home))
    else:
        if cfg.get("allow_named_profile_experts", True) is False:
            raise ValueError(f"Profile expert '{profile}' is not allowlisted.")
        from hermes_cli.profiles import resolve_profile_env

        home = _expand_path(resolve_profile_env(profile))

    if not (home / "config.yaml").is_file():
        raise ValueError(f"Profile expert '{profile}' has no config.yaml at {home}")
    return home


def _resolve_profile_toolsets(profile: str, cfg: dict) -> Optional[str]:
    entry = _profile_expert_entry(cfg, profile)
    toolsets = _as_list(entry.get("toolsets"))
    if not toolsets:
        return None
    return ",".join(toolsets)


def _resolve_timeout(profile: str, requested: Optional[int], cfg: dict) -> int:
    entry = _profile_expert_entry(cfg, profile)
    default_timeout = entry.get(
        "timeout_seconds",
        cfg.get("profile_timeout_seconds", DEFAULT_PROFILE_TIMEOUT_SECONDS),
    )
    max_timeout = cfg.get("profile_max_timeout_seconds", DEFAULT_PROFILE_MAX_TIMEOUT_SECONDS)
    try:
        timeout = int(requested if requested is not None else default_timeout)
    except (TypeError, ValueError):
        timeout = DEFAULT_PROFILE_TIMEOUT_SECONDS
    try:
        max_timeout_i = int(max_timeout)
    except (TypeError, ValueError):
        max_timeout_i = DEFAULT_PROFILE_MAX_TIMEOUT_SECONDS
    return max(1, min(timeout, max_timeout_i))


def _build_profile_prompt(profile: str, task: str, context: Optional[str] = None) -> str:
    parts = [
        f'You are being asked as the "{profile}" specialist Hermes profile.',
        "",
        "Contract:",
        "- Stay inside this specialist lane and its available tools.",
        "- Return evidence, findings, recommendations, and uncertainty for the caller to synthesize.",
        "- Separate verified facts from assumptions.",
        "- Cite files, tools, issues, docs, or source paths when available.",
        "- State tool failures or unavailable sources plainly.",
        "- Prefer plain ASCII text in headings and evidence labels.",
        "- Do not update durable memory or external systems unless the task explicitly asks and your profile allows it.",
        "- Do not claim durable truth. Your output is evidence for the caller.",
        "",
        "Task:",
        task.strip(),
    ]
    if context and context.strip():
        parts.extend(["", "Additional context:", context.strip()])
    return "\n".join(parts)


def _build_command(prompt: str, profile: str, cfg: dict) -> list[str]:
    command = [
        sys.executable,
        "-m",
        "hermes_cli.main",
        "chat",
        "-Q",
        "--source",
        f"profile-expert:{profile}",
        "-q",
        prompt,
    ]
    toolsets = _resolve_profile_toolsets(profile, cfg)
    if toolsets:
        command.extend(["--toolsets", toolsets])
    return command


def ask_profile(
    profile: str,
    task: str,
    context: Optional[str] = None,
    timeout_seconds: Optional[int] = None,
) -> str:
    """Ask a configured Hermes profile to perform a bounded expert task."""
    profile = (profile or "").strip()
    task = (task or "").strip()
    if not profile:
        return json.dumps({"error": "profile is required"})
    if not task:
        return json.dumps({"error": "task is required"})

    cfg = _load_delegation_config()
    try:
        profile_home = _resolve_profile_home(profile, cfg)
    except Exception as exc:
        return json.dumps({"error": str(exc)})

    timeout = _resolve_timeout(profile, timeout_seconds, cfg)
    prompt = _build_profile_prompt(profile, task, context)
    command = _build_command(prompt, profile, cfg)
    repo_root = Path(__file__).resolve().parents[1]

    env = os.environ.copy()
    env["HERMES_HOME"] = str(profile_home)
    env["HERMES_PROFILE_EXPERT_PROFILE"] = profile
    parent_home = os.environ.get("HERMES_HOME")
    if parent_home:
        env["HERMES_PROFILE_EXPERT_PARENT_HOME"] = parent_home
    env["PYTHONPATH"] = (
        str(repo_root)
        if not env.get("PYTHONPATH")
        else str(repo_root) + os.pathsep + env["PYTHONPATH"]
    )

    started = time.monotonic()
    try:
        completed = subprocess.run(
            command,
            cwd=os.getcwd(),
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        duration = round(time.monotonic() - started, 3)
        return json.dumps(
            {
                "profile": profile,
                "status": "timed_out",
                "exit_code": 124,
                "duration_seconds": duration,
                "timeout_seconds": timeout,
                "hermes_home": str(profile_home),
                "output": (exc.stdout or "").strip(),
                "error": (exc.stderr or "").strip() or f"Timed out after {timeout}s",
            }
        )

    duration = round(time.monotonic() - started, 3)
    status = "completed" if completed.returncode == 0 else "failed"
    result: Dict[str, Any] = {
        "profile": profile,
        "status": status,
        "exit_code": completed.returncode,
        "duration_seconds": duration,
        "timeout_seconds": timeout,
        "hermes_home": str(profile_home),
        "output": (completed.stdout or "").strip(),
    }
    stderr = (completed.stderr or "").strip()
    if stderr:
        result["stderr"] = stderr
    return json.dumps(result)


ASK_PROFILE_SCHEMA = {
    "name": "ask_profile",
    "description": (
        "Ask another Hermes profile to act as a bounded expert and return its "
        "answer as evidence. This uses a fresh Hermes subprocess with that "
        "profile's HERMES_HOME, rather than switching the current runtime. "
        "Use this when you need a specialist profile's narrower tools, "
        "identity, or operating rules."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "profile": {
                "type": "string",
                "description": (
                    "Target Hermes profile name or configured profile expert "
                    "alias from delegation.profile_experts."
                ),
            },
            "task": {
                "type": "string",
                "description": "The bounded task or question for the expert profile.",
            },
            "context": {
                "type": "string",
                "description": "Optional concise context to pass to the expert profile.",
            },
            "timeout_seconds": {
                "type": "integer",
                "description": (
                    "Optional timeout for this expert run. Capped by "
                    "delegation.profile_max_timeout_seconds."
                ),
            },
        },
        "required": ["profile", "task"],
    },
}


registry.register(
    name="ask_profile",
    toolset="delegation",
    schema=ASK_PROFILE_SCHEMA,
    handler=lambda args, **kw: ask_profile(
        profile=args.get("profile", ""),
        task=args.get("task", ""),
        context=args.get("context"),
        timeout_seconds=args.get("timeout_seconds"),
    ),
    emoji="🧭",
)
