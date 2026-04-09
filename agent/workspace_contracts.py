"""Category-specific plugin contracts for the workspace pipeline.

Each category has its own ABC defining the contract that plugins must
implement.  Built-in and external plugins alike conform to the same
interface.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from agent.workspace_types import (
    PluginHealth,
    WorkspaceChunk,
    WorkspaceDocument,
    WorkspaceHit,
    WorkspaceIndexSession,
    WorkspacePluginContext,
)

# ---------------------------------------------------------------------------
# Shared base
# ---------------------------------------------------------------------------


class WorkspacePlugin(ABC):
    """Minimal base shared by all workspace category plugins."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique plugin name (e.g. 'builtin-text')."""

    def is_available(self, config: dict[str, Any], context: WorkspacePluginContext) -> bool:
        """Return True if this plugin can run in the current environment."""
        return True

    def warm_up(self, config: dict[str, Any], context: WorkspacePluginContext) -> None:
        """Optional eager initialization (model loading, connection pooling)."""

    def healthcheck(self, config: dict[str, Any], context: WorkspacePluginContext) -> PluginHealth:
        """Return health status.  Default: healthy."""
        return PluginHealth(healthy=True)

    def signature(self, config: dict[str, Any]) -> str:
        """Return a string that changes when plugin behaviour changes.

        Used for index invalidation.  Default: plugin name.
        """
        return self.name

    def config_schema(self) -> list[dict[str, Any]]:
        """Return JSON-schema-style config field descriptors."""
        return []


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


class WorkspaceParserPlugin(WorkspacePlugin):
    """Reads files and normalizes them into WorkspaceDocuments."""

    @abstractmethod
    def supported_suffixes(self) -> set[str]:
        """File extensions this parser handles (e.g. {'.md', '.txt'})."""

    @abstractmethod
    def parse(
        self,
        path: Path,
        *,
        config: dict[str, Any],
        context: WorkspacePluginContext,
    ) -> WorkspaceDocument | None:
        """Parse a file into a WorkspaceDocument, or None if unsupported."""


# ---------------------------------------------------------------------------
# Chunker
# ---------------------------------------------------------------------------


class WorkspaceChunkerPlugin(WorkspacePlugin):
    """Splits normalized documents into retrieval chunks."""

    @abstractmethod
    def chunk(
        self,
        document: WorkspaceDocument,
        *,
        path: Path,
        config: dict[str, Any],
        context: WorkspacePluginContext,
    ) -> list[WorkspaceChunk]:
        """Chunk a parsed document."""


# ---------------------------------------------------------------------------
# Embedder
# ---------------------------------------------------------------------------


class WorkspaceEmbedderPlugin(WorkspacePlugin):
    """Generates document and query embeddings."""

    @abstractmethod
    def embed_documents(
        self,
        texts: list[str],
        *,
        config: dict[str, Any],
        context: WorkspacePluginContext,
    ) -> list[list[float]]:
        """Embed a batch of document texts."""

    @abstractmethod
    def embed_query(
        self,
        text: str,
        *,
        config: dict[str, Any],
        context: WorkspacePluginContext,
    ) -> list[float]:
        """Embed a single query text."""

    @abstractmethod
    def dimensions(self, config: dict[str, Any]) -> int:
        """Return the embedding dimensionality."""


# ---------------------------------------------------------------------------
# Reranker
# ---------------------------------------------------------------------------


class WorkspaceRerankerPlugin(WorkspacePlugin):
    """Reorders retrieval candidates after initial retrieval."""

    @abstractmethod
    def rerank(
        self,
        query: str,
        candidates: list[WorkspaceHit],
        *,
        config: dict[str, Any],
        context: WorkspacePluginContext,
    ) -> list[WorkspaceHit]:
        """Rerank candidates, returning them in new order with rerank_score set."""


# ---------------------------------------------------------------------------
# Retriever
# ---------------------------------------------------------------------------


class WorkspaceRetrieverPlugin(WorkspacePlugin):
    """Runs retrieval strategy over the active index store."""

    @abstractmethod
    def retrieve(
        self,
        query: str,
        *,
        index_session: WorkspaceIndexSession,
        embedder: WorkspaceEmbedderPlugin,
        config: dict[str, Any],
        context: WorkspacePluginContext,
    ) -> list[WorkspaceHit]:
        """Retrieve candidates from the index store."""


# ---------------------------------------------------------------------------
# Index store
# ---------------------------------------------------------------------------


class WorkspaceIndexStorePlugin(WorkspacePlugin):
    """Persists files, chunks, embeddings, and search indexes."""

    @abstractmethod
    def open(
        self,
        *,
        indexes_dir: Path,
        config: dict[str, Any],
        context: WorkspacePluginContext,
    ) -> WorkspaceIndexSession:
        """Open an index session."""
