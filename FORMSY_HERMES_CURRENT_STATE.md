# FormSy on Hermes: Current State Review

Date: 2026-05-22

This document summarizes the current FormSy integration work in Hermes, with a
follow-up design direction for removing direct model-loop modifications.

## Scope

The review covers two areas:

1. How Hermes currently connects FormSy Server capabilities through memory,
   context, and observability plugin surfaces.
2. What has been changed in the Hermes agent loop, because this area is
   regression-prone and may be harder to port to other code agents.

Current local worktree changes at the time of review:

- `plugins/memory/formsy_memory/provider.py`
- `run_agent.py`
- `tests/plugins/memory/test_formsy_memory_provider.py`
- `tests/run_agent/test_tool_call_guardrail_runtime.py`

Additional untracked analysis notes exist in the repo root:

- `FORMSY_TIMEOUT_FIX.md`
- `FRESH_RUN_MEMORY_ANALYSIS.md`
- `MEMORY_ISSUE_ANALYSIS.md`
- `SUBMISSION_FIX.md`

## High-Level Architecture

The FormSy integration is split into three independent surfaces:

| Surface | Hermes extension point | Main plugin | Main purpose |
| --- | --- | --- | --- |
| Memory provider | `memory.provider` | `plugins/memory/formsy_memory` | Cross-session recall and turn writeback |
| Context engine | `context.engine` | `plugins/context_engine/formsy` | Repository context retrieval through `context_search` / `context_read` |
| Observability plugin | Generic plugin hooks | `plugins/formsy-observability` | Metrics-only task reports |

All three surfaces share the FormSy runtime client layer under
`plugins/formsy/`. That layer encapsulates HTTP calls to FormSy Server:

- `/v1/runtime/memory_prefetch`
- `/v1/runtime/memory_sync_turn`
- `/v1/runtime/session_end`
- `/api/v1/compile`
- configured query endpoint, default `/api/v1/query`
- `/api/v1/read`
- observability task report endpoint, default `/v1/observations/task_reports`

The integration is therefore not a single monolithic Hermes patch. Most FormSy
behavior is implemented as plugins, with `run_agent.py` acting as the host that
invokes provider lifecycle methods and plugin hooks.

## FormSy Memory Provider

The memory provider is registered by:

```python
# plugins/memory/formsy_memory/__init__.py
def register(ctx) -> None:
    ctx.register_memory_provider(FormSyMemoryProvider())
```

It is enabled through Hermes config:

```yaml
memory:
  provider: formsy_memory

formsy:
  base_url: https://api.formsy.ai
  workspace_id: ws_default
  timeout_s: 30
```

The provider also accepts environment overrides such as:

- `FORMSY_API_KEY`
- `FORMSY_BASE_URL`
- `FORMSY_WORKSPACE_ID`
- `FORMSY_TIMEOUT`
- `FORMSY_REPO_ID`
- `FORMSY_REVISION`

### Lifecycle

`FormSyMemoryProvider.initialize()` reads config, stores runtime identity, and
creates:

- `RuntimeClient`
- `MemoryClient`

The provider lifecycle then follows the Hermes memory-provider contract:

- `on_turn_start(turn_number, message, **kwargs)`
  - Stores the current turn number.
  - Builds a stable `turn_id`.
  - Updates the runtime identity snapshot.
  - Resets per-turn memory trace fields.

- `prefetch(query, session_id=...)`
  - Calls FormSy memory prefetch.
  - Sends workspace id, session id, turn id, query, runtime identity, and token
    budget.
  - Returns `response.memory_block` when available.
  - Falls back to the local memory store when the server returns no useful
    memory block or the request fails.

- `sync_turn(user_content, assistant_content, session_id=..., **kwargs)`
  - Builds a compact two-message turn payload.
  - Builds a `CodingSummary` from accepted targets, terminal calls, changed
    files, tests, and retrieval state.
  - Includes context artifacts returned by context retrieval.
  - Dispatches the sync event to FormSy Server.
  - Appends a compact local JSONL copy for fallback recall.

- `on_session_end(messages)`
  - Sends a session-end event to FormSy Server.
  - Includes a summary hint extracted from the final conversation messages.
  - Deduplicates session-end submission per session id.

- `on_session_switch(new_session_id, reset=...)`
  - Updates provider-local session state when Hermes resumes, branches, resets,
    starts a new session, or compresses into a new session.

### Tool Surface

When enabled, the memory provider exposes:

- `cc_memory_search`

This lets the model explicitly search FormSy memory for prior-session facts,
repo lessons, preferences, or similar task history. The tool is injected into
the agent tool list by `run_agent.py` after memory manager initialization.

