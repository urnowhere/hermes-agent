#!/usr/bin/env python3
"""
refresh_openrouter_models.py

Weekly script for keeping the subagent-model-routing skill current.

Hits the OpenRouter /api/v1/models endpoint, compares the live catalog against
your configured whitelists, and produces a read-only advisory digest for review.

USAGE — run as a Hermes cron job script:

    cronjob(
        action="create",
        name="OpenRouter Model Refresh",
        script="refresh_openrouter_models.py",       # path relative to ~/.hermes/scripts/
        schedule="0 11 * * 0",                        # every Sunday at 11 AM
        model={"model": "openrouter/auto", "provider": "openrouter"},
        deliver="origin",
        prompt=CRON_PROMPT_TEMPLATE,                  # see bottom of this file
    )

The script's stdout is injected into the cron prompt as context. The cron agent
reads the output and produces a digest for the operator. It does NOT write any
files — all changes require explicit operator approval.

SETUP:
    1. Copy this file to ~/.hermes/scripts/refresh_openrouter_models.py
    2. Customize the WHITELISTS dict below for your use case
    3. Create the cron job (see usage above)
    4. Set OPENROUTER_API_KEY in your environment

REQUIREMENTS:
    - Python 3.10+ (stdlib only, no dependencies)
    - OPENROUTER_API_KEY environment variable
"""

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

# ---------------------------------------------------------------------------
# WHITELISTS — CUSTOMIZE THESE FOR YOUR USE CASE
#
# Define which models are allowed in each routing tier. The tier names and
# model selections here are examples — rename, add, or remove tiers to match
# your operational needs.
#
# Keep these in sync with:
#   - Your subagent-model-routing skill's whitelist/tier tables
#   - Any account-level OpenRouter whitelist settings you've configured
#
# When the cron reports changes and you approve them, update BOTH this dict
# AND the skill's tier tables atomically in the same session.
# ---------------------------------------------------------------------------

WHITELISTS: dict[str, list[str]] = {
    # FULL: Top-tier models available — orchestrator-level, synthesis, judgment
    "full": [
        "google/gemini-2.5-flash",
        "x-ai/grok-4.1-fast",
        "openai/gpt-5-nano",
        "meta-llama/llama-4-maverick",
        "deepseek/deepseek-chat-v3.1",
        "qwen/qwen3-coder-flash",
        "mistralai/devstral-small",
        "anthropic/claude-haiku-4-5",
        "openai/o4-mini",
        "google/gemini-2.5-pro",
        "x-ai/grok-code-fast-1",
        "openai/gpt-5.1-codex-mini",
        "deepseek/deepseek-r1-0528",
        "qwen/qwq-32b",
        "anthropic/claude-opus-4-7",
        "anthropic/claude-sonnet-4-6",
        "x-ai/grok-4.20",
        "openai/o3",
    ],
    # STANDARD: Regular delegation — business ops, analysis, general tasks
    "standard": [
        "google/gemini-2.5-flash",
        "x-ai/grok-4.1-fast",
        "openai/gpt-5-nano",
        "meta-llama/llama-4-maverick",
        "deepseek/deepseek-chat-v3.1",
        "qwen/qwen3-coder-flash",
        "mistralai/devstral-small",
        "anthropic/claude-haiku-4-5",
        "openai/o4-mini",
        "x-ai/grok-code-fast-1",
        "openai/gpt-5.1-codex-mini",
        "deepseek/deepseek-r1-0528",
        "qwen/qwq-32b",
    ],
    # CODING: Code writing, review, and modification tasks only
    "coding": [
        "x-ai/grok-code-fast-1",
        "qwen/qwen3-coder-flash",
        "mistralai/devstral-small",
        "openai/gpt-5.1-codex-mini",
        "openai/gpt-5.1-codex",
        "deepseek/deepseek-r1-0528",
        "anthropic/claude-sonnet-4-6",
        "google/gemini-2.5-pro",
        "openai/o3",
    ],
    # BUDGET: Cron jobs, automated extraction, simple parsing
    # Account-level default — workhorse only, nothing expensive
    "budget": [
        "google/gemini-2.5-flash",
        "google/gemini-2.5-flash-lite",
        "x-ai/grok-4.1-fast",
        "openai/gpt-5-nano",
        "meta-llama/llama-4-maverick",
        "deepseek/deepseek-chat-v3.1",
        "qwen/qwen3-coder-flash",
        "mistralai/devstral-small",
    ],
}

# ---------------------------------------------------------------------------
# TRACKED PROVIDERS — new model discovery scope
# Only models from these providers will appear in the "new models" section.
# Add or remove providers to match what you care about.
# ---------------------------------------------------------------------------

TRACKED_PROVIDERS: set[str] = {
    "anthropic",
    "google",
    "openai",
    "x-ai",
    "mistralai",
    "deepseek",
    "qwen",
    "meta-llama",
}

