"""Gateway runtime-metadata footer.

Renders a compact footer showing runtime state (model, context %, cwd) and
appends it to the FINAL message of an agent turn when enabled.  Off by default
to keep replies minimal.

Config (``~/.hermes/config.yaml``)::

    display:
      runtime_footer:
        enabled: true                       # off by default
        fields: [model, context_pct, cwd]   # order shown; drop any to hide

Per-platform overrides live under ``display.platforms.<platform>.runtime_footer``.
Users can toggle the global setting with ``/footer on|off`` from both the CLI
and any gateway platform.

The footer is appended to the final response text in ``gateway/run.py`` right
before returning the response to the adapter send path — so it only lands on
the final message a user sees, not on tool-progress updates or streaming
partials.  When streaming is on and the final text has already been delivered
piecemeal, the footer is sent as a separate trailing message via
``send_trailing_footer()``.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Iterable, Optional

_DEFAULT_FIELDS: tuple[str, ...] = ("model", "context_pct", "cwd")
_SEP = " · "


def _home_relative_cwd(cwd: str) -> str:
    """Return *cwd* with ``$HOME`` collapsed to ``~``.  Empty string if unset."""
    if not cwd:
        return ""
    try:
        home = os.path.expanduser("~")
        p = os.path.abspath(cwd)
        if home and (p == home or p.startswith(home + os.sep)):
            return "~" + p[len(home):]
        return p
    except Exception:
        return cwd


def _model_short(model: Optional[str]) -> str:
    """Drop ``vendor/`` prefix for readability (``openai/gpt-5.4`` → ``gpt-5.4``)."""
    if not model:
        return ""
    return model.rsplit("/", 1)[-1]


def resolve_footer_config(
    user_config: dict[str, Any] | None,
    platform_key: str | None = None,
) -> dict[str, Any]:
    """Resolve effective runtime-footer config for *platform_key*.

    Merge order (later wins):
        1. Built-in defaults (enabled=False)
        2. ``display.runtime_footer``
        3. ``display.platforms.<platform_key>.runtime_footer``
    """
    resolved = {"enabled": False, "fields": list(_DEFAULT_FIELDS)}
    cfg = (user_config or {}).get("display") or {}

    global_cfg = cfg.get("runtime_footer")
    if isinstance(global_cfg, dict):
        if "enabled" in global_cfg:
            resolved["enabled"] = bool(global_cfg.get("enabled"))
        if isinstance(global_cfg.get("fields"), list) and global_cfg["fields"]:
            resolved["fields"] = [str(f) for f in global_cfg["fields"]]

    if platform_key:
        platforms = cfg.get("platforms") or {}
        plat_cfg = platforms.get(platform_key)
        if isinstance(plat_cfg, dict):
            plat_footer = plat_cfg.get("runtime_footer")
            if isinstance(plat_footer, dict):
                if "enabled" in plat_footer:
                    resolved["enabled"] = bool(plat_footer.get("enabled"))
                if isinstance(plat_footer.get("fields"), list) and plat_footer["fields"]:
                    resolved["fields"] = [str(f) for f in plat_footer["fields"]]

    return resolved


def _parse_json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}
    return {}


def _count_web_results(payload: dict[str, Any]) -> Optional[int]:
    data = payload.get("data")
    if isinstance(data, dict):
        for key in ("web", "results"):
            results = data.get(key)
            if isinstance(results, list):
                return len(results)
    results = payload.get("results")
    if isinstance(results, list):
        return len(results)
    web = payload.get("web")
    if isinstance(web, list):
        return len(web)
    return None


def _tool_call_metadata(messages: Iterable[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    meta: dict[str, dict[str, Any]] = {}
    for msg in messages or []:
        if not isinstance(msg, dict):
            continue
        for tc in msg.get("tool_calls") or []:
            if not isinstance(tc, dict):
                continue
            fn = tc.get("function") or {}
            if not isinstance(fn, dict):
                continue
            call_id = tc.get("id")
            if not call_id:
                continue
            meta[str(call_id)] = {
                "name": fn.get("name") or tc.get("name") or "",
                "args": _parse_json_object(fn.get("arguments")),
            }
    return meta


def _codex_item_to_search_trace(item: dict[str, Any]) -> Optional[dict[str, Any]]:
    if not isinstance(item, dict):
        return None
    action = item.get("action") if isinstance(item.get("action"), dict) else {}
    action_type = str(action.get("type") or item.get("type") or "")
    if action_type in {"open_page", "find_in_page"}:
        url = action.get("url") or action.get("page_url")
        if not url:
            return None
        return {
            "kind": "extract",
            "source": "provider_native",
            "provider": "codex",
            "tool": "web_extract",
            "urls": [str(url)],
            "status": item.get("status") or "completed",
        }
    if action_type and action_type not in {"search", "web_search_call"}:
        return None
    queries = action.get("queries") if isinstance(action.get("queries"), list) else []
    query = action.get("query") or item.get("query") or (queries[0] if queries else "")
    if query and query not in queries:
        queries = [query, *queries]
    if not query and not queries:
        return None
    return {
        "kind": "search",
        "source": "provider_native",
        "provider": "codex",
        "tool": "web_search",
        "query": str(query or queries[0]),
        "queries": [str(q) for q in queries if q],
        "status": item.get("status") or "completed",
    }


def extract_search_traces_from_messages(messages: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Extract normalized search traces from the current user turn.

    The normalized shape lets the gateway render one UX for provider-native
    search (Codex Responses ``web_search_call``) and Hermes web tools
    (``web_search`` / ``web_extract``) without exposing provider-specific raw
    metadata in the final response.

    Gateway histories may include previous turns, so by default we scope traces
    to messages after the latest user message. This matches normal tool-progress
    UX: the footer describes what happened for *this* reply, not every search in
    the resumed session.
    """
    message_list = [m for m in (messages or []) if isinstance(m, dict)]
    last_user_idx = max(
        (idx for idx, msg in enumerate(message_list) if msg.get("role") == "user"),
        default=-1,
    )
    turn_messages = message_list[last_user_idx + 1:] if last_user_idx >= 0 else message_list

    traces: list[dict[str, Any]] = []
    tool_meta = _tool_call_metadata(turn_messages)

    for msg in turn_messages:
        if not isinstance(msg, dict):
            continue

        for item in msg.get("codex_web_search_items") or []:
            trace = _codex_item_to_search_trace(item)
            if trace:
                traces.append(trace)

        if msg.get("role") != "tool":
            continue
        tool_name = str(msg.get("name") or "")
        if tool_name not in {"web_search", "web_extract", "browser_navigate", "browser_open"}:
            continue

        call_meta = tool_meta.get(str(msg.get("tool_call_id") or ""), {})
        args = call_meta.get("args") or {}
        payload = _parse_json_object(msg.get("content"))
        result_count = _count_web_results(payload)
        if tool_name == "web_search":
            query = str(args.get("query") or "")
            trace = {
                "kind": "search",
                "source": "hermes_tool",
                "provider": str(payload.get("backend") or args.get("backend") or "web"),
                "tool": "web_search",
                "query": query,
                "queries": [query] if query else [],
                "status": "completed" if payload.get("success", True) else "failed",
            }
            if result_count is not None:
                trace["result_count"] = result_count
            traces.append(trace)
        elif tool_name == "web_extract":
            urls = args.get("urls") if isinstance(args.get("urls"), list) else []
            trace = {
                "kind": "extract",
                "source": "hermes_tool",
                "provider": str(payload.get("backend") or args.get("backend") or "web"),
                "tool": "web_extract",
                "urls": [str(u) for u in urls],
                "result_count": result_count or 0,
                "status": "completed" if payload.get("success", True) else "failed",
            }
            traces.append(trace)
        else:
            url = str(args.get("url") or "")
            traces.append({
                "kind": "browse",
                "source": "hermes_tool",
                "provider": "browser",
                "tool": tool_name,
                "urls": [url] if url else [],
                "status": "completed" if payload.get("success", True) else "failed",
            })

    return traces


