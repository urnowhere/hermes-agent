# FormSy on Hermes: No-Loop-Patch Design

Date: 2026-05-22

This note tracks the proposed design for making FormSy usable by Hermes users
without requiring direct changes to the Hermes model loop.

## Goal

Make FormSy usable by Hermes users without requiring direct changes to
`run_agent.py`. The integration should degrade gracefully when Hermes core does
not accept agent-loop guardrails.

## Design Principle

FormSy should depend only on accepted extension surfaces:

- memory provider plugin
- context engine plugin
- generic observability plugin hooks
- optional model-provider/proxy configuration

It should not require Hermes to change:

- the tool-calling loop
- final-response handling
- retry behavior
- message role construction
- iteration-budget logic
- session persistence logic

## Split the Integration Into Two Tiers

Tier 1 is the default, community-friendly integration.

It contains:

- `plugins/memory/formsy_memory`
- `plugins/context_engine/formsy`
- `plugins/formsy-observability`
- shared `plugins/formsy` runtime client

This tier uses normal Hermes plugin/provider surfaces. If Hermes accepts only
these pieces, FormSy still provides memory, repository context retrieval, and
task-level observability.

Tier 2 is optional robustness for models that emit tool calls as plain text.

It should not patch `run_agent.py`. Instead, it can be implemented as either:

- an OpenAI-compatible FormSy model proxy, configured as the model `base_url`
- a Hermes model-provider plugin that wraps an upstream provider

Both options sit outside the Hermes loop. They can inspect the provider request
and response before Hermes sees the response.

## Move Text Tool-Call Repair Out of the Agent Loop

The current loop patch detects assistant text such as:

- `to=functions.terminal`
- `<function=terminal>`

and asks the model to retry with structured tool calls. That is useful, but it
is exactly the kind of loop behavior Hermes maintainers may reject.

The important point is where the bad shape first appears.

Hermes expects the model provider to return one of two normal shapes:

- an assistant text message, which Hermes treats as the final answer
- an assistant message with structured `tool_calls`, which Hermes sends to the
  tool executor

The problematic response is in the middle: the model *intended* to call a tool,
but returned that intent as plain text. If Hermes core handles that case, Hermes
must add special retry logic inside the model loop. That touches final-answer
handling, role ordering, retry budgets, and session persistence.

A provider/proxy repair layer moves the fix earlier. Instead of changing how
Hermes reacts after it receives a malformed response, the proxy normalizes the
provider response before Hermes sees it. From Hermes's point of view, nothing
special happened: it simply receives either normal text or normal structured
`tool_calls`.

The no-loop-patch replacement is therefore:

1. Hermes sends a normal chat-completions request with `tools`.
2. The FormSy proxy forwards the request to the configured upstream model.
3. If the upstream response already contains structured `tool_calls`, the proxy
   passes it through unchanged.
4. If the upstream response is plain text that clearly encodes a known requested
   tool call, the proxy converts it into the provider's structured tool-call
   shape.
5. If conversion is ambiguous, unsafe, or references a tool not present in the
   request schema, the proxy passes the text through unchanged.
6. Hermes receives either a normal structured tool call or a normal assistant
   text response. No Hermes loop retry branch is needed.

This is similar to normal provider compatibility work. Different model APIs
already need adapters that translate provider-specific response shapes into the
OpenAI-compatible shape Hermes consumes. Text-tool-call repair is treated as one
more conservative normalization step, not as new agent-loop behavior.

This boundary is useful because:

- Hermes core stays unchanged.
- The repair can be enabled or disabled per provider/proxy.
- The proxy can use the request `tools` array as the allowlist for valid tool
  names.
- Ambiguous responses can be passed through unchanged instead of forcing a
  retry.
- The same proxy can be reused by Hermes, OpenCode, Codex-style clients, or any
  code agent that speaks OpenAI-compatible chat completions.

In short: the loop patch says "Hermes should recover from malformed provider
output." The proxy design says "FormSy should make provider output look normal
before Hermes receives it."

## Proxy Repair Constraints

The repair layer should be conservative:

- only repair when exactly one known tool call is detected
- require valid JSON or a narrowly supported argument syntax
- reject unknown tool names
- reject multiple conflicting tool calls
- reject incomplete arguments
- never execute tools itself
- never mutate session history
- add a response metadata/debug header or trace field when repair happened
- expose a config flag, default off or conservative

Suggested config:

```yaml
formsy:
  tool_call_repair:
    enabled: false
    mode: conservative
    max_repairs_per_turn: 1
```

For Hermes users who do not enable the proxy/provider wrapper, malformed text
tool calls remain normal model output. Observability can count them, but FormSy
does not try to control the Hermes loop.

## Plugin-Only Fallback Behavior

Without Tier 2, FormSy should still improve success rate through prompt and
tool-surface design rather than runtime loop mutation:

- make `context_search` and `context_read` tool schemas explicit and compact
- include tool-use guidance in tool descriptions, not by modifying the system
  prompt
- use `pre_llm_call` only for ephemeral user-message context
- use observability to report malformed textual tool-call attempts
- keep memory sync and context retrieval independent of repair behavior

This means the worst case is not integration failure. The worst case is that a
weak model writes a malformed final response, and FormSy records that outcome
for debugging.

## What Must Be Added Before Coding

The design is not ready for implementation until these choices and contracts are
written down.

### Pick the First Tier 2 Target

The first implementation should be the OpenAI-compatible FormSy model proxy.

Reason: the current Hermes model-provider plugin surface is declarative. It can
prepare messages, add request kwargs, fetch model catalogs, and describe
provider metadata, but it does not own the provider HTTP response. That means a
model-provider plugin alone cannot convert malformed assistant text into
structured `tool_calls` without adding a new Hermes core hook.

