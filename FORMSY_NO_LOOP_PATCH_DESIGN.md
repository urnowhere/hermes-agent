# FormSy on Hermes: No-Loop-Patch Design

Date: 2026-05-22

This note tracks the design for making Hermes better at coding and bug-fix
tasks with FormSy, while avoiding direct changes to the Hermes model loop.

## Business Goal

The goal is not merely to integrate FormSy. The goal is:

- improve coding and bug-fix success rate
- reduce wasted context and tool-output tokens
- preserve enterprise/private-model compatibility
- make the Hermes integration acceptable to the Hermes community

Direct `run_agent.py` changes can make Hermes align tightly with FormSy context
and memory responses, but they are hard to upstream. A public model proxy can
avoid loop patches, but it introduces a new architecture, duplicates part of
Hermes's provider stack, and fails when the upstream model is reachable only
inside an enterprise network.

So the primary design should not be "repair model output." The primary design
should be "make FormSy valuable through normal Hermes extension points."

## Core Conclusion

If the specific requirement is:

> Convert malformed assistant text into structured tool calls after the model
> has responded.

then there are only two natural places to do it:

- inside the agent loop, after Hermes receives the model response
- in the provider/proxy path, before Hermes receives the model response

Both options are expensive for this project:

- agent-loop repair is unlikely to be accepted upstream
- proxy repair creates deployment and business-model problems

Therefore the no-loop-patch design should drop tool-call repair as a required
feature. FormSy should observe malformed text tool-call attempts, but should not
try to fix them in Hermes core or in a mandatory proxy.

## Recommended Architecture

Use a no-repair, plugin-first architecture:

```text
Hermes -> upstream model directly
Hermes -> FormSy memory API through memory provider plugin
Hermes -> FormSy context API through context engine plugin
Hermes -> FormSy observability API through generic plugin hooks
```

This keeps Hermes in charge of:

- model/provider selection
- the agent loop
- tool execution
- retries and budgets
- session persistence
- CLI/TUI/gateway behavior

FormSy owns:

- compact memory recall
- repository context retrieval
- server-side context bundling
- optional context compression
- task-level observability
- token-saving tool-result summarization where existing hooks allow it

## How FormSy Still Improves Coding Without Loop Changes

The current loop patch tries to recover when a model emits a malformed tool
call. That is useful, but it is not the main source of FormSy value.

The main source of value should be that the model needs fewer, better tool
calls and receives smaller, more relevant context.

### Trace Evidence

The saved Hermes trace at:

```text
/Users/wayneliu/.hermes/sessions/saved/hermes_conversation_20260520_200851.json
```

supports this direction.

In that run, the model used structured tool calls successfully. The expensive
parts were not malformed text tool calls. The expensive parts were:

- `context_search` returned very large tool results, around 10-12 KB each.
- `context_read` failed for the primary target, forcing fallback to file reads.
- Retrieval gates blocked normal `read_file` / `write_file` attempts until the
  model satisfied FormSy's expected retrieval sequence.
- The first context bundle contained noisy likely-edit targets, including
  reproduction and unrelated model files.
- The test plan fell back to broad `pytest -v` with weak confidence.
- Terminal test output returned thousands of characters.

This trace shows that the highest-leverage no-loop-patch work is:

- smaller and sharper context bundles
- more reliable `context_read`
- less intrusive retrieval gating
- better test-plan generation
- terminal/test output summarization

Those improvements directly support the business goal: better bug-fix success
and lower token use, while keeping Hermes on its normal model path.

### 1. Return Agent-Native Context Bundles

`context_search` should return a compact bundle that is directly useful to a
coding agent:

- problem summary
- likely files and symbols
- relevant snippets under a token budget
- must-read files
- likely edit targets
- test plan
- known prior memory hints
- confidence / coverage
- suggested next query only when needed

This reduces the number of back-and-forth retrieval calls. Instead of requiring
Hermes to keep asking FormSy for alignment, FormSy gives Hermes a normal tool
result that already fits the agent workflow.

The bundle should be compact enough to fit in the tool result without flooding
the model. Exact source reads still go through `context_read` when needed.

### 2. Add a Higher-Level Context Tool

Keep `context_search` and `context_read`, but consider adding one higher-level
tool through the context engine, for example:

```text
formsy_context_bundle
```

Purpose:

- do search, grounding, memory hint merge, and test-plan generation server-side
- return one concise bug-fix context package
- avoid requiring the model to discover the exact FormSy retrieval sequence

This remains a normal Hermes tool. No loop change is required.

### 3. Use Real Context Compression Through the Context Engine

The current FormSy context engine implements `compress(...)` as a pass-through.
For token savings, this is a major accepted extension point.

Future FormSy compression should:

- compress old conversation turns
- preserve active task state
- preserve accepted retrieval targets
- preserve unresolved test failures
- preserve memory/context artifact ids
- omit raw shell noise and stale exploration

This gives token savings without touching the model loop. It also improves
long-running bug-fix tasks, where context pressure causes failures.

### 4. Use Existing Tool-Result Transformation Hooks

Hermes already has plugin hooks for result canonicalization:

- `transform_tool_result`
- `transform_terminal_output`

FormSy can use these hooks to reduce token waste from:

- long terminal output
- repetitive test failures
- huge search/read results
- noisy diagnostics

This is a better upstream story than loop mutation because Hermes already
exposes these hooks for plugins.

The transform should be conservative:

- preserve return code
- preserve failing test names
- preserve stack trace head/tail
- preserve file paths and line numbers
- never hide command failure
- keep raw output out of FormSy telemetry unless explicitly allowed

The saved trace has a concrete example: one targeted test command returned a
multi-kilobyte pytest session output. The model usually needs the command,
return code, failing tests, and failure snippets, not the entire uncompressed
stream.

### 5. Keep Memory Recall Ephemeral and Compact

Use the memory provider and existing pre-turn injection behavior:

- prefetch once per user turn
- inject compact memory context into the user message
- do not persist injected memory into session history
- sync compact coding summaries after completed turns

This improves repeated bug-fix work without requiring any model path changes.

### 6. Report Malformed Tool Calls, Do Not Repair Them

If a model emits text such as:

- `to=functions.terminal`
- `<function=terminal>`

FormSy should record it in observability:

- `text_tool_call_attempt_count`
- `first_text_tool_call_attempt_tool`
- `first_text_tool_call_attempt_summary`

This gives product feedback and model-quality data without controlling the
Hermes loop.

The operational guidance becomes:

> FormSy improves context, memory, and token use. It does not guarantee repair
> for providers/models that fail structured tool calling.

That is honest and much easier to upstream.

## Optional Cross-Agent Surface: MCP

For OpenCode, Codex-style clients, and other agents, a FormSy MCP server may be
a better portability layer than a model proxy.

An MCP server can expose the same capabilities:

- `formsy_context_bundle`
- `formsy_context_search`
- `formsy_context_read`
- `formsy_memory_search`
- `formsy_task_report`

This keeps FormSy out of the model path and lets each agent keep its own model
provider handling.

MCP is optional for Hermes because Hermes already has native plugin surfaces,
but it is a good cross-agent adoption path.

## Proxy Is Deferred, Not Primary

An OpenAI-compatible FormSy proxy can still exist later, but it should not be
the default answer to the no-loop-patch problem.

Use a proxy only when all of these are true:

- the user explicitly wants response repair
- the upstream model is reachable from where the proxy runs
- the user accepts the extra deployment component
- the proxy is thin and does not replace Hermes provider management

For enterprise-private models, the proxy must be self-hosted inside the same
network, or skipped entirely.

## Minimal Hermes Core Ask

The preferred upstream ask is no FormSy-specific model-loop patch.

The default integration should require only:

- memory provider plugin
- context engine plugin
- generic plugin hooks
- optional MCP configuration for non-Hermes agents

If Hermes maintainers later want a generic response-normalization hook, FormSy
could use it. But this must not be a blocker for adoption.

## Migration From Current Loop Patch

1. Remove the text-tool-call leak retry logic from `run_agent.py`.
2. Remove tests that require Hermes to retry malformed text tool calls inside
   the model loop.
3. Add observability-only detection of malformed text tool-call patterns.
4. Improve context tool outputs so FormSy returns compact, agent-native bundles.
5. Add or improve tests for:
   - context bundle shape and token bounds
   - memory hint merge
   - terminal/tool result summarization
   - context compression behavior
   - malformed text tool-call observability
6. Keep proxy experiments outside the required Hermes integration path.

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

Hermes continues to call the user's chosen model directly. FormSy improves the
quality and compactness of context and memory around that model.