def _display_provider(trace: dict[str, Any]) -> str:
    provider = str(trace.get("provider") or trace.get("source") or "web")
    aliases = {"codex": "Codex", "web": "Web", "firecrawl": "Firecrawl", "tavily": "Tavily", "exa": "Exa", "parallel": "Parallel", "searxng": "SearXNG", "browser": "Browser"}
    return aliases.get(provider.lower(), provider[:1].upper() + provider[1:])


def _shorten(value: str, limit: int = 80) -> str:
    value = " ".join(str(value or "").split())
    return value if len(value) <= limit else value[: limit - 1].rstrip() + "…"


def format_search_traces(search_traces: Iterable[dict[str, Any]] | None) -> str:
    traces = [t for t in (search_traces or []) if isinstance(t, dict)]
    if not traces:
        return ""
    trace = traces[-1]
    provider = _display_provider(trace)
    kind = trace.get("kind") or "search"
    if kind == "extract":
        count = trace.get("result_count")
        suffix = f" ({count} pages)" if isinstance(count, int) and count >= 0 else ""
        return f"🌐 {provider} extract{suffix}"
    if kind == "browse":
        urls = trace.get("urls") if isinstance(trace.get("urls"), list) else []
        target = _shorten(urls[0]) if urls else "page"
        return f"🌐 Browser: {target}"

    query = trace.get("query") or ""
    label = f"🔎 {provider} search"
    if query:
        label += f": {_shorten(str(query))}"
    count = trace.get("result_count")
    if isinstance(count, int):
        label += f" ({count} result{'s' if count != 1 else ''})"
    if len(traces) > 1:
        label += f" +{len(traces) - 1} more"
    return label


