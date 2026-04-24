---
name: subagent-model-routing
description: Model selection guide for delegate_task and cron jobs — use openrouter/auto with a named whitelist. Updated with live OpenRouter pricing.
version: 2
tags: [delegation, cost, models, routing, auto-router]
---

# Subagent Model Routing

> **MAINTENANCE NOTICE**
> The whitelist tables and tier pricing in this skill are maintained by an automated weekly refresh cron job.
> **Canonical source:** `scripts/refresh_openrouter_models.py` (`WHITELISTS` dict)
> **Last updated:** 220426
> **Staleness warning:** If today's date is more than 10 days after the "Last updated" date above, treat the whitelist tables and pricing data as a starting point — verify against live OpenRouter pricing before making high-stakes routing decisions.
> **Changes:** Never edit whitelist/tier sections manually. When changes are approved from the weekly cron report, patch BOTH this skill AND `scripts/refresh_openrouter_models.py → WHITELISTS` atomically in the same session, then update the "Last updated" date above.

Load this skill EVERY TIME you use delegate_task or create a cron job.      

**Load this skill every time you use `delegate_task` and the user has not explicitly specified a model or provider.** The `delegate_task` schema description will prompt you to load it — follow that prompt. Wrong model selection silently degrades output quality or wastes cost with no error message.

## Why This Matters

`delegate_task` supports `model` and `provider` overrides per call and per task in a batch. When omitted, subagents inherit `delegation.model` from `config.yaml` (typically `openrouter/auto`). The auto-router is a reasonable default, but many tasks have strong model requirements:

- **Coding tasks** on general-purpose models consistently produce off-target code — the model invents its own abstractions instead of following integration requirements
- **Simple extraction tasks** on premium models waste 10–50× the cost for no quality benefit
- **Mixed-provider batches** fail silently if the wrong provider is set at the top level

## How to Specify Model in delegate_task

```python
# No override — inherits delegation.model from config
delegate_task(goal="...", toolsets=["file", "terminal"])

# Top-level override — all tasks use this model
delegate_task(
    goal="...",
    model="anthropic/claude-haiku-4-5",
    provider="anthropic",
    toolsets=["file", "terminal"]
)

# Per-task override in batch — mix models by task type
delegate_task(
    model="x-ai/grok-4.1-fast",      # batch default
    provider="openrouter",
    tasks=[
        {"goal": "write the feature", "model": "openai/gpt-5.1-codex-mini"},  # coding specialist
        {"goal": "review the output"},                                          # inherits default
    ]
)
```

## Provider Rules

- **Omit `provider`** when all tasks use models from the same provider as the parent, or when using `openrouter/auto`
- **Set `provider="openrouter"`** for mixed-provider batches or when you want OpenRouter to serve any model string
- **Set `provider="anthropic"`** when pinning to Anthropic models directly (better latency, prompt caching)
- **`provider` is resolved once per `delegate_task` call** and shared across all tasks in the batch — you cannot mix native providers in a single batch

### Mixed-Provider Batches: Use OpenRouter

```python
# WRONG — anthropic doesn't serve grok
delegate_task(provider="anthropic", tasks=[
    {"goal": "...", "model": "anthropic/claude-haiku-4-5"},  # ✅
    {"goal": "...", "model": "x-ai/grok-4.1-fast"},          # ❌ 404
])

# CORRECT — openrouter serves everything
delegate_task(provider="openrouter", tasks=[
    {"goal": "...", "model": "anthropic/claude-haiku-4-5"},
    {"goal": "...", "model": "x-ai/grok-4.1-fast"},
])
```

### OpenRouter-Specific Model Names

`model="auto"` and `model="openrouter/auto"` are OpenRouter meta-routing models — valid **only** with `provider="openrouter"` (or no provider, if `delegation.provider` is openrouter in config). Using `auto` with any other native provider triggers a mismatch warning in the logs and will fail at runtime.

## Routing Decision Matrix

