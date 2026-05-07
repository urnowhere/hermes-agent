---
name: subagent-model-routing
description: Model selection guide for delegate_task and cron jobs. Tiers are LLM decision guides (not API constraints).
version: 3
tags: [delegation, cost, models, routing, auto-router]
---

# Subagent Model Routing

> **Last updated:** 030526 | **Source:** `~/.hermes/scripts/refresh_openrouter_models.py → WHITELISTS`
> If today is >10 days past the date above, verify pricing before high-stakes decisions.

Load this skill every time you use `delegate_task` without an explicit model.

## Tiers

Budget/standard/premium are mutually exclusive. Coding overlaps all.

**PREMIUM** — synthesis, judgment, reviews
`x-ai/grok-4.20` | `x-ai/grok-4.20-multi-agent` | `x-ai/grok-4.3`
`anthropic/claude-opus-4.7` | `anthropic/claude-sonnet-4.6`
`google/gemini-2.5-pro` | `google/gemini-3.1-pro-preview` | `openai/gpt-5.5`

**STANDARD** — ops, analysis, general delegation
`anthropic/claude-haiku-4.5` | `openai/gpt-5.4-mini` | `deepseek/deepseek-v4-pro`
`moonshotai/kimi-k2.6` | `minimax/minimax-m2.7` | `z-ai/glm-5.1`

**BUDGET** — cron jobs, extraction, parsing (account default)
`google/gemini-2.5-flash` | `google/gemini-2.5-flash-lite` | `x-ai/grok-4.1-fast`
`openai/gpt-5-nano` | `deepseek/deepseek-v4-flash`

**CODING** — anything touching code (overlaps permitted)
`x-ai/grok-code-fast-1` | `openai/gpt-5.1-codex-mini` | `openai/gpt-5.1-codex`
`qwen/qwen3-coder-flash`
`deepseek/deepseek-v4-pro` *(also standard)*
`moonshotai/kimi-k2.6` *(also standard)* | `minimax/minimax-m2.7` *(also standard)* | `z-ai/glm-5.1` *(also standard)*
`anthropic/claude-sonnet-4.6` *(also premium)* | `anthropic/claude-opus-4.7` *(also premium)*
`openai/gpt-5.5` *(also premium)*

## Routing Matrix

| Task | Model | Tier |
|------|-------|------|
| Extraction / parsing / summarization | gemini-2.5-flash | budget |
| Web research | gemini-2.5-flash | budget |
| Simple file ops / renames | gpt-5-nano | budget |
| New script / feature | gpt-5.1-codex-mini | coding |
| Refactor / complex integration | gpt-5.1-codex | coding |
| Code review | gemini-2.5-pro + grok-4.20 second opinion | coding + premium |
| Business ops / analysis | claude-haiku-4.5 | standard |
| Reasoning / math | deepseek-v4-pro or gpt-5.4-mini | standard |
| Intelligence synthesis | grok-4.20 | premium ← ONLY valid Grok use |
| Architecture judgment | gpt-5.5 or grok-4.3 | premium |

## Per-Call Model Pinning (030526 — verified working)

