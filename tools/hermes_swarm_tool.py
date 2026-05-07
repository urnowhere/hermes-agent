"""hermes_swarm — parallel sub-agent fan-out, in-process, no external deps.

This is the **default** swarm primitive.  It runs entirely inside the current
Hermes process by reusing :func:`tools.delegate_tool.delegate_task` as the
worker engine.  No Telegram, no manager bot, no roster — just decompose,
fan out, aggregate, return.

Why this exists alongside ``telegram_orchestrate_swarm``:

* The Telegram variant is the visible / multi-bot flavour: each subtask is
  bound to a specific child bot, status is streamed to a report chat as that
  bot, and the user sees real Telegram identities working in parallel.  It
  requires manager-bot setup.
* This tool is the zero-config flavour.  The agent reaches for it for any
  "research X across N angles" / "draft N variants" / "verify X via M
  independent checks" request, regardless of whether a fleet exists.

Both tools share the same orchestration patterns drawn from multi-agent
scaling research (Moonshot's published PARL methodology, hierarchical RL,
the Options framework — see ``skills/agents/swarm-orchestration/SKILL.md``
for citations):

1. **Critical Path metric** — wall-clock per parallel stage = max(workers),
   not sum.  Spawning more workers only helps if it shortens the longest
   chain.  Surfaced in the result as ``critical_path_seconds`` so the
   leader can self-tune.
2. **Anti-serialization guard.**  A "fan-out" call with a single subtask is
   rejected — fan-out of one is just a delegation, and the prompt explicitly
   asks for parallel work.
3. **Worker count sanity.**  Subtask count is capped at
   :data:`MAX_SUBTASKS` (16); for production tasks 3–5 is the sweet spot
   per multi-agent scaling research.
4. **Distilled returns.**  Each worker is a leaf delegate (cannot recurse),
   so its full reasoning trace stays in its own context; only the final
   answer comes back.  Bounded local context per worker.
"""

from __future__ import annotations

import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Callable, Dict, List, Optional

from tools.registry import registry, tool_error, tool_result

logger = logging.getLogger(__name__)

MAX_SUBTASKS = 16
MIN_SUBTASKS_FOR_FANOUT = 2
DEFAULT_PER_TASK_TIMEOUT_S = 600.0
DEFAULT_MAX_PARALLEL = 8


def _resolve_delegate() -> Callable[..., str]:
    """Lazy import — keeps module import lightweight."""
    from tools.delegate_tool import delegate_task

    return delegate_task