def format_runtime_footer(
    *,
    model: Optional[str],
    context_tokens: int,
    context_length: Optional[int],
    cwd: Optional[str] = None,
    fields: Iterable[str] = _DEFAULT_FIELDS,
    search_traces: Iterable[dict[str, Any]] | None = None,
) -> str:
    """Render the footer line, or return "" if no fields have data.

    Fields are skipped silently when their underlying data is missing — a
    partially-populated footer is better than a line with ``?%`` or empty slots.
    """
    parts: list[str] = []
    for field in fields:
        if field == "model":
            m = _model_short(model)
            if m:
                parts.append(m)
        elif field == "context_pct":
            if context_length and context_length > 0 and context_tokens >= 0:
                pct = max(0, min(100, round((context_tokens / context_length) * 100)))
                parts.append(f"{pct}%")
        elif field == "cwd":
            rel = _home_relative_cwd(cwd or os.environ.get("TERMINAL_CWD", ""))
            if rel:
                parts.append(rel)
        elif field == "search":
            rendered = format_search_traces(search_traces)
            if rendered:
                parts.append(rendered)
        # Unknown field names are silently ignored.

    if not parts:
        return ""
    return _SEP.join(parts)


def build_footer_line(
    *,
    user_config: dict[str, Any] | None,
    platform_key: str | None,
    model: Optional[str],
    context_tokens: int,
    context_length: Optional[int],
    cwd: Optional[str] = None,
    search_traces: Iterable[dict[str, Any]] | None = None,
    messages: Iterable[dict[str, Any]] | None = None,
) -> str:
    """Top-level entry point used by gateway/run.py.

    Returns the footer text (empty string when disabled or no data).  Callers
    append this to the final response themselves, preserving a single blank
    line of separation.
    """
    cfg = resolve_footer_config(user_config, platform_key)
    if not cfg.get("enabled"):
        return ""
    traces = list(search_traces or [])
    if not traces and messages:
        traces = extract_search_traces_from_messages(messages)
    return format_runtime_footer(
        model=model,
        context_tokens=context_tokens,
        context_length=context_length,
        cwd=cwd,
        fields=cfg.get("fields") or _DEFAULT_FIELDS,
        search_traces=traces,
    )
