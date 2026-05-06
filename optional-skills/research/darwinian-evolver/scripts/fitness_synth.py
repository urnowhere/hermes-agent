"""Automatic fitness-function synthesis (A4 — v0.4).

Claim: given ≤ 20 labelled I/O pairs, an LLM can pick one of three
stock scoring archetypes and emit a runnable ``fitness.py``.

Archetypes
----------
* ``exact``  — hard equality between candidate output and the label.
* ``soft``   — normalised Levenshtein similarity.
* ``judge``  — LLM-as-judge constitution, producing a scalar 0..1.

The user reviews the emitted file, edits if needed, accepts. We
never auto-accept: meta-APE is powerful, but writing a fitness
blindfolded is the one operation that silently invalidates every
subsequent experiment, so the human stays in the loop.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Optional

from llm import LLMClient


_TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates" / "fitness_archetypes"


_SYSTEM = """You design fitness functions for evolutionary prompt
optimisation. Given I/O pairs, choose the archetype that best
reflects the user's intent and reply with STRICT JSON:

  {"archetype": "exact" | "soft" | "judge",
   "rationale": "<one sentence>"}

Archetypes:
  exact — the candidate must produce a string that matches the
          label (case-sensitive). Use when outputs are short,
          discrete (commands, regexes, JSON blobs).
  soft  — measure text similarity. Use when outputs are free-form
          (summaries, rewrites, translations).
  judge — call an LLM to score each pair against a criterion.
          Use when the label is an ideal exemplar, not a reference.

