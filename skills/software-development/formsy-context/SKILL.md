---
name: formsy-context
description: Use when solving non-trivial coding tasks in a workspace where FormSy context tools, context_search, context_read, or Completion Verifier are available or mentioned.
version: 1.1.2
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
- If the FormSy plugin/runtime provides a TOCS lookup identity, preserve it in
  the first `context_search` call, for example under
  `metadata.tocs_lookup_identity`. The normal identity is a structured key
  such as `tocs_case_id`, `tocs_run_profile`, `repo_id`, and `base_revision`;
  preserve those fields exactly. If a runtime supplies a legacy or debug
  `tocs_artifact_ref`, forward it unchanged, but do not create it from the
  prompt. Do not ask the user for local artifact paths, do not expose
  machine-specific directories in the task prompt, and do not infer or invent a
  TOCS case id from natural language.
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

## Context Attention

Read high-priority FormSy guidance before forming the patch plan. When the
context is long, do not treat trailing guidance as low-priority background if it
contains task evidence, must-read files, candidate tests, runtime anchors, or a
TOCS-aware evidence chain.

Prefer action-oriented summaries and structured slots over long copied evidence
dumps. First extract the priority summary, must-read files, evidence chain,
candidate tests, and patch traceability hints, then use targeted reads instead
of scanning the whole bundle. Respect TOCS-aware context budgets where
provided. Prefer concise summaries with provenance refs over copied source,
logs, or relation dumps. If a platform provides a TOCS-aware ContextBundle,
treat it as integrated guidance, not as text appended after the normal prompt.

If `context_search` returns `guidance.tocs` or a `### TOCS Guidance` block,
consume it before patch planning. Read `priority_summary` first, then interpret
`lane_b_mode`: `repair_ready_exact` can drive exact validation, while
`diagnostic_source_fallback` means candidate tests are recovered obligations and
may not exist in the base workspace. If `diagnostic_selector_drift` appears,
verify or normalize selectors before relying on test commands. Always consider
`selector_kind` together with `test_source_mode`; an exact benchmark selector
with fallback test source is still a diagnostic guardrail, not proof that the
local test file is present. Use `must_read_files` as the first read set and
`candidate_tests` as validation obligations.

Treat candidate tests as validation obligations, not edit permission. If a
candidate test path is missing from the current workspace, do not reconstruct or
write it from compiled context, memory, bytecode caches, git history, or copied
snippets unless FormSy explicitly lists that test path as an accepted edit
target. Report the missing candidate test source and use the nearest available
same-module validation only when it does not require writing tests.

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
- Do not use `git stash`, temporary checkout, or any command that hides the
  current diff to produce completion evidence. Validation must run against the
  latest patch that Completion Verifier is auditing.

## Completion Verifier

If a Completion Verifier tool exists, call it before final task submission.

- `NEED_MORE_VALIDATION` means repair the listed blocking conditions.
- If the verifier lists failed validation commands, rerun those exact commands
  successfully after the current diff, or explicitly report why the validation
  contract must be narrowed. Do not rerun already passing candidate tests as a
  substitute for unresolved failed commands.
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
- [ ] High-priority FormSy / TOCS-aware guidance was read before planning.
- [ ] Current workspace source was verified before patching when context
      freshness was unknown.
- [ ] Patch touched accepted targets first.
- [ ] Diff review used compact, path-limited commands.
- [ ] Completion Verifier reached `ACCEPT_DONE`, or remaining blockers were
      reported accurately.
