# PR draft — Continuity Control Plane

Title:
feat(memory): introduce the Continuity Control Plane umbrella for Hermes memory and recovery work

## Summary

This PR bundles Hermes continuity work under one explicit umbrella: the Continuity Control Plane.

The branch already includes the real implementation pieces:
- SQLite-backed persistent memory v2
- markdown export/import compatibility
- llm-wiki recall lane
- portable memory snapshot import/export
- stage-0 bootstrap recovery core
- Clerk reset handoff restoration and hardening

This PR adds the naming and architecture doc that explains how those pieces fit together without pretending they are all the same subsystem.

## Why

Hermes continuity work landed in several real rails over time:
- durable local memory
- transcript recall
- wiki synthesis
- reset continuity
- recovery/bootstrap
- chain-backed external recovery truth

The missing piece was not another memory backend.
It was one honest umbrella and one vocabulary for the lanes.

## What this PR claims

- Hermes has a continuity stack, not just a memory file
- persistent memory v2 is the canonical local fact store
- session_search is transcript recall, not user-preference storage
- llm-wiki is a derived synthesis lane
- Clerk is a reset continuity lane
- bootstrap recovery is a resurrection/portability lane
- chain-backed continuity remains the external recovery truth lane

## What this PR does not claim

This PR does not claim the full control-plane governance layer is complete.
It does not claim all recall routing, write compilation, supersession, and recovery policy have been formally unified yet.

That next layer is separate work.

## Branch inventory

Committed branch history included here:
- `feat: add sqlite-backed persistent memory v2`
- `feat: add portable memory snapshot import/export`
- `docs: add semantic retrieval phase 3 plan`
- `feat: add foureleven stage-0 recovery bootstrap core`
- `feat: restore llm wiki memory layer`
- `fix(memory): respect live home and test overrides`
- `fix(memory): wire live paths and avoid cli parser collision`
- `feat(cli): restore Clerk reset handoff`
- `fix(clerk): keep reset handoff to one chain write`

## Verification

Evidence collected locally on this branch:
- branch is 9 commits ahead of `main`
- key continuity files are present in the committed diff:
  - `tools/persistent_memory_store.py`
  - `agent/llm_wiki.py`
  - `bootstrap_recovery/foureleven_bootstrap.py`
  - `cli.py` reset handoff changes
- focused control-plane contract tests on the in-flight next layer also pass locally:
  - `tests/agent/test_memory_event.py`
  - `tests/agent/test_memory_lanes.py`
  - `tests/agent/test_recall_receipt.py`
  - `tests/agent/test_write_compiler.py`

## Follow-up work

The next real step is to formalize the Continuity Control Plane as runtime policy:
- lane registry
- memory event contract
- recall receipt contract
- write compiler
- recall assembler
- recovery and supersession policy
- proof surfaces in CLI/doctor

## Suggested reviewer framing

Please review this PR as:
- a naming + architecture umbrella over already-committed continuity work
- not as a claim that all future control-plane work is already finished
