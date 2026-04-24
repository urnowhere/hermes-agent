"""Model Observability Plugin — evidence-based model tracking.

Subscribes to the post_api_request hook that fires on every LLM API call
(parent agent + all subagents) and writes a structured JSONL record to
~/.hermes/logs/model_usage.jsonl.

Each record answers: WHICH model ACTUALLY answered, not which model was
requested. This is the evidence layer that lets us verify auto-router
selections, confirm explicit model overrides actually took effect, and
audit delegation cost/quality over time.

Record schema (one JSON object per line):
    ts             — ISO 8601 UTC timestamp
    session_id     — parent session ID
    task_id        — delegated task ID (None for parent calls)
    agent_type     — "parent" | "subagent"
    model_request  — model the agent was configured with (what we asked for)
    model_response — model field echoed back in API response (what answered)
    provider       — provider the agent was configured with
    api_mode       — e.g. "chat_completions", "anthropic_messages"
    api_call       — which API call within this agent's lifetime (1-indexed)
    duration_s     — wall clock duration of this single API call
    finish_reason  — stop / tool_calls / length / etc.
    tokens_in      — prompt token count
    tokens_out     — completion token count
    assistant_chars — length of assistant text output
    tool_calls     — number of tool calls in this response
    platform       — cli / telegram / discord / etc.
    match          — True if model_request == model_response (normalized)

Query with: python3 ~/.hermes/scripts/model_usage.py
"""

from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Resolve log path once. ~/.hermes/logs/ is the established convention.
LOG_PATH = Path(os.path.expanduser("~/.hermes/logs/model_usage.jsonl"))

# Thread lock for concurrent subagent writes.
_write_lock = threading.Lock()


def _normalize_model(model: Any) -> str:
    """Lowercase + strip for case-insensitive comparison."""
    if not model:
        return ""
    return str(model).strip().lower()


def _models_match(req: Any, resp: Any) -> bool:
    """True if the requested model and the response model are effectively the same.

    Two cases we treat as a match:
    1. Exact equality after normalize (the common case).
    2. The response model is a date-versioned alias of the requested model —
       e.g. "anthropic/claude-sonnet-4.6" vs "anthropic/claude-4.6-sonnet-20260217",
       or "openai/gpt-5.1-codex" vs "openai/gpt-5.1-codex-20251113".
       Detection heuristic: the response model starts with the requested model
       string (after stripping any provider prefix), OR the requested model
       string appears as a substring of the response model after the last '/'.

    Intentionally NOT a match:
    - openrouter/auto → <anything>  (auto-router resolution is real signal)
    - genuinely different models
    """
    r = _normalize_model(req)
    s = _normalize_model(resp)

    # Empty/unknown models are never a meaningful match.
    if not r or not s:
        return False

    # Never treat auto-router as a match — it's always a resolution.
    if r in ("openrouter/auto", "auto"):
        return False

    if r == s:
        return True

    # Strip provider prefix (everything after the last '/') for alias comparison.
    def _bare(m: str) -> str:
        return m.rsplit("/", 1)[-1] if "/" in m else m

    r_bare = _bare(r)
    s_bare = _bare(s)

    import re

    # Date-suffix heuristic: response bare name starts with request bare name
    # and the extra characters look like a date suffix (-YYYYMMDD or -YYYYMMDD-...).
    if s_bare.startswith(r_bare) and len(s_bare) > len(r_bare):
        suffix = s_bare[len(r_bare):]
        if re.match(r"^-\d{6,}", suffix):
            return True

    # Reverse: request has a date suffix, response is the base name.
    if r_bare.startswith(s_bare) and len(r_bare) > len(s_bare):
        suffix = r_bare[len(s_bare):]
        if re.match(r"^-\d{6,}", suffix):
            return True

    # Token-set heuristic: handles Anthropic's name reordering
    # e.g. "claude-sonnet-4.6" vs "claude-4.6-sonnet-20260217"
    # Strip trailing date token (8+ digit pure number) and compare token sets.
    def _non_date_tokens(name: str) -> frozenset:
        parts = re.split(r"[-.]", name)
        return frozenset(p for p in parts if p and not re.fullmatch(r"\d{6,}", p))

    if _non_date_tokens(r_bare) == _non_date_tokens(s_bare) and _non_date_tokens(r_bare):
        return True

    return False


def _extract_tokens(usage: Any) -> tuple[int, int]:
    """Pull prompt/completion tokens from the usage dict robustly.

    The hook passes `usage` as a dict (already summarized from the response).
    Common keys: prompt_tokens, completion_tokens, input_tokens, output_tokens.
    """
    if not isinstance(usage, dict):
        return 0, 0
    tokens_in = (
        usage.get("prompt_tokens")
        or usage.get("input_tokens")
        or 0
    )
    tokens_out = (
        usage.get("completion_tokens")
        or usage.get("output_tokens")
        or 0
    )
    try:
        return int(tokens_in), int(tokens_out)
    except (ValueError, TypeError):
        return 0, 0


def _on_post_api_request(**kwargs: Any) -> None:
    """Write a JSONL record for one LLM API call.

    All kwargs come from run_agent.py's _invoke_hook("post_api_request", ...)
    at line ~10882. This hook fires for parent AND subagent calls.
    """
    try:
        task_id = kwargs.get("task_id") or None
        model_request = kwargs.get("model") or ""
        model_response = kwargs.get("response_model") or ""
        provider = kwargs.get("provider") or ""

        tokens_in, tokens_out = _extract_tokens(kwargs.get("usage"))

        record = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "session_id": kwargs.get("session_id") or "",
            "task_id": task_id,
            "agent_type": "subagent" if task_id else "parent",
            "model_request": model_request,
            "model_response": model_response,
            "provider": provider,
            "api_mode": kwargs.get("api_mode") or "",
            "api_call": kwargs.get("api_call_count") or 0,
            "duration_s": round(float(kwargs.get("api_duration") or 0.0), 3),
            "finish_reason": kwargs.get("finish_reason") or "",
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
            "assistant_chars": int(kwargs.get("assistant_content_chars") or 0),
            "tool_calls": int(kwargs.get("assistant_tool_call_count") or 0),
            "platform": kwargs.get("platform") or "",
            "match": _models_match(model_request, model_response),
        }

        line = json.dumps(record, ensure_ascii=False)

        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with _write_lock:
            with open(LOG_PATH, "a", encoding="utf-8") as f:
                f.write(line + "\n")
    except Exception as exc:  # Never break the agent loop for logging
        logger.debug("model_observability write failed: %s", exc)


def _on_session_start(**kwargs: Any) -> None:
    """Write a session boundary marker so logs are groupable."""
    try:
        record = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "event": "session_start",
            "session_id": kwargs.get("session_id") or "",
            "platform": kwargs.get("platform") or "",
            "model": kwargs.get("model") or "",
            "provider": kwargs.get("provider") or "",
        }
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with _write_lock:
            with open(LOG_PATH, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception as exc:
        logger.debug("model_observability session_start write failed: %s", exc)


def register(ctx) -> None:
    """Entry point called by the plugin manager at load time."""
    ctx.register_hook("post_api_request", _on_post_api_request)
    ctx.register_hook("on_session_start", _on_session_start)
    logger.info("model_observability plugin registered (log: %s)", LOG_PATH)
