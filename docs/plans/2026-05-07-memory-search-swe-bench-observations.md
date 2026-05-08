# SWE-bench Memory Search Observation Notes

**Date:** 2026-05-07

**Trajectory:** `/Users/xx/.hermes/sessions/saved/hermes_conversation_20260507_160551.json`

**Target repo:** `/Users/xx/software/mini-swe-agent/runs/data/repos/django__django`

**Task:** Evaluate whether `memory_search` helped in a SWE-bench run and identify interface improvements.

---

## Summary

`memory_search` did help, but only at the first level: it quickly located the relevant Django source area. It did not reliably guide the agent toward the correct repair semantics, and it did not keep the agent inside the intended memory-first source-context workflow.

The trajectory shows that the first `memory_search` call immediately returned the central implementation of `HashedFilesMixin.post_process()` in `django/contrib/staticfiles/storage.py`. However, the agent later used direct file-reading tools and produced a one-line patch that removed duplicate yields from subsequent passes. That patch addresses the visible duplicate-yield symptom, but it likely loses the expected final hashed filename in yielded results because the first pass can yield an intermediate hash before later passes settle nested references.

The main lesson is that `memory_search` currently works as a semantic locator, not as a complete code-context interface for SWE-bench. The tool needs better structured results and a companion precise-read mode so agents can stay within the memory workflow while still doing rigorous source analysis.

---

## Evidence From The Trajectory

The run made three `memory_search` calls:

1. `HashedFilesMixin post_process yields duplicate`
2. `django/contrib/staticfiles/storage.py HashedFilesMixin`
3. `collectstatic collect post_process yielded files stats`

The first result was high value. It returned the relevant `post_process()` implementation around lines 203-249, including:

- the initial `_post_process()` pass that yielded every file;
- the loop over `max_post_process_passes`;
- the second yield inside the repeated pass loop;
- the final `self.hashed_files.update(hashed_files)`.

This was enough to locate the bug surface.

The second result returned a broader class snippet for `HashedFilesMixin`, which helped with local context.

The third query was less useful. It was intended to inspect how collectstatic consumes yielded values, but the returned context was noisy and did not clearly surface the collectstatic consumer logic. This matters because the correct fix depends on what the yielded `hashed_name` means to callers.

After these calls, the agent switched to direct source reads:

- `read_file` on `/testbed/django/contrib/staticfiles/storage.py`, which failed because `/testbed` did not exist;
- `pwd && ls`, discovering the actual repo path;
- `read_file` on `django/contrib/staticfiles/storage.py`, which succeeded.

This indicates the current tool setup did not make `memory_search` feel sufficient as the source inspection path, despite the SWE-bench instruction that source-content lookup should go through memory.

---

## Patch Quality Concern

The submitted patch removed this line from the repeated pass loop:

```python
yield name, hashed_name, processed
```

This prevents duplicate yields, but it probably does not satisfy the full behavioral requirement. The PR description expects one yielded result per original file, and the example expects that single result to contain the final settled hashed name:

```text
Post-processed 'admin/css/base.css' as 'admin/css/base.6b517d0d5813.css'
```

The submitted patch yields during the first pass and suppresses later yields. For adjustable files, the first pass can produce an intermediate hash before nested references stabilize. Later passes still update `hashed_files`, but callers that consume the generator already received the first-pass tuple.

So the likely correct implementation shape is closer to:

- run the processing passes;
- retain the latest successful `(name, hashed_name, processed)` per original file;
- yield each original file once, using its final result;
- preserve error yields such as max-pass exhaustion and processing exceptions.

This is exactly the kind of semantic distinction that a better memory interface should help agents discover before they patch.

---

## Current Interface Limitations

The `memory_search` schema in `plugins/context_engine/formsy/engine.py` currently exposes:

- `query`
- `repo_id`
- `revision`
- `budget`
- deprecated `limit`

The runtime client sends:

- `repo_id`
- `query`
- `revision`
- `budget`
- profiling flags
- metadata

The tool response currently returns only:

```json
{
  "ok": true,
  "query": "...",
  "extra_context": "..."
}
```

This drops potentially useful backend structure such as matches, paths, scores, symbol names, line ranges, and retrieval rationale. Tests explicitly assert that `matches` are not returned to the agent.

That design makes the tool easy to consume as prose context, but weak for rigorous code work. The agent cannot reliably know:

- which files were hit;
- whether the answer is complete or partial;
- which line ranges should be inspected next;
- whether a result is a symbol definition, caller, test, or unrelated text match;
- what query would retrieve the next missing piece.

---

## Recommended Changes

