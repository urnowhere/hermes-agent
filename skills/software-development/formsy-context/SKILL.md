---
name: formsy-context
description: Use when solving non-trivial coding tasks in a workspace where FormSy context tools, context_search, context_read, or Completion Verifier are available or mentioned.
version: 1.1.1
author: Hermes Agent
license: MIT
compatibility: hermes
metadata:
  hermes:
    tags: [formsy, context, retrieval, grounding, context-search, context-read, verifier]
    related_skills: [systematic-debugging, test-driven-development]
---

# FormSy Context

## Overview

Use FormSy to search, read, and verify repository context before broad native
exploration shapes a coding plan. FormSy gives repository-scale grounding;
local file reads and shell commands still matter, but they should be targeted by
the context results.

Server guidance remains authoritative for accepted edit targets, patch
readiness, semantic contracts, and Completion Verifier results. This skill
teaches how to use those facts; it does not replace them.

## When To Use

Use this skill when any of these are true:

- the task or environment mentions FormSy, `context_search`, `context_read`, or
  Completion Verifier;
- FormSy context tools are available in the tool list;
- the task is a non-trivial source-code change in a repository;
- the user wants less manual system prompting for FormSy context usage.

Do not force this skill for simple shell questions, formatting-only edits,
one-file explanations where the relevant content is already provided, or tasks
where the user explicitly disables FormSy or external retrieval.

## Tools

- Use `context_search` to find task-relevant code, symbols, tests, and prior
  observations in the FormSy context index.
- Use `context_read` after search returns a relevant path or line range.
- Use `formsy_compile_repo` only for explicit stale-index, missing-index, or
  significant-codebase-change repair.
- No manual `formsy_compile_repo` step is needed before ordinary
  `context_search`; search should ensure compile readiness where the platform
  supports it.
- If a platform requires prerequisites such as a FormSy plugin, gateway URL, or
  API key, satisfy those before expecting the tools to work.

## Retrieval-First Workflow

For non-trivial coding tasks, run `context_search` before broad source
exploration, patching, or final submission unless FormSy tools are unavailable,
the user explicitly disabled FormSy, or you already have current-workspace
evidence for the same accepted target.

Practical first tool batch:

- Prefer `context_search` with the task title, key symbols, expected behavior,
  and suspected file names if known.
- One lightweight sanity command such as `pwd`, `ls`, or `git status --short`
  is acceptable.
- If an expected workspace path such as `/testbed` does not exist, use bounded
  workspace discovery only; do not run `find /` or broad repository scans before
  consuming FormSy grounding.
- Do not start broad `grep`, `find`, many file reads, or patch attempts before
  the seed result.

After `context_search`, read the returned guidance before planning the patch. If
the result names a next action such as `context_read path=<target>`, read that
target first. Broaden only after same-target evidence is insufficient.

## Context Freshness

Interpret `context_read` source metadata conservatively:

- `source=workspace_file` and `working_tree_alignment=matched`: safe to treat as
  current workspace source.
- `source=compiled_repo`: planning evidence; verify the same path from the
  workspace before patching.
- `source=memory`: historical hint; verify current source before patching.
- missing freshness metadata: planning evidence only.
- synthetic grounding-required payload: run the requested search/read or use a
  same-target local fallback if FormSy tools are unavailable.

If `context_read` is unavailable, fails, or returns non-current context, use a
same-target local read fallback. Treat that as preserving FormSy guidance, not
as permission for broad native exploration.

## Patch And Validation

- Patch server accepted edit targets first.
- Keep exploration bounded to facts needed for the patch.
- Prefer compact, path-limited review:
  - `git diff --stat`
  - `git diff -- <accepted-target>`
  - `git diff --check`
- Avoid repeated full diff output or broad repository scans unless a validation
  failure makes them necessary.
- **After a verifier rejection (e.g. `NEED_MORE_VALIDATION`)**: do not repeat
  unchanged final output. Instead, inspect the diff for the specific contract
  violation, fix it, and re-verify. Common FormSy semantic contract violations
  include:
  - Internal code still using legacy aliases after a backward-compat migration.
  - Missing `__all__` updates, missing exports, or broken imports.
  - Incomplete test coverage for new public types.
  - `HostState.__init__` or similar constructors using deprecated constants
    instead of new types.

## Completion Verifier

If a Completion Verifier tool exists, call it before final task submission.

- `NEED_MORE_VALIDATION` means repair the listed blocking conditions.
- Do not repeat unchanged final output after a verifier rejection.
- `ACCEPT_DONE` is the verified completion state.
- After `ACCEPT_DONE`, avoid extra full patch dumps unless the task harness
  explicitly requires final patch output.

## Fallbacks

- If FormSy tools are unavailable, say so briefly and continue with the closest
  available repository-retrieval workflow. Do not invent tool calls.
- If `context_search` reports stale or missing index state, use
  `formsy_compile_repo` when the platform exposes it.
- If `context_read` is stale or compiled-only, verify the same path from the
  working tree before patching.
- If the user explicitly disables FormSy, follow the user's instruction and use
  normal local repo exploration.

## Verification Checklist

- [ ] `context_search` ran before broad source exploration.
- [ ] Returned target guidance was consumed with `context_read` or same-target
      local fallback.
- [ ] Current workspace source was verified before patching when context
      freshness was unknown.
- [ ] Patch touched accepted targets first.
- [ ] Diff review used compact, path-limited commands.
- [ ] Completion Verifier reached `ACCEPT_DONE`, or remaining blockers were
      reported accurately.