# PRICE_CHANGE_THRESHOLD: reserved for a future pricing-delta feature.
# The script currently reports a live pricing snapshot but does not compare
# against a baseline (previous run or skill file). Implementing delta tracking
# would require persisting previous prices to disk between runs. Not yet
# implemented — when added, use this threshold (20%) to filter noise.

# ---------------------------------------------------------------------------
# Implementation — no customization needed below this line
# ---------------------------------------------------------------------------

# Flatten all unique models across all whitelists
ALL_WHITELISTED: set[str] = set()
for _models in WHITELISTS.values():
    ALL_WHITELISTED.update(_models)


def fetch_models(api_key: str, order: str | None = None) -> list[dict]:
    """Fetch model list from OpenRouter /api/v1/models."""
    url = "https://openrouter.ai/api/v1/models"
    if order:
        url += "?" + urllib.parse.urlencode({"order": order})
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {api_key}",
            "HTTP-Referer": "https://hermes-agent.local",
            "X-Title": "Hermes Model Refresh",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"OpenRouter API returned {e.code}: {e.reason}") from e
    except Exception as e:
        raise RuntimeError(f"Failed to fetch models: {e}") from e
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise RuntimeError(
            f"OpenRouter returned malformed JSON (offset {e.pos}): {e.msg}\n"
            f"Response snippet: {raw[:200]!r}"
        ) from e
    return data.get("data", [])


def parse_price(pricing: dict, key: str) -> float | None:
    """Parse a pricing field to float (price per token)."""
    val = pricing.get(key)
    if val is None:
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


def price_per_million(price_per_token: float | None) -> str:
    if price_per_token is None:
        return "unknown"
    return f"${price_per_token * 1_000_000:.2f}"


def fmt_ctx(ctx) -> str:
    c = int(ctx) if ctx else 0
    if not c:
        return "unknown"
    return f"{c // 1000}K" if c >= 1000 else "< 1K"


