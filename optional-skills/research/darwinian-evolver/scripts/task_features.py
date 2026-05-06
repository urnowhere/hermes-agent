"""Task featurisation for cross-task transfer (A5 — v0.6).

Given an experiment (its fitness.py, seeds, objectives, best-so-far
lineage), emit a compact feature vector that the transfer policy
can key on. The policy is a small gradient-boosted classifier that
predicts ``(operator_weights, seed_corpus_tag)`` from these features;
cold-starting a new experiment with the predicted initial state
shortcuts the first few generations.

Feature vector (all floats, stable order)
----------------------------------------
0  len(fitness.py) normalised
1  number of I/O examples declared in fitness.py
2  avg seed length in characters
3  objective count (1 = scalar, >1 = multi-objective)
4  contains 'pairwise' in fitness_spec string
5  contains 'critic' in fitness_spec string
6  fitness archetype hash mod 17 (discrete task family)
7  seed-token entropy (Shannon over char 3-grams)
8  judge presence indicator (judge='pairwise' or 'scalar' fallback)
"""

from __future__ import annotations

import hashlib
import math
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


_ARCHETYPE_HINTS = {
    "exact match":   1, "exact":  1,
    "soft":          2, "levenshtein": 2,
    "judge":         3, "pairwise":   3,
    "regex":         4, "re.compile": 4,
    "sql":           5, "sqlite3":    5,
    "pytest":        6, "sandbox":    6,
}


@dataclass
class TaskFeatures:
    experiment: str
    vector:     list[float]
    keywords:   list[str]

    def to_dict(self) -> dict:
        return {"experiment": self.experiment, "vector": self.vector, "keywords": self.keywords}


def featurise(experiment_dir: Path) -> TaskFeatures:
    experiment_dir = Path(experiment_dir)
    fit_src = (experiment_dir / "fitness.py").read_text("utf-8", errors="replace") \
        if (experiment_dir / "fitness.py").exists() else ""
    seeds = [p.read_text("utf-8", errors="replace")
             for p in (experiment_dir / "seed").glob("*.txt")] \
        if (experiment_dir / "seed").exists() else []

    vector: list[float] = [
        min(len(fit_src) / 4096, 4.0),
        float(sum(1 for _ in re.finditer(r'"input"\s*:\s*"', fit_src))),
        (sum(len(s) for s in seeds) / max(len(seeds), 1)) / 500.0 if seeds else 0.0,
        _count_objectives(fit_src),
        1.0 if "pairwise" in fit_src else 0.0,
        1.0 if 'critic="on"' in fit_src else 0.0,
        _archetype_hash(fit_src),
        _char_ngram_entropy(" ".join(seeds), n=3),
        1.0 if 'judge=' in fit_src else 0.0,
    ]
    low = fit_src.lower()
    keywords = [k for k in _ARCHETYPE_HINTS if k in low][:8]
    return TaskFeatures(
        experiment=experiment_dir.name,
        vector=vector,
        keywords=keywords,
    )


def _count_objectives(src: str) -> float:
    m = re.search(r"objectives\s*=\s*\[([^\]]*)\]", src)
    if not m:
        return 1.0
    return float(len([p for p in m.group(1).split(",") if p.strip()]) or 1)


def _archetype_hash(src: str) -> float:
    low = src.lower()
    tally = 0
    for kw, w in _ARCHETYPE_HINTS.items():
        if kw in low:
            tally += w
    return float(tally % 17)


def _char_ngram_entropy(text: str, n: int = 3) -> float:
    text = text.strip()
    if len(text) < n:
        return 0.0
    grams = Counter(text[i:i+n] for i in range(len(text) - n + 1))
    total = sum(grams.values())
    return -sum((c / total) * math.log2(c / total) for c in grams.values())