```
Task Type                → Recommended Model              → Tier
──────────────────────────────────────────────────────────────────
Text extraction/parsing  → gemini-2.0-flash-lite          → Budget
Simple file scanning     → gemini-2.5-flash or gpt-5-nano → Budget
Web research             → gemini-2.5-flash                → Budget
Translation / formatting → gemini-2.5-flash or haiku      → Budget
Simple patches/renames   → devstral or gpt-5-nano         → Budget
New script / feature     → gpt-5.1-codex-mini             → Standard
Bulk code changes        → gpt-5.1-codex or devstral      → Standard
Refactoring              → gpt-5.1-codex or claude-haiku  → Standard
Code review              → gemini-2.5-pro or gpt-5.1-codex→ Standard
Architecture / judgment  → o3 or deepseek-r1              → Standard/Premium
Complex reasoning        → o4-mini or deepseek-r1         → Standard
Intelligence synthesis   → grok-4.20                      → Premium  ← ONLY valid Grok use
```

> ⚠️ **JORDAN RULE — GROK IS REASONING/INTELLIGENCE ONLY:**
> Grok (ALL variants: grok-4.20, grok-4.1-fast, grok-code-fast-1) must ONLY be used for
> reasoning and intelligence synthesis tasks. Never use any Grok model for code implementation,
> code review, or general delegation. Violations waste budget and produce off-target results.

## Tier Reference

### Budget — workhorse tasks, cron jobs, extraction
Cost-efficient models for straightforward work. Use for anything that doesn't require deep reasoning or tight code integration.

- `google/gemini-2.5-flash` — best all-round budget model, 1M context
- `google/gemini-2.0-flash-lite-001` — cheapest useful model
- `x-ai/grok-4.1-fast` — fast, large context (2M), good quality
- `openai/gpt-5-nano` — cheapest OpenAI option
- `meta-llama/llama-4-maverick` — open source, big context
- `mistralai/devstral-small` — cheap coding specialist
- `qwen/qwen3-coder-flash` — coding + huge context

### Standard — most delegation tasks
Use when Budget tier produces inconsistent results, or for code tasks.

**General:**
- `anthropic/claude-haiku-4-5` — fast, reliable Anthropic quality
- `openai/o4-mini` — reasoning specialist
- `deepseek/deepseek-r1-0528` — strong open reasoning model
- `google/gemini-2.5-pro` — Google flagship, 1M context

**Coding (use for any task involving code writing or modification):**
- `openai/gpt-5.1-codex-mini` — best cost/quality for code generation
- `x-ai/grok-code-fast-1` — xAI code specialist
- `qwen/qwen3-coder-flash` — code + huge context
- `openai/gpt-5.1-codex` — full Codex for complex integration work

### Premium — reviews and high-stakes judgment
Use for code review, architecture decisions, or when you want a trusted second opinion from a model with no skin in the game.

- `x-ai/grok-4.20` — trusted third-party reviewer, 2M context
- `openai/o3` — deep reasoning
- `anthropic/claude-sonnet-4` — high-quality Anthropic

## Adversarial Code Review: Use a Different Coding Model, Not a Reasoning Model

**Adversarial value comes from different training DNA, not deeper reasoning.**

Reasoning models (o3, o4-mini, grok-4.20) apply extended logical deduction — useful for math proofs, not for catching Python integration anti-patterns or abstraction violations. Code review needs coding muscle: pattern recognition trained on code, not inference chains.

**Correct adversarial pairing:**
- Implementation: `openai/gpt-5.1-codex-mini`
- Adversarial review: `openai/gpt-5.1-codex` — same family, different capacity, different tendencies
- Maximum divergence review: `anthropic/claude-sonnet-4` — completely different training basis from OpenAI Codex; catches what a Codex model is systematically blind to

**Wrong choice for code review:** `grok-4.20`, `o3`, `o4-mini`. These are reasoning specialists; use them for architecture decisions, cost modeling, or logic proofs — not for catching bad abstractions in a codebase.

## Critical: Coding Tasks Need Coding Models

