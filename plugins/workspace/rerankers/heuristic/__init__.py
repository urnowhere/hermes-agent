from __future__ import annotations

import re
from typing import Any

from agent.workspace_contracts import WorkspaceRerankerPlugin
from agent.workspace_types import WorkspaceHit, WorkspacePluginContext


class HeuristicReranker(WorkspaceRerankerPlugin):

    @property
    def name(self) -> str:
        return "heuristic"

    def is_available(self, config: dict[str, Any], context: WorkspacePluginContext) -> bool:
        return True

    def signature(self, config: dict[str, Any]) -> str:
        return "heuristic"

    def rerank(
        self,
        query: str,
        candidates: list[WorkspaceHit],
        *,
        config: dict[str, Any],
        context: WorkspacePluginContext,
    ) -> list[WorkspaceHit]:
        if not candidates:
            return []
        query_terms = set(re.findall(r"[A-Za-z0-9_./:-]+", query.lower()))
        scored: list[WorkspaceHit] = []
        for candidate in candidates:
            content_terms = set(re.findall(r"[A-Za-z0-9_./:-]+", candidate.content.lower()))
            overlap = len(query_terms & content_terms)
            lexical = overlap / max(1, len(query_terms))
            hit = WorkspaceHit(
                chunk_id=candidate.chunk_id,
                relative_path=candidate.relative_path,
                content=candidate.content,
                metadata=candidate.metadata,
                sparse_score=candidate.sparse_score,
                dense_score=candidate.dense_score,
                fusion_score=candidate.fusion_score,
                rerank_score=lexical + (candidate.dense_score or 0.0) * 0.1,
            )
            scored.append(hit)
        scored.sort(
            key=lambda h: (h.rerank_score or 0.0, h.fusion_score or 0.0, h.dense_score or 0.0),
            reverse=True,
        )
        return scored


def register(ctx) -> None:
    ctx.register_workspace_reranker(HeuristicReranker())