### Context Hints

The provider exposes `get_context_hints()`, which returns memory-derived hints
for the context engine:

- `memory_artifact_ids`
- `memory_query_hints`
- `memory_test_hints`
- `memory_status`
- `memory_freshness`

The FormSy context engine later merges these hints into `context_search`
metadata. This is the main coupling between the memory provider and the context
provider.

### Current Local Fallback Store

The current worktree adds a local fallback memory store:

- File: `$HERMES_HOME/formsy-memory-local.jsonl`
- Max records: 500
- Written from `sync_turn()`
- Queried from `prefetch()` when server memory is unavailable or empty

Each local record stores:

- creation timestamp
- workspace id
- session id
- turn id
- runtime identity
- compact user/assistant messages
- coding summary
- context artifact ids

The local matcher filters by workspace id and repo id, then scores records by
query-term overlap, repo match, and presence of patch summary. A hit produces a
`## Relevant Memory` block with prior changed files, patch summaries, and test
commands. It also sets memory trace hints such as:

- `memory_status = "hit"`
- `memory_freshness = "local"`
- `memory_test_hints`
- `memory_query_hints`

This fallback is best understood as a resilience layer for repeated local runs;
it does not replace FormSy Server memory.

## FormSy Context Engine

The context engine is registered by:

```python
# plugins/context_engine/formsy/__init__.py
def register(ctx) -> None:
    ctx.register_context_engine(FormsyContextEngine())
```

It is enabled through Hermes config:

```yaml
context:
  engine: formsy

formsy:
  base_url: http://127.0.0.1:8000
  memory_search_endpoint: /api/v1/query
  repo_id: ""
  revision: latest
  query_budget: 4000
  workspace_id: ws_default
  timeout_s: 120
```

### Role in Hermes

Although it implements the `ContextEngine` interface, the current FormSy engine
does not perform real message compression:

```python
def compress(...):
    return messages
```

Its primary role is to expose FormSy-backed repository context tools:

- `context_search`
- `context_read`

It still tracks token usage fields expected by Hermes, such as:

- `last_prompt_tokens`
- `last_completion_tokens`
- `last_total_tokens`
- `threshold_tokens`
- `context_length`
- `compression_count`

### Session Lifecycle

On session start, the engine:

- Stores session id.
- Stores runtime identity snapshot.
- Receives the memory manager reference.
- Loads FormSy config.
- Creates `RuntimeClient`.
- Creates `EngineClient`.

On session end, it calls `_flush_session_for_task_boundary()` and closes the
runtime client.

The engine also has `on_user_turn()` task-injection detection. In batch or
SWE-bench-style runs, a new task can be injected into the same Hermes session.
When markers such as `<pr_description>`, `<instructions>`, or
`COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT` are detected, the engine flushes the
current task boundary and resets retrieval state.

### `context_search`

`context_search` is the primary retrieval tool.

Current flow:

1. Validate non-empty query.
2. Resolve repo identity from runtime identity, git remote, git revision, or
   config fallback.
3. Ensure repository memory is compiled.
4. If compile fails, attempt memory-only prefetch fallback.
5. If memory fallback also fails, return degraded recovery instructions.
6. Build query metadata.
7. Merge memory hints from the memory provider.
8. Call FormSy query API.
9. Normalize and return the result payload.
10. Update retrieval trace and cached context-read snippets.

The tool schema supports metadata such as:

- `retrieval_mode`: `symbolic` or `legacy`
- `grounding_phase`: `seed`, `grounded`, or `fallback`
- `response_format`: `bundle` or `legacy`
- `trace_id`
- `case_id`
- `grounded_symbols`
- `grounded_files`
- `retrieval_feedback`

The returned payload may include:

- `extra_context`
- `symbolic_prompt`
- `matches`
- `suggested_queries`
- `coverage`
- `missing_context`
- `diagnostics`
- `test_plan`
- `requirement_analysis`
- `template_family`
- `retrieval_targets`
- `bundle`
- `context_package`
- `grounded_symbols`
- `grounded_files`
- `retrieval_feedback`
- `retrieval_state`
- `preferred_next_step`
- `accepted_targets`
- `exploration_closed`
- `blocked_tool_reason`
- derived `direct_match_files`
- derived `bundle_primary_files`
- derived `bundle_must_edit`

### Repository Compile

Before search, the engine calls `/api/v1/compile` through `compile_repo()`.

The local source collection:

- includes common source, config, and markdown suffixes
- skips low-value or large directories such as `.git`, virtualenvs,
  `node_modules`, build artifacts, docs, migrations, locale, and fixtures
- caps compile payload by file count and total bytes
- reserves room for test files
- scores test files by query terms

Current hard caps:

- `MAX_COMPILE_FILES = 500`
- `MAX_COMPILE_BYTES = 4 * 1024 * 1024`
- `RESERVED_TEST_FILES = 100`

Existing analysis notes indicate that compile timeout has been a real failure
mode for large repositories such as Django. In that failure mode,
`context_search` falls back to memory-only prefetch and then degraded recovery.

### `context_read`

`context_read` reads exact source context for a path and optional line range.

Current flow:

1. Validate path.
2. Check whether the read is allowed by current retrieval state.
3. Resolve repo identity.
4. Call `/api/v1/read`.
5. If the read API fails, return cached snippets from prior `context_search`
   `extra_context` when possible.
6. Return formatted source context.

The read gate is intended to keep the agent grounded on accepted targets or
test-plan files after retrieval has narrowed the search space.

## FormSy Observability Plugin

The observability plugin is a generic Hermes plugin, not a memory provider and
not a context engine.

Registration:

```python
ctx.register_hook("on_session_start", _reporter.on_session_start)
ctx.register_hook("pre_llm_call", _reporter.pre_llm_call)
ctx.register_hook("post_api_request", _reporter.post_api_request)
ctx.register_hook("pre_tool_call", _reporter.pre_tool_call)
ctx.register_hook("post_tool_call", _reporter.post_tool_call)
ctx.register_hook("post_llm_call", _reporter.post_llm_call)
ctx.register_hook("on_session_end", _reporter.on_session_end)
ctx.register_hook("on_session_finalize", _reporter.on_session_finalize)
ctx.register_hook("on_session_reset", _reporter.on_session_reset)
```

### Configuration

Key environment variables:

- `FORMSY_OBSERVABILITY_ENABLED`
- `FORMSY_OBSERVABILITY_URL`
- `FORMSY_OBSERVABILITY_API_KEY`
- `FORMSY_OBSERVABILITY_TIMEOUT`
- `FORMSY_OBSERVABILITY_PARTIAL_INTERVAL_S`
- `FORMSY_OBSERVABILITY_SPOOL_DIR`
- `FORMSY_OBSERVABILITY_SPOOL_MAX_BYTES`

The submit URL is built from:

1. `FORMSY_OBSERVABILITY_URL`
2. `FORMSY_BASE_URL`
3. `formsy.base_url` in config
5. default `http://127.0.0.1:8000`

Default endpoint:

```text
/v1/observations/task_reports
```

### Data Collected

The plugin maintains per-session `TaskState` and counters:

- turn count
- model turn count
- context search count
- context read count
- shell fallback count
- test command count
- file edit count
- server request count
- input tokens
- output tokens
- cost

It also tracks:

- first task label as a redacted text summary
- first test command summary and kind
- edited file path hashes
- used observation/request/trace ids
- grounded accepted target hashes

### Privacy Contract

Reports contain metrics and short redacted summaries. The report explicitly marks:

```json
{
  "redaction": "metrics_and_redacted_summaries",
  "contains_prompt": false,
  "contains_source": false,
  "contains_diff": false,
  "contains_shell_output": false
}
```

The plugin summarizes task text and test commands, redacts obvious secrets, and
hashes paths before reporting them where
needed. It does not submit raw prompt text, source content, diffs, or shell
output.

### Submit and Spool

Reports are submitted asynchronously on a daemon thread. Submission failure is
non-fatal. Failed reports are spooled to:

```text
$HERMES_HOME/formsy-observability/task-reports/<YYYY-MM-DD>/task-reports-<YYYY-MM-DD>.jsonl
```

The spool directory is trimmed by total byte size.

## Where Hermes Agent Loop Hosts These Capabilities

Most of the FormSy integration depends on existing or plugin-oriented
extension points in `run_agent.py`.

### Memory Provider Initialization

During `AIAgent.__init__`, Hermes:

1. Reads `memory.provider`.
2. Loads the provider plugin.
3. Checks `is_available()`.
4. Adds it to `MemoryManager`.
5. Initializes it with session/platform/profile/runtime identity context.
6. Injects provider tool schemas into `self.tools`.
7. Adds provider tool names to `self.valid_tool_names`.

This is the main place where memory-provider tools become visible to the model.

### Context Engine Session Start

The active context engine receives session start state including:

- `session_id`
- `hermes_home`
- `platform`
- `model`
- runtime identity snapshot
- memory manager