def hermes_swarm(
    objective: str,
    subtasks: List[Dict[str, Any]],
    max_parallel: int = DEFAULT_MAX_PARALLEL,
    per_task_timeout_s: float = DEFAULT_PER_TASK_TIMEOUT_S,
    parent_agent: Any = None,
    delegate_fn: Optional[Callable[..., str]] = None,
    **_: Any,
) -> str:
    """Fan *subtasks* across in-process subagents and return aggregated results.

    *objective* is the user-facing goal; it's woven into each worker's context
    so they ground their angle in the bigger picture.  Each entry in
    *subtasks* is::

        {
            "goal":     "...",            # required
            "persona":  "...",            # optional system-prompt addendum
            "context":  "...",            # optional extra grounding
            "toolsets": ["web", "file"],  # optional whitelist
        }
    """
    if not isinstance(objective, str) or not objective.strip():
        return tool_error("objective must be a non-empty string", code="bad_request")
    if not isinstance(subtasks, list) or not subtasks:
        return tool_error("subtasks must be a non-empty list", code="bad_request")
    if len(subtasks) < MIN_SUBTASKS_FOR_FANOUT:
        return tool_error(
            f"hermes_swarm requires at least {MIN_SUBTASKS_FOR_FANOUT} subtasks "
            "for fan-out — for a single task, call delegate_task directly.",
            code="anti_serialization",
        )
    if len(subtasks) > MAX_SUBTASKS:
        return tool_error(
            f"hermes_swarm capped at {MAX_SUBTASKS} subtasks per call (got "
            f"{len(subtasks)}).  Multi-agent research shows 3-5 is the production "
            f"sweet spot; coordination overhead dominates beyond that.",
            code="too_many_subtasks",
        )
    bindings = _validate_subtasks(subtasks)
    if isinstance(bindings, str):
        return bindings  # validation error JSON

    delegate = delegate_fn or _resolve_delegate()
    parallel = max(1, min(int(max_parallel or DEFAULT_MAX_PARALLEL), len(bindings)))
    timeout = float(per_task_timeout_s or DEFAULT_PER_TASK_TIMEOUT_S)

    stage_started_at = time.monotonic()
    results: List[Dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=parallel) as ex:
        futures = {
            ex.submit(
                _run_one,
                binding=b,
                objective=objective,
                delegate_fn=delegate,
                parent_agent=parent_agent,
            ): b
            for b in bindings
        }
        for fut in as_completed(futures):
            b = futures[fut]
            try:
                result = fut.result(timeout=timeout + 5)
            except Exception as e:  # pragma: no cover - delegate-side failure
                result = {
                    "goal": b["goal"],
                    "persona": b.get("persona", ""),
                    "response": "",
                    "duration_seconds": 0.0,
                    "error": f"{type(e).__name__}: {e}",
                }
            results.append(result)

    # Critical Path metric — wall-clock per stage = max(workers).  In a
    # single-stage fan-out like ours the critical path is just the slowest
    # worker; we keep it as a list so multi-stage callers can append.
    durations = [r.get("duration_seconds", 0.0) for r in results]
    critical_path = max(durations) if durations else 0.0
    total_serial = sum(durations)
    speedup = (total_serial / critical_path) if critical_path > 0 else 1.0

    payload = {
        "success": True,
        "objective": objective,
        "results": results,
        "metrics": {
            "workers": len(results),
            "failures": sum(1 for r in results if r.get("error")),
            "critical_path_seconds": round(critical_path, 3),
            "total_serial_seconds": round(total_serial, 3),
            "wall_clock_seconds": round(time.monotonic() - stage_started_at, 3),
            "parallel_speedup": round(speedup, 2),
        },
        "summary": _format_summary(objective, results),
    }
    return json.dumps(payload, ensure_ascii=False)


# ── internals ─────────────────────────────────────────────────────────


def _validate_subtasks(subtasks: List[Dict[str, Any]]):
    bindings: List[Dict[str, Any]] = []
    for i, raw in enumerate(subtasks):
        if not isinstance(raw, dict):
            return tool_error(
                f"subtask #{i} must be an object, got {type(raw).__name__}",
                code="bad_request",
            )
        goal = str(raw.get("goal") or "").strip()
        if not goal:
            return tool_error(f"subtask #{i} is missing 'goal'", code="bad_request")
        bindings.append(
            {
                "goal": goal,
                "persona": str(raw.get("persona") or ""),
                "context": str(raw.get("context") or ""),
                "toolsets": list(raw.get("toolsets") or []) or None,
            }
        )
    return bindings


def _run_one(
    *,
    binding: Dict[str, Any],
    objective: str,
    delegate_fn: Callable[..., str],
    parent_agent: Any,
) -> Dict[str, Any]:
    start = time.monotonic()
    persona = binding.get("persona") or ""
    context_parts = [f"Swarm objective: {objective}"]
    if persona:
        context_parts.append(f"Your persona for this task: {persona}")
    if binding.get("context"):
        context_parts.append(str(binding["context"]))
    full_context = "\n\n".join(context_parts)
    try:
        response = delegate_fn(
            goal=binding["goal"],
            context=full_context,
            toolsets=binding.get("toolsets"),
            role="leaf",
            parent_agent=parent_agent,
        )
    except Exception as e:
        return {
            "goal": binding["goal"],
            "persona": persona,
            "response": "",
            "duration_seconds": time.monotonic() - start,
            "error": f"{type(e).__name__}: {e}",
        }
    return {
        "goal": binding["goal"],
        "persona": persona,
        "response": str(response),
        "duration_seconds": time.monotonic() - start,
    }


