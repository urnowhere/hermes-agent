"""Self-adapting descriptor controller for MAP-Elites (A1).

Every ``trigger_every_k`` generations, the controller asks an LLM
whether the current behavioral descriptor is "illuminating" the
search space well. Signals it inspects:

* **Coverage**: fraction of bins populated. A low coverage on a
  fine-grained grid suggests the descriptor is too discriminating;
  a coverage plateau combined with flat fitness suggests the
  descriptor is not discriminating ENOUGH.
* **Population entropy per axis**: how much real variation exists
  along each dimension? A descriptor axis where every candidate
  lands in the same bin is noise.
* **Recent fitness trajectory**: if fitness has plateaued but the
  archive is also full, the descriptor may need to open new axes.

Safety
------
The LLM's reply is parsed through ``descriptor_dsl.parse_descriptor``.
Any parse error, missing action field, or invalid grid is treated as
"keep current" — a bad proposal never corrupts the run. This is the
same contract as the v0.2 critic's malformed-JSON path.

The controller never writes Python. All new descriptors come from a
whitelist of feature extractors, so an adversarial LLM cannot
pivot this into arbitrary code execution.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from typing import Any, Optional

import algorithms
import descriptor_dsl
from llm import LLMClient


# ---------------------------------------------------------------------------
# Structured signals we give the LLM
# ---------------------------------------------------------------------------


def _summarise_archive(archive: algorithms.MapElitesArchive) -> dict[str, Any]:
    """Reduce the archive to a small JSON blob the LLM can reason about."""
    occupied = len(archive.cells)
    capacity = 1
    for c in archive.bin_counts:
        capacity *= c
    # Per-axis histogram: how many unique bin values are seen along each axis?
    axis_unique: list[int] = []
    for dim in range(len(archive.bin_counts)):
        uniq = {coord[dim] for coord in archive.cells}
        axis_unique.append(len(uniq))
    # Fitness stats across the archive.
    scores = []
    for ind in archive.cells.values():
        f = ind.fitness
        if isinstance(f, dict):
            # Use the controller's target objective if set, else the first key.
            key = archive.objective or next(iter(f))
            scores.append(float(f.get(key, float("nan"))))
        elif not (isinstance(f, float) and math.isnan(f)):
            scores.append(float(f))
    scores = [s for s in scores if not math.isnan(s)]
    return {
        "capacity":       capacity,
        "occupied":       occupied,
        "coverage":       round(occupied / capacity, 4) if capacity else 0,
        "bin_counts":     list(archive.bin_counts),
        "axis_unique":    axis_unique,
        "fitness_best":   max(scores) if scores else None,
        "fitness_median": sorted(scores)[len(scores) // 2] if scores else None,
    }


def _fitness_plateau(deltas: list[float], window: int = 4, tol: float = 1e-3) -> bool:
    """Return True when the last *window* generations have sub-*tol* deltas."""
    if len(deltas) < window:
        return False
    return max(abs(d) for d in deltas[-window:]) < tol


# ---------------------------------------------------------------------------
# Controller system prompt
# ---------------------------------------------------------------------------


_SYSTEM = """You are a MAP-Elites descriptor designer. The archive's
behavioral descriptor partitions the search space; your job is to
decide whether the current descriptor is illuminating the space well
and, if not, propose a better one from a fixed whitelist of feature
extractors.

Available extractors (use EXACTLY these; do NOT invent new names):

  length(bins=N, max=M)              log-scaled text length
  cot_presence()                     binary chain-of-thought marker
  token_entropy(bins=N, max=M)       Shannon entropy of word frequency
  punctuation_density(bins=N)        punctuation chars per 100 chars
  reading_grade(bins=N)              Flesch-Kincaid-style grade level
  embedding_cluster(k=N)             (reserved — acts as constant 0)

Emit ONE JSON object, no prose, schema:

  {
    "action": "keep" | "replace",
    "grid":   "grid(extractor(...), extractor(...))",
    "reason": "<one-sentence justification>"
  }