**Never use general-purpose models (Gemini Flash, Grok Fast, Llama) for tasks that involve writing or modifying code.**

General models lack the discipline to follow precise integration requirements — they invent their own abstractions instead of using existing codebase patterns. This is not a quality difference; it is a reliability difference. Two failed delegations at $0.20/Mi costs more than one successful delegation at $1.25/Mi — in tokens, time, and momentum.

**Decision rule for writing code:** If the task requires calling 3+ existing functions from a codebase, or referencing specific file paths, constants, or patterns from an existing monolith — consider writing it directly in the orchestrator instead of delegating. Context transfer cost can exceed delegation benefit.

## When to Delegate

**Handle it yourself (no delegation) when:**
- The task is 3 tool calls or fewer
- You already hold all the context — transferring it to a subagent would cost more than doing it yourself
- Two prior delegations have already failed on the same task — write it yourself at that point
- The task requires your ongoing session state or personality (e.g. replying to the user, maintaining conversation history)

**Delegate when:**
- The task is reasoning-heavy and would flood your context with intermediate data
- It can run in parallel with other independent tasks (batch mode)
- It genuinely needs a different model capability — coding specialist, second opinion, vision, etc.
- The user explicitly requests a specific model or provider

## Escalation Ladder

In autonomous mode (cron jobs, background tasks), never skip steps. Start cheap, escalate only with evidence. When the user is present, offer the tradeoff before escalating — it's their budget.

```
1. Handle it yourself (orchestrator)        free
2. Budget-tier model                         default starting point for delegation
3. Different budget model for perspective    when first result is uncertain or incomplete
4. Standard-tier model                       when budget tier underperforms or task warrants it
5. Coding-tier model                         mandatory for any task involving writing/modifying code
6. Premium-tier model                        trusted second opinion, code review, architecture only
   └── ONLY after above steps fail, or task explicitly requires it
```

**Autonomous mode rule:** Never self-select Premium without exhausting cheaper options first. The cost difference is 10–40×.

**User-present rule:** Before escalating past Standard, say what you have and offer the choice: *"I could get a second opinion from a stronger model — your call."* The user may know context you don't.

## Cost-Effective Patterns

### Many for One
Three Budget-tier calls ≈ one Standard-tier call in cost, but yields three independent perspectives instead of one. Use when you want diverse results or when a single model might miss edge cases.

```python
delegate_task(
    provider="openrouter",
    tasks=[
        {"goal": "...", "model": "google/gemini-2.5-flash"},
        {"goal": "...", "model": "x-ai/grok-4.1-fast"},
        {"goal": "...", "model": "meta-llama/llama-4-maverick"},
    ]
)
```

### Scout Party
Send Budget models to investigate first. Escalate to Standard or Premium only if the scouts can't crack it. If the cheap models return good results, skip the escalation entirely.

```python
# Round 1: scouts
result = delegate_task(goal="...", model="google/gemini-2.5-flash", provider="openrouter")
# Round 2: only if needed
if result_is_insufficient:
    result = delegate_task(goal="...", model="anthropic/claude-haiku-4-5", provider="openrouter")
```

### Orchestrator-Direct
When a task requires 3+ existing functions from a codebase, specific file paths, or tight integration with patterns only you have loaded — write it yourself. Context transfer cost to a subagent often exceeds the delegation benefit. See the *Critical: Coding Tasks Need Coding Models* section for a case study.

## Cron Job Model Pinning

Cron jobs support per-job model pinning via the `model` parameter on `cronjob(action="create")`:

```python
cronjob(
    action="create",
    model={"model": "openrouter/auto", "provider": "openrouter"},
    prompt="...",
    schedule="0 9 * * 6"
)
```

For automated background tasks (email scanning, file parsing, briefings), Budget tier models are usually sufficient. Pin a specific model when the task has known quality requirements.

## Model Observability

`delegate_task` results include two fields that provide ground-truth model routing data without requiring a separate script call:

