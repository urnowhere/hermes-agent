"""Local LanceDB-backed memory store with a SQLite metadata index."""

from __future__ import annotations

import hashlib
import logging
import re
import sqlite3
import threading
from pathlib import Path
from typing import Any, Dict, Iterable, List, Sequence

logger = logging.getLogger(__name__)

_TOKEN_RE = re.compile(r"[A-Za-z0-9_]{2,}")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS records (
    row_id INTEGER PRIMARY KEY AUTOINCREMENT,
    id TEXT NOT NULL UNIQUE,
    content_hash TEXT NOT NULL UNIQUE,
    content TEXT NOT NULL,
    record_type TEXT NOT NULL,
    category TEXT NOT NULL,
    source TEXT NOT NULL,
    user_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    agent_identity TEXT NOT NULL,
    workspace TEXT NOT NULL,
    importance REAL NOT NULL DEFAULT 0.5,
    tags TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_records_type_updated
    ON records(record_type, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_records_user_identity
    ON records(user_id, agent_identity);
CREATE INDEX IF NOT EXISTS idx_records_session
    ON records(session_id);

CREATE VIRTUAL TABLE IF NOT EXISTS records_fts
USING fts5(content, tags, content='records', content_rowid='row_id');

CREATE TRIGGER IF NOT EXISTS records_ai AFTER INSERT ON records BEGIN
    INSERT INTO records_fts(rowid, content, tags)
    VALUES (new.row_id, new.content, new.tags);
END;

CREATE TRIGGER IF NOT EXISTS records_ad AFTER DELETE ON records BEGIN
    INSERT INTO records_fts(records_fts, rowid, content, tags)
    VALUES ('delete', old.row_id, old.content, old.tags);
END;

CREATE TRIGGER IF NOT EXISTS records_au AFTER UPDATE ON records BEGIN
    INSERT INTO records_fts(records_fts, rowid, content, tags)
    VALUES ('delete', old.row_id, old.content, old.tags);
    INSERT INTO records_fts(rowid, content, tags)
    VALUES (new.row_id, new.content, new.tags);
END;
"""


def content_hash(text: str) -> str:
    return hashlib.sha256(text.strip().encode("utf-8")).hexdigest()


def normalize_tags(tags: str | Sequence[str] | None) -> str:
    if tags is None:
        return ""
    if isinstance(tags, str):
        return ", ".join(part.strip() for part in tags.split(",") if part.strip())
    return ", ".join(str(part).strip() for part in tags if str(part).strip())


def _fts_query(text: str) -> str:
    tokens = sorted(set(token.lower() for token in _TOKEN_RE.findall(text or "")))
    return " OR ".join(tokens)


def _clamp_importance(value: Any, default: float = 0.5) -> float:
    try:
        parsed = float(value)
    except Exception:
        parsed = default
    return max(0.0, min(1.0, parsed))


def _sql_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


class SQLiteMetadataIndex:
    """Canonical metadata store used for dedupe, profile views, and lexical fallback."""

    def __init__(self, path: str | Path):
        self.path = Path(path).expanduser()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False, timeout=10.0)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def get_by_hash(self, hashed: str) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM records WHERE content_hash = ?",
                (hashed,),
            ).fetchone()
            return dict(row) if row else None

    def upsert(self, record: Dict[str, Any]) -> dict:
        with self._lock:
            existing = self.get_by_hash(record["content_hash"])
            if existing:
                merged = dict(existing)
                merged["updated_at"] = record["updated_at"]
                merged["source"] = record["source"]
                merged["importance"] = max(
                    _clamp_importance(existing.get("importance"), 0.5),
                    _clamp_importance(record.get("importance"), 0.5),
                )
                if record.get("tags"):
                    merged["tags"] = normalize_tags(
                        [existing.get("tags", ""), record.get("tags", "")]
                    )
                self._conn.execute(
                    """
                    UPDATE records
                    SET updated_at = ?, source = ?, importance = ?, tags = ?
                    WHERE id = ?
                    """,
                    (
                        merged["updated_at"],
                        merged["source"],
                        merged["importance"],
                        merged["tags"],
                        existing["id"],
                    ),
                )
                self._conn.commit()
                merged["created"] = False
                return merged

            payload = (
                record["id"],
                record["content_hash"],
                record["content"],
                record["record_type"],
                record["category"],
                record["source"],
                record["user_id"],
                record["session_id"],
                record["agent_identity"],
                record["workspace"],
                _clamp_importance(record.get("importance"), 0.5),
                normalize_tags(record.get("tags")),
                record["created_at"],
                record["updated_at"],
            )
            self._conn.execute(
                """
                INSERT INTO records (
                    id, content_hash, content, record_type, category, source,
                    user_id, session_id, agent_identity, workspace,
                    importance, tags, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                payload,
            )
            self._conn.commit()
            result = dict(record)
            result["importance"] = _clamp_importance(record.get("importance"), 0.5)
            result["tags"] = normalize_tags(record.get("tags"))
            result["created"] = True
            return result

    def fetch_many(self, ids: Iterable[str]) -> list[dict]:
        id_list = [item for item in ids if item]
        if not id_list:
            return []
        placeholders = ",".join("?" for _ in id_list)
        with self._lock:
            rows = self._conn.execute(
                f"SELECT * FROM records WHERE id IN ({placeholders})",
                id_list,
            ).fetchall()
            return [dict(row) for row in rows]

    def delete_many(self, ids: Iterable[str]) -> None:
        id_list = [item for item in ids if item]
        if not id_list:
            return
        placeholders = ",".join("?" for _ in id_list)
        with self._lock:
            self._conn.execute(f"DELETE FROM records WHERE id IN ({placeholders})", id_list)
            self._conn.commit()

    def lexical_search(
        self,
        query: str,
        *,
        limit: int,
        record_types: Sequence[str] | None = None,
        user_id: str = "",
        agent_identity: str = "",
    ) -> list[dict]:
        fts_query = _fts_query(query)
        if not fts_query:
            return []

        sql = """
            SELECT r.*, bm25(records_fts) AS rank
            FROM records_fts
            JOIN records r ON r.row_id = records_fts.rowid
            WHERE records_fts MATCH ?
        """
        params: list[Any] = [fts_query]

        if user_id:
            sql += " AND r.user_id = ?"
            params.append(user_id)
        if agent_identity:
            sql += " AND r.agent_identity = ?"
            params.append(agent_identity)
        if record_types:
            placeholders = ",".join("?" for _ in record_types)
            sql += f" AND r.record_type IN ({placeholders})"
            params.extend(record_types)

        sql += " ORDER BY rank LIMIT ?"
        params.append(limit)

        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()

        results = []
        for row in rows:
            record = dict(row)
            rank = record.pop("rank", 0.0)
            try:
                lexical_score = 1.0 / (1.0 + max(float(rank), 0.0))
            except Exception:
                lexical_score = 0.0
            record["lexical_score"] = lexical_score
            results.append(record)
        return results

    def list_profile(
        self,
        *,
        limit: int,
        user_id: str = "",
        agent_identity: str = "",
    ) -> list[dict]:
        sql = """
            SELECT *
            FROM records
            WHERE record_type IN ('profile', 'memory')
        """
        params: list[Any] = []
        if user_id:
            sql += " AND user_id = ?"
            params.append(user_id)
        if agent_identity:
            sql += " AND agent_identity = ?"
            params.append(agent_identity)
        sql += " ORDER BY importance DESC, updated_at DESC LIMIT ?"
        params.append(limit)
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
            return [dict(row) for row in rows]

    def counts(self, *, user_id: str = "", agent_identity: str = "") -> dict[str, int]:
        sql = "SELECT record_type, COUNT(*) AS count FROM records WHERE 1=1"
        params: list[Any] = []
        if user_id:
            sql += " AND user_id = ?"
            params.append(user_id)
        if agent_identity:
            sql += " AND agent_identity = ?"
            params.append(agent_identity)
        sql += " GROUP BY record_type"
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        counts = {"profile": 0, "memory": 0, "episode": 0}
        for row in rows:
            counts[str(row["record_type"])] = int(row["count"])
        return counts


class LanceMemoryStore:
    """Vector store backed by LanceDB plus a SQLite metadata index."""

    def __init__(self, db_path: str | Path, table_name: str, embedder):
        self.db_path = Path(db_path).expanduser()
        self.db_path.mkdir(parents=True, exist_ok=True)
        self.table_name = table_name
        self.embedder = embedder
        self.index = SQLiteMetadataIndex(self.db_path / "metadata.sqlite3")
        self._lock = threading.RLock()
        self._db = None
        self._table = None

    def close(self) -> None:
        self.index.close()

    def _connect(self):
        if self._table is not None:
            return self._table

        with self._lock:
            if self._table is not None:
                return self._table

            import lancedb
            import pyarrow as pa

            self._db = lancedb.connect(str(self.db_path))
            try:
                table_names = set(self._db.table_names())
            except Exception:
                table_names = set()

            if self.table_name in table_names:
                self._table = self._db.open_table(self.table_name)
                return self._table

            dimension = int(self.embedder.dimension)
            schema = pa.schema([
                pa.field("id", pa.string()),
                pa.field("content", pa.string()),
                pa.field("vector", pa.list_(pa.float32(), dimension)),
                pa.field("record_type", pa.string()),
                pa.field("category", pa.string()),
                pa.field("source", pa.string()),
                pa.field("user_id", pa.string()),
                pa.field("session_id", pa.string()),
                pa.field("agent_identity", pa.string()),
                pa.field("workspace", pa.string()),
                pa.field("importance", pa.float32()),
                pa.field("tags", pa.string()),
                pa.field("created_at", pa.string()),
                pa.field("updated_at", pa.string()),
                pa.field("content_hash", pa.string()),
            ])
            self._table = self._db.create_table(self.table_name, schema=schema)
            return self._table

    def upsert(self, record: Dict[str, Any]) -> dict:
        existing = self.index.get_by_hash(record["content_hash"])
        if existing:
            return self.index.upsert(record)

        vector = self.embedder.embed_texts([record["content"]])[0]
        row = {
            "id": record["id"],
            "content": record["content"],
            "vector": vector,
            "record_type": record["record_type"],
            "category": record["category"],
            "source": record["source"],
            "user_id": record["user_id"],
            "session_id": record["session_id"],
            "agent_identity": record["agent_identity"],
            "workspace": record["workspace"],
            "importance": float(record["importance"]),
            "tags": normalize_tags(record.get("tags")),
            "created_at": record["created_at"],
            "updated_at": record["updated_at"],
            "content_hash": record["content_hash"],
        }
        with self._lock:
            table = self._connect()
            table.add([row])
        return self.index.upsert(record)

    def delete_many(self, ids: Sequence[str]) -> int:
        valid_ids = [item for item in ids if item]
        if not valid_ids:
            return 0
        self.index.delete_many(valid_ids)
        condition = " OR ".join(f"id = {_sql_quote(item)}" for item in valid_ids)
        try:
            with self._lock:
                table = self._connect()
                table.delete(condition)
        except Exception:
            logger.debug("LanceDB delete failed for condition: %s", condition, exc_info=True)
        return len(valid_ids)

    def search(
        self,
        query: str,
        *,
        limit: int,
        record_types: Sequence[str] | None = None,
        user_id: str = "",
        agent_identity: str = "",
    ) -> list[dict]:
        if not query.strip():
            return []

        desired = max(limit * 4, 8)
        combined: dict[str, dict[str, Any]] = {}

        try:
            vector = self.embedder.embed_texts([query])[0]
            with self._lock:
                table = self._connect()
                rows = table.search(vector).limit(desired).to_list()
        except Exception:
            rows = []
            logger.debug("LanceDB vector search failed", exc_info=True)

        vector_candidates = []
        for index, row in enumerate(rows):
            record_id = str(row.get("id", "")).strip()
            if not record_id:
                continue
            vector_score = self._score_vector_row(row, index, len(rows))
            combined.setdefault(record_id, {})["vector_score"] = vector_score
            vector_candidates.append(record_id)

        lexical_rows = self.index.lexical_search(
            query,
            limit=desired,
            record_types=record_types,
            user_id=user_id,
            agent_identity=agent_identity,
        )
        for row in lexical_rows:
            combined.setdefault(row["id"], {})["lexical_score"] = row.get("lexical_score", 0.0)

        metadata_rows = self.index.fetch_many(combined.keys())
        results = []
        for row in metadata_rows:
            if record_types and row["record_type"] not in record_types:
                continue
            if user_id and row["user_id"] != user_id:
                continue
            if agent_identity and row["agent_identity"] != agent_identity:
                continue

            scores = combined.get(row["id"], {})
            vector_score = float(scores.get("vector_score", 0.0))
            lexical_score = float(scores.get("lexical_score", 0.0))
            importance = _clamp_importance(row.get("importance"), 0.5)
            blended = (0.7 * vector_score) + (0.3 * lexical_score) + (0.08 * importance)
            item = dict(row)
            item["score"] = round(blended, 4)
            results.append(item)

        results.sort(key=lambda item: (item["score"], item["importance"], item["updated_at"]), reverse=True)
        return results[:limit]

    @staticmethod
    def _score_vector_row(row: dict, index: int, total: int) -> float:
        for key in ("_score", "score", "similarity"):
            if key in row:
                try:
                    value = float(row[key])
                    if value > 1.0:
                        return 1.0 / (1.0 + value)
                    return max(0.0, min(1.0, value))
                except Exception:
                    pass
        for key in ("_distance", "distance"):
            if key in row:
                try:
                    return 1.0 / (1.0 + max(float(row[key]), 0.0))
                except Exception:
                    pass
        if total <= 0:
            return 0.0
        return max(0.0, 1.0 - (index / float(total + 1)))

    def list_profile(self, *, limit: int, user_id: str = "", agent_identity: str = "") -> list[dict]:
        return self.index.list_profile(limit=limit, user_id=user_id, agent_identity=agent_identity)

    def counts(self, *, user_id: str = "", agent_identity: str = "") -> dict[str, int]:
        return self.index.counts(user_id=user_id, agent_identity=agent_identity)
