"""LanceDB memory plugin — local-first semantic memory using LanceDB."""

from __future__ import annotations

import json
import logging
import os
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from agent.memory_provider import MemoryProvider
from hermes_constants import display_hermes_home, get_hermes_home
from tools.registry import tool_error

from .store import LanceMemoryStore, content_hash, normalize_tags

logger = logging.getLogger(__name__)

_DEFAULT_TABLE_NAME = "memories"
_DEFAULT_SENTENCE_MODEL = "all-MiniLM-L6-v2"
_DEFAULT_OPENAI_MODEL = "text-embedding-3-small"


def _default_config() -> dict[str, Any]:
    return {
        "db_path": f"{display_hermes_home()}/lancedb",
        "table_name": _DEFAULT_TABLE_NAME,
        "embedding_backend": "openai",
        "embedding_model": _DEFAULT_OPENAI_MODEL,
        "memory_mode": "hybrid",
        "auto_recall": True,
        "auto_capture": True,
        "max_prefetch_results": 6,
        "max_tool_results": 8,
    }


def _parse_bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _load_config(hermes_home: str | None = None) -> dict[str, Any]:
    base = _default_config()
    home = Path(hermes_home or get_hermes_home())
    config_path = home / "lancedb.json"
    if config_path.exists():
        try:
            file_data = json.loads(config_path.read_text())
            if isinstance(file_data, dict):
                base.update(file_data)
        except Exception:
            logger.debug("Failed to parse lancedb.json", exc_info=True)

    env_map = {
        "LANCEDB_DB_PATH": "db_path",
        "LANCEDB_TABLE_NAME": "table_name",
        "LANCEDB_EMBEDDING_BACKEND": "embedding_backend",
        "LANCEDB_EMBEDDING_MODEL": "embedding_model",
        "LANCEDB_MEMORY_MODE": "memory_mode",
        "LANCEDB_MAX_PREFETCH_RESULTS": "max_prefetch_results",
        "LANCEDB_MAX_TOOL_RESULTS": "max_tool_results",
    }
    for env_var, key in env_map.items():
        if os.environ.get(env_var):
            base[key] = os.environ[env_var]

    if "LANCEDB_AUTO_RECALL" in os.environ:
        base["auto_recall"] = _parse_bool(os.environ.get("LANCEDB_AUTO_RECALL"), True)
    if "LANCEDB_AUTO_CAPTURE" in os.environ:
        base["auto_capture"] = _parse_bool(os.environ.get("LANCEDB_AUTO_CAPTURE"), True)

    base["db_path"] = str(base["db_path"]).replace("$HERMES_HOME", str(home)).replace(
        "${HERMES_HOME}", str(home)
    )
    base["auto_recall"] = _parse_bool(base.get("auto_recall"), True)
    base["auto_capture"] = _parse_bool(base.get("auto_capture"), True)
    try:
        base["max_prefetch_results"] = max(1, min(12, int(base.get("max_prefetch_results", 6))))
    except Exception:
        base["max_prefetch_results"] = 6
    try:
        base["max_tool_results"] = max(1, min(20, int(base.get("max_tool_results", 8))))
    except Exception:
        base["max_tool_results"] = 8
    backend = str(base.get("embedding_backend", "openai")).strip().lower()
    if backend not in {"sentence-transformers", "openai"}:
        backend = "openai"
    base["embedding_backend"] = backend
    mode = str(base.get("memory_mode", "hybrid")).strip().lower()
    if mode not in {"context", "tools", "hybrid"}:
        mode = "hybrid"
    base["memory_mode"] = mode
    if not str(base.get("table_name", "")).strip():
        base["table_name"] = _DEFAULT_TABLE_NAME
        if not str(base.get("embedding_model", "")).strip():
            base["embedding_model"] = (
                _DEFAULT_OPENAI_MODEL if backend == "openai" else _DEFAULT_SENTENCE_MODEL
        )
    return base


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clean_text(text: str, *, limit: int = 2500) -> str:
    collapsed = " ".join((text or "").split())
    if len(collapsed) <= limit:
        return collapsed
    return collapsed[:limit].rstrip() + " ..."


def _scope_to_types(scope: str) -> list[str] | None:
    mapping = {
        "all": None,
        "durable": ["profile", "memory"],
        "profile": ["profile"],
        "memory": ["memory"],
        "episode": ["episode"],
    }
    return mapping.get(scope, None)


