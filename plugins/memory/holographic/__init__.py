"""hermes-memory-store — holographic memory plugin using MemoryProvider interface.

Registers as a MemoryProvider plugin, giving the agent structured fact storage
with entity resolution, trust scoring, and HRR-based compositional retrieval.

Original plugin by dusterbloom (PR #2351), adapted to the MemoryProvider ABC.

Config in $HERMES_HOME/config.yaml (profile-scoped):
  plugins:
    hermes-memory-store:
      db_path: $HERMES_HOME/memory_store.db   # omit to use the default
      auto_extract: false
      default_trust: 0.5
      min_trust_threshold: 0.3
      temporal_decay_half_life: 0
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List

from agent.memory_provider import MemoryProvider
from tools.registry import tool_error
from .store import MemoryStore
from .retrieval import FactRetriever
from hermes_cli.config import cfg_get

logger = logging.getLogger(__name__)


_AUTO_EXTRACT_MAX_SOURCE_CHARS = 800
_AUTO_EXTRACT_MAX_CLAUSE_CHARS = 180
_AUTO_EXTRACT_MAX_FACT_CHARS = 260

_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+|\n+")
_TASK_REQUEST_RE = re.compile(
    r"\b("
    r"can you|could you|would you|please|let'?s|lets|"
    r"look|fix|install|research|build|make|update|run|check|"
    r"tell me|show me|go ahead|keep going|continue"
    r")\b",
    re.IGNORECASE,
)
_UNSTABLE_REFERENCE_RE = re.compile(
    r"\b(this|that|it|stuff|thing|something|anything|everything|here|there|now|today|tomorrow|yesterday)\b",
    re.IGNORECASE,
)


def _clean_auto_extract_clause(value: str) -> str:
    value = re.sub(r"\s+", " ", str(value or "")).strip(" \t\r\n\"'`.,;:")
    value = re.sub(r"^(?:that|to)\s+", "", value, flags=re.IGNORECASE).strip()
    return value[:_AUTO_EXTRACT_MAX_CLAUSE_CHARS].strip(" \t\r\n\"'`.,;:")


def _is_stable_auto_extract_clause(value: str) -> bool:
    value = _clean_auto_extract_clause(value)
    if len(value) < 3:
        return False
    if "?" in value or "\n" in value:
        return False
    if _TASK_REQUEST_RE.search(value):
        return False
    if re.search(r"\byou(?:r|rs|self)?\b", value, re.IGNORECASE):
        return False
    if _UNSTABLE_REFERENCE_RE.fullmatch(value):
        return False
    return True


def _format_auto_extract_fact(template: str, *parts: str) -> str | None:
    cleaned = [_clean_auto_extract_clause(part) for part in parts]
    if not all(_is_stable_auto_extract_clause(part) for part in cleaned):
        return None
    fact = template.format(*cleaned)
    fact = re.sub(r"\s+", " ", fact).strip()
    if not fact.endswith("."):
        fact += "."
    if len(fact) > _AUTO_EXTRACT_MAX_FACT_CHARS:
        return None
    return fact


def _auto_extract_facts_from_text(content: str) -> list[tuple[str, str]]:
    """Return distilled ``(fact, category)`` pairs from a user message.

    This extractor is deliberately conservative. It should never store raw user
    turns; it only keeps short normalized facts with stable anchors.
    """
    if not isinstance(content, str):
        return []
    text = re.sub(r"\s+", " ", content.strip())
    if len(text) < 10 or len(text) > _AUTO_EXTRACT_MAX_SOURCE_CHARS:
        return []

    # Questions and explicit task requests are usually active-work context, not
    # durable memory. Avoid storing snippets from "can you..." / "I need..." turns.
    if "?" in text or _TASK_REQUEST_RE.search(text):
        return []

    patterns: list[tuple[re.Pattern[str], str, Any]] = [
        (
            re.compile(r"\bI\s+prefer\s+(?P<value>[^.!?\n]+)", re.IGNORECASE),
            "user_pref",
            lambda m: _format_auto_extract_fact("User prefers {}.", m.group("value")),
        ),
        (
            re.compile(r"\bI\s+(?:like|love)\s+(?P<value>[^.!?\n]+)", re.IGNORECASE),
            "user_pref",
            lambda m: _format_auto_extract_fact("User likes {}.", m.group("value")),
        ),
        (
            re.compile(r"\bI\s+use\s+(?P<value>[^.!?\n]+)", re.IGNORECASE),
            "user_pref",
            lambda m: _format_auto_extract_fact("User uses {}.", m.group("value")),
        ),
        (
            re.compile(r"\bI\s+(?:always|usually)\s+(?P<value>[^.!?\n]+)", re.IGNORECASE),
            "user_pref",
            lambda m: _format_auto_extract_fact("User usually {}.", m.group("value")),
        ),
        (
            re.compile(r"\bI\s+never\s+(?P<value>[^.!?\n]+)", re.IGNORECASE),
            "user_pref",
            lambda m: _format_auto_extract_fact("User never {}.", m.group("value")),
        ),
        (
            re.compile(
                r"\bmy\s+(?P<kind>favorite|preferred|default)\s+(?P<thing>[A-Za-z0-9 _/-]{2,60})\s+is\s+(?P<value>[^.!?\n]+)",
                re.IGNORECASE,
            ),
            "user_pref",
            lambda m: _format_auto_extract_fact(
                "User's {} {} is {}.",
                m.group("kind").lower(),
                m.group("thing"),
                m.group("value"),
            ),
        ),
        (
            re.compile(r"\bwe\s+(?:decided|agreed|chose)\s+(?:to\s+)?(?P<value>[^.!?\n]+)", re.IGNORECASE),
            "project",
            lambda m: _format_auto_extract_fact("Project decision: {}.", m.group("value")),
        ),
        (
            re.compile(
                r"\b(?P<subject>the project|hermes|the repo|the codebase)\s+(?P<verb>uses|needs|requires)\s+(?P<value>[^.!?\n]+)",
                re.IGNORECASE,
            ),
            "project",
            lambda m: _format_auto_extract_fact(
                "{} {} {}.",
                m.group("subject").capitalize(),
                m.group("verb").lower(),
                m.group("value"),
            ),
        ),
    ]

    extracted: list[tuple[str, str]] = []
    seen: set[str] = set()
    for sentence in _SENTENCE_SPLIT_RE.split(text):
        sentence = sentence.strip()
        if not sentence or len(sentence) > 320 or "?" in sentence:
            continue
        for pattern, category, render in patterns:
            match = pattern.search(sentence)
            if not match:
                continue
            fact = render(match)
            if fact and fact.lower() not in seen:
                seen.add(fact.lower())
                extracted.append((fact, category))
            break
    return extracted


# ---------------------------------------------------------------------------
# Tool schemas (unchanged from original PR)
# ---------------------------------------------------------------------------

FACT_STORE_SCHEMA = {
    "name": "fact_store",
    "description": (
        "Deep structured memory with algebraic reasoning. "
        "Use alongside the memory tool — memory for always-on context, "
        "fact_store for deep recall and compositional queries.\n\n"
        "ACTIONS (simple → powerful):\n"
        "• add — Store a fact the user would expect you to remember.\n"
        "• search — Keyword lookup ('editor config', 'deploy process').\n"
        "• probe — Entity recall: ALL facts about a person/thing.\n"
        "• related — What connects to an entity? Structural adjacency.\n"
        "• reason — Compositional: facts connected to MULTIPLE entities simultaneously.\n"
        "• contradict — Memory hygiene: find facts making conflicting claims.\n"
        "• update/remove/list — CRUD operations.\n\n"
        "IMPORTANT: Before answering questions about the user, ALWAYS probe or reason first."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["add", "search", "probe", "related", "reason", "contradict", "update", "remove", "list"],
            },
            "content": {"type": "string", "description": "Fact content (required for 'add')."},
            "query": {"type": "string", "description": "Search query (required for 'search')."},
            "entity": {"type": "string", "description": "Entity name for 'probe'/'related'."},
            "entities": {"type": "array", "items": {"type": "string"}, "description": "Entity names for 'reason'."},
            "fact_id": {"type": "integer", "description": "Fact ID for 'update'/'remove'."},
            "category": {"type": "string", "enum": ["user_pref", "project", "tool", "general"]},
            "tags": {"type": "string", "description": "Comma-separated tags."},
            "trust_delta": {"type": "number", "description": "Trust adjustment for 'update'."},
            "min_trust": {"type": "number", "description": "Minimum trust filter (default: 0.3)."},
            "limit": {"type": "integer", "description": "Max results (default: 10)."},
        },
        "required": ["action"],
    },
}

FACT_FEEDBACK_SCHEMA = {
    "name": "fact_feedback",
    "description": (
        "Rate a fact after using it. Mark 'helpful' if accurate, 'unhelpful' if outdated. "
        "This trains the memory — good facts rise, bad facts sink."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["helpful", "unhelpful"]},
            "fact_id": {"type": "integer", "description": "The fact ID to rate."},
        },
        "required": ["action", "fact_id"],
    },
}


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

def _load_plugin_config() -> dict:
    from hermes_constants import get_hermes_home
    config_path = get_hermes_home() / "config.yaml"
    if not config_path.exists():
        return {}
    try:
        import yaml
        with open(config_path) as f:
            all_config = yaml.safe_load(f) or {}
        return cfg_get(all_config, "plugins", "hermes-memory-store", default={}) or {}
    except Exception:
        return {}


# ---------------------------------------------------------------------------
# MemoryProvider implementation
# ---------------------------------------------------------------------------

class HolographicMemoryProvider(MemoryProvider):
    """Holographic memory with structured facts, entity resolution, and HRR retrieval."""

    def __init__(self, config: dict | None = None):
        self._config = config or _load_plugin_config()
        self._store = None
        self._retriever = None
        self._min_trust = float(self._config.get("min_trust_threshold", 0.3))

    @property
    def name(self) -> str:
        return "holographic"

    def is_available(self) -> bool:
        return True  # SQLite is always available, numpy is optional

    def save_config(self, values, hermes_home):
        """Write config to config.yaml under plugins.hermes-memory-store."""
        from pathlib import Path
        config_path = Path(hermes_home) / "config.yaml"
        try:
            import yaml
            existing = {}
            if config_path.exists():
                with open(config_path) as f:
                    existing = yaml.safe_load(f) or {}
            existing.setdefault("plugins", {})
            existing["plugins"]["hermes-memory-store"] = values
            with open(config_path, "w") as f:
                yaml.dump(existing, f, default_flow_style=False)
        except Exception:
            pass

    def get_config_schema(self):
        from hermes_constants import display_hermes_home
        _default_db = f"{display_hermes_home()}/memory_store.db"
        return [
            {"key": "db_path", "description": "SQLite database path", "default": _default_db},
            {"key": "auto_extract", "description": "Auto-extract facts at session end", "default": "false", "choices": ["true", "false"]},
            {"key": "default_trust", "description": "Default trust score for new facts", "default": "0.5"},
            {"key": "hrr_dim", "description": "HRR vector dimensions", "default": "1024"},
        ]

    def initialize(self, session_id: str, **kwargs) -> None:
        from hermes_constants import get_hermes_home
        _hermes_home = str(get_hermes_home())
        _default_db = _hermes_home + "/memory_store.db"
        db_path = self._config.get("db_path", _default_db)
        # Expand $HERMES_HOME in user-supplied paths so config values like
        # "$HERMES_HOME/memory_store.db" or "~/.hermes/memory_store.db" both
        # resolve to the active profile's directory.
        if isinstance(db_path, str):
            db_path = db_path.replace("$HERMES_HOME", _hermes_home)
            db_path = db_path.replace("${HERMES_HOME}", _hermes_home)
        default_trust = float(self._config.get("default_trust", 0.5))
        hrr_dim = int(self._config.get("hrr_dim", 1024))
        hrr_weight = float(self._config.get("hrr_weight", 0.3))
        temporal_decay = int(self._config.get("temporal_decay_half_life", 0))

        self._store = MemoryStore(db_path=db_path, default_trust=default_trust, hrr_dim=hrr_dim)
        self._retriever = FactRetriever(
            store=self._store,
            temporal_decay_half_life=temporal_decay,
            hrr_weight=hrr_weight,
            hrr_dim=hrr_dim,
        )
        self._session_id = session_id

    def system_prompt_block(self) -> str:
        if not self._store:
            return ""
        try:
            total = self._store._conn.execute(
                "SELECT COUNT(*) FROM facts"
            ).fetchone()[0]
        except Exception:
            total = 0
        if total == 0:
            return (
                "# Holographic Memory\n"
                "Active. Empty fact store — proactively add facts the user would expect you to remember.\n"
                "Use fact_store(action='add') to store durable structured facts about people, projects, preferences, decisions.\n"
                "Use fact_feedback to rate facts after using them (trains trust scores)."
            )
        return (
            f"# Holographic Memory\n"
            f"Active. {total} facts stored with entity resolution and trust scoring.\n"
            f"Use fact_store to search, probe entities, reason across entities, or add facts.\n"
            f"Use fact_feedback to rate facts after using them (trains trust scores)."
        )

    def prefetch(self, query: str, *, session_id: str = "") -> str:
        if not self._retriever or not query:
            return ""
        try:
            results = self._retriever.search(query, min_trust=self._min_trust, limit=5)
            if not results:
                return ""
            lines = []
            for r in results:
                trust = r.get("trust_score", r.get("trust", 0))
                lines.append(f"- [{trust:.1f}] {r.get('content', '')}")
            return "## Holographic Memory\n" + "\n".join(lines)
        except Exception as e:
            logger.debug("Holographic prefetch failed: %s", e)
            return ""

    def sync_turn(self, user_content: str, assistant_content: str, *, session_id: str = "") -> None:
        # Holographic memory stores explicit facts via tools, not auto-sync.
        # The on_session_end hook handles auto-extraction if configured.
        pass

    def get_tool_schemas(self) -> List[Dict[str, Any]]:
        return [FACT_STORE_SCHEMA, FACT_FEEDBACK_SCHEMA]

    def handle_tool_call(self, tool_name: str, args: Dict[str, Any], **kwargs) -> str:
        if tool_name == "fact_store":
            return self._handle_fact_store(args)
        elif tool_name == "fact_feedback":
            return self._handle_fact_feedback(args)
        return tool_error(f"Unknown tool: {tool_name}")

    def on_session_end(self, messages: List[Dict[str, Any]]) -> None:
        if not self._config.get("auto_extract", False):
            return
        if not self._store or not messages:
            return
        self._auto_extract_facts(messages)

    def on_memory_write(self, action: str, target: str, content: str) -> None:
        """Mirror built-in memory writes as facts."""
        if action == "add" and self._store and content:
            try:
                category = "user_pref" if target == "user" else "general"
                self._store.add_fact(content, category=category)
            except Exception as e:
                logger.debug("Holographic memory_write mirror failed: %s", e)

    def shutdown(self) -> None:
        self._store = None
        self._retriever = None

    # -- Tool handlers -------------------------------------------------------

    def _handle_fact_store(self, args: dict) -> str:
        try:
            action = args["action"]
            store = self._store
            retriever = self._retriever

            if action == "add":
                fact_id = store.add_fact(
                    args["content"],
                    category=args.get("category", "general"),
                    tags=args.get("tags", ""),
                )
                return json.dumps({"fact_id": fact_id, "status": "added"})

            elif action == "search":
                results = retriever.search(
                    args["query"],
                    category=args.get("category"),
                    min_trust=float(args.get("min_trust", self._min_trust)),
                    limit=int(args.get("limit", 10)),
                )
                return json.dumps({"results": results, "count": len(results)})

            elif action == "probe":
                results = retriever.probe(
                    args["entity"],
                    category=args.get("category"),
                    limit=int(args.get("limit", 10)),
                )
                return json.dumps({"results": results, "count": len(results)})

            elif action == "related":
                results = retriever.related(
                    args["entity"],
                    category=args.get("category"),
                    limit=int(args.get("limit", 10)),
                )
                return json.dumps({"results": results, "count": len(results)})

            elif action == "reason":
                entities = args.get("entities", [])
                if not entities:
                    return tool_error("reason requires 'entities' list")
                results = retriever.reason(
                    entities,
                    category=args.get("category"),
                    limit=int(args.get("limit", 10)),
                )
                return json.dumps({"results": results, "count": len(results)})

            elif action == "contradict":
                results = retriever.contradict(
                    category=args.get("category"),
                    limit=int(args.get("limit", 10)),
                )
                return json.dumps({"results": results, "count": len(results)})

            elif action == "update":
                updated = store.update_fact(
                    int(args["fact_id"]),
                    content=args.get("content"),
                    trust_delta=float(args["trust_delta"]) if "trust_delta" in args else None,
                    tags=args.get("tags"),
                    category=args.get("category"),
                )
                return json.dumps({"updated": updated})

            elif action == "remove":
                removed = store.remove_fact(int(args["fact_id"]))
                return json.dumps({"removed": removed})

            elif action == "list":
                facts = store.list_facts(
                    category=args.get("category"),
                    min_trust=float(args.get("min_trust", 0.0)),
                    limit=int(args.get("limit", 10)),
                )
                return json.dumps({"facts": facts, "count": len(facts)})

            else:
                return tool_error(f"Unknown action: {action}")

        except KeyError as exc:
            return tool_error(f"Missing required argument: {exc}")
        except Exception as exc:
            return tool_error(str(exc))

    def _handle_fact_feedback(self, args: dict) -> str:
        try:
            fact_id = int(args["fact_id"])
            helpful = args["action"] == "helpful"
            result = self._store.record_feedback(fact_id, helpful=helpful)
            return json.dumps(result)
        except KeyError as exc:
            return tool_error(f"Missing required argument: {exc}")
        except Exception as exc:
            return tool_error(str(exc))

    # -- Auto-extraction (on_session_end) ------------------------------------

    def _auto_extract_facts(self, messages: list) -> None:
        extracted = 0
        seen: set[str] = set()
        for msg in messages:
            if msg.get("role") != "user":
                continue
            content = msg.get("content", "")
            for fact, category in _auto_extract_facts_from_text(content):
                if fact.lower() in seen:
                    continue
                seen.add(fact.lower())
                try:
                    self._store.add_fact(fact, category=category)
                    extracted += 1
                except Exception:
                    pass

        if extracted:
            logger.info("Auto-extracted %d facts from conversation", extracted)


# ---------------------------------------------------------------------------
# Plugin entry point
# ---------------------------------------------------------------------------

def register(ctx) -> None:
    """Register the holographic memory provider with the plugin system."""
    config = _load_plugin_config()
    provider = HolographicMemoryProvider(config=config)
    ctx.register_memory_provider(provider)
