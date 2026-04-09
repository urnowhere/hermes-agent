from __future__ import annotations

import logging
from typing import Any

from agent.workspace_contracts import WorkspaceEmbedderPlugin, WorkspaceRetrieverPlugin
from agent.workspace_types import WorkspaceHit, WorkspaceIndexSession, WorkspacePluginContext

logger = logging.getLogger(__name__)

_RRF_K = 60


class BuiltinHybridRRFRetriever(WorkspaceRetrieverPlugin):

    @property
    def name(self) -> str:
        return "builtin-hybrid-rrf"

    def is_available(self, config: dict[str, Any], context: WorkspacePluginContext) -> bool:
        return True

    def signature(self, config: dict[str, Any]) -> str:
        sparse = int(config.get("sparse_top_k", 40) or 40)
        dense = int(config.get("dense_top_k", 40) or 40)
        fused = int(config.get("fused_top_k", 30) or 30)
        return f"hybrid-rrf:{sparse}:{dense}:{fused}"

    def retrieve(
        self,
        query: str,
        *,
        index_session: WorkspaceIndexSession,
        embedder: WorkspaceEmbedderPlugin,
        config: dict[str, Any],
        context: WorkspacePluginContext,
    ) -> list[WorkspaceHit]:
        sparse_top_k = int(config.get("sparse_top_k", 40) or 40)
        dense_top_k = int(config.get("dense_top_k", 40) or 40)
        fused_top_k = int(config.get("fused_top_k", 30) or 30)

        plugin_configs = context.runtime_metadata.get("workspace_plugin_configs", {})
        embedder_cfg = plugin_configs.get("embedders", {}) if isinstance(plugin_configs, dict) else {}
        query_embedding = embedder.embed_query(
            query, config=embedder_cfg, context=context
        )

        # Sparse (BM25) search
        sparse_hits = index_session.sparse_search(query, sparse_top_k)

        # Dense (vector) search
        dense_hits = index_session.dense_search(query_embedding, dense_top_k)

        # Reciprocal Rank Fusion
        merged: dict[str, WorkspaceHit] = {}

        for rank, hit in enumerate(sparse_hits, start=1):
            if hit.chunk_id not in merged:
                merged[hit.chunk_id] = WorkspaceHit(
                    chunk_id=hit.chunk_id,
                    relative_path=hit.relative_path,
                    content=hit.content,
                    metadata=hit.metadata,
                    sparse_score=hit.sparse_score,
                    dense_score=0.0,
                    fusion_score=0.0,
                )
            entry = merged[hit.chunk_id]
            entry.sparse_score = hit.sparse_score
            entry.fusion_score = (entry.fusion_score or 0.0) + 1.0 / (_RRF_K + rank)

        for rank, hit in enumerate(dense_hits, start=1):
            if hit.chunk_id not in merged:
                merged[hit.chunk_id] = WorkspaceHit(
                    chunk_id=hit.chunk_id,
                    relative_path=hit.relative_path,
                    content=hit.content,
                    metadata=hit.metadata,
                    sparse_score=None,
                    dense_score=hit.dense_score,
                    fusion_score=0.0,
                )
            entry = merged[hit.chunk_id]
            entry.dense_score = hit.dense_score
            entry.fusion_score = (entry.fusion_score or 0.0) + 1.0 / (_RRF_K + rank)

        results = sorted(
            merged.values(),
            key=lambda h: (h.fusion_score or 0.0, h.dense_score or 0.0),
            reverse=True,
        )
        return results[:fused_top_k]


def register(ctx) -> None:
    ctx.register_workspace_retriever(BuiltinHybridRRFRetriever())
