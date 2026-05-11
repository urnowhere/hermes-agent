# Hermes Agent Retrieval Flow Fixes

## Goal

Hermes should behave like a retrieval-driven coding agent, not a coding agent
that only calls `context_search` once. A successful code patch is not enough to
validate symbolic retrieval. The agent must prove that it followed the expected
retrieval loop.

This document is about agent control flow, not server implementation details.
If `context_search` is weak, the agent must recover by changing its own
retrieval strategy before it falls back to shell exploration.

Expected flow:

```text
seed context_search
  -> inspect result quality
  -> context_read relevant spans
  -> grounded context_search
  -> edit
  -> test
  -> submit
```

If symbolic retrieval fails, Hermes should explicitly fall back to legacy
retrieval before using direct shell/file exploration.

Deadlock is not allowed. If both symbolic and legacy retrieval are weak, Hermes
must enter a bounded degraded-recovery mode instead of blocking indefinitely.

## Required Agent Behavior

### 1. Treat `context_search` as a hard gate

The first retrieval call is mandatory.

The agent must not continue to shell `find`, `grep`, or generic `read_file` if
the seed result is weak.

A seed result is weak if:

```text
matches == []
coverage == "poor"
retrieval_state == "retry"
missing_context is non-empty and no relevant file/symbol is returned
```

If the seed result is weak, the next action must be one of:

```text
retry context_search with a narrower symbolic query
retry context_search using one of suggested_queries
fallback context_search with metadata.retrieval_mode = "legacy"
```

The agent must not enter shell-based file discovery while the search gate is
active. That is a recovery violation, not a valid fallback.

### 2. Enforce a retrieval state machine

Hermes should track an explicit retrieval state:

```text
seed_search
inspect_seed_result
retry_symbolic_search
context_read
grounded_search
legacy_fallback
edit
test
submit
```

Allowed transitions:

```text
seed_search -> inspect_seed_result

inspect_seed_result -> context_read
  only if matches is non-empty and coverage is not poor

inspect_seed_result -> retry_symbolic_search
  if matches is empty or coverage is poor

retry_symbolic_search -> context_read
  only if retry finds useful candidates

retry_symbolic_search -> legacy_fallback
  if symbolic retry is still poor

context_read -> grounded_search
  after confirming relevant files or symbols

grounded_search -> edit
  only after grounded evidence is present

legacy_fallback -> edit
  only after legacy retrieval returns useful context
```

If a search returns `coverage="poor"` or `matches=[]`, the state must remain in
`retry_symbolic_search` or move to `legacy_fallback`. It must not jump to
`edit`, `test`, or shell exploration.

### 3. Require `context_read` before grounding

Generic `read_file` must not count as symbolic grounding.

A file or symbol is grounded only if it came from:

```text
context_read
grounded context_search
legacy fallback context_search
```

Valid grounded metadata:

```json
{
  "grounded_symbols": ["ASCIIUsernameValidator", "UnicodeUsernameValidator"],
  "grounded_files": ["django/contrib/auth/validators.py"]
}
```

### 4. Always run a grounded search before editing

After `context_read` confirms relevant files or symbols, Hermes should run a
second `context_search`:

```json
{
  "query": "confirm UsernameValidator regex anchor fix in validators.py",
  "repo_id": "django__django-11099",
  "revision": "latest",
  "metadata": {
    "retrieval_mode": "symbolic",
    "grounding_phase": "grounded",
    "response_format": "bundle",
    "grounded_symbols": [
      "ASCIIUsernameValidator",
      "UnicodeUsernameValidator"
    ],
    "grounded_files": [
      "django/contrib/auth/validators.py"
    ]
  }
}
```

The agent should not edit before this grounded call unless it has explicitly
entered legacy fallback mode.

### 5. Follow `suggested_queries`

If `context_search` returns `suggested_queries`, Hermes should use one of them
before switching to shell exploration.

Example weak result:

```json
{
  "matches": [],
  "coverage": "poor",
  "suggested_queries": [
    "call flow for ASCIIUsernameValidator UnicodeUsernameValidator username validator regex",
    "tests for ASCIIUsernameValidator UnicodeUsernameValidator username validator regex"
  ],
  "retrieval_state": "retry",
  "preferred_next_step": "context_search"
}
```

Expected next action:

```json
{
  "query": "tests for ASCIIUsernameValidator UnicodeUsernameValidator username validator regex",
  "repo_id": "django__django-11099",
  "revision": "latest",
  "metadata": {
    "retrieval_mode": "symbolic",
    "grounding_phase": "seed",
    "response_format": "bundle"
  }
}
```

### 6. Add explicit legacy fallback

If two symbolic attempts fail, Hermes should fall back explicitly:

```json
{
  "query": "ASCIIUsernameValidator UnicodeUsernameValidator username validator regex",
  "repo_id": "django__django-11099",
  "revision": "latest",
  "metadata": {
    "retrieval_mode": "legacy",
    "grounding_phase": "fallback",
    "response_format": "bundle",
    "retrieval_feedback": "Symbolic seed searches returned no matches and poor coverage."
  }
}
```