def _format_summary(objective: str, results: List[Dict[str, Any]]) -> str:
    successes = [r for r in results if not r.get("error")]
    failures = [r for r in results if r.get("error")]
    lines = [
        f"Swarm objective: {objective}",
        "",
        f"Workers: {len(results)}  ·  ok: {len(successes)}  ·  failed: {len(failures)}",
        "",
    ]
    for r in results:
        header = "—"
        if r.get("persona"):
            header += f" {r['persona'][:60]}"
        header += f"  [{r.get('duration_seconds', 0):.1f}s]"
        lines.append(header)
        body = r.get("error") or _truncate(r.get("response", ""), 600)
        lines.append(body)
        lines.append("")
    return "\n".join(lines).rstrip()


def _truncate(s: str, n: int) -> str:
    s = s or ""
    if len(s) <= n:
        return s
    return s[: n - 1].rstrip() + "…"


# ── Schema + registration ────────────────────────────────────────────


HERMES_SWARM_SCHEMA = {
    "type": "function",
    "function": {
        "name": "hermes_swarm",
        "description": (
            "Parallel sub-agent fan-out, in-process, zero config.  YOU are "
            "the leader — decompose the user's request into 2–8 atomic "
            "subtasks (one per persona/angle/check), then call this tool "
            "ONCE with all of them.  Each subtask runs as an isolated "
            "Hermes subagent in parallel; results come back as structured "
            "data including critical-path latency for you to synthesise.  "
            "PREFER THIS over delegate_task whenever the user's request "
            "decomposes into independent angles (research across N facets, "
            "draft N variants, verify a claim via M independent checks, "
            "monitor N feeds).  DO NOT use it for tightly-coupled "
            "sequential work where each step depends on the previous one — "
            "use delegate_task or solve inline.  Do NOT call with fewer "
            "than 2 subtasks; the tool will reject single-subtask plans.  "
            "For a Telegram-visible variant where each subtask runs as a "
            "named child bot, use telegram_orchestrate_swarm (requires the "
            "manager bot setup at `hermes fleet setup`)."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "objective": {
                    "type": "string",
                    "description": "Overall goal (1–3 sentences).  Each worker sees this for grounding.",
                },
                "subtasks": {
                    "type": "array",
                    "minItems": 2,
                    "maxItems": MAX_SUBTASKS,
                    "description": (
                        "List of 2–8 atomic subtasks.  Aim for similar work-sizes per task "
                        "(critical-path optimisation: balanced branches minimise wall-clock)."
                    ),
                    "items": {
                        "type": "object",
                        "properties": {
                            "goal": {"type": "string"},
                            "persona": {
                                "type": "string",
                                "description": "Free-text role/specialist hint, e.g. 'legal analyst', 'skeptic verifier'.",
                            },
                            "context": {"type": "string"},
                            "toolsets": {
                                "type": "array",
                                "items": {"type": "string"},
                            },
                        },
                        "required": ["goal"],
                    },
                },
                "max_parallel": {
                    "type": "integer",
                    "default": DEFAULT_MAX_PARALLEL,
                    "description": "Max concurrent workers (default 8).",
                },
                "per_task_timeout_s": {
                    "type": "number",
                    "default": DEFAULT_PER_TASK_TIMEOUT_S,
                    "description": "Per-subtask timeout in seconds (default 600).",
                },
            },
            "required": ["objective", "subtasks"],
        },
    },
}


registry.register(
    name="hermes_swarm",
    toolset="delegate",
    schema=HERMES_SWARM_SCHEMA,
    handler=lambda args, **kw: hermes_swarm(
        **args, parent_agent=kw.get("parent_agent")
    ),
    emoji="🌊",
    description=HERMES_SWARM_SCHEMA["function"]["description"],
)
