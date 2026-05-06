"""Cross-model validation (C2 — v1.0).

After evolving an experiment on a cheap local model, the user often
wants to know: do the evolved prompts generalise to the expensive
model we'd ship with? This module re-scores the archive's best-K
under a target model and reports Spearman-ρ between the local and
target rankings.

The Spearman correlation captures the RIGHT thing: we don't require
absolute scores to match across models (they rarely do), only the
order of candidates. High ρ = the evolved ranking transferred; low
ρ = the evolved winners are model-specific and you should re-evolve
on the target directly.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Awaitable, Callable


def _spearman(a: list[float], b: list[float]) -> float:
    if len(a) != len(b) or len(a) < 2:
        return 0.0

    def ranks(xs: list[float]) -> list[float]:
        sorted_idx = sorted(range(len(xs)), key=lambda i: xs[i])
        r = [0.0] * len(xs)
        for rank, idx in enumerate(sorted_idx):
            r[idx] = rank + 1
        return r

    ra, rb = ranks(a), ranks(b)
    n = len(a)
    mean_a = sum(ra) / n
    mean_b = sum(rb) / n
    num = sum((ra[i] - mean_a) * (rb[i] - mean_b) for i in range(n))
    den_a = (sum((r - mean_a) ** 2 for r in ra)) ** 0.5
    den_b = (sum((r - mean_b) ** 2 for r in rb)) ** 0.5
    if den_a == 0 or den_b == 0:
        return 0.0
    return num / (den_a * den_b)


@dataclass
class ValidationReport:
    target_model:  str
    spearman:      float
    local_scores:  list[float]
    target_scores: list[float]
    candidate_ids: list[str]


async def cross_model_validate(
    candidates: list[dict],            # [{id, genome, score}]
    *,
    target_scorer: Callable[[str], Awaitable[float]],
    target_model:  str,
) -> ValidationReport:
    """Re-score every candidate under *target_scorer*; return ρ."""
    target_scores = await asyncio.gather(*[
        target_scorer(c["genome"]) for c in candidates
    ])
    local_scores  = [float(c["score"]) for c in candidates]
    ids           = [str(c["id"]) for c in candidates]
    return ValidationReport(
        target_model=target_model,
        spearman=_spearman(local_scores, list(target_scores)),
        local_scores=local_scores,
        target_scores=list(target_scores),
        candidate_ids=ids,
    )
