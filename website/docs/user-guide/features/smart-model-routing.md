---
title: Smart Model Routing
description: Route easy tasks to a cheap/local model and hard tasks to a smart cloud model, before each turn starts.
sidebar_label: Smart Model Routing
sidebar_position: 9
---

# Smart Model Routing

Smart model routing decides — before each turn even starts — whether the
upcoming user message should run on your configured primary model, on a
cheaper local model, or on a smarter cloud model. It complements
[fallback providers](./fallback-providers.md): fallback handles
provider/runtime failure mid-turn, smart routing handles task-difficulty
matching at turn start.

Two operating modes are supported. Hermes auto-detects the right mode
from your config:

- **Local-first** (Chad's default) — primary model is a fast/cheap local
  model. Hermes uses it for routine work and routes hard tasks to a
  smart cloud model.
- **Smart-primary** — primary model is a strong cloud model. Hermes uses
  it for everything by default and routes simple tasks to a configured
  cheap/local model to save cost and latency.

Routing is **turn scoped**: each user turn re-evaluates the route, and
the next turn always starts back on the configured primary model. If
routing is disabled or misconfigured, behavior matches stock Hermes.

## Configuration

Add a `smart_model_routing` block to your `config.yaml`:

```yaml
smart_model_routing:
  enabled: true

  # Length thresholds used by the deterministic heuristic.  Anything over
  # either limit is treated as "hard".  Default 160 / 28.
  max_simple_chars: 160
  max_simple_words: 28

  # Optional: pin a mode.  When unset, mode is auto-detected from the
  # configured cheap_model / smart_model and your primary provider.
  # Values: auto | local-first | smart-primary
  mode: auto

  # Used in smart-primary mode when the upcoming task looks simple.
  cheap_model:
    provider: custom
    model: fast-local-model
    base_url: http://fred:9069/v1
    api_key: local

  # Used in local-first mode when the upcoming task looks hard.  If
  # omitted, Hermes uses the first entry of fallback_providers /
  # fallback_model as the smart target so a single config can drive both
  # difficulty escalation and provider failover.
  smart_model:
    provider: openrouter
    model: anthropic/claude-sonnet-4

  # Optional list of additional words/phrases that should classify a
  # turn as "hard" (e.g. internal codenames, project-specific terms).
  extra_hard_keywords:
    - permitting
    - rezoning
```

Both `cheap_model` and `smart_model` use the same shape as
`fallback_providers` entries (`provider`, `model`, optional `base_url`
and `api_key`). Missing or invalid entries simply disable that route —
they do not crash the session.

## Heuristic

The MVP heuristic is fully deterministic. A turn is classified as
**hard** when any one of the following is true:

- the message exceeds `max_simple_chars` characters or `max_simple_words`
  words
- the message contains a fenced code block, a Python traceback, or three
  or more `?` characters (multi-part reasoning)
- the message contains any hard keyword token, including:
  `debug`, `build`, `code`, `refactor`, `architect`, `architecture`,
  `design`, `plan`, `investigate`, `strategy`, `lawsuit`, `contract`,
  `legal`, `financial`, `risk`, `complex`, `hard`, `regression`,
  `traceback`, `audit`, `permit`, `escalation`, plus any
  `extra_hard_keywords` you configure
- the message matches a hard phrase pattern such as `root cause`,
  `test failure`, `stack trace`, or `security review`

Otherwise the turn is **simple**.

There is no LLM classifier in the MVP, so routing decisions are free,
deterministic, and reproducible. Tune behavior via thresholds and
`extra_hard_keywords`.

## Routing rules

| Mode           | Hard task         | Simple task        |
|----------------|-------------------|--------------------|
| local-first    | route to smart    | stay on primary    |
| smart-primary  | stay on primary   | route to cheap     |

Mode auto-detection (when `mode: auto` or unset):

- If only `smart_model` is configured → **local-first**.
- If only `cheap_model` is configured → **smart-primary**.
- If both are configured → **local-first** when the primary provider is
  in the local-like set (`custom`, `ollama`, `vllm`, `lmstudio`,
  `llamacpp`, `local`, `localhost`, `fred`, …), otherwise
  **smart-primary**.
- If neither is configured → smart routing is effectively disabled and
  the primary model handles every turn (fallback providers still apply
  to provider failures).

## Interaction with fallback

Smart routing runs *before* the API call. If the routed model later
fails for provider reasons, the existing
[fallback chain](./fallback-providers.md) still applies. Restoration to
the primary model happens automatically at the start of the next user
turn, just like fallback restoration. There is no additional
configuration required to combine the two.

## Observability

Routing decisions are logged at INFO when a route activates and at
DEBUG when the primary is kept. Each decision includes the route
target, the classification (`simple` / `hard`), and the human-readable
reason. The most recent decision is also stored on the agent as
`_last_smart_routing_decision` for tooling inspection.

## Constraints

- The MVP makes no LLM classifier calls.
- Hermes does not call any local model endpoint to decide a route.
- Disabling `smart_model_routing` (or omitting it) reproduces stock
  Hermes behavior exactly.