### 1. Return structured retrieval metadata

Keep `extra_context`, but add structured fields:

```json
{
  "ok": true,
  "query": "HashedFilesMixin post_process",
  "extra_context": "...",
  "matches": [
    {
      "path": "django/contrib/staticfiles/storage.py",
      "start_line": 203,
      "end_line": 249,
      "score": 0.91,
      "kind": "symbol_definition",
      "symbol": "HashedFilesMixin.post_process",
      "why_relevant": "Defines the generator that yields duplicate post-process results."
    }
  ],
  "suggested_queries": [
    "collectstatic collect post_process yielded hashed_name",
    "_post_process hashed_files substitutions final hashed_name",
    "tests staticfiles post_process duplicate yields final hash"
  ]
}
```

This should be exposed to the agent rather than stripped by the context-engine adapter.

### 2. Add a precise code-context read tool

`memory_search` is not enough for source inspection. SWE-bench agents need exact snippets once a relevant file and line range are known.

Add either a new tool:

```json
{
  "name": "memory_read",
  "arguments": {
    "repo_id": "django__django-14053",
    "revision": "latest",
    "path": "django/contrib/staticfiles/storage.py",
    "start_line": 203,
    "end_line": 330
  }
}
```

Or extend `memory_search` with a `mode`:

```json
{
  "mode": "read",
  "path": "django/contrib/staticfiles/storage.py",
  "start_line": 203,
  "end_line": 330
}
```

Prefer a separate `memory_read` or `code_context_read` tool. It makes tool intent clearer and avoids overloading natural-language search with exact source reads.

### 3. Add query intent

Search quality should improve if the caller can declare what it is looking for:

```json
{
  "intent": "symbol_definition | call_flow | caller | tests | behavior | regression | implementation_plan",
  "query": "collectstatic collect post_process yielded files stats",
  "repo_id": "django__django-14053",
  "revision": "latest"
}
```

For this trajectory, `intent=call_flow` or `intent=behavior` should have prioritized `collectstatic` consumer logic over unrelated settings code.

### 4. Make repo identity explicit or automatic

The tool schema only requires `query`, but runtime handling requires `repo_id` unless configured elsewhere. This mismatch invites avoidable failed calls.

For SWE-bench runs, prefer automatic injection from task metadata. If that is not guaranteed, mark `repo_id` as required in the tool schema for this context.

### 5. Include result completeness signals

Return simple signals so the agent knows whether to keep searching:

```json
{
  "coverage": "partial",
  "missing_context": [
    "caller behavior for yielded hashed_name",
    "tests asserting collectstatic post_processed stats"
  ]
}
```

This would directly counter the trajectory's premature statement that the agent had a "complete understanding" after only locating the producer side.

### 6. Tighten SWE-bench tool guidance

The prompt says to use `memory_search`, but the available tools still allowed direct `read_file` source inspection. If the evaluation goal is to test memory-assisted coding, the harness should either:

- disable direct source-content reading tools for indexed repositories; or
- make those tools redirect to `memory_read`; or
- add a policy layer that rejects source reads and suggests the equivalent memory call.

Without enforcement, the agent can drift back to normal file-reading behavior.

---

## Suggested Minimal Implementation Plan

1. Update `FormsyContextEngine.handle_tool_call()` to preserve structured search fields from the runtime response.
2. Add tests that verify `matches`, `suggested_queries`, and optional coverage metadata survive the adapter.
3. Add a `memory_read` tool schema to `FormsyContextEngine.get_tool_schemas()`.
4. Add a runtime client method for exact file/range retrieval, or reuse the query endpoint with `mode=read` if that is what the backend supports.
5. Update SWE-bench prompt guidance to use:
   - `memory_search` for discovery;
   - `memory_read` for exact source context;
   - repeated targeted searches for call flow and tests before patching.
6. Add a small trajectory-level evaluator that records:
   - number of memory calls before first patch;
   - whether direct source reads were used;
   - whether final patch depended on memory-provided lines;
   - whether memory results included caller/test context.

---

## Success Criteria

A better run on this same task should show:

- `memory_search` finds `HashedFilesMixin.post_process()`;
- structured matches identify `django/contrib/staticfiles/storage.py`;
- `memory_read` retrieves the exact `post_process()` and `_post_process()` ranges;
- a follow-up `memory_search` with `intent=call_flow` finds collectstatic consumer behavior;
- a follow-up `memory_search` with `intent=tests` finds relevant staticfiles tests;
- the agent recognizes that a single yield should contain the final hashed name;
- the patch yields each original file once with final settled data, rather than merely suppressing later yields.