Only after this fallback may Hermes use direct repo inspection as a last resort.

### 6.1 Add a shell-unlock rule

Shell file search should be unlocked only after one of these happens:

```text
context_read confirmed a relevant file
grounded context_search returned relevant context
legacy fallback returned relevant context
```

If none of these are true, the agent should keep retrying retrieval rather than
forcing `find`, `grep`, or similar terminal exploration.

### 6.2 Add bounded degraded recovery

If symbolic and legacy retrieval both fail, Hermes must not deadlock on the
gate. It should:

1. record the retrieval failure explicitly,
2. switch to a `degraded_recovery` state,
3. allow bounded shell inspection for the smallest relevant surface,
4. keep the result labeled as low-confidence until a file or symbol is grounded.

This is a controlled escape hatch, not a normal path. The agent still needs to
prefer `context_search` and `context_read`, but it must always have a way
forward.

In `degraded_recovery`, the gate must stop blocking all terminal actions. The
agent may inspect only the already-narrowed candidate surface, but it must be
allowed to continue with shell-based evidence gathering if retrieval remains
insufficient. A failed grounding attempt must downgrade confidence, not freeze
the run.

### 6.3 Prevent full-block states

The runtime must never enter a state where:

```text
context_search is weak
context_read has already been attempted
shell exploration is blocked
editing is blocked
```

If that state is reached, the policy engine must immediately switch to
`degraded_recovery` and unlock the smallest safe shell surface for the selected
candidate files. Deadlock is a policy error.

### 7. Normalize metadata in the plugin

The Hermes plugin should normalize top-level retrieval controls into
`metadata`.

Input like this:

```json
{
  "retrieval_mode": "symbolic",
  "grounding_phase": "seed",
  "response_format": "bundle"
}
```

should be sent to the API as:

```json
{
  "metadata": {
    "retrieval_mode": "symbolic",
    "grounding_phase": "seed",
    "response_format": "bundle"
  }
}
```

Recommended default metadata:

```json
{
  "retrieval_mode": "symbolic",
  "grounding_phase": "seed",
  "response_format": "bundle",
  "trace_id": "<stable-run-id>",
  "case_id": "<swebench-instance-id>"
}
```

### 8. Separate retrieval success from coding success

Hermes should track both:

```text
retrieval_status: good | weak | failed | legacy_fallback
coding_status: fixed | not_fixed | unverified
```

A run can produce a correct patch while still failing retrieval validation.

Example:

```json
{
  "retrieval_status": "failed",
  "coding_status": "fixed"
}
```

This should be reported as a retrieval failure, not a full E2E success.

### 8.1 Record gate failures explicitly

When the terminal is blocked by the retrieval gate, Hermes should log it as an
agent-policy failure with the triggering search state, not as a missing file or
server outage.

### 8.2 Never allow an unrecoverable gate

The gate must never end in a state where every tool is blocked. If the current
search state is weak and the agent has exhausted symbolic retries plus legacy
fallback, the gate should downgrade to degraded recovery instead of refusing
all terminal actions.

### 9. Block editing until retrieval is valid

Hermes should not edit source files until at least one condition is true:

```text
context_read confirmed a relevant file
grounded context_search returned relevant context
legacy fallback returned relevant context
```

If none is true, editing should be blocked.

If retrieval remains invalid after bounded degraded recovery, Hermes should
continue gathering shell evidence or retry retrieval. It must not sit in a
state where editing is blocked and no follow-up action is permitted.

### 10. Add retrieval decision logs

For each retrieval step, Hermes should log:

```text
current retrieval state
query used
result quality
reason for accepting or rejecting result
next state
fallback reason, if any
```

Example:

```json
{
  "retrieval_state": "inspect_seed_result",
  "query": "ASCIIUsernameValidator UnicodeUsernameValidator username validator regex",
  "coverage": "poor",
  "matches_count": 0,
  "decision": "retry_symbolic_search",
  "reason": "No structured file or symbol matches were selected."
}
```

## Minimal Acceptance Criteria

A Hermes run validates symbolic retrieval only if the trace contains:

```text
1. At least one seed context_search with metadata.retrieval_mode = symbolic
2. If seed is poor, at least one retry or legacy fallback
3. At least one context_read for exact source inspection
4. At least one grounded context_search with grounded_files or grounded_symbols
5. No source edit before retrieval is grounded or legacy fallback is complete
6. Final patch and tests still pass
```

## Current Trace Failure Pattern

The recent traces fail because Hermes does this:

```text
context_search seed
  -> poor result
  -> shell find / grep
  -> read_file
  -> edit/submit
```

Expected behavior:

```text
context_search seed
  -> poor result
  -> retry symbolic or legacy fallback
  -> context_read
  -> grounded context_search
  -> edit/submit
```

This is an agent-side recovery bug. The server may still be returning weak
results, but the immediate regression is that the agent does not recover cleanly
from those weak results.

## Summary

The main fix is to make Hermes enforce retrieval as a control-flow requirement.
Prompt instructions are useful, but they are not enough. The plugin or agent
runtime should gate state transitions so the model cannot silently bypass
symbolic retrieval after a poor seed result.
