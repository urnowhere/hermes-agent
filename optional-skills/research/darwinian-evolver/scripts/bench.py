"""Benchmark hub registry + scorer (C1 — v1.0).

A tiny registry of canonical tasks with frozen fitness functions.
Anyone who wants to compare evolutionary runs can submit a
``lineage.db`` + the benchmark id; we reproduce the score
deterministically by loading the corresponding fitness module and
re-scoring the archive's best-K.

This file ships the registry + scorer plumbing. The companion
``NousResearch/hermes-evolver-leaderboard`` repo (append-only JSONL)
is used for publication; that integration lives under a ``publish``
command added later in the v1.0 ecosystem sprint.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable


@dataclass
class Benchmark:
    id:          str
    title:       str
    description: str
    fitness:     Callable[[str, dict], float]
    examples:    list[dict] = field(default_factory=list)


_REGISTRY: dict[str, Benchmark] = {}


def register(bench: Benchmark) -> None:
    if bench.id in _REGISTRY:
        raise ValueError(f"benchmark {bench.id!r} already registered")
    _REGISTRY[bench.id] = bench


def get(bench_id: str) -> Benchmark:
    if bench_id not in _REGISTRY:
        raise KeyError(f"no benchmark {bench_id!r}; list: {sorted(_REGISTRY)}")
    return _REGISTRY[bench_id]


def list_benchmarks() -> list[dict]:
    return [
        {"id": b.id, "title": b.title, "description": b.description,
         "examples_n": len(b.examples)}
        for b in sorted(_REGISTRY.values(), key=lambda b: b.id)
    ]


# ---------------------------------------------------------------------------
# Canonical fitness implementations
# ---------------------------------------------------------------------------


def _email_regex_fitness(candidate: str, ctx: dict) -> float:
    positives = [
        "user@example.com", "first.last+tag@sub.domain.org",
        "a.b.c@d.co",
    ]
    negatives = [
        "not an email", "a@b", "@nope.com", "two@@at.example",
    ]
    try:
        pat = re.compile(candidate)
    except re.error:
        return 0.0
    tp = sum(1 for s in positives if pat.fullmatch(s))
    fp = sum(1 for s in negatives if pat.fullmatch(s))
    p = tp / (tp + fp) if (tp + fp) else 0.0
    r = tp / len(positives)
    return 0.0 if p + r == 0 else 2 * p * r / (p + r)


def _ten_word_summary_fitness(candidate: str, ctx: dict) -> float:
    words = re.findall(r"\b\w+\b", candidate)
    diff = abs(len(words) - 10) / 10
    return max(0.0, 1.0 - diff)


def _sql_select_fitness(candidate: str, ctx: dict) -> float:
    import sqlite3
    conn = sqlite3.connect(":memory:")
    try:
        conn.executescript(
            "CREATE TABLE users(id INT, age INT);"
            "INSERT INTO users VALUES (1,31),(2,42),(3,28),(4,55),(5,19);"
        )
        rows = conn.execute(candidate).fetchall()
    except sqlite3.Error:
        return 0.0
    finally:
        conn.close()
    return 1.0 - min(1.0, abs(len(rows) - 3) / 3)  # expect 3 users > 25


# ---------------------------------------------------------------------------
# Initial registrations
# ---------------------------------------------------------------------------


register(Benchmark(
    id="email-regex/v1",
    title="Email regex F1",
    description="F1 over a fixed 3/4 positive/negative corpus.",
    fitness=_email_regex_fitness,
    examples=[{"input": "", "output": r"^[^@]+@[^@]+\.[^@]+$"}],
))

register(Benchmark(
    id="ten-word-summary/v1",
    title="Prompt producing 10-word summaries",
    description="Rewards prompts that elicit exactly 10-word outputs.",
    fitness=_ten_word_summary_fitness,
    examples=[
        {"input": "1984 is a dystopian novel by George Orwell.",
         "output": "Orwell's dystopia warns how totalitarian power twists truth and memory."},
    ],
))

register(Benchmark(
    id="sql-select-easy/v1",
    title="SELECT users older than 25",
    description="Fixture table; expects 3 rows.",
    fitness=_sql_select_fitness,
    examples=[{"input": "", "output": "SELECT * FROM users WHERE age > 25;"}],
))


# ---------------------------------------------------------------------------
# Scorer
# ---------------------------------------------------------------------------


def score_archive(conn, bench_id: str, top_k: int = 5) -> dict:
    """Re-score the top-K of an archive against a registered benchmark."""
    import storage
    bench = get(bench_id)
    rows = storage.get_best(conn, "fitness", k=top_k)
    scores = []
    for row in rows:
        s = bench.fitness(row["genome"], {"seed": 0, "fidelity": 1.0, "held_out": False})
        scores.append({"id": row["id"], "score": float(s),
                       "preview": row["genome"][:140]})
    best = max((s["score"] for s in scores), default=0.0)
    return {"benchmark": bench_id, "scores": scores, "best": best}