For FormSy, this is how the context engine receives a reference to the memory
manager and can later merge memory hints into context retrieval metadata.

### Pre-LLM Context Injection

Before the tool loop begins for a user turn, Hermes:

1. Invokes generic `pre_llm_call` plugin hooks.
2. Calls memory-provider `on_turn_start()`.
3. Calls memory-provider `prefetch_all()`.
4. Caches the returned memory block for the whole turn.
5. Injects memory context and plugin context into the current user message only
   for the API request.

Important detail: injected context is ephemeral. It is not written back into the
stored session messages. This preserves prompt-cache stability and avoids
polluting session history.

### Tool Execution Routing

When a model calls a tool, Hermes routes calls in this order:

- built-in special tools
- context-engine tools, such as `context_search` / `context_read`
- memory-provider tools, such as `cc_memory_search`
- regular tool registry functions

After tool execution, Hermes can feed useful evidence back into memory
providers. Current evidence capture includes:

- terminal commands and output
- patch operations and diffs

This evidence is later used to build `CodingSummary` in `sync_turn()`.

### Post-API and Post-Turn Hooks

After each model API response, Hermes invokes `post_api_request` with metadata
such as:

- task id
- session id
- platform
- model/provider/base URL
- api mode
- api call count
- duration
- finish reason
- message count
- usage summary
- assistant content length
- assistant tool call count

This is the main observability point for model-request metrics.

After the tool loop completes, Hermes:

- persists session state
- invokes `post_llm_call`
- syncs external memory for the completed turn
- invokes generic `on_session_end` hook for end-of-turn cleanup/reporting

Note that memory-provider `on_session_end()` and `shutdown_all()` are not called
after every turn. They are reserved for actual session boundaries.

## Current Agent Loop Code Change

The current `run_agent.py` diff adds a runtime guardrail for models that emit
tool-call markup as plain assistant text instead of using structured
`tool_calls`.

### New Detection

A new regex detects patterns like:

- `to=functions.terminal`
- `<function=terminal>`

The helper:

```python
_leaked_text_tool_call_names(content: str) -> List[str]
```

extracts candidate names and only reports a leak if the final tool name exists
in `self.valid_tool_names`.

The wrapper:

```python
_looks_like_leaked_text_tool_call(content: str) -> bool
```

currently delegates to `_leaked_text_tool_call_names()`.

### New Runtime Behavior

The check runs in the no-structured-tool-call branch, where Hermes would
otherwise treat assistant content as the final response.

If leaked tool-call text is detected:

1. Increment `text_tool_leak_retries`.
2. Log a warning with tool names and a snippet.
3. For the first two attempts:
   - emit a status message
   - append an empty assistant message with `finish_reason = "incomplete"`
   - append a user nudge instructing the model to use structured tool calls
   - save the session log
   - continue the loop
4. On the third leak:
   - set `_turn_exit_reason = "text_tool_call_leak_halt"`
   - return a controlled final response explaining the halt
   - append that final response as an assistant message
   - break the loop

The leaked model text itself is not appended to final result messages in the
new tests.

### Why This Is Sensitive

This is direct agent-loop logic, not a plugin. It affects:

- final-response handling
- retry behavior
- message sequence construction
- role alternation
- API-call budget usage
- session persistence

Because other code agents may not allow loop modification, and because upstream
Hermes may reject broad loop changes, this change should be treated as a
high-risk integration point.

## Current Test Coverage

Relevant tests were run with the existing `venv`:

```bash
venv/bin/pytest tests/plugins/memory/test_formsy_memory_provider.py tests/run_agent/test_tool_call_guardrail_runtime.py
```

Result:

```text
48 passed in 7.89s
```

`uv run pytest ...` did not reach test execution because dependency resolution
failed under the current project constraints. The resolver failed on
`anthropic` with `exclude-newer` while solving a Python 3.14 split. This was an
environment/dependency-resolution failure, not a test assertion failure.

### Memory Provider Tests

Current memory provider tests cover:

- config and provider setup helpers
- coding summary construction
- terminal call capture
- confidence clamping
- local memory fallback across provider instances

The newly added local fallback test verifies that:

1. One provider instance records terminal evidence and syncs a turn.
2. A second provider instance using the same Hermes home can recall that
   memory locally.
3. The recalled block contains relevant file and test-command hints.
4. `memory_status` becomes `hit`.
5. `memory_test_hints` includes the prior test command.

### Agent Loop Guardrail Tests

Current runtime guardrail tests cover:

- A model first emits `<function=terminal>` as text, then recovers by returning
  a structured `terminal` tool call.
