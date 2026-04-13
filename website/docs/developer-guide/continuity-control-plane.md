---
sidebar_position: 11
title: "Continuity Control Plane"
description: "The umbrella over Hermes durable memory, session recall, wiki synthesis, reset handoff, and recovery rails"
---

# Continuity Control Plane

Hermes now has multiple continuity rails. That is useful, but it can also turn into a bureaucratic nightmare where facts live in five places and none of them show up when needed.

This document names the umbrella: the Continuity Control Plane.

Short version:
- persistent memory v2 is the local durable fact store
- session search is transcript recall
- llm-wiki is the markdown synthesis lane
- Clerk is one-turn reset continuity
- bootstrap recovery is machine resurrection and portability
- chain-backed continuity rails are the recovery truth lane outside the core repo

The point is not to pretend these are one database. The point is to treat them as one governed continuity system.

## Why this exists

Before this umbrella, Hermes continuity work landed in pieces:
- SQLite-backed memory for durable facts
- prompt-time memory retrieval and markdown export compatibility
- llm-wiki recall for deeper synthesized context
- session_search for transcript history
- Clerk reset handoff for first-turn continuity after `/reset`
- portable bootstrap and recovery artifacts

All of those pieces solve real problems. None of them, by themselves, answer the full question:

"How does Hermes remember the right thing at the right time, survive resets, and recover on a fresh machine without resurrecting stale lies?"

That larger question is the Continuity Control Plane.

## The lanes

### 1. Persistent Memory v2

Files:
- `tools/persistent_memory_store.py`
- `tools/memory_tool.py`
- `plans/persistent-memory-v2.md`

Purpose:
- store durable user preferences, prohibitions, instructions, environment facts, and project notes in `~/.hermes/memory.db`
- preserve correction and forgetting semantics
- render a compact startup memory packet instead of dumping the whole landfill into the prompt

Authority:
- canonical local durable fact store inside Hermes Agent

### 2. Session Search

Files:
- `tools/session_search_tool.py`
- `hermes_state.py`

Purpose:
- search prior transcripts when the question is about what happened in earlier sessions
- keep transcript history separate from durable facts

Authority:
- transcript recall, not durable preference storage

### 3. LLM Wiki Memory Lane

Files:
- `agent/llm_wiki.py`
- `tests/agent/test_llm_wiki.py`

Purpose:
- mirror durable memory into a markdown wiki structure
- retrieve synthesized, query-relevant notes beyond the startup hot-memory packet
- provide a human-inspectable and extensible knowledge layer

Authority:
- derived lane; useful for synthesis, not the source of truth

### 4. Clerk Reset Handoff

Files:
- `cli.py`
- `tests/cli/test_cli_new_session.py`

Purpose:
- preserve one-turn continuity across `/reset`
- hand off the minimum useful state, then clear it

Authority:
- reset continuity lane, not general long-term memory

### 5. Portable Bootstrap and Recovery

Files:
- `bootstrap_recovery/foureleven_bootstrap.py`
- `bootstrap_recovery/build_windows.bat`
- `plans/foureleven-stage0-bootstrap.md`

Purpose:
- inspect a Hermes home for recoverability
- export and restore deterministic bundles
- move identity, memory, and recovery artifacts onto a fresh machine

Authority:
- recovery and portability lane

### 6. Chain-backed continuity rails

Primary implementation today lives outside the core Hermes repo.

Representative files in Hermes home:
- `~/.hermes/bin/hclerk`
- `~/.hermes/scripts/clerk_pass.py`
- `~/.hermes/scripts/clerk_restore.py`
- `~/.hermes/scripts/chain_of_shells.py`

Purpose:
- produce recovery packets
- maintain chain-of-shells indexes and bundle manifests
- anchor restore-critical continuity outside the local runtime

Authority:
- recovery truth and external continuity verification

## What is committed in this branch already

The current branch bundles real shipped work in this repo:
- `feat: add sqlite-backed persistent memory v2`
- `feat: add portable memory snapshot import/export`
- `docs: add semantic retrieval phase 3 plan`
- `feat: add foureleven stage-0 recovery bootstrap core`
- `feat: restore llm wiki memory layer`
- `fix(memory): respect live home and test overrides`
- `fix(memory): wire live paths and avoid cli parser collision`
- `feat(cli): restore Clerk reset handoff`
- `fix(clerk): keep reset handoff to one chain write`

That is the current bundle.

## What is not claimed yet

This branch does not claim that Hermes already has a perfect grand-unified memory brain.

Specifically, the next layer is still in flight:
- explicit lane registry
- canonical memory event contract
- canonical recall receipt contract
- write compiler over multiple lanes
- recall assembler with proof surfaces
- formal recovery policy and supersession policy across lanes

That work is the difference between:
- several useful continuity rails existing
and
- one real control plane governing them.

## Design rules

1. Do not collapse transcript search into durable memory.
2. Do not pretend derived wiki pages outrank canonical source facts.
3. Do not use Clerk as a replacement for long-term memory.
4. Do not call bootstrap portability the same thing as live recall.
5. Do not let forgotten or superseded facts come back from a side lane.
6. Do not claim unification just because the components can all spell the word memory.

## Practical mental model

If the user asks:
- "remember my preference" -> Persistent Memory v2
- "what did we work on last week" -> Session Search
- "what broader notes relate to this topic" -> LLM Wiki lane
- "what was I doing right before reset" -> Clerk handoff
- "can I restore this shell on a fresh machine" -> Bootstrap + recovery bundle
- "what is the external continuity truth for recovery-critical state" -> chain-backed rails

That routing logic is the real heart of the control-plane thesis.

## Honest status

Hermes already has a continuity stack.
The repo did not need a fake new invention. It needed one name, one map, and one governing thesis.

That thesis is the Continuity Control Plane.
