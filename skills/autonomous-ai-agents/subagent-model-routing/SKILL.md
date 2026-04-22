---
name: subagent-model-routing
description: Model selection guide for delegate_task and cron jobs. Load this skill whenever you use delegate_task and the user has not explicitly specified a model — it provides the routing decision matrix, tier tables, provider rules, and known failure modes.
version: 1.0.0
author: thesunofdog
license: MIT
metadata:
  hermes:
    tags: [delegation, models, routing, cost, orchestration]
    related_skills: [multi-agent-orchestration, claude-code, codex, hermes-agent]
---

# Subagent Model Routing

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
Web research             → grok-4.1-fast or gemini-2.5-flash → Budget
Translation / formatting → grok-4.1-fast                  → Budget
Simple patches/renames   → grok-4.1-fast                  → Budget
New script / feature     → gpt-5.1-codex-mini             → Standard
Bulk code changes        → grok-code-fast-1 or devstral   → Standard
Refactoring              → gpt-5.1-codex or claude-haiku  → Standard
Code review              → gemini-2.5-pro or gpt-5.1-codex→ Standard
Architecture / judgment  → grok-4.20 or o3                → Premium
Trusted 2nd opinion      → grok-4.20                      → Premium
Complex reasoning        → o4-mini or deepseek-r1         → Standard
```

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

## Critical: Coding Tasks Need Coding Models

**Never use general-purpose models (Gemini Flash, Grok Fast, Llama) for tasks that involve writing or modifying code.**

General models lack the discipline to follow precise integration requirements — they invent their own abstractions instead of using existing codebase patterns. This is not a quality difference; it is a reliability difference. Two failed delegations at $0.20/Mi costs more than one successful delegation at $1.25/Mi — in tokens, time, and momentum.

**Decision rule for writing code:** If the task requires calling 3+ existing functions from a codebase, or referencing specific file paths, constants, or patterns from an existing monolith — consider writing it directly in the orchestrator instead of delegating. Context transfer cost can exceed delegation benefit.

## When to Skip Delegation Entirely

Delegate when a task:
- Is reasoning-heavy and would flood your context with intermediate data
- Can run in parallel with other independent tasks
- Requires a different model than the parent session

Do not delegate when:
- The task requires deep context from your current session that would be expensive to transfer
- It's a single tool call
- Two prior delegations have already failed on the same task — write it yourself

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

## Verifying What Actually Ran

The `model_observability` plugin (if installed) logs every API call to `~/.hermes/logs/model_usage.jsonl`. Each entry includes `model_request` (what was sent) and `model_response` (what actually responded). Always check this log after a delegation where the model mattered — OpenRouter's auto-router may resolve `openrouter/auto` to a different model than expected.

```bash
tail -5 ~/.hermes/logs/model_usage.jsonl | python3 -m json.tool
```

## Mismatch Warnings

`delegate_task` emits a `logger.WARNING` before the subagent spins up when:
1. A per-task model has a provider prefix incompatible with the resolved top-level provider (e.g. `model="x-ai/grok-4.1-fast"` with `provider="anthropic"`)
2. An OpenRouter-specific bare model name (`auto`, `openrouter/auto`) is used with a non-OpenRouter provider

These are early warnings — they do not block execution. Check your logs if a per-task model call fails with an unexpected API error.