When "action" is "keep", the "grid" field may be empty. When
"action" is "replace", the grid must be a valid expression whose
extractors each declare their bins (if they take a `bins=` kwarg).
Prefer 2-D grids with 4-16 bins per axis; 1-D grids waste the
controller's capacity; 3+-D grids fragment the archive.
"""


# ---------------------------------------------------------------------------
# Controller
# ---------------------------------------------------------------------------


@dataclass
class DescriptorProposal:
    action: str                   # "keep" | "replace"
    grid: Optional[descriptor_dsl.DescriptorFn] = None
    reason: str = ""
    raw: str = ""


@dataclass
class DescriptorController:
    client: LLMClient
    trigger_every_k: int = 5
    mode: str = "periodic"       # off | periodic | continuous
    temperature: float = 0.2
    _history: list[DescriptorProposal] = field(default_factory=list)

    def should_trigger(self, generation: int) -> bool:
        if self.mode == "off":
            return False
        if self.mode == "continuous":
            return generation > 0
        # periodic: fire on multiples of k, skipping generation 0
        return generation > 0 and generation % self.trigger_every_k == 0

    async def propose(
        self,
        current: descriptor_dsl.DescriptorFn,
        archive: algorithms.MapElitesArchive,
        fitness_deltas: list[float],
        *,
        seed: Optional[int] = None,
    ) -> DescriptorProposal:
        """Ask the LLM for a keep/replace verdict.

        Returns a :class:`DescriptorProposal`. Callers treat
        ``action=="keep"`` (or any parse failure) as a no-op.
        """
        signals = {
            "current_grid":   current.canonical(),
            "archive":        _summarise_archive(archive),
            "fitness_deltas": [round(d, 5) for d in fitness_deltas[-8:]],
            "plateau":        _fitness_plateau(fitness_deltas),
        }
        user = (
            "Signals:\n" + json.dumps(signals, indent=2, ensure_ascii=False)
            + "\n\nRespond with the JSON verdict."
        )
        raw = await self.client.complete(
            _SYSTEM, user,
            seed=seed, temperature=self.temperature,
            operator="descriptor_controller",
        )
        proposal = self._parse(raw)
        self._history.append(proposal)
        return proposal

    # ---- parsing (mirrors the critic's JSON-or-keep contract) ----

    _JSON_BLOCK = re.compile(r"\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}", re.DOTALL)

    @classmethod
    def _parse(cls, raw: str) -> DescriptorProposal:
        text = raw.strip()
        if text.startswith("```"):
            text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
            text = re.sub(r"\n?```$", "", text)
        try:
            obj = json.loads(text)
        except json.JSONDecodeError:
            match = cls._JSON_BLOCK.search(text)
            if not match:
                return DescriptorProposal(action="keep", raw=raw,
                                          reason="unparsable JSON")
            try:
                obj = json.loads(match.group(0))
            except json.JSONDecodeError:
                return DescriptorProposal(action="keep", raw=raw,
                                          reason="unparsable JSON")
        action = str(obj.get("action", "keep")).lower()
        if action not in ("keep", "replace"):
            return DescriptorProposal(action="keep", raw=raw,
                                      reason=f"unknown action {action!r}")
        reason = str(obj.get("reason", ""))[:500]
        if action == "keep":
            return DescriptorProposal(action="keep", reason=reason, raw=raw)
        grid_expr = str(obj.get("grid", "") or "")
        try:
            fn = descriptor_dsl.parse_descriptor(grid_expr)
        except descriptor_dsl.DescriptorParseError as exc:
            return DescriptorProposal(action="keep", raw=raw,
                                      reason=f"bad grid: {exc}")
        return DescriptorProposal(action="replace", grid=fn, reason=reason, raw=raw)


# ---------------------------------------------------------------------------
# Archive remap helper (used by the evolver runner after accepting a proposal)
# ---------------------------------------------------------------------------


def remap_archive(
    archive: algorithms.MapElitesArchive,
    new_fn: descriptor_dsl.DescriptorFn,
) -> algorithms.MapElitesArchive:
    """Build a new archive under *new_fn* and re-place every occupant.

    Collisions (two candidates mapping to the same new bin) are
    resolved by fitness: the better one wins, matching the invariant
    that every cell keeps its best seen occupant.

    Returns a fresh archive — callers must reassign, the input is
    untouched so dry-runs are cheap.
    """
    fresh = algorithms.MapElitesArchive(
        bin_counts=new_fn.bin_counts,
        lows=new_fn.lows,
        highs=new_fn.highs,
        objective=archive.objective,
    )
    for ind in archive.cells.values():
        ind.descriptor = new_fn(ind.genome)
        fresh.place(ind)
    return fresh
