# Provider Concurrency Semaphore

**Status**: Implemented for provider request concurrency. RPM/TPM pacing remains a separate follow-up.

Hermes can make multiple model requests at once: the main agent loop may be waiting on one provider call while auxiliary tasks such as compression, vision, or web summarization start their own calls. Some providers enforce a low maximum number of simultaneous requests per API key, so those auxiliary calls can trigger transient 429s even when the account still has quota.

This change adds a process-local semaphore keyed by `(provider, api_key)`:

- Main agent requests acquire a priority slot.
- Auxiliary requests acquire a non-priority slot and wait behind main agent work.
- Critical auxiliary work, currently context compression, waits briefly and then proceeds if no slot becomes available.
- Concurrency-related 429s use a short credential-pool cooldown instead of the normal one-hour 429 cooldown.

## Defaults

Defaults live in `agent/model_metadata.py`.

| Provider | Model | Max concurrent requests |
| --- | --- | --- |
| `zai` | `glm-5.1*` | 1 |
| `zai` | `glm-5*` | 2 |
| `zai` | `glm-4.5*`, `glm-4-air*`, `glm-4-flash*`, `glm-4-long*`, `embedding-3*` | 10 |
| `zai` | `cogview*` | 2 |
| `zai` | `cogvideox*` | 5 |
| `kimi`, `kimi-coding`, `moonshot` | any | 1 |

Providers without known concurrency limits default to a high limit so normal RPM/TPM-limited providers are effectively unchanged by this feature.

## User Overrides

Users can override the default for the primary model:

```yaml
model:
  provider: zai
  default: glm-5.1
  max_concurrent: 2
```

Named custom providers can set a provider-wide default:

```yaml
custom_providers:
  - name: my-zai
    base_url: https://api.z.ai/api/coding/paas/v4
    api_key: sk-...
    max_concurrent: 2
```

They can also set a per-model override:

```yaml
custom_providers:
  - name: my-zai
    base_url: https://api.z.ai/api/coding/paas/v4
    models:
      glm-5.1:
        max_concurrent: 1
      glm-5:
        max_concurrent: 2
```

Invalid override values, such as `0` or non-numeric strings, are ignored and Hermes falls back to provider defaults.

## Implementation Notes

- `agent/concurrency.py` implements `ConcurrencySemaphore`, the process-local registry, and config override lookup.
- `run_agent.py` gates primary LLM calls with priority.
- `agent/auxiliary_client.py` gates sync and async auxiliary LLM calls without priority.
- `agent/credential_pool.py` treats concurrency-shaped 429s, including z.ai error `1302` and messages containing `concurren`, as short-lived exhaustion.
- The semaphore is intentionally process-local. It coordinates concurrent work within one Hermes process; it is not a distributed rate limiter across multiple Hermes processes.

## Follow-Up

This is concurrency-only. RPM/TPM pacing based on provider rate-limit headers should be handled separately.