- **`task_id`** — the child's unique task ID (e.g. `subagent-0-a3f9c12b`)
- **`observability`** — a dict populated from the model usage log at return time:

```json
{
  "models_used": {"google/gemini-2.5-flash": 8},
  "models_requested": {"openrouter/auto": 8},
  "api_calls": 8,
  "tokens": {"input": 24312, "output": 1847},
  "duration_seconds": 18.4,
  "auto_router_resolutions": {
    "openrouter/auto": {"google/gemini-2.5-flash": 8}
  }
}
```

`auto_router_resolutions` is keyed by the **requested** model; the value is a dict of **resolved** models with call counts. `models_used` is ground truth — read from `~/.hermes/logs/model_usage.jsonl` by the `model_observability` plugin.

**If `override_mismatches` is non-empty, proactively alert the user** — a model override was silently dropped and the task ran on the wrong model.

**If `observability` is absent:** both the inline field and the `model_metadata_for_task.py` fallback script depend on the same observability plugin. Without the plugin, neither method works. Note this explicitly rather than silently omitting the metadata.

The `model_observability` plugin (if installed) logs every API call to `~/.hermes/logs/model_usage.jsonl`. Each entry includes `model_request` (what was sent) and `model_response` (what actually responded). Always check this log after a delegation where the model mattered — OpenRouter's auto-router may resolve `openrouter/auto` to a different model than expected.

## Mismatch Warnings

`delegate_task` emits a `logger.WARNING` before the subagent spins up when:
1. A per-task model has a provider prefix incompatible with the resolved top-level provider (e.g. `model="x-ai/grok-4.1-fast"` with `provider="anthropic"`)
2. An OpenRouter-specific bare model name (`auto`, `openrouter/auto`) is used with a non-OpenRouter provider

These are early warnings — they do not block execution. Check your logs if a per-task model call fails with an unexpected API error.

## Keeping This Skill Current

The tier tables and pricing data in this skill go stale as OpenRouter's catalog evolves — new models launch, pricing changes, and older models are deprecated. The recommended way to stay current is a weekly automated refresh cron job.

### How it works

A script (`scripts/refresh_openrouter_models.py`, included in this repo) polls the OpenRouter `/api/v1/models` endpoint weekly, diffs the live catalog against your configured whitelists, and delivers a read-only digest to the operator for review. The operator approves changes; only then does an agent update the skill and the script's whitelist dict together.

**The three-component loop:**

```
scripts/refresh_openrouter_models.py   ← canonical source of truth for whitelists
         ↓  runs weekly via cron
   digest delivered to operator
         ↓  operator approves
  skill + script patched atomically    ← both updated in same session
```

### Setup

1. Copy `scripts/refresh_openrouter_models.py` to `~/.hermes/scripts/`
2. Customize the `WHITELISTS` dict and `TRACKED_PROVIDERS` set for your use case
3. Set `OPENROUTER_API_KEY` in your environment
4. Create the cron job (see `CRON_PROMPT_TEMPLATE` at the bottom of the script):

```python
cronjob(
    action="create",
    name="OpenRouter Model Refresh",
    script="refresh_openrouter_models.py",
    schedule="0 11 * * 0",   # every Sunday at 11 AM
    model={"model": "openrouter/auto", "provider": "openrouter"},
    deliver="origin",
    prompt=CRON_PROMPT_TEMPLATE,
)
```

### Rules

- **The cron agent is read-only.** It never writes files. It reports and stops.
- **All changes require operator approval** in a subsequent session.
- **Updates are atomic.** When approved, patch both the script's `WHITELISTS` dict and the skill's tier tables in the same session. A partial update leaves them inconsistent.
- **Never edit the skill's whitelist/tier sections manually.** The script is the source of truth; the skill is the mirror.

### Without the refresh cron

If you're running without this automation, treat the tier tables in this skill as a starting point. Verify pricing against [openrouter.ai/models](https://openrouter.ai/models) before making cost-sensitive routing decisions, and update the skill manually when you notice significant changes.

---
