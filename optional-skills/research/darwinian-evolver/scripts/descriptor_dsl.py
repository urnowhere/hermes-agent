"""Restricted DSL for behavioral descriptors used by MAP-Elites.

Background
----------
``A1 — LLM-conditioned MAP-Elites`` needs an LLM to propose new
descriptor functions mid-run. Executing LLM-generated Python would be
a glaring security and reliability risk, so the controller never
writes Python. Instead it emits a string in a narrow DSL that this
module parses into a pure function composed from a whitelist of
feature extractors.

Grammar
-------
A descriptor expression is a top-level ``grid(...)`` call whose
arguments are extractor invocations. Each extractor is a function
from ``(str) -> int`` (its bin coordinate) and declares its bin
count via the ``bins=N`` keyword. The ``grid`` combinator stacks
coordinates into a tuple and exposes the aggregate ``bin_counts``.

    descriptor = "grid(length(bins=8, max=2000), cot_presence())"

Call the parser::

    desc = parse_descriptor(expr)          # returns DescriptorFn
    desc("let's think step by step ...")    # → (3, 1)
    desc.bin_counts                         # → (8, 2)

Extractors
----------
* ``length(bins, max)`` — log-scaled length bucket.
* ``cot_presence()`` — binary: does the text invoke CoT reasoning?
* ``token_entropy(bins, max)`` — Shannon entropy of word frequencies.
* ``punctuation_density(bins)`` — punctuation chars per 100 chars.
* ``reading_grade(bins)`` — Flesch-Kincaid-style grade approximation.
* ``embedding_cluster(k)`` — **deferred** to Phase 2 (needs embedding
  model plumbing); raises ``NotImplementedError`` for now so the
  parser accepts it but the runtime degrades gracefully.

Whitelist semantics keep this module:

- Pure-function; safe to run on untrusted input.
- Hashable: the descriptor's canonical form is used as a cache key
  when the controller asks "have we tried this grid before?".
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any, Callable


# ---------------------------------------------------------------------------
# Token-level helpers (shared)
# ---------------------------------------------------------------------------

_WORD_RE = re.compile(r"\b\w+\b")
_PUNCT_RE = re.compile(r"[\.,;:\!\?\-\—\(\)\[\]\{\}\"\'\/]")
_COT_PHRASES = (
    "let's think", "let us think", "step by step", "reason step",
    "explain your reasoning", "break this down",
)


def _bin_float(value: float, lo: float, hi: float, bins: int) -> int:
    """Linearly bin *value* into ``[0, bins-1]``; clamp out-of-range."""
    if bins <= 1 or hi <= lo:
        return 0
    v = min(max(value, lo), hi - 1e-9)
    return min(int((v - lo) / (hi - lo) * bins), bins - 1)


# ---------------------------------------------------------------------------
# Extractor registry — pure functions, no side effects
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Extractor:
    name: str
    bins: int
    call: Callable[[str], int]
    repr_str: str


def _extractor_length(bins: int = 8, max_len: int = 2000, **extra) -> Extractor:
    """``length(bins=N, max=M)`` — log-scaled length bucket.

    ``max`` is accepted as a DSL alias for ``max_len`` so we never
    shadow the ``max()`` builtin inside the closure.
    """
    if "max" in extra:
        max_len = int(extra["max"])
    cap = int(max_len)
    def _fn(text: str) -> int:
        n = min(len(text), cap)
        if n <= 1:
            return 0
        return min(int(math.log2(max(n, 2)) / math.log2(cap) * bins), bins - 1)
    return Extractor(
        name="length", bins=bins, call=_fn,
        repr_str=f"length(bins={bins}, max={cap})",
    )


def _extractor_cot_presence() -> Extractor:
    def _fn(text: str) -> int:
        low = text.lower()
        return 1 if any(p in low for p in _COT_PHRASES) else 0
    return Extractor(
        name="cot_presence", bins=2, call=_fn,
        repr_str="cot_presence()",
    )


def _extractor_token_entropy(bins: int = 4, max_val: float = 8.0, **extra) -> Extractor:
    if "max" in extra:
        max_val = float(extra["max"])
    cap = float(max_val)
    def _fn(text: str) -> int:
        words = _WORD_RE.findall(text.lower())
        if not words:
            return 0
        counts: dict[str, int] = {}
        for w in words:
            counts[w] = counts.get(w, 0) + 1
        total = len(words)
        entropy = -sum((c / total) * math.log2(c / total) for c in counts.values())
        return _bin_float(entropy, 0.0, cap, bins)
    return Extractor(
        name="token_entropy", bins=bins, call=_fn,
        repr_str=f"token_entropy(bins={bins}, max={cap})",
    )


def _extractor_punctuation_density(bins: int = 4, max_val: float = 25.0, **extra) -> Extractor:
    if "max" in extra:
        max_val = float(extra["max"])
    cap = float(max_val)
    def _fn(text: str) -> int:
        if not text:
            return 0
        punct = len(_PUNCT_RE.findall(text))
        density = (punct / max(len(text), 1)) * 100
        return _bin_float(density, 0.0, cap, bins)
    return Extractor(
        name="punctuation_density", bins=bins, call=_fn,
        repr_str=f"punctuation_density(bins={bins}, max={cap})",
    )


def _extractor_reading_grade(bins: int = 4, max_val: float = 16.0, **extra) -> Extractor:
    """Approximate Flesch-Kincaid grade level.

    F-K grade requires syllable counts; we approximate syllables as
    vowel-group counts per word (well-studied surrogate, Kincaid 1975).
    """
    _VOWEL_GROUP = re.compile(r"[aeiouy]+", re.IGNORECASE)
    if "max" in extra:
        max_val = float(extra["max"])
    cap = float(max_val)

    def _fn(text: str) -> int:
        sentences = max(1, text.count(".") + text.count("!") + text.count("?"))
        words = _WORD_RE.findall(text)
        if not words:
            return 0
        syllables = sum(len(_VOWEL_GROUP.findall(w)) for w in words) or 1
        words_per_sentence = len(words) / sentences
        syllables_per_word = syllables / len(words)
        grade = 0.39 * words_per_sentence + 11.8 * syllables_per_word - 15.59
        return _bin_float(grade, 0.0, cap, bins)
    return Extractor(
        name="reading_grade", bins=bins, call=_fn,
        repr_str=f"reading_grade(bins={bins}, max={cap})",
    )


def _extractor_embedding_cluster(k: int = 8) -> Extractor:
    """Placeholder — embedding-based clustering is deferred to Phase 2.

    The parser accepts the call so controllers can emit forward-looking
    descriptors; at runtime it degrades to a constant ``0`` bin until
    the embedding backend lands. This keeps the DSL expression space
    stable for the A1 ablation plan.
    """
    def _fn(text: str) -> int:
        return 0
    return Extractor(
        name="embedding_cluster", bins=max(1, k), call=_fn,
        repr_str=f"embedding_cluster(k={k})",
    )


# ---------------------------------------------------------------------------
# Grid combinator
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DescriptorFn:
    extractors: tuple[Extractor, ...]

    @property
    def bin_counts(self) -> tuple[int, ...]:
        return tuple(e.bins for e in self.extractors)

    @property
    def lows(self) -> tuple[float, ...]:
        return tuple(0.0 for _ in self.extractors)

    @property
    def highs(self) -> tuple[float, ...]:
        return tuple(float(e.bins) for e in self.extractors)

    def __call__(self, text: str) -> tuple[int, ...]:
        return tuple(e.call(text) for e in self.extractors)

    def canonical(self) -> str:
        inner = ", ".join(e.repr_str for e in self.extractors)
        return f"grid({inner})"


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


_EXTRACTORS: dict[str, Callable[..., Extractor]] = {
    "length":              _extractor_length,
    "cot_presence":        _extractor_cot_presence,
    "token_entropy":       _extractor_token_entropy,
    "punctuation_density": _extractor_punctuation_density,
    "reading_grade":       _extractor_reading_grade,
    "embedding_cluster":   _extractor_embedding_cluster,
}


_TOP_RE = re.compile(r"^\s*grid\s*\(\s*(.+?)\s*\)\s*$", re.DOTALL)
_CALL_RE = re.compile(r"^([a-zA-Z_][a-zA-Z0-9_]*)\s*\((.*)\)$", re.DOTALL)
_KWARG_RE = re.compile(r"^\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*=\s*(.+?)\s*$")


class DescriptorParseError(ValueError):
    """Raised when a DSL expression cannot be parsed."""


def _split_top_args(inner: str) -> list[str]:
    """Split a comma-separated argument list respecting nested parens.

    ``length(bins=8), cot_presence()`` → ``["length(bins=8)", "cot_presence()"]``.
    A dumb ``inner.split(",")`` would break on nested calls.
    """
    parts: list[str] = []
    depth = 0
    buf: list[str] = []
    for ch in inner:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append("".join(buf).strip())
            buf = []
            continue
        buf.append(ch)
    tail = "".join(buf).strip()
    if tail:
        parts.append(tail)
    if depth != 0:
        raise DescriptorParseError(f"unbalanced parentheses in {inner!r}")
    return parts


def _parse_kwargs(raw: str) -> dict[str, Any]:
    """Parse ``bins=8, max=2000`` into ``{"bins": 8, "max": 2000}``.

    Only int and float literals are accepted — anything else is
    rejected to keep the DSL hermetic.
    """
    if not raw.strip():
        return {}
    out: dict[str, Any] = {}
    for part in _split_top_args(raw):
        m = _KWARG_RE.match(part)
        if not m:
            raise DescriptorParseError(f"expected kwarg, got {part!r}")
        key, val_str = m.group(1), m.group(2).strip()
        try:
            if "." in val_str or "e" in val_str.lower():
                out[key] = float(val_str)
            else:
                out[key] = int(val_str)
        except ValueError as exc:
            raise DescriptorParseError(
                f"non-numeric literal {val_str!r} in kwargs",
            ) from exc
    return out


def parse_descriptor(expr: str) -> DescriptorFn:
    """Parse a DSL string into a :class:`DescriptorFn`.

    Raises :class:`DescriptorParseError` on any deviation from the
    grammar. The controller treats that as "keep the current
    descriptor" — a malformed proposal never corrupts the run.
    """
    if not expr or not isinstance(expr, str):
        raise DescriptorParseError("empty descriptor expression")

    top = _TOP_RE.match(expr.strip())
    if not top:
        raise DescriptorParseError(f"expected grid(...), got {expr!r}")
    parts = _split_top_args(top.group(1))
    if not parts:
        raise DescriptorParseError("grid() requires at least one extractor")

    extractors: list[Extractor] = []
    for part in parts:
        call = _CALL_RE.match(part)
        if not call:
            raise DescriptorParseError(f"not an extractor call: {part!r}")
        name, inside = call.group(1), call.group(2)
        factory = _EXTRACTORS.get(name)
        if factory is None:
            raise DescriptorParseError(f"unknown extractor {name!r}")
        kwargs = _parse_kwargs(inside)
        try:
            extractors.append(factory(**kwargs))
        except TypeError as exc:
            raise DescriptorParseError(
                f"bad kwargs for {name}: {kwargs} — {exc}",
            ) from exc
    return DescriptorFn(tuple(extractors))


def list_extractors() -> list[str]:
    """Return the whitelist of extractor names the parser accepts."""
    return sorted(_EXTRACTORS)
