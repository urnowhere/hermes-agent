# Explicit Custom API Mode Design

**Context**

Hermes supports multiple wire protocols behind a shared provider abstraction. For custom OpenAI-compatible endpoints, users need a way to explicitly declare that an endpoint should use the OpenAI Responses API. URL heuristics are not reliable enough for self-hosted providers.

**Decision**

- `model.api_mode` remains the authoritative explicit override for the active model runtime.
- `custom_providers[].api_mode` stores the explicit transport for a named custom provider.
- If `api_mode` is explicitly configured, Hermes must honor it.
- If `api_mode` is not configured, Hermes keeps existing behavior. This change does not add any new heuristics.

**Scope**

- Runtime provider resolution for named and active custom providers
- Custom provider persistence in `config.yaml`
- Custom provider activation flows in `hermes model`

**Non-Goals**

- No new endpoint probing logic
- No automatic Responses API detection for localhost or `/v1`
- No change to built-in provider transport selection
