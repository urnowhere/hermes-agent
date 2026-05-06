"""Cross-task transfer policy (A5 — v0.6).

A meta-model learns, from previously-run experiments, which operator
weights + seed corpus produce the best warm-start for a new task
whose :class:`task_features.TaskFeatures` are given.

Design
------
Training ``N`` completed experiments yields rows of
``(task_feature_vector, observed_operator_histogram, best_seeds)``.
We fit the simplest sensible model — a k-NN over cosine distance in
feature space — because it (a) is stdlib-only, (b) makes zero
assumptions about the task distribution, and (c) is directly
interpretable ("this task looks most like X, borrow its prior").
Gradient-boosted trees or a small transformer policy can slot in
later behind the same :class:`TransferPolicy` interface.
"""

from __future__ import annotations

import hashlib
import json
import math
import pickle
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional

import task_features


@dataclass
class TrainingPoint:
    features:  list[float]
    operator_weights: dict[str, float]
    best_seeds: list[str]


@dataclass
class TransferPolicy:
    """k-NN meta-policy over task-feature vectors."""
    points: list[TrainingPoint] = field(default_factory=list)
    k:      int = 3

    @property
    def policy_hash(self) -> str:
        h = hashlib.blake2b(digest_size=8)
        for p in self.points:
            h.update(str(p.features).encode("utf-8"))
            h.update(b"|")
        h.update(str(self.k).encode("utf-8"))
        return h.hexdigest()

    def add(self, point: TrainingPoint) -> None:
        self.points.append(point)

    def predict(
        self,
        features: list[float],
    ) -> dict:
        if not self.points:
            return {"operator_weights": {}, "seeds": [],
                    "neighbours": [], "confidence": 0.0}
        distances = [
            (_cosine_distance(features, p.features), idx)
            for idx, p in enumerate(self.points)
        ]
        distances.sort()
        neighbours = distances[: self.k]
        weights: dict[str, float] = {}
        seeds: list[str] = []
        total_w = 0.0
        for d, idx in neighbours:
            w = 1.0 / (1.0 + d)
            total_w += w
            p = self.points[idx]
            for op, ov in p.operator_weights.items():
                weights[op] = weights.get(op, 0.0) + w * ov
            seeds.extend(p.best_seeds[: max(1, self.k)])
        if total_w > 0:
            weights = {op: v / total_w for op, v in weights.items()}
        return {
            "operator_weights": weights,
            "seeds": list(dict.fromkeys(seeds))[: 8],  # dedup, cap
            "neighbours": [idx for _, idx in neighbours],
            "confidence": 1.0 / (1.0 + neighbours[0][0]),
        }

    def save(self, path: Path) -> None:
        Path(path).write_bytes(pickle.dumps(self))

    @classmethod
    def load(cls, path: Path) -> "TransferPolicy":
        obj = pickle.loads(Path(path).read_bytes())
        if not isinstance(obj, cls):
            raise TypeError(f"not a TransferPolicy: {type(obj)}")
        return obj


# ---------------------------------------------------------------------------
# Similarity
# ---------------------------------------------------------------------------


def _cosine_distance(a: list[float], b: list[float]) -> float:
    if len(a) != len(b):
        return 1.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0 or norm_b == 0:
        return 1.0
    return 1.0 - dot / (norm_a * norm_b)


# ---------------------------------------------------------------------------
# Training
# ---------------------------------------------------------------------------


def collect_training_points(experiment_dirs: Iterable[Path]) -> list[TrainingPoint]:
    """Scan completed experiments; build training points for the policy.

    Each experiment dir must contain a ``lineage.db`` and a
    ``fitness.py``. We featurise the task, read operator frequencies
    from the lineage table, and pick the top-5 genomes as seed
    candidates.
    """
    import storage
    points: list[TrainingPoint] = []
    for exp in experiment_dirs:
        exp = Path(exp)
        db_path = exp / "lineage.db"
        if not db_path.exists():
            continue
        feats = task_features.featurise(exp)
        conn = storage.open_db(db_path)
        try:
            op_rows = conn.execute(
                "SELECT operator, COUNT(*) AS n FROM lineage GROUP BY operator"
            ).fetchall()
            total = sum(r["n"] for r in op_rows) or 1
            weights = {r["operator"]: r["n"] / total for r in op_rows}
            seed_rows = conn.execute(
                "SELECT genome FROM candidates c JOIN fitness f ON f.candidate_id = c.id "
                "WHERE f.held_out = 0 ORDER BY f.value DESC LIMIT 5"
            ).fetchall()
            best = [r["genome"] for r in seed_rows]
        finally:
            conn.close()
        points.append(TrainingPoint(
            features=feats.vector, operator_weights=weights, best_seeds=best,
        ))
    return points


def train_policy(experiment_dirs: Iterable[Path], *, k: int = 3) -> TransferPolicy:
    return TransferPolicy(points=collect_training_points(experiment_dirs), k=k)
