from __future__ import annotations

import json
import logging
import re
import sqlite3
from pathlib import Path
from typing import Any

from agent.workspace_contracts import WorkspaceIndexStorePlugin
from agent.workspace_types import (
    PluginHealth,
    WorkspaceChunk,
    WorkspaceHit,
    WorkspaceIndexSession,
    WorkspacePluginContext,
)

logger = logging.getLogger(__name__)


class SqliteIndexSession(WorkspaceIndexSession):
    """SQLite-backed index session with FTS5 and optional sqlite-vec."""

    _brute_force_warned: bool = False

    def __init__(self, conn: sqlite3.Connection, sqlite_vec_module: Any | None, dimensions: int) -> None:
        self._conn = conn
        self._vec = sqlite_vec_module
        self._dimensions = dimensions

    def get_file_record(self, rel_path: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT rel_path, abs_path, content_hash, size_bytes, modified_at, "
            "indexed_at, chunk_count, config_signature FROM files WHERE rel_path = ?",
            (rel_path,),
        ).fetchone()
        if row is None:
            return None
        return dict(row)

    def upsert_file(
        self,
        rel_path: str,
        abs_path: str,
        content_hash: str,
        size_bytes: int,
        modified_at: float,
        chunk_count: int,
        config_signature: str,
    ) -> None:
        from datetime import datetime, timezone

        self._conn.execute(
            "INSERT OR REPLACE INTO files"
            "(rel_path, abs_path, content_hash, size_bytes, modified_at, indexed_at, chunk_count, config_signature) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                rel_path,
                abs_path,
                content_hash,
                size_bytes,
                modified_at,
                datetime.now(timezone.utc).isoformat(),
                chunk_count,
                config_signature,
            ),
        )

    def delete_file(self, rel_path: str) -> None:
        rowids = [
            row["rowid"]
            for row in self._conn.execute(
                "SELECT rowid FROM chunks WHERE rel_path = ?", (rel_path,)
            ).fetchall()
        ]
        if self._vec and rowids:
            for rowid in rowids:
                self._conn.execute("DELETE FROM chunks_vec WHERE rowid = ?", (rowid,))
        self._conn.execute("DELETE FROM chunks WHERE rel_path = ?", (rel_path,))
        self._conn.execute("DELETE FROM chunks_fts WHERE rel_path = ?", (rel_path,))
        self._conn.execute("DELETE FROM files WHERE rel_path = ?", (rel_path,))

    def insert_chunks(
        self,
        rel_path: str,
        chunks: list[WorkspaceChunk],
        embeddings: list[list[float]],
    ) -> None:
        for idx, chunk in enumerate(chunks):
            chunk_id = f"{rel_path}#chunk-{idx:04d}"
            embedding_vector = embeddings[idx] if idx < len(embeddings) else []
            embedding_json = json.dumps(embedding_vector)
            cursor = self._conn.execute(
                "INSERT INTO chunks(chunk_id, rel_path, chunk_index, content, token_estimate, embedding) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (chunk_id, rel_path, idx, chunk.content, chunk.token_estimate, embedding_json),
            )
            if self._vec and embedding_vector:
                serialized = (
                    self._vec.serialize_float32(embedding_vector)
                    if hasattr(self._vec, "serialize_float32")
                    else json.dumps(embedding_vector)
                )
                self._conn.execute(
                    "INSERT OR REPLACE INTO chunks_vec(rowid, embedding) VALUES (?, ?)",
                    (cursor.lastrowid, serialized),
                )
            self._conn.execute(
                "INSERT INTO chunks_fts(chunk_id, rel_path, content) VALUES (?, ?, ?)",
                (chunk_id, rel_path, chunk.content),
            )

    def sparse_search(self, query: str, limit: int) -> list[WorkspaceHit]:
        fts_query = _fts_terms(query)
        if not fts_query:
            return []
        try:
            rows = self._conn.execute(
                "SELECT chunk_id, rel_path, content, bm25(chunks_fts) AS bm25_score "
                "FROM chunks_fts WHERE chunks_fts MATCH ? ORDER BY bm25_score LIMIT ?",
                (fts_query, limit),
            ).fetchall()
        except sqlite3.OperationalError:
            return []
        return [
            WorkspaceHit(
                chunk_id=row["chunk_id"],
                relative_path=row["rel_path"],
                content=row["content"],
                sparse_score=float(row["bm25_score"]),
            )
            for row in rows
        ]

    def dense_search(self, query_embedding: list[float], limit: int) -> list[WorkspaceHit]:
        if self._vec:
            try:
                serialized = (
                    self._vec.serialize_float32(query_embedding)
                    if hasattr(self._vec, "serialize_float32")
                    else json.dumps(query_embedding)
                )
                rows = self._conn.execute(
                    "SELECT chunks.chunk_id, chunks.rel_path, chunks.content, chunks_vec.distance "
                    "FROM chunks_vec JOIN chunks ON chunks.rowid = chunks_vec.rowid "
                    "WHERE chunks_vec.embedding MATCH ? ORDER BY chunks_vec.distance LIMIT ?",
                    (serialized, limit),
                ).fetchall()
                return [
                    WorkspaceHit(
                        chunk_id=row["chunk_id"],
                        relative_path=row["rel_path"],
                        content=row["content"],
                        dense_score=1.0 / (1.0 + float(row["distance"])),
                    )
                    for row in rows
                ]
            except Exception:
                pass

        # Fallback: brute-force cosine similarity (no sqlite-vec extension)
        chunk_count_row = self._conn.execute("SELECT COUNT(*) AS cnt FROM chunks").fetchone()
        total_chunks = int(chunk_count_row["cnt"]) if chunk_count_row else 0
        if total_chunks > 0 and not SqliteIndexSession._brute_force_warned:
            SqliteIndexSession._brute_force_warned = True
            if total_chunks > 50_000:
                logger.warning(
                    "sqlite-vec not available — falling back to brute-force cosine similarity "
                    "over %d chunks. This WILL be slow. "
                    "Install sqlite-vec: pip install sqlite-vec", total_chunks
                )
            else:
                logger.warning(
                    "sqlite-vec not available — falling back to brute-force cosine similarity "
                    "over %d chunks. Install sqlite-vec for faster dense search.", total_chunks
                )
        chunk_rows = self._conn.execute(
            "SELECT chunk_id, rel_path, content, embedding FROM chunks"
        ).fetchall()
        scored: list[tuple[WorkspaceHit, float]] = []
        for row in chunk_rows:
            try:
                embedding = json.loads(row["embedding"])
            except Exception:
                embedding = []
            score = _cosine_similarity(query_embedding, embedding)
            scored.append((
                WorkspaceHit(
                    chunk_id=row["chunk_id"],
                    relative_path=row["rel_path"],
                    content=row["content"],
                    dense_score=score,
                ),
                score,
            ))
        scored.sort(key=lambda pair: pair[1], reverse=True)
        return [hit for hit, _ in scored[:limit]]

    def all_indexed_paths(self) -> set[str]:
        rows = self._conn.execute("SELECT rel_path FROM files").fetchall()
        return {row["rel_path"] for row in rows}

    def store_meta(self, key: str, value: str) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)",
            (key, value),
        )

    def read_meta(self, key: str) -> str | None:
        row = self._conn.execute(
            "SELECT value FROM meta WHERE key = ?", (key,)
        ).fetchone()
        return row["value"] if row else None

    def status(self) -> dict[str, Any]:
        row = self._conn.execute("SELECT COUNT(*) AS count FROM chunks").fetchone()
        chunk_count = int(row["count"]) if row else 0
        index_info: dict[str, Any] = {}
        meta_row = self._conn.execute(
            "SELECT value FROM meta WHERE key = 'index_info'"
        ).fetchone()
        if meta_row and meta_row["value"]:
            try:
                index_info = json.loads(meta_row["value"])
            except Exception:
                pass
        return {"chunk_count": chunk_count, "index_info": index_info}

    def commit(self) -> None:
        self._conn.commit()

    def close(self) -> None:
        try:
            self._conn.close()
        except Exception:
            pass


