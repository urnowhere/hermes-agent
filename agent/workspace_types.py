"""Canonical workspace data types.

All workspace category plugins communicate through these types.
They form the shared contract between parsers, chunkers, embedders,
rerankers, retrievers, and index stores.
"""

from __future__ import annotations

import logging

# Shared set of binary file extensions used by both the file iterator
# and the text parser plugin to skip non-text files.
BINARY_SUFFIXES: frozenset[str] = frozenset({
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".ico",
    ".zip", ".gz", ".tar", ".xz", ".7z", ".mp3", ".wav", ".ogg", ".mp4",
    ".mov", ".avi", ".sqlite", ".db", ".bin", ".exe", ".dll", ".so", ".dylib",
    ".woff", ".woff2", ".ttf", ".otf", ".doc",
})
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class WorkspaceBlock:
    """A structural block within a parsed document."""

    kind: str  # "heading", "code", "paragraph", "table", etc.
    text: str
    heading_path: tuple[str, ...] = ()
    page_start: int | None = None
    page_end: int | None = None
    ordinal: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class WorkspaceDocument:
    """Normalized output of a parser plugin."""

    source_path: str
    relative_path: str
    media_type: str
    text: str
    blocks: tuple[WorkspaceBlock, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class WorkspaceChunk:
    """A retrieval/index chunk produced by a chunker plugin."""

    chunk_id_hint: str | None = None
    content: str = ""
    token_estimate: int = 0
    kind: str = "text"
    section_title: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class WorkspaceQuery:
    """A retrieval query."""

    text: str
    filters: dict[str, Any] = field(default_factory=dict)
    limit: int = 8


@dataclass
class WorkspaceHit:
    """A retrieval result (mutable for score enrichment during pipeline)."""

    chunk_id: str
    relative_path: str
    content: str
    metadata: dict[str, Any] = field(default_factory=dict)
    sparse_score: float | None = None
    dense_score: float | None = None
    fusion_score: float | None = None
    rerank_score: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "chunk_id": self.chunk_id,
            "relative_path": self.relative_path,
            "content": self.content,
            "metadata": self.metadata,
            "sparse_score": self.sparse_score,
            "dense_score": self.dense_score,
            "fusion_score": self.fusion_score,
            "rerank_score": self.rerank_score,
        }


class WorkspaceIndexSession(ABC):
    """Abstract session returned by an index-store plugin."""

    @abstractmethod
    def get_file_record(self, rel_path: str) -> dict[str, Any] | None:
        """Return stored file metadata or None if not indexed."""

    @abstractmethod
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
        """Insert or update a file record."""

    @abstractmethod
    def delete_file(self, rel_path: str) -> None:
        """Delete a file and all its chunks."""

    @abstractmethod
    def insert_chunks(
        self,
        rel_path: str,
        chunks: list[WorkspaceChunk],
        embeddings: list[list[float]],
    ) -> None:
        """Insert chunks with their embeddings for a file."""

    @abstractmethod
    def sparse_search(self, query: str, limit: int) -> list[WorkspaceHit]:
        """BM25/FTS sparse search."""

    @abstractmethod
    def dense_search(
        self, query_embedding: list[float], limit: int
    ) -> list[WorkspaceHit]:
        """Vector similarity dense search."""

    @abstractmethod
    def all_indexed_paths(self) -> set[str]:
        """Return set of all currently indexed relative paths."""

    @abstractmethod
    def store_meta(self, key: str, value: str) -> None:
        """Store arbitrary metadata."""

    @abstractmethod
    def read_meta(self, key: str) -> str | None:
        """Read stored metadata."""

    @abstractmethod
    def status(self) -> dict[str, Any]:
        """Return index status info (chunk count, etc.)."""

    @abstractmethod
    def commit(self) -> None:
        """Commit pending changes."""

    @abstractmethod
    def close(self) -> None:
        """Close the session and release resources."""

    def __enter__(self) -> WorkspaceIndexSession:
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()


@dataclass(frozen=True)
class WorkspacePluginContext:
    """Runtime context passed to workspace plugins."""

    hermes_home: str
    workspace_root: str
    knowledgebase_root: str
    platform: str = "cli"
    session_id: str = ""
    logger: logging.Logger = field(default_factory=lambda: logging.getLogger("workspace"))
    resolved_plugins: dict[str, str] = field(default_factory=dict)
    runtime_metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PluginHealth:
    """Health check result for a workspace plugin."""

    healthy: bool
    message: str = ""
    details: dict[str, Any] = field(default_factory=dict)