Return JSON only — no prose, no code fences.
"""


@dataclass
class SynthesisResult:
    archetype:   str
    rationale:   str
    fitness_src: str    # full text of fitness.py
    raw:         str


# ---------------------------------------------------------------------------
# Archetype templates (inline so the module works with no templates/ dir)
# ---------------------------------------------------------------------------


_EXACT_TEMPLATE = '''
"""Auto-synthesised fitness (archetype: exact).
Replace EXAMPLES with your full I/O set; edit COMPARE_FN if case-
insensitivity or whitespace normalisation changes the semantics.
"""

from evolver_sdk import fitness_spec


EXAMPLES = {examples!r}


def COMPARE_FN(candidate_output: str, label: str) -> float:
    return 1.0 if candidate_output == label else 0.0


@fitness_spec(held_out_frac=0.2, timeout_s=15)
def fitness(candidate: str, context: dict) -> float:
    """Run `candidate` against every example as a templatable string.

    The default interpretation: the candidate IS the output. For
    prompt-evolution tasks you'll want to actually CALL an LLM here
    with `candidate` as the system prompt and each example's input
    as the user prompt; this stub just compares strings so the
    file runs unchanged before you fill in the generator.
    """
    if not EXAMPLES:
        return 0.0
    hits = sum(
        COMPARE_FN(candidate, ex["output"]) for ex in EXAMPLES
    )
    return hits / len(EXAMPLES)
'''


_SOFT_TEMPLATE = '''
"""Auto-synthesised fitness (archetype: soft).
Uses normalised Levenshtein similarity. Replace EXAMPLES and the
generator hook with your real pipeline before long runs.
"""

from evolver_sdk import fitness_spec


EXAMPLES = {examples!r}


def _levenshtein_ratio(a: str, b: str) -> float:
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    la, lb = len(a), len(b)
    dp = list(range(lb + 1))
    for i in range(1, la + 1):
        prev, dp[0] = dp[0], i
        for j in range(1, lb + 1):
            cur = dp[j]
            cost = 0 if a[i - 1] == b[j - 1] else 1
            dp[j] = min(dp[j] + 1, dp[j - 1] + 1, prev + cost)
            prev = cur
    return 1.0 - dp[lb] / max(la, lb)


@fitness_spec(held_out_frac=0.2, timeout_s=20)
def fitness(candidate: str, context: dict) -> float:
    if not EXAMPLES:
        return 0.0
    scores = [
        _levenshtein_ratio(candidate, ex["output"]) for ex in EXAMPLES
    ]
    return sum(scores) / len(scores)
'''


_JUDGE_TEMPLATE = '''
"""Auto-synthesised fitness (archetype: judge).
Uses the configured Hermes LLM as a pairwise judge vs. each label.
Requires `httpx` and a reachable LLM endpoint.
"""

import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent.parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import asyncio
from evolver_sdk import fitness_spec
from llm import LLMClient


EXAMPLES = {examples!r}
CRITERION = {criterion!r}


async def _score_pair(client: LLMClient, candidate: str, label: str) -> float:
    verdict = await client.complete(
        system="You are a strict judge. Return a float 0..1 indicating "
               "how well the candidate satisfies the criterion relative to "
               "the label. Output ONLY the number.",
        user=f"Criterion: {{CRITERION}}\\n\\nCandidate:\\n{{candidate}}\\n\\n"
             f"Label:\\n{{label}}",
        temperature=0.0, max_tokens=16,
    )
    try:
        return max(0.0, min(1.0, float(verdict.strip())))
    except ValueError:
        return 0.0


@fitness_spec(held_out_frac=0.2, timeout_s=60)
def fitness(candidate: str, context: dict) -> float:
    if not EXAMPLES:
        return 0.0

    async def _run() -> float:
        async with LLMClient.from_hermes() as client:
            scores = await asyncio.gather(*[
                _score_pair(client, candidate, ex["output"]) for ex in EXAMPLES
            ])
        return sum(scores) / len(scores)

    return asyncio.run(_run())
'''


_TEMPLATES = {
    "exact": _EXACT_TEMPLATE,
    "soft":  _SOFT_TEMPLATE,
    "judge": _JUDGE_TEMPLATE,
}


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


_JSON_RE = re.compile(r"\{[\s\S]*?\}", re.DOTALL)


def _parse_archetype(raw: str) -> tuple[str, str]:
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text)
    try:
        obj = json.loads(text)
    except json.JSONDecodeError:
        m = _JSON_RE.search(text)
        if not m:
            return "soft", "default — unparsable judge response"
        try:
            obj = json.loads(m.group(0))
        except json.JSONDecodeError:
            return "soft", "default — unparsable judge response"
    arch = str(obj.get("archetype", "soft")).lower()
    if arch not in _TEMPLATES:
        arch = "soft"
    rationale = str(obj.get("rationale", ""))[:500]
    return arch, rationale


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def synthesise(
    client: LLMClient,
    examples: list[dict],
    *,
    criterion: str = "correctness",
    seed: Optional[int] = None,
) -> SynthesisResult:
    """Pick an archetype given a few labelled pairs; emit fitness.py text.

    ``examples`` is a list of ``{"input": ..., "output": ...}`` dicts.
    """
    if not examples:
        raise ValueError("fitness synthesis requires at least one example pair")
    user = (
        "Criterion: " + criterion + "\n\n"
        + "Examples (JSON array, up to 20 pairs):\n"
        + json.dumps(examples[:20], ensure_ascii=False, indent=2)
        + "\n\nReturn the JSON verdict."
    )
    raw = await client.complete(
        _SYSTEM, user, seed=seed, temperature=0.1,
        operator="fitness_synth",
    )
    arch, rationale = _parse_archetype(raw)
    fitness_src = render_fitness(arch, examples, criterion=criterion)
    return SynthesisResult(
        archetype=arch, rationale=rationale,
        fitness_src=fitness_src, raw=raw,
    )


def render_fitness(
    archetype: str,
    examples: list[dict],
    *,
    criterion: str = "correctness",
) -> str:
    """Render the fitness.py source for *archetype* with *examples* inlined."""
    if archetype not in _TEMPLATES:
        raise ValueError(f"unknown archetype {archetype!r}")
    # ``examples`` is inlined as a Python literal via ``repr``; this is
    # safe because it's always a list of dicts of str→str, not user code.
    return _TEMPLATES[archetype].format(
        examples=examples,
        criterion=criterion,
    ).lstrip()
