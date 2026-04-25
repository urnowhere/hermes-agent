# Persistent Memory V3 — Optional Semantic Retrieval

Goal: add optional semantic retrieval for local persistent memory without replacing the current sqlite-ranked lexical system that already works.

Why this is phase 3:
- V2 already gives real durable memory, correction/forget semantics, and compact startup recall.
- Cross-machine movement now exists via portable memory snapshots.
- Semantic retrieval only matters if lexical/ranked retrieval misses meaning often enough to justify new complexity.

Principles:
1. Keep sqlite-backed V2 as source of truth.
2. Semantic retrieval must be optional and local-first.
3. Fallback to current lexical ranking when embeddings are unavailable.
4. Never let semantic retrieval resurrect forgotten/superseded rows.

Proposed design:
- Add `memory_embeddings` table keyed by memory entry id.
- Store vector metadata plus embedding model name/version.
- Compute embeddings only for active rows, but keep tombstone filters in retrieval.
- Use hybrid scoring:
  final_score = lexical_score + semantic_score + kind/importance/scope bonuses
- Rebuild embeddings when content changes or model version changes.

Candidate implementation paths:
A. simplest local path
- sentence-transformers mini model
- cosine similarity in Python over cached vectors
- no external service

B. optional higher-performance path
- local sqlite extension / vector index only if environment supports it
- otherwise stay on path A

Minimum tests before implementation:
- semantically related phrasing retrieves the right preference even without keyword overlap
- forgotten/superseded entries never appear
- lexical fallback still works when embeddings unavailable
- startup prompt stays within budget
- embedding rebuild is deterministic and idempotent

Exit criteria:
- a query like "keep it brief" retrieves "User prefers brutally concise replies"
- semantic layer can be disabled without breaking V2
- no hosted dependency required

Current recommendation:
Do not implement yet by default. Measure whether V2 lexical ranking actually misses meaningful recalls in practice first.