class _SentenceTransformerEmbedder:
    def __init__(self, model_name: str):
        self.model_name = model_name
        self._model = None
        self.dimension = 384

    def _get_model(self):
        if self._model is None:
            from sentence_transformers import SentenceTransformer

            self._model = SentenceTransformer(self.model_name)
            dim = getattr(self._model, "get_sentence_embedding_dimension", None)
            if callable(dim):
                try:
                    self.dimension = int(dim())
                except Exception:
                    pass
        return self._model

    def embed_texts(self, texts: List[str]) -> List[List[float]]:
        model = self._get_model()
        embeddings = model.encode(texts, normalize_embeddings=True)
        return [list(map(float, row)) for row in embeddings]


class _OpenAIEmbedder:
    def __init__(self, model_name: str, api_key: str):
        self.model_name = model_name
        self.api_key = api_key
        self.dimension = 1536 if "small" in model_name else 3072
        self._client = None

    def _get_client(self):
        if self._client is None:
            from openai import OpenAI

            self._client = OpenAI(api_key=self.api_key)
        return self._client

    def embed_texts(self, texts: List[str]) -> List[List[float]]:
        client = self._get_client()
        response = client.embeddings.create(model=self.model_name, input=texts)
        return [list(map(float, item.embedding)) for item in response.data]


STORE_SCHEMA = {
    "name": "lancedb_store",
    "description": "Store a durable memory in the local LanceDB memory store.",
    "parameters": {
        "type": "object",
        "properties": {
            "content": {"type": "string", "description": "Memory content to store."},
            "record_type": {
                "type": "string",
                "enum": ["memory", "profile"],
                "description": "Use 'profile' for user identity/preferences, 'memory' for general durable facts.",
            },
            "category": {"type": "string", "description": "Short category such as preference, project, decision, or fact."},
            "importance": {"type": "number", "description": "Importance from 0.0 to 1.0."},
            "tags": {"type": "string", "description": "Optional comma-separated tags."},
        },
        "required": ["content"],
    },
}

SEARCH_SCHEMA = {
    "name": "lancedb_search",
    "description": "Search the local LanceDB memory store for relevant memories.",
    "parameters": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Search query."},
            "scope": {
                "type": "string",
                "enum": ["all", "durable", "profile", "memory", "episode"],
                "description": "Limit the search to a subset of memory types.",
            },
            "limit": {"type": "integer", "description": "Maximum number of results to return."},
        },
        "required": ["query"],
    },
}

FORGET_SCHEMA = {
    "name": "lancedb_forget",
    "description": "Forget one or more memories from the local LanceDB memory store.",
    "parameters": {
        "type": "object",
        "properties": {
            "id": {"type": "string", "description": "Exact memory id to delete."},
            "query": {"type": "string", "description": "Delete the top matches for this query if id is not provided."},
            "scope": {
                "type": "string",
                "enum": ["all", "durable", "profile", "memory", "episode"],
                "description": "Limit query-based deletion to a subset of memory types.",
            },
            "limit": {"type": "integer", "description": "Maximum number of query matches to delete."},
        },
    },
}

PROFILE_SCHEMA = {
    "name": "lancedb_profile",
    "description": "Show the most important durable memories currently stored for the user.",
    "parameters": {
        "type": "object",
        "properties": {
            "limit": {"type": "integer", "description": "Maximum number of profile items to return."},
        },
    },
}


