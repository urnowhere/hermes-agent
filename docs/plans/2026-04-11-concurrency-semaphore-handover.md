# Concurrency Semaphore for Provider Rate Limits — Handover Doc

**Date**: 2026-04-11
**Status**: Design phase — clarifying questions complete, ready for approach proposals
**Scope**: Phase 1 = concurrency gating only. Phase 2 (follow-up) = RPM-based throttling.

---

## Problem

Providers like z.ai and Kimi enforce **concurrency limits** (max simultaneous requests), not just RPM/TPM. Hermes-agent fires auxiliary calls (session summarization, vision auto-detect, context compression) in parallel with the main agent loop, easily exceeding a concurrency limit of 1.

**Concrete failure**: z.ai GLM-5.1 has a concurrency limit of 1. When the main agent is mid-request and an auxiliary call fires, the auxiliary call gets HTTP 429 (error code 1302). Currently `credential_pool.py` treats this the same as a billing error — 1-hour cooldown on the credential — which is far too aggressive for a transient concurrency issue.

## What We Know

### z.ai Concurrency Limits (verified from https://z.ai/manage-apikey/rate-limits)

| Model | Max Concurrent Requests |
|-------|------------------------|
| GLM-5.1 | 1 |
| GLM-5 | 2 |
| GLM-4.5 | 10 |
| GLM-4-Air / Flash / Long | 10 |
| Embedding-3 | 10 |
| CogView / CogVideoX | 2-5 |

### Kimi/Moonshot Concurrency Limits (verified from official docs)

Per-account limits by tier:
- Free: 1 concurrent
- Paid tiers: 50-1000 concurrent
- Also has RPM/TPM/TPD limits (out of scope for Phase 1)

### Other Major Providers

Anthropic, OpenAI, OpenRouter, Google — use RPM/TPM limits, not concurrency. No concurrency gating needed for these (Phase 2 RPM work will cover them).

## Agreed Design Decisions