Per-task model pinning works on the feat branch (PR #12794). Pass `model` at the task level:

```python
delegate_task(tasks=[
    {"goal": "...", "model": "anthropic/claude-haiku-4.5", "toolsets": [...]},
    {"goal": "...", "model": "anthropic/claude-opus-4.7",  "toolsets": [...]},
])
```

**Verified live (030526):** Three tasks with distinct pins ran on their correct models. The `model_observability` plugin confirmed `match: true` for each pinned task via JSONL evidence.

**Note:** This requires the gateway to run from `hermes-agent-feat/` (feat branch). Upstream main does NOT have `model` in the `delegate_task` signature — pins are silently discarded there. Until PR #12794 merges, `delegation.model` in `config.yaml` is the only routing lever on upstream main.

## Hard Rules

0. **Never second-guess Jordan's model slug.** If Jordan pins a model you don't recognize, load this skill and `openrouter-expert` BEFORE questioning him. Jordan knows his routing environment. The correct response to an unfamiliar slug is to verify it, not to tell him he's wrong. Confirmed live failure (050526): claimed `grok-4.3` didn't exist, used `grok-4` instead — Jordan called it out explicitly. `x-ai/grok-4.3` is a valid premium-tier model.

1. **Grok (ALL variants):** reasoning + intelligence synthesis ONLY. Never code, never general tasks.
2. **Coding tasks:** coding-tier models ONLY. General models invent abstractions instead of following integration requirements.
3. **Adversarial code review:** use a different coding model (different training DNA), not a reasoning model.
4. **Mixed providers in one batch:** always `provider="openrouter"`. Native providers only serve their own models.
5. **Never self-select Premium autonomously** without exhausting cheaper options. Cost difference is 10–40×.
6. **Escalating past Standard when user is present:** offer the tradeoff before acting.
7. **No `openrouter/auto` for cron jobs** where tool execution is mandatory — it can route to a model that fabricates output instead of calling tools. Pin a specific model.
8. **When Jordan names a specific model slug, load this skill first before questioning it.** If the slug isn't in your training data or memory, that does NOT mean it's wrong — it may be a newer model. Check the whitelist here. If still not found, ask Jordan to clarify rather than substituting a different model unilaterally. Second-guessing a user-provided slug without loading this skill first is an error. (Lesson: `grok-4.3` was valid; `grok-4` substitution was wrong.)

## When Not to Delegate

- Task is 3 tool calls or fewer
- Task needs 3+ existing codebase functions/patterns already in your context — write it directly
- Two prior delegations already failed on the same task
- Task requires your session state or ongoing conversation context

## Ephemeral Subagents vs. Persistent Profiles — Do NOT Conflate

`delegate_task` spawns **ephemeral anonymous subagents** — they live for one task, have no memory, no SOUL.md, no accumulated context. They are cheap throwaway workers.

**Persistent profiles** are a fundamentally different concept: a named profile with its own `config.yaml`, `SOUL.md`, memory store, cron schedule, and identity that accumulates context across sessions.

**Common mistake (caught 040526):** Connecting `delegate_task`'s per-task model routing to the design of persistent multi-agent profiles (e.g., a Librarian profile or Ops profile). PR #12794 adds model overrides to ephemeral subagents only. A persistent profile running in the gateway as its own named agent is a completely separate architectural decision that has nothing to do with delegate_task's model parameter.

**Rule:** When discussing multi-agent architecture — which profiles to create, what each specialist accumulates over time — do not reference `delegate_task` or PR #12794 as the mechanism. Those are for ephemeral fan-out only.

## Syntax

```python
# Single task
delegate_task(goal="...", model="anthropic/claude-haiku-4.5", provider="openrouter")

# Batch — mixed models
delegate_task(
    provider="openrouter",
    tasks=[
        {"goal": "implement feature", "model": "openai/gpt-5.1-codex-mini"},
        {"goal": "review output",     "model": "x-ai/grok-4.20"},
    ]
)

# Cron job model pin
cronjob(action="create", model={"model": "google/gemini-2.5-flash", "provider": "openrouter"}, ...)
```

## Provider Rules

- Omit `provider` only when all tasks share the same provider as the parent session
- `provider="openrouter"` for mixed-provider batches or any OpenRouter model
- `provider="anthropic"` for Anthropic-only batches (better latency, prompt caching)
- Per-task `provider` override does NOT exist — top-level `provider` applies to all tasks in a batch

## Escalation Ladder

```
1. Handle yourself       free — no context transfer cost
2. Budget                default starting point
3. Standard              when budget underperforms or task warrants reliability
4. Coding                mandatory for any code writing/modification
5. Premium               review, synthesis, architecture — after cheaper options exhausted
```

## Observability

Every `delegate_task` result includes an `observability` field:
```json
{
  "models_used": {"google/gemini-2.5-flash": 8},
  "models_requested": {"openrouter/auto": 8},
  "api_calls": 8,
  "tokens": {"input": 24312, "output": 1847},
  "auto_router_resolutions": {"openrouter/auto": {"google/gemini-2.5-flash": 8}},
  "override_mismatches": []
}
```

If `override_mismatches` is non-empty → alert Jordan immediately. A model override was silently dropped and the task ran on the wrong model.

## Known Limitations

- No per-task `provider` in batches. Workaround: use `provider="openrouter"` for mixed batches — it serves all providers.
- OpenRouter uses dot notation for slugs: `claude-haiku-4.5` not `claude-haiku-4-5`. Verify slugs via live API before writing any model ID into code, config, or cron.

## Whitelist Maintenance

**Canonical source:** `~/.hermes/scripts/refresh_openrouter_models.py → WHITELISTS`

The refresh cron runs every Sunday 11 AM, delivers a read-only digest, and requires operator approval before any changes are applied. When approving changes, patch **both** the script's `WHITELISTS` dict and this skill's tier tables atomically in the same session. Also copy the updated script to the feat branch — see the MAINTENANCE section inside the script for the exact commands.

**Tier exclusivity is enforced by the script itself.** The `WHITELISTS` dict has a validator that raises at import time if any model appears in more than one of budget/standard/premium. Coding is explicitly exempt. If you add a model, ensure it belongs to exactly one non-coding tier.

**Price delta tracking uses a local cache.** The script writes `~/.hermes/caches/openrouter_prices_last.json` after each run and diffs against it the following week. Section 2b of the report shows changes ≥20% with direction arrows. No external persistence needed — the cache is the baseline.

**OpenRouter response caching (030526):** Default TTL is 300s and is on by default — no config needed. Decision: leave at 300s. Dynamic cron prompts (briefings, intel) never hit the cache because they include fresh dates/content; 300s captures legitimate repeat aux calls within a session. Longer TTLs offer marginal savings but more exposure to the beta feature's edge cases. Do not increase unless cost audits show a clear win.

**Do not embed a `CRON_PROMPT_TEMPLATE` in the refresh script.** The live prompt lives in `jobs.json` (job `6d0271a4d5cb`). A template in the script is a diverging copy with no enforcement mechanism — it goes stale silently and misleads future agents reading the script. The script's job is data collection; the prompt belongs in the scheduler.

> See `references/routing-rationale.md` for case studies, QA history, and operational notes.