def main() -> None:
    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        print("ERROR: OPENROUTER_API_KEY not set", file=sys.stderr)
        sys.exit(1)

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    print(f"OpenRouter Model Refresh — {now}")
    print("=" * 60)

    # Fetch with top-weekly ordering: returns full catalog ranked by weekly usage.
    try:
        models = fetch_models(api_key, order="top-weekly")
    except RuntimeError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)

    # Sanity checks — fewer than 50 almost certainly means truncation.
    if len(models) <= 50:
        print(
            f"WARNING: Only {len(models)} models returned — expected >50; "
            "aborting to avoid false MISSING positives.",
            file=sys.stderr,
        )
        sys.exit(1)
    if len(models) < 300:
        print(
            f"WARNING: Only {len(models)} models returned — OpenRouter usually has 300+; "
            "possible truncation.",
            file=sys.stderr,
        )

    live_ids = {m["id"] for m in models if "id" in m}
    live_by_id = {m["id"]: m for m in models if "id" in m}

    # Precompute per-model whitelist membership for O(1) lookup.
    whitelist_by_model: dict[str, list[str]] = {}
    for wl_name, wl_models in WHITELISTS.items():
        for mid in wl_models:
            whitelist_by_model.setdefault(mid, []).append(wl_name)

    print(f"Total models available on OpenRouter: {len(models)}")
    print()

    # ------------------------------------------------------------------
    # 1. Missing — whitelisted models no longer in live catalog
    # ------------------------------------------------------------------
    missing = [m for m in ALL_WHITELISTED if m not in live_ids]
    if missing:
        print("⚠️  MISSING — Whitelisted models NOT found in live catalog:")
        for m in sorted(missing):
            in_lists = [k for k, v in WHITELISTS.items() if m in v]
            print(f"  - {m}  [in: {', '.join(in_lists)}]")
    else:
        print("✅ All whitelisted models found in live catalog.")
    print()

    # ------------------------------------------------------------------
    # 2. Pricing snapshot for all whitelisted models
    # ------------------------------------------------------------------
    print("💰 PRICING SNAPSHOT — Current prices for whitelisted models:")
    print(f"  {'Model':<40} {'In ($/Mi)':>12} {'Out ($/Mi)':>12}  {'Context':>10}")
    print(f"  {'-'*40} {'-'*12} {'-'*12}  {'-'*10}")
    for model_id in sorted(ALL_WHITELISTED):
        if model_id not in live_by_id:
            print(f"  {model_id:<40} {'MISSING':>12} {'MISSING':>12}  {'':>10}")
            continue
        m = live_by_id[model_id]
        pricing = m.get("pricing", {})
        p_in = parse_price(pricing, "prompt")
        p_out = parse_price(pricing, "completion")
        ctx = m.get("context_length", 0)
        print(
            f"  {model_id:<40} {price_per_million(p_in):>12} "
            f"{price_per_million(p_out):>12}  {fmt_ctx(ctx):>10}"
        )
    print()

    # ------------------------------------------------------------------
    # 3. New models from tracked providers not yet in any whitelist
    # ------------------------------------------------------------------
    new_models = []
    for m in models:
        mid = m["id"]
        if mid in ALL_WHITELISTED:
            continue
        provider = mid.split("/")[0] if "/" in mid else ""
        if provider not in TRACKED_PROVIDERS:
            continue
        pricing = m.get("pricing", {})
        p_in = parse_price(pricing, "prompt")
        p_out = parse_price(pricing, "completion")
        ctx = m.get("context_length", 0)
        new_models.append((mid, p_in, p_out, ctx))

    if new_models:
        print(f"🆕 NEW MODELS from tracked providers not in any whitelist ({len(new_models)}):")
        print(f"  {'Model':<45} {'In ($/Mi)':>12} {'Out ($/Mi)':>12}  {'Context':>10}")
        print(f"  {'-'*45} {'-'*12} {'-'*12}  {'-'*10}")
        for mid, p_in, p_out, ctx in sorted(new_models, key=lambda x: x[0]):
            print(
                f"  {mid:<45} {price_per_million(p_in):>12} "
                f"{price_per_million(p_out):>12}  {fmt_ctx(ctx):>10}"
            )
    else:
        print("✅ No new models from tracked providers outside whitelists.")
    print()

    # ------------------------------------------------------------------
    # 4. Whitelist membership summary
    # ------------------------------------------------------------------
    print("📋 WHITELIST SUMMARY:")
    for name, models_list in WHITELISTS.items():
        live_count = sum(1 for m in models_list if m in live_ids)
        print(f"  {name}: {live_count}/{len(models_list)} models live")
    print()

    # ------------------------------------------------------------------
    # 5. Top 15 trending models this week
    # ------------------------------------------------------------------
    print("🔥 TOP 15 TRENDING MODELS — Most used on OpenRouter this week:")
    print(
        f"  {'Rank':<5} {'Model':<45} {'In ($/Mi)':>10} {'Out ($/Mi)':>11}"
        f"  {'Context':>8}  In Whitelist?"
    )
    print(f"  {'-'*5} {'-'*45} {'-'*10} {'-'*11}  {'-'*8}  {'-'*13}")
    for rank, m in enumerate(models[:15], 1):
        mid = m["id"]
        pricing = m.get("pricing", {})
        p_in = parse_price(pricing, "prompt")
        p_out = parse_price(pricing, "completion")
        ctx = m.get("context_length", 0)
        in_wl = whitelist_by_model.get(mid, [])
        wl_str = ", ".join(in_wl) if in_wl else "—"
        print(
            f"  #{rank:<4} {mid:<45} {price_per_million(p_in):>10} "
            f"{price_per_million(p_out):>11}  {fmt_ctx(ctx):>8}  {wl_str}"
        )
    print()

    print("=" * 60)
    print("Agent instructions:")
    print("1. Report any MISSING models — they may need replacing.")
    print("2. Highlight compelling NEW models worth adding to a whitelist.")
    print("3. Note significant pricing changes vs. skill documentation.")
    print("4. If whitelist changes are warranted, propose specific edits.")
    print("5. Flag trending models NOT in whitelists that appear cost-competitive.")
    print()
    print("⛔ DO NOT write any files. Present findings only.")
    print("   All changes require operator approval in a subsequent session.")
    print("   When approved: patch BOTH this script's WHITELISTS dict AND")
    print("   the skill's tier tables atomically in the same session.")


# ---------------------------------------------------------------------------
# CRON PROMPT TEMPLATE
#
# Copy this as the `prompt` argument when creating the cron job.
# Customize the approval workflow section to match your preferences.
# ---------------------------------------------------------------------------

CRON_PROMPT_TEMPLATE = """
You have just received a live OpenRouter model catalog report from the refresh
script. Your job:

1. Check for MISSING models — any whitelisted model not found in the live
   catalog needs flagging immediately.
2. Scan NEW MODELS — identify any that would be strong additions to the
   whitelists. Focus on models that are cheaper, faster, or more capable than
   current whitelist members in the same tier. Flag standouts only.
3. Confirm PRICING is consistent with the subagent-model-routing skill. Flag
   any model where the live price differs significantly from the documented price.
4. Produce a concise digest — 3 sections:
   (a) Alerts (missing models, major price changes)
   (b) Recommended whitelist additions or removals with rationale
   (c) All-clear if nothing notable

⛔ DO NOT WRITE ANY FILES. This run is read-only and advisory only.
   Do not patch the skill, modify this script, or update any config.
   Your output is a report to the operator. They will review and approve
   changes in a subsequent session.

When the operator approves changes in that follow-up session, the approving
agent must apply them ATOMICALLY — updating BOTH of the following before
finishing:
  (a) ~/.hermes/scripts/refresh_openrouter_models.py — update the WHITELISTS dict
  (b) ~/.hermes/skills/.../subagent-model-routing/SKILL.md — update tier tables

Both must be updated together. A partial update leaves the system inconsistent.
After both are patched, update the "Last updated" date in the skill header.
""".strip()


if __name__ == "__main__":
    main()