1. **Generic mechanism** — not z.ai-specific. A concurrency semaphore system any provider can use.
2. **z.ai defaults baked in** — the known concurrency limits above should ship as defaults so z.ai works out of the box.
3. **Kimi defaults included** — same treatment.
4. **Hybrid priority + defer for auxiliary calls**:
   - Main agent loop gets **priority** access to the semaphore.
   - Auxiliary calls (session summarization, vision, web extraction) **defer** when the semaphore is busy — they wait for an idle gap rather than competing.
   - **Critical auxiliary** (context compression — agent can't continue without it) gets a short timeout before proceeding, rather than infinite deferral.
5. **Concurrency-only for Phase 1** — RPM-based throttling (pacing requests using `x-ratelimit-remaining-requests` headers) is a separate follow-up.
6. **`rate_limit_delay` field exists but is unimplemented** — defined in `hermes_cli/config.py` line 1444 in `_VALID_CUSTOM_PROVIDER_FIELDS` but never wired up. Could be a hook point or could be replaced by the new system.

## Key Files

| File | Relevance |
|------|-----------|
| `agent/credential_pool.py` | Manages API keys, rotation, exhaustion tracking. Has 1-hour cooldown for 429/402. Does NOT distinguish error code 1113 (billing) vs 1302 (rate limit). **Needs**: concurrency-aware error handling, shorter retry for 1302. |
| `agent/auxiliary_client.py` | Central `call_llm()` / `async_call_llm()` for all auxiliary tasks. Client cache keyed by `(provider, async_mode, base_url, api_key)`. **Needs**: semaphore integration before making requests. |
| `run_agent.py` | Main agent loop. Makes primary model calls. **Needs**: semaphore acquisition before LLM calls. |
| `hermes_cli/config.py` | Config schema. `rate_limit_delay` field defined but unused. **Needs**: new config fields for concurrency limits. |
| `agent/model_metadata.py` | Model metadata, context lengths. Contains `_PROVIDER_PREFIXES`. **Could hold**: default concurrency limits per provider/model. |
| `hermes_cli/auth.py` | Has `ZAI_ENDPOINTS` list and `detect_zai_endpoint()`. Provider detection logic. |

## Architecture Sketch

```
                    ┌─────────────────────┐
                    │  ConcurrencySemaphore │
                    │  per (provider, key)  │
                    ├──────────────────────┤
                    │ max_concurrent: int   │
                    │ active: int           │
                    │ priority_queue        │
                    │ defer_queue           │
                    └──────┬───────────────┘
                           │
              ┌────────────┼────────────────┐
              │            │                │
         main agent    auxiliary         critical aux
         (priority)    (deferred)       (short timeout)
```

**Semaphore keyed by**: `(provider_name, api_key)` — because concurrency limits are per-key per-provider.

**Two acquisition modes**:
- `acquire(priority=True)` — main agent, goes to front of queue
- `acquire(priority=False, timeout=None)` — auxiliary, waits for idle gap
- `acquire(priority=False, timeout=5.0)` — critical auxiliary (compression), waits briefly then proceeds

## Default Concurrency Table (to ship with)

```python
# provider -> model_pattern -> max_concurrent
PROVIDER_CONCURRENCY_DEFAULTS = {
    "zai": {
        "glm-5.1": 1,
        "glm-5": 2,
        "glm-4.5": 10,
        "glm-4-air": 10,
        "glm-4-flash": 10,
        "glm-4-long": 10,
        "embedding-3": 10,
        "cogview-*": 2,
        "cogvideox-*": 5,
        "_default": 10,
    },
    "kimi": {
        "_default": 1,  # conservative; paid tiers are higher
    },
}
```

## Config Integration

Users should be able to override concurrency limits in `config.yaml`:

```yaml
model:
  provider: zai
  model: glm-5.1
  max_concurrent: 3  # override default of 1

custom_providers:
  - name: my-zai
    base_url: https://api.z.ai/api/coding/paas/v4
    max_concurrent: 2
```

## Error Handling Changes Needed

In `credential_pool.py`, the 429 handler currently applies a 1-hour cooldown regardless of error code. Needs:

1. **Parse z.ai error codes**: 1302 = concurrency rate limit (retry in seconds), 1113 = billing (keep 1-hour cooldown)
2. **Short retry for concurrency 429s**: Back off 1-5 seconds, not 1 hour
3. **Respect `Retry-After` header** if present

## Phase 2: RPM-Based Throttling (Future)

Separate follow-up work:
- Parse `x-ratelimit-remaining-requests` / `x-ratelimit-reset` headers from API responses
- Proactively pace requests when remaining quota is low
- Applies to providers that use RPM/TPM (Anthropic, OpenAI, OpenRouter, etc.)
- `rate_limit_delay` config field could be repurposed or extended for this

## Next Steps for Continuing Agent

1. **Read this doc** and the key files listed above
2. **Propose 2-3 implementation approaches** with trade-offs (brainstorming task #3)
3. **Present design** to user for approval (task #4)
4. **Write detailed spec** in `docs/superpowers/specs/` (task #5)
5. **Create implementation plan** via writing-plans skill (task #6)
6. **Implement** the concurrency semaphore

Key design questions already answered:
- Generic, not z.ai-specific: YES
- Hybrid priority+defer for aux calls: YES
- Concurrency-only scope: YES (RPM is Phase 2)
- Ship with provider defaults: YES (z.ai + kimi)
- User-overridable via config: YES

## Context from This Session

- We cherry-picked PR #6757 (intermediate ACK detection) into the local branch `fix/update-restart-health-check` — that fix is separate from this concurrency work
- GLM-5.1 billing was fixed by setting `GLM_BASE_URL=https://api.z.ai/api/coding/paas/v4` in `~/.hermes/.env`
- GLM-5.1 Chinese responses fixed by adding language directive to `~/.hermes/SOUL.md`
- The brainstorming skill was active — the continuing agent should resume with approach proposals
