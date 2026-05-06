"""Self-modifying Exp3 bandit director (A2 — v0.4).

Claim: the human-curated operator library is a prior; letting the
bandit *add* arms proposed by an LLM (and retire low-weight ones)
mid-run closes the gap between handcrafted and task-specific
mutation.

Mechanism
---------
Every ``R`` generations the director is called with

1. the current bandit state (arms + Exp3 weights)
2. the operator-attribution window: for each arm, how often was it
   chosen and what was the average fitness delta of its offspring?
3. the last few generations' best-fitness trajectory.

The director replies with zero or more ``add`` / ``retire`` / ``merge``
actions in a strict JSON schema. Proposals that parse cleanly are
materialised as :class:`DynamicOperator` instances — thin wrappers
around a system-prompt template — and spliced into the operator
registry. New arms enter with a small initial weight so an
adversarial LLM cannot dominate selection until its output has been
vetted by the full evaluation pipeline.

Safety rails
------------
* The director never writes Python. Every proposal is a prompt
  template string that goes through the same ``LLMClient.complete``
  path as a handcrafted operator.
* Retirement is bounded: an arm must have spent *F* generations
  below ``retire_floor`` before it can be dropped; the floor is
  tuned so genuinely struggling ops eventually exit but noise
  spikes don't erase a useful arm.
* ``merge`` only combines two existing arms into one — it does not
  rewrite the combined prompt; instead it averages their Exp3
  weights and retires the duplicate.

Author: v0.4 roadmap feature A2.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional

import algorithms
from llm import LLMClient


_SYSTEM = """You audit a mutation-operator library for an evolutionary
prompt optimiser. Given the current bandit state, decide whether to
add, retire, or merge operators. Return STRICT JSON with one field:

  {"actions": [<ActionDict>, ...]}

Allowed action shapes:

  {"type": "add",
   "name": "<lowercase-snake>",
   "template": "<mutation prompt for the LLM>",
   "temperature": <float 0..1.5>}

  {"type": "retire",
   "name": "<existing operator name>"}

  {"type": "merge",
   "keep":  "<name-to-keep>",
   "drop":  "<name-to-drop>"}

Guidelines:
* add at most 2 operators per call; prefer editing the template
  of a retiring op over inventing entirely new ones.
* Do NOT propose adding an operator whose name already exists in
  the bandit (caller verifies; duplicates get dropped silently).
* If the bandit looks healthy (no starved arms, fitness improving)
  emit {"actions": []} — the caller treats that as a no-op.
