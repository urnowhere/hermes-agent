"""Pre-turn smart model routing.

Decides whether the upcoming turn should run on the configured primary
model or be routed to a cheaper local model (for simple tasks) or a
smarter cloud model (for hard tasks).  Routing is **turn scoped** — the
existing fallback restoration path puts the agent back on its primary
runtime at the start of the next user turn.

This module is intentionally pure.  It does not import any provider SDK,
make HTTP calls, or touch the AIAgent runtime.  All side effects live in
``run_agent.py``.  The decision logic is deterministic and exhaustively
unit-tested so behavior changes are easy to reason about.

Two operating modes:

- ``local-first`` — primary is a cheap/local model.  Hard tasks route to
  ``smart_model`` (or to the first ``fallback_providers``/``fallback_model``
  entry when ``smart_model`` is omitted).  Simple tasks stay on primary.
- ``smart-primary`` — primary is a smart cloud model.  Simple tasks route
  to ``cheap_model`` when configured.  Hard tasks stay on primary.

Mode auto-detection (when ``mode`` is unset or ``auto``):

- Both ``cheap_model`` and ``smart_model`` configured → look at the
  primary provider; local-like providers map to ``local-first``, anything
  else maps to ``smart-primary``.
- Only ``smart_model`` configured → ``local-first``.
- Only ``cheap_model`` configured → ``smart-primary``.
- Neither → routing is disabled regardless of ``enabled``.

Fallback compatibility: when no ``smart_model`` is configured but
``fallback_providers`` / ``fallback_model`` exists, the first entry of the
chain is used as the smart target.  This preserves the existing fallback
configuration surface for users who only want hard-task escalation.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional

logger = logging.getLogger(__name__)

# ── Defaults (mirrors profile-config stub) ───────────────────────────────
DEFAULT_MAX_SIMPLE_CHARS = 160
DEFAULT_MAX_SIMPLE_WORDS = 28

# Providers we treat as "local/cheap" by default.  Used for mode
# auto-detection when both cheap_model and smart_model are configured and
# the user did not pin ``mode`` explicitly.  Conservative on purpose —
# misidentifying a smart provider as local would route hard tasks away
# from a perfectly capable primary.
LOCAL_LIKE_PROVIDERS: frozenset = frozenset({
    "custom",
    "ollama",
    "ollama-cloud",
    "lmstudio",
    "lm-studio",
    "llamacpp",
    "llama-cpp",
    "llama-swap",
    "vllm",
    "local",
    "localai",
    "localhost",
    "fred",
})

# Hard-task signals.  Lowercased word matches inside tokenized message.
HARD_KEYWORDS: frozenset = frozenset({
    "debug",
    "build",
    "code",
    "coding",
    "refactor",
    "architect",
    "architecture",
    "design",
    "plan",
    "investigate",
    "investigation",
    "strategy",
    "lawsuit",
    "contract",
    "financial",
    "finance",
    "risk",
    "compliance",
    "legal",
    "complex",
    "hard",
    "root-cause",
    "rca",
    "test-failure",
    "regression",
    "stacktrace",
    "traceback",
    "exception",
    "permit",
    "escalation",
    "audit",
})

# Multi-word hard phrases (compiled once).  Each pattern is tested against
# the lowercased raw message so phrases that span tokens are caught.
HARD_PHRASE_PATTERNS: List[re.Pattern] = [
    re.compile(r"\broot[\s-]?cause\b"),
    re.compile(r"\btest\s+fail(?:ure|ing|ed)?\b"),
    re.compile(r"\bbuild\s+fail(?:ure|ing|ed)?\b"),
    re.compile(r"\bstack\s+trace\b"),
    re.compile(r"\bdebug\s+(?:this|the|my|that)\b"),
    re.compile(r"\bwhy\s+is\s+this\s+(?:broken|failing)\b"),
    re.compile(r"\bcontract\s+review\b"),
    re.compile(r"\bsecurity\s+(?:audit|review)\b"),
    re.compile(r"\bmulti[\s-]?step\b"),
]

# ── Data classes ─────────────────────────────────────────────────────────


@dataclass(frozen=True)
class RoutingTarget:
    provider: str
    model: str
    base_url: Optional[str] = None
    api_key: Optional[str] = None

    def is_valid(self) -> bool:
        return bool(self.provider and self.model)

    def to_fallback_entry(self) -> Dict[str, Any]:
        """Render as a fallback_providers-style dict consumable by
        ``_try_activate_fallback`` and friends."""
        out: Dict[str, Any] = {"provider": self.provider, "model": self.model}
        if self.base_url:
            out["base_url"] = self.base_url
        if self.api_key:
            out["api_key"] = self.api_key
        return out


@dataclass(frozen=True)
class RoutingDecision:
    route: str  # "primary" | "cheap" | "smart"
    target: Optional[RoutingTarget]
    reason: str
    mode: str  # "local-first" | "smart-primary" | "disabled"
    classification: str  # "simple" | "hard" | "unknown"


# ── Heuristic classifier ─────────────────────────────────────────────────


def _word_count(text: str) -> int:
    return len(re.findall(r"\b\w+\b", text or ""))


def _normalize(text: str) -> str:
    return (text or "").strip().lower()


def classify_difficulty(
    text: str,
    *,
    max_simple_chars: int = DEFAULT_MAX_SIMPLE_CHARS,
    max_simple_words: int = DEFAULT_MAX_SIMPLE_WORDS,
    extra_hard_keywords: Optional[Iterable[str]] = None,
) -> str:
    """Deterministic heuristic returning ``"simple"`` or ``"hard"``.

    Hard signals (any one wins):
    - any HARD_KEYWORD token appears in the message
    - any HARD_PHRASE_PATTERNS regex matches
    - message is longer than ``max_simple_chars`` or ``max_simple_words``
    - message contains a fenced code block, a stack trace marker, or
      multiple sentences ending in question marks (suggests multi-part
      reasoning)

    Otherwise the message is ``"simple"``.  Empty/whitespace-only text is
    treated as ``"simple"``.
    """
    if not text or not text.strip():
        return "simple"

    raw = text
    norm = _normalize(text)

    # Length triggers
    if len(raw) > max_simple_chars:
        return "hard"
    if _word_count(raw) > max_simple_words:
        return "hard"

    # Code fences / tracebacks → almost always coding/debugging
    if "```" in raw or "Traceback (most recent call last)" in raw:
        return "hard"

    # Multiple question marks → multi-part reasoning request
    if raw.count("?") >= 3:
        return "hard"

    # Keyword token match.  Tokenize on word boundaries so partial
    # matches (e.g. "encoded" → "code") don't over-trigger.
    tokens = set(re.findall(r"[a-z][a-z0-9_-]*", norm))
    haystack = HARD_KEYWORDS
    if extra_hard_keywords:
        haystack = haystack | {k.lower() for k in extra_hard_keywords}
    if tokens & haystack:
        return "hard"

    # Phrase patterns
    for pat in HARD_PHRASE_PATTERNS:
        if pat.search(norm):
            return "hard"

    return "simple"


# ── Mode + target resolution ─────────────────────────────────────────────


def _coerce_target(raw: Any) -> Optional[RoutingTarget]:
    if not isinstance(raw, dict):
        return None
    provider = (raw.get("provider") or "").strip()
    model = (raw.get("model") or "").strip()
    if not provider or not model:
        return None
    base_url = (raw.get("base_url") or "").strip() or None
    api_key = (raw.get("api_key") or "").strip() or None
    return RoutingTarget(provider=provider, model=model, base_url=base_url, api_key=api_key)


def _first_valid_fallback(fallback_chain: Optional[List[Dict[str, Any]]]) -> Optional[RoutingTarget]:
    if not fallback_chain:
        return None
    for entry in fallback_chain:
        target = _coerce_target(entry)
        if target is not None:
            return target
    return None


def _resolve_mode(
    *,
    explicit_mode: Optional[str],
    primary_provider: str,
    has_cheap: bool,
    has_smart: bool,
) -> str:
    if explicit_mode and explicit_mode in {"local-first", "smart-primary"}:
        return explicit_mode

    if has_cheap and has_smart:
        # Auto: derive from primary.  If primary looks local, we want
        # local-first behavior so hard tasks escalate.  Otherwise
        # smart-primary so simple tasks fall back to cheap.
        if (primary_provider or "").strip().lower() in LOCAL_LIKE_PROVIDERS:
            return "local-first"
        return "smart-primary"
    if has_smart:
        return "local-first"
    if has_cheap:
        return "smart-primary"
    return "disabled"


def decide_route(
    *,
    user_message: str,
    primary_provider: str,
    primary_model: str,  # accepted for log context, unused in heuristic
    config: Optional[Dict[str, Any]],
    fallback_chain: Optional[List[Dict[str, Any]]] = None,
) -> RoutingDecision:
    """Compute a routing decision for the upcoming turn.

    Safe on bad config: any unrecognized shape returns the ``primary``
    decision with a descriptive ``reason``.  Never raises.
    """
    cfg = config or {}
    if not cfg.get("enabled"):
        return RoutingDecision("primary", None, "disabled", "disabled", "unknown")

    # Coerce optional cheap/smart targets.
    cheap_target = _coerce_target(cfg.get("cheap_model"))
    smart_target = _coerce_target(cfg.get("smart_model"))

    # If smart_model is missing, fall back to first fallback entry.
    if smart_target is None:
        smart_target = _first_valid_fallback(fallback_chain)
        smart_from_fallback = smart_target is not None
    else:
        smart_from_fallback = False

    mode = _resolve_mode(
        explicit_mode=(cfg.get("mode") or cfg.get("strategy_mode") or "").strip().lower() or None,
        primary_provider=primary_provider,
        has_cheap=cheap_target is not None,
        has_smart=smart_target is not None,
    )
    if mode == "disabled":
        return RoutingDecision(
            "primary", None,
            "no cheap_model or smart_model configured (and no fallback target)",
            "disabled", "unknown",
        )

    classification = classify_difficulty(
        user_message,
        max_simple_chars=int(cfg.get("max_simple_chars") or DEFAULT_MAX_SIMPLE_CHARS),
        max_simple_words=int(cfg.get("max_simple_words") or DEFAULT_MAX_SIMPLE_WORDS),
        extra_hard_keywords=cfg.get("extra_hard_keywords") or None,
    )

    if mode == "local-first":
        if classification == "hard" and smart_target is not None:
            reason = (
                "hard task; routing to smart_model"
                + (" (sourced from fallback chain)" if smart_from_fallback else "")
            )
            return RoutingDecision("smart", smart_target, reason, mode, classification)
        return RoutingDecision(
            "primary", None,
            "simple task; primary local model is appropriate"
            if classification == "simple" else "no smart target available",
            mode, classification,
        )

    # smart-primary
    if classification == "simple" and cheap_target is not None:
        return RoutingDecision(
            "cheap", cheap_target,
            "simple task; routing to cheap_model", mode, classification,
        )
    return RoutingDecision(
        "primary", None,
        "hard task; primary smart model is appropriate"
        if classification == "hard" else "no cheap target available",
        mode, classification,
    )


__all__ = [
    "DEFAULT_MAX_SIMPLE_CHARS",
    "DEFAULT_MAX_SIMPLE_WORDS",
    "HARD_KEYWORDS",
    "HARD_PHRASE_PATTERNS",
    "LOCAL_LIKE_PROVIDERS",
    "RoutingDecision",
    "RoutingTarget",
    "classify_difficulty",
    "decide_route",
]