class BuiltinSqliteIndexStore(WorkspaceIndexStorePlugin):

    @property
    def name(self) -> str:
        return "builtin-sqlite"

    def is_available(self, config: dict[str, Any], context: WorkspacePluginContext) -> bool:
        return True

    def signature(self, config: dict[str, Any]) -> str:
        max_mb = int(config.get("max_file_mb", 10) or 10)
        vec = bool(config.get("enable_sqlite_vec", True))
        return f"builtin-sqlite:{max_mb}:{vec}"

    def open(
        self,
        *,
        indexes_dir: Path,
        config: dict[str, Any],
        context: WorkspacePluginContext,
    ) -> WorkspaceIndexSession:
        db_path = indexes_dir / "workspace.sqlite"
        db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(
            "CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        conn.execute(
            "CREATE TABLE IF NOT EXISTS files ("
            "rel_path TEXT PRIMARY KEY, abs_path TEXT NOT NULL, content_hash TEXT NOT NULL, "
            "size_bytes INTEGER NOT NULL, modified_at REAL NOT NULL, indexed_at TEXT NOT NULL, "
            "chunk_count INTEGER NOT NULL, config_signature TEXT NOT NULL)"
        )
        conn.execute(
            "CREATE TABLE IF NOT EXISTS chunks ("
            "chunk_id TEXT PRIMARY KEY, rel_path TEXT NOT NULL, chunk_index INTEGER NOT NULL, "
            "content TEXT NOT NULL, token_estimate INTEGER NOT NULL, embedding TEXT NOT NULL)"
        )
        conn.execute(
            "CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(chunk_id, rel_path, content)"
        )

        dimensions = 768  # default; caller should set from embedder
        vec_module = None
        if bool(config.get("enable_sqlite_vec", True)):
            vec_module = _maybe_enable_sqlite_vec(conn, dimensions)

        return SqliteIndexSession(conn, vec_module, dimensions)


def _maybe_enable_sqlite_vec(conn: sqlite3.Connection, dimensions: int) -> Any | None:
    try:
        import sqlite_vec
    except ImportError:
        return None
    try:
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.enable_load_extension(False)
        if dimensions:
            conn.execute(
                f"CREATE VIRTUAL TABLE IF NOT EXISTS chunks_vec USING vec0(embedding float[{int(dimensions)}])"
            )
        return sqlite_vec
    except Exception:
        return None


def _fts_terms(query: str) -> str:
    terms = [term for term in re.findall(r"[A-Za-z0-9_./:-]+", query.lower()) if len(term) >= 2]
    return " OR ".join(dict.fromkeys(terms))


def _cosine_similarity(vec_a: list[float], vec_b: list[float]) -> float:
    if not vec_a or not vec_b or len(vec_a) != len(vec_b):
        return 0.0
    return sum(a * b for a, b in zip(vec_a, vec_b))


def register(ctx) -> None:
    ctx.register_workspace_index_store(BuiltinSqliteIndexStore())