So the first code path should be:

```text
Hermes -> FormSy proxy /v1/chat/completions -> upstream model provider
```

The Hermes model-provider wrapper can still be useful later as a convenience
profile that points Hermes at the proxy, but the repair logic should live in the
proxy path first.

### Define the Proxy API Contract

Before coding, specify which OpenAI-compatible endpoints are supported:

- `POST /v1/chat/completions`
- `GET /v1/models`

For the first version, decide whether streaming is supported. The simplest
safe first version is:

- support non-streaming requests
- pass streaming requests through unchanged, or reject them with a clear error
- add streaming repair only after the non-streaming repair logic is proven

The response contract should preserve:

- upstream `id`
- upstream `model`
- upstream `usage`
- upstream `finish_reason` when no repair happens
- OpenAI-compatible `choices[*].message.tool_calls` when repair happens

When repair happens, the proxy should add a non-invasive debug marker, such as:

```json
{
  "formsy_repair": {
    "applied": true,
    "kind": "text_tool_call_to_structured_tool_call"
  }
}
```

If that top-level marker is too risky for strict clients, use an HTTP response
header instead.

### Define the Repair Parser Contract

The parser should be conservative and testable as a pure function:

```python
repair_text_tool_call(content: str, tools: list[dict]) -> RepairResult
```

Inputs:

- assistant text content
- request `tools` array

Outputs:

- `applied = false` with original content unchanged
- or `applied = true` with one structured tool call

Supported first-version text forms:

- `<function=tool_name>{"arg": "value"}</function>`
- `<function=tool_name>{"arg": "value"}`
- `to=functions.tool_name` followed by one JSON object

Rejection cases:

- no request `tools`
- tool name is not present in request `tools`
- more than one candidate tool call
- malformed JSON arguments
- arguments are not a JSON object
- candidate includes unknown or ambiguous tool names
- text contains substantial prose around the candidate that makes intent unclear

The parser must never execute tools. It only rewrites the model response.

### Define Configuration

Add a concrete config contract before implementation:

```yaml
formsy:
  tool_call_repair:
    enabled: false
    mode: conservative
    max_repairs_per_response: 1
  proxy:
    base_url: http://127.0.0.1:8787/v1
    upstream_base_url: https://api.openai.com/v1
    upstream_provider: openai
    upstream_model: ""
```

Environment variables should be named before coding, for example:

- `FORMSY_PROXY_BASE_URL`
- `FORMSY_PROXY_UPSTREAM_BASE_URL`
- `FORMSY_PROXY_UPSTREAM_API_KEY`
- `FORMSY_TOOL_CALL_REPAIR_ENABLED`

Keep secrets in environment variables, not `config.yaml`.

### Define Observability Additions

The plugin-only Tier 1 path should record malformed text tool-call attempts
without controlling the loop.

Add fields such as:

- `counters.text_tool_call_attempt_count`
- `observed_behavior.first_text_tool_call_attempt_summary`
- `observed_behavior.first_text_tool_call_attempt_tool`

The summary must be redacted and bounded, following the existing task/test
summary privacy pattern.

### Define Tests Before Implementation

Add tests in three layers.

Parser unit tests:

- converts one unambiguous known tool call
- rejects unknown tools
- rejects malformed JSON
- rejects multiple tool calls
- rejects prose-heavy ambiguous text

Proxy tests:

- passes through structured `tool_calls` unchanged
- converts one accepted text-form tool call
- preserves upstream usage/model/id
- does not execute tools
- passes through ambiguous text unchanged
- handles upstream errors without hiding them

Hermes integration tests:

- FormSy memory/context/observability still work without the proxy
- observability records text-form tool-call attempts as metrics
- no test requires `run_agent.py` to retry malformed text tool calls

### Define the Migration Boundary

Before deleting loop code, decide what counts as replacement coverage:

- parser tests pass
- proxy pass-through tests pass
- observability malformed-attempt tests pass
- existing memory/context/observability tests pass
- no remaining FormSy acceptance test depends on loop-level retry behavior

Only after that should the `run_agent.py` guardrail and its loop-specific tests
be removed.

## Minimal Hermes Core Ask

The preferred upstream ask is no FormSy-specific loop patch.

If Hermes maintainers are open to a generic extension later, the smallest
acceptable hook would be a provider-response normalization hook before final
response handling:

```python
post_provider_response(response, request_tools, session_id, task_id) -> response
```

But this should be treated as an optional upstream enhancement, not a blocker.
The FormSy default integration should work without it.

## Migration From Current Loop Patch

1. Remove the text-tool-call leak retry logic from `run_agent.py`.
2. Remove tests that assert Hermes retries malformed text tool calls inside the
   model loop.
3. Add observability-only detection of malformed text tool-call patterns, so
   reports can show that a model attempted a text-form tool call.
4. Implement proxy/provider-wrapper tests outside the Hermes loop:
   - pass through valid structured tool calls
   - convert one unambiguous text-form tool call when the tool exists in the
     request schema
   - pass through unknown or ambiguous text unchanged
   - never execute tools in the proxy
5. Keep memory, context engine, and observability tests as the primary Hermes
   integration tests.

## Acceptance Story

This design gives Hermes maintainers a clean choice:

- accept plugin/provider additions that use established extension points
- reject direct model-loop changes without blocking FormSy adoption

For users, the adoption path remains simple:

```yaml
memory:
  provider: formsy_memory

context:
  engine: formsy

formsy:
  base_url: https://api.formsy.ai
```

Users who need malformed-tool-call repair can opt into the FormSy proxy/model
provider wrapper separately. Users who do not need it get the normal Hermes
behavior.