class LanceDBMemoryProvider(MemoryProvider):
    """Local-first memory provider backed by LanceDB."""

    def __init__(self):
        self._config = _load_config()
        self._store: Optional[LanceMemoryStore] = None
        self._embedder = None
        self._session_id = ""
        self._user_id = "default"
        self._agent_identity = "default"
        self._workspace = "hermes"
        self._memory_mode = "hybrid"
        self._auto_recall = True
        self._auto_capture = True
        self._max_prefetch_results = 6
        self._max_tool_results = 8
        self._write_enabled = True
        self._prefetch_lock = threading.Lock()
        self._prefetch_result = ""
        self._prefetch_thread: Optional[threading.Thread] = None
        self._sync_thread: Optional[threading.Thread] = None

    @property
    def name(self) -> str:
        return "lancedb"

    def is_available(self) -> bool:
        cfg = _load_config()
        try:
            __import__("lancedb")
        except Exception:
            return False

        backend = cfg.get("embedding_backend", "sentence-transformers")
        if backend == "sentence-transformers":
            try:
                __import__("sentence_transformers")
                return True
            except Exception:
                return False
        return bool(os.environ.get("OPENAI_API_KEY", ""))

    def save_config(self, values: Dict[str, Any], hermes_home: str) -> None:
        config_path = Path(hermes_home) / "lancedb.json"
        existing = {}
        if config_path.exists():
            try:
                existing = json.loads(config_path.read_text())
            except Exception:
                existing = {}
        existing.update(values)
        config_path.write_text(json.dumps(existing, indent=2) + "\n")

    def get_config_schema(self):
        return [
            {"key": "db_path", "description": "LanceDB directory path", "default": f"{display_hermes_home()}/lancedb"},
            {"key": "table_name", "description": "LanceDB table name", "default": _DEFAULT_TABLE_NAME},
            {
                "key": "embedding_backend",
                "description": "Embedding backend",
                "default": "openai",
                "choices": ["openai", "sentence-transformers"],
            },
            {
                "key": "embedding_model",
                "description": "Embedding model",
                "default": _DEFAULT_OPENAI_MODEL,
                "when": {"embedding_backend": "openai"},
            },
            {
                "key": "embedding_model",
                "description": "Embedding model",
                "default": _DEFAULT_SENTENCE_MODEL,
                "when": {"embedding_backend": "sentence-transformers"},
            },
            {
                "key": "openai_api_key",
                "description": "OpenAI API key for embeddings",
                "secret": True,
                "env_var": "OPENAI_API_KEY",
                "url": "https://platform.openai.com/api-keys",
                "when": {"embedding_backend": "openai"},
            },
            {
                "key": "memory_mode",
                "description": "Memory integration mode",
                "default": "hybrid",
                "choices": ["hybrid", "context", "tools"],
            },
            {"key": "auto_recall", "description": "Automatically recall memories before each turn", "default": "true", "choices": ["true", "false"]},
            {"key": "auto_capture", "description": "Automatically store conversation turns as episodic memories", "default": "true", "choices": ["true", "false"]},
            {"key": "max_prefetch_results", "description": "Maximum recalled memories per turn", "default": "6"},
            {"key": "max_tool_results", "description": "Maximum tool results", "default": "8"},
        ]

    def initialize(self, session_id: str, **kwargs) -> None:
        hermes_home = kwargs.get("hermes_home") or str(get_hermes_home())
        self._config = _load_config(hermes_home)
        self._session_id = session_id
        self._user_id = kwargs.get("user_id", "default") or "default"
        self._agent_identity = kwargs.get("agent_identity", "default") or "default"
        self._workspace = kwargs.get("agent_workspace", "hermes") or "hermes"
        self._memory_mode = self._config["memory_mode"]
        self._auto_recall = bool(self._config["auto_recall"])
        self._auto_capture = bool(self._config["auto_capture"])
        self._max_prefetch_results = int(self._config["max_prefetch_results"])
        self._max_tool_results = int(self._config["max_tool_results"])
        agent_context = kwargs.get("agent_context", "primary")
        self._write_enabled = agent_context not in {"cron", "flush", "subagent"}

        backend = self._config["embedding_backend"]
        model_name = self._config["embedding_model"]
        if backend == "openai":
            self._embedder = _OpenAIEmbedder(model_name, os.environ.get("OPENAI_API_KEY", ""))
        else:
            self._embedder = _SentenceTransformerEmbedder(model_name)

        self._store = LanceMemoryStore(
            db_path=self._config["db_path"],
            table_name=self._config["table_name"],
            embedder=self._embedder,
        )

    def system_prompt_block(self) -> str:
        if not self._store:
            return ""
        counts = self._store.counts(user_id=self._user_id, agent_identity=self._agent_identity)
        if self._memory_mode == "context":
            return (
                "# LanceDB Memory\n"
                f"Active (context mode). Durable memories: {counts.get('profile', 0) + counts.get('memory', 0)}, "
                f"episodic memories: {counts.get('episode', 0)}.\n"
                "Relevant memories are injected automatically before each turn."
            )
        if self._memory_mode == "tools":
            return (
                "# LanceDB Memory\n"
                f"Active (tools mode). Durable memories: {counts.get('profile', 0) + counts.get('memory', 0)}, "
                f"episodic memories: {counts.get('episode', 0)}.\n"
                "Use lancedb_search, lancedb_store, lancedb_forget, and lancedb_profile for explicit memory operations."
            )
        return (
            "# LanceDB Memory\n"
            f"Active. Durable memories: {counts.get('profile', 0) + counts.get('memory', 0)}, "
            f"episodic memories: {counts.get('episode', 0)}.\n"
            "Relevant memories are injected automatically before each turn. "
            "Use lancedb_search, lancedb_store, lancedb_forget, and lancedb_profile for explicit memory operations."
        )

    def _format_prefetch(self, query: str) -> str:
        if not self._store or not query.strip():
            return ""

        durable = self._store.search(
            query,
            limit=self._max_prefetch_results,
            record_types=["profile", "memory"],
            user_id=self._user_id,
            agent_identity=self._agent_identity,
        )
        episodic = self._store.search(
            query,
            limit=max(1, self._max_prefetch_results // 2),
            record_types=["episode"],
            user_id=self._user_id,
            agent_identity=self._agent_identity,
        )

        parts = []
        if durable:
            lines = [f"- [{item['category']}] {item['content']}" for item in durable]
            parts.append("## Stable profile\n" + "\n".join(lines))
        if episodic:
            lines = [f"- {item['content']}" for item in episodic]
            parts.append("## Relevant past context\n" + "\n".join(lines))
        return "\n\n".join(parts)

    def prefetch(self, query: str, *, session_id: str = "") -> str:
        if self._memory_mode == "tools" or not self._auto_recall:
            return ""
        if self._prefetch_thread and self._prefetch_thread.is_alive():
            self._prefetch_thread.join(timeout=3.0)
        with self._prefetch_lock:
            cached = self._prefetch_result
            self._prefetch_result = ""
        if cached:
            return cached
        return self._format_prefetch(query)

    def queue_prefetch(self, query: str, *, session_id: str = "") -> None:
        if self._memory_mode == "tools" or not self._auto_recall or not query.strip():
            return

        def _run():
            try:
                result = self._format_prefetch(query)
                if result:
                    with self._prefetch_lock:
                        self._prefetch_result = result
            except Exception:
                logger.debug("LanceDB prefetch failed", exc_info=True)

        self._prefetch_thread = threading.Thread(target=_run, daemon=True, name="lancedb-prefetch")
        self._prefetch_thread.start()

    def _store_record(
        self,
        *,
        content: str,
        record_type: str,
        category: str,
        source: str,
        importance: float,
        tags: str | list[str] | None = None,
    ) -> dict:
        if not self._store:
            raise RuntimeError("LanceDB store is not initialized")
        now = _utc_now()
        record = {
            "id": str(uuid.uuid4()),
            "content": content,
            "content_hash": content_hash(content),
            "record_type": record_type,
            "category": category,
            "source": source,
            "user_id": self._user_id,
            "session_id": self._session_id,
            "agent_identity": self._agent_identity,
            "workspace": self._workspace,
            "importance": importance,
            "tags": normalize_tags(tags),
            "created_at": now,
            "updated_at": now,
        }
        return self._store.upsert(record)

    def sync_turn(self, user_content: str, assistant_content: str, *, session_id: str = "") -> None:
        if not self._write_enabled or not self._auto_capture or not self._store:
            return

        clean_user = _clean_text(user_content, limit=1800)
        clean_assistant = _clean_text(assistant_content, limit=1800)
        if len(clean_user) < 12 or len(clean_assistant) < 12:
            return

        combined = (
            f"[user]\n{clean_user}\n[/user]\n\n"
            f"[assistant]\n{clean_assistant}\n[/assistant]"
        )

        def _run():
            try:
                self._store_record(
                    content=combined,
                    record_type="episode",
                    category="conversation",
                    source="sync_turn",
                    importance=0.35,
                )
            except Exception:
                logger.debug("LanceDB sync_turn failed", exc_info=True)

        if self._sync_thread and self._sync_thread.is_alive():
            self._sync_thread.join(timeout=3.0)
        self._sync_thread = threading.Thread(target=_run, daemon=True, name="lancedb-sync")
        self._sync_thread.start()

    def on_memory_write(self, action: str, target: str, content: str) -> None:
        if action not in {"add", "replace"} or not self._write_enabled or not self._store:
            return
        cleaned = _clean_text(content, limit=1200)
        if not cleaned:
            return
        try:
            self._store_record(
                content=cleaned,
                record_type="profile" if target == "user" else "memory",
                category="preference" if target == "user" else "fact",
                source="memory_write",
                importance=0.9 if target == "user" else 0.75,
            )
        except Exception:
            logger.debug("LanceDB on_memory_write failed", exc_info=True)

    def get_tool_schemas(self) -> List[Dict[str, Any]]:
        if self._memory_mode == "context":
            return []
        return [STORE_SCHEMA, SEARCH_SCHEMA, FORGET_SCHEMA, PROFILE_SCHEMA]

    def _tool_store(self, args: dict) -> str:
        content = _clean_text(str(args.get("content") or ""), limit=2500)
        if not content:
            return tool_error("content is required")
        record_type = str(args.get("record_type") or "memory").strip().lower()
        if record_type not in {"memory", "profile"}:
            return tool_error("record_type must be either 'memory' or 'profile'")
        category = str(args.get("category") or ("preference" if record_type == "profile" else "fact")).strip() or "fact"
        importance = args.get("importance", 0.8 if record_type == "profile" else 0.7)
        try:
            stored = self._store_record(
                content=content,
                record_type=record_type,
                category=category,
                source="tool",
                importance=float(importance),
                tags=args.get("tags"),
            )
        except Exception as exc:
            return tool_error(f"Failed to store memory: {exc}")
        return json.dumps(
            {
                "stored": True,
                "id": stored["id"],
                "created": stored.get("created", False),
                "record_type": stored["record_type"],
                "category": stored["category"],
            }
        )

    def _tool_search(self, args: dict) -> str:
        if not self._store:
            return tool_error("LanceDB store is not initialized")
        query = str(args.get("query") or "").strip()
        if not query:
            return tool_error("query is required")
        try:
            limit = max(1, min(self._max_tool_results, int(args.get("limit", self._max_tool_results))))
        except Exception:
            limit = self._max_tool_results
        scope = str(args.get("scope") or "all").strip().lower()
        results = self._store.search(
            query,
            limit=limit,
            record_types=_scope_to_types(scope),
            user_id=self._user_id,
            agent_identity=self._agent_identity,
        )
        payload = [
            {
                "id": item["id"],
                "record_type": item["record_type"],
                "category": item["category"],
                "content": item["content"],
                "score": item["score"],
            }
            for item in results
        ]
        return json.dumps({"results": payload, "count": len(payload)})

    def _tool_forget(self, args: dict) -> str:
        if not self._store:
            return tool_error("LanceDB store is not initialized")
        direct_id = str(args.get("id") or "").strip()
        if direct_id:
            deleted = self._store.delete_many([direct_id])
            return json.dumps({"deleted": deleted, "ids": [direct_id] if deleted else []})

        query = str(args.get("query") or "").strip()
        if not query:
            return tool_error("Provide either id or query")
        try:
            limit = max(1, min(10, int(args.get("limit", 3))))
        except Exception:
            limit = 3
        scope = str(args.get("scope") or "all").strip().lower()
        matches = self._store.search(
            query,
            limit=limit,
            record_types=_scope_to_types(scope),
            user_id=self._user_id,
            agent_identity=self._agent_identity,
        )
        ids = [item["id"] for item in matches]
        deleted = self._store.delete_many(ids)
        return json.dumps({"deleted": deleted, "ids": ids})

    def _tool_profile(self, args: dict) -> str:
        if not self._store:
            return tool_error("LanceDB store is not initialized")
        try:
            limit = max(1, min(self._max_tool_results, int(args.get("limit", self._max_tool_results))))
        except Exception:
            limit = self._max_tool_results
        rows = self._store.list_profile(
            limit=limit,
            user_id=self._user_id,
            agent_identity=self._agent_identity,
        )
        profile = [
            {
                "id": row["id"],
                "record_type": row["record_type"],
                "category": row["category"],
                "content": row["content"],
                "importance": row["importance"],
                "updated_at": row["updated_at"],
            }
            for row in rows
        ]
        return json.dumps({"profile": profile, "count": len(profile)})

    def handle_tool_call(self, tool_name: str, args: Dict[str, Any], **kwargs) -> str:
        if tool_name == "lancedb_store":
            return self._tool_store(args)
        if tool_name == "lancedb_search":
            return self._tool_search(args)
        if tool_name == "lancedb_forget":
            return self._tool_forget(args)
        if tool_name == "lancedb_profile":
            return self._tool_profile(args)
        return tool_error(f"Unknown tool: {tool_name}")

    def shutdown(self) -> None:
        for thread in (self._prefetch_thread, self._sync_thread):
            if thread and thread.is_alive():
                thread.join(timeout=5.0)
        if self._store:
            self._store.close()
            self._store = None


def register(ctx) -> None:
    ctx.register_memory_provider(LanceDBMemoryProvider())