- Hermes inserts the structured-tool-call nudge.
- Hermes executes the structured tool call after recovery.
- The leaked text is not persisted in final result messages.
- Repeated text-form tool-call leaks halt after retries.
- The result includes `turn_exit_reason = "text_tool_call_leak_halt"`.

## Systematic Test Plan for Agent Loop Changes

This section describes the current desired coverage shape for agent-loop
changes. It is a test plan, not a proposal to broaden the runtime behavior.

### Message Invariant Tests

These should verify that every guardrail path preserves valid chat message
ordering:

- no dangling assistant `tool_calls` without matching tool results
- no orphan tool results
- no invalid consecutive user/assistant sequences for strict providers
- no internal-only fields sent to strict APIs
- no leaked final text accidentally persisted as a normal assistant response

### Retry and Budget Tests

These should verify:

- each leaked text tool-call retry consumes at most one normal loop iteration
- the guardrail respects `max_iterations`
- the guardrail respects `iteration_budget`
- the halt path produces a deterministic `turn_exit_reason`
- retries reset or do not reset the correct counters
- guardrail retries do not interfere with empty-response retries, invalid-tool
  retries, truncated-tool-call retries, or Codex incomplete continuation retries

### Provider Compatibility Tests

The same guardrail behavior should be exercised against normalized responses
that resemble:

- OpenAI Chat Completions
- Anthropic-compatible adapters
- Codex Responses mode
- OpenRouter-style responses
- strict OpenAI-compatible providers that reject unknown fields

The important assertion is that provider-specific response normalization still
produces the same internal assistant message shape before the guardrail runs.

### Tool Surface Tests

These should verify:

- leak detection only triggers for known `valid_tool_names`
- unknown function-like text is left as normal assistant text
- namespaced forms such as `to=functions.terminal` resolve to `terminal`
- context-engine tools such as `context_search` are treated like valid tools
- memory-provider tools such as `cc_memory_search` are treated like valid tools
- ordinary XML or markdown that happens to mention non-tool tags is not halted

### Persistence Tests

These should verify:

- leaked text is not persisted as a normal final assistant message on recovery
- the retry nudge is persisted or omitted consistently with current session-log
  expectations
- halt response is persisted as the final assistant message
- resumed sessions do not re-trigger the same leak from prior internal retry
  messages

### Observability Tests

These should verify:

- `post_api_request` still fires for API responses that later trigger the leak
  guardrail
- `post_llm_call` fires only when a final response is produced and the turn is
  not interrupted
- `on_session_end` hook receives the correct `completed` and `interrupted`
  values
- observability reports count retries and model turns consistently

### Memory and Context Interaction Tests

These should verify:

- memory prefetch is called once per user turn, not once per retry iteration
- ephemeral memory context is not persisted into session history
- context-engine tool schemas remain available after guardrail retry
- `context_search` / `context_read` structured tool calls still execute after a
  text-leak retry
- memory sync happens only for completed turns, not interrupted or partial turns

## Portability Notes for Other Code Agents

Based on the current Hermes implementation, the portable part is the plugin
surface, not the loop-specific guardrail.

Portable surfaces:

- memory provider lifecycle
- context provider tool schemas and tool handler
- observability hooks
- FormSy runtime client
- metrics plus redacted-summary privacy contract

Hermes-specific surfaces:

- direct mutation of `run_agent.py`
- message construction around retries
- iteration budget handling
- provider-specific API message sanitization
- session DB persistence behavior
- context compression and session rotation behavior

For Opencode, Codex, or other code agents, the most reusable contract is:

- pre-turn memory recall injection
- tool-surface registration for context retrieval
- post-tool observation
- post-turn memory sync
- task-level observability hook emission

The least portable contract is direct modification of the model loop after a
malformed assistant response.

## No-Loop-Patch Design

The proposed design for removing direct model-loop modifications has moved to
`FORMSY_NO_LOOP_PATCH_DESIGN.md`. Keep this document focused on the current
Hermes integration state and observed risks.

## Current Known Failure Modes

The existing notes and code paths show these current failure modes:

- FormSy Server unavailable or misconfigured.
- API key missing or invalid.
- Repository compile timeout on large repos.
- Compile failure causing `context_search` degraded recovery.
- Server memory prefetch returning empty memory blocks.
- Local fallback memory matching only by simple token overlap.
- Observability submit failures, handled by local spool.
- Agent models emitting tool calls as text instead of structured tool calls.

These are described here as observed current behavior. This document does not
attempt to prioritize fixes.