"""


# ---------------------------------------------------------------------------
# Dynamic operator wrapper
# ---------------------------------------------------------------------------


@dataclass
class DynamicOperator:
    """Runtime-installed mutation operator defined by a prompt template.

    ``template`` is a plain string the director emitted; it is used as
    the *system prompt* when the operator runs. ``__call__`` mirrors
    the handcrafted operator signature in ``operators.py``.
    """

    name:        str
    template:    str
    temperature: float = 0.8
    origin:      str = "director"

    @property
    def prompt_hash(self) -> str:
        h = hashlib.blake2b(digest_size=8)
        h.update(self.template.encode("utf-8"))
        return h.hexdigest()

    async def __call__(
        self,
        client: LLMClient,
        parent: str,
        *,
        seed: Optional[int] = None,
    ):
        """Apply the generated operator; returns ``operators.OperatorResult``-shaped dict.

        The import is local to avoid an operators ↔ bandit_director cycle.
        """
        import operators as _operators
        child = await client.complete(
            self.template, parent,
            seed=seed, temperature=self.temperature,
            operator=f"director::{self.name}",
        )
        return _operators.OperatorResult(
            child=(child or parent).strip(),
            prompt_hash=self.prompt_hash,
            operator=self.name,
            parents=[parent],
        )


# ---------------------------------------------------------------------------
# Director
# ---------------------------------------------------------------------------


@dataclass
class OperatorStats:
    """Per-arm attribution summary used to brief the director."""
    name:            str
    weight:          float
    selections:      int
    mean_delta:      float
    last_good_gen:   int   # generation at which the arm last produced a win


@dataclass
class DirectorAction:
    type:        str
    payload:    dict


@dataclass
class DirectorVerdict:
    actions: list[DirectorAction] = field(default_factory=list)
    raw:     str = ""


@dataclass
class BanditDirector:
    client:               LLMClient
    trigger_every_r:      int = 4
    retire_floor:         float = 0.05
    retire_consecutive_f: int = 3
    max_arms:             int = 16
    temperature:          float = 0.25

    _below_floor_counts:  dict[str, int] = field(default_factory=dict)

    def should_trigger(self, generation: int) -> bool:
        return generation > 0 and generation % self.trigger_every_r == 0

    async def audit(
        self,
        bandit: algorithms.Exp3Bandit,
        stats: list[OperatorStats],
        recent_best: list[float],
        *,
        seed: Optional[int] = None,
    ) -> DirectorVerdict:
        """Ask the LLM for keep/add/retire/merge actions."""
        self._refresh_below_floor(stats)
        starved = [s.name for s in stats if self._below_floor_counts[s.name] >= self.retire_consecutive_f]

        state = {
            "arms":           [s.name for s in stats],
            "weights":        {s.name: round(s.weight, 4) for s in stats},
            "selections":     {s.name: s.selections for s in stats},
            "mean_deltas":    {s.name: round(s.mean_delta, 5) for s in stats},
            "last_good_gen":  {s.name: s.last_good_gen for s in stats},
            "starved_arms":   starved,
            "recent_best":    [round(v, 5) for v in recent_best[-8:]],
        }
        raw = await self.client.complete(
            _SYSTEM,
            "Bandit state:\n" + json.dumps(state, indent=2, ensure_ascii=False),
            seed=seed, temperature=self.temperature,
            operator="bandit_director",
        )
        return self._parse(raw)

    # ---- book-keeping ----

    def _refresh_below_floor(self, stats: list[OperatorStats]) -> None:
        for s in stats:
            if s.weight < self.retire_floor:
                self._below_floor_counts[s.name] = self._below_floor_counts.get(s.name, 0) + 1
            else:
                self._below_floor_counts[s.name] = 0

    # ---- parsing ----

    _JSON_BLOCK = re.compile(r"\{[\s\S]*\}", re.DOTALL)

    @classmethod
    def _parse(cls, raw: str) -> DirectorVerdict:
        text = raw.strip()
        if text.startswith("```"):
            text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
            text = re.sub(r"\n?```$", "", text)
        try:
            obj = json.loads(text)
        except json.JSONDecodeError:
            m = cls._JSON_BLOCK.search(text)
            if not m:
                return DirectorVerdict(raw=raw)
            try:
                obj = json.loads(m.group(0))
            except json.JSONDecodeError:
                return DirectorVerdict(raw=raw)
        actions_raw = obj.get("actions") or []
        if not isinstance(actions_raw, list):
            return DirectorVerdict(raw=raw)

        actions: list[DirectorAction] = []
        for a in actions_raw:
            if not isinstance(a, dict):
                continue
            t = str(a.get("type", "")).lower()
            if t not in ("add", "retire", "merge"):
                continue
            if t == "add":
                name = str(a.get("name", "")).strip()
                tmpl = str(a.get("template", "")).strip()
                if not name or not tmpl:
                    continue
                actions.append(DirectorAction(
                    type="add",
                    payload={"name": re.sub(r"[^a-z0-9_]", "_", name.lower())[:48],
                             "template": tmpl,
                             "temperature": float(a.get("temperature", 0.8))},
                ))
            elif t == "retire":
                name = str(a.get("name", "")).strip()
                if name:
                    actions.append(DirectorAction(type="retire", payload={"name": name}))
            elif t == "merge":
                keep = str(a.get("keep", "")).strip()
                drop = str(a.get("drop", "")).strip()
                if keep and drop and keep != drop:
                    actions.append(DirectorAction(
                        type="merge",
                        payload={"keep": keep, "drop": drop},
                    ))
        return DirectorVerdict(actions=actions, raw=raw)


# ---------------------------------------------------------------------------
# Applying a verdict to the live bandit + operator registry
# ---------------------------------------------------------------------------


def apply_verdict(
    verdict: DirectorVerdict,
    bandit: algorithms.Exp3Bandit,
    registry: dict[str, Callable[..., Awaitable[Any]]],
    director: BanditDirector,
    *,
    initial_weight: float = 0.25,
) -> list[DirectorAction]:
    """Mutate *bandit* and *registry* in place; return applied actions.

    Respects the safety rails: retirement skipped when arm isn't
    starved; adds skipped when name collides or the arm cap is hit;
    merges skipped when either side is missing.
    """
    applied: list[DirectorAction] = []
    for action in verdict.actions:
        if action.type == "add":
            name = action.payload["name"]
            if name in registry:
                continue
            if len(bandit.arms) >= director.max_arms:
                continue
            op = DynamicOperator(
                name=name,
                template=action.payload["template"],
                temperature=action.payload["temperature"],
            )
            registry[name] = op
            bandit.arms.append(name)
            bandit.weights.append(initial_weight)
            applied.append(action)
        elif action.type == "retire":
            name = action.payload["name"]
            if name not in bandit.arms:
                continue
            if director._below_floor_counts.get(name, 0) < director.retire_consecutive_f:
                continue
            idx = bandit.arms.index(name)
            bandit.arms.pop(idx)
            bandit.weights.pop(idx)
            registry.pop(name, None)
            applied.append(action)
        elif action.type == "merge":
            keep = action.payload["keep"]
            drop = action.payload["drop"]
            if keep not in bandit.arms or drop not in bandit.arms:
                continue
            ik, idp = bandit.arms.index(keep), bandit.arms.index(drop)
            bandit.weights[ik] = (bandit.weights[ik] + bandit.weights[idp]) / 2
            bandit.arms.pop(idp)
            bandit.weights.pop(idp)
            registry.pop(drop, None)
            applied.append(action)
    return applied
