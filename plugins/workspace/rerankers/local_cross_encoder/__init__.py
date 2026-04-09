from __future__ import annotations

import logging
import threading
from typing import Any

from agent.workspace_contracts import WorkspaceRerankerPlugin
from agent.workspace_types import PluginHealth, WorkspaceHit, WorkspacePluginContext

logger = logging.getLogger(__name__)


class LocalCrossEncoderReranker(WorkspaceRerankerPlugin):

    _MODEL_CACHE: dict[tuple[str, str], Any] = {}
    _MODEL_CACHE_LOCK = threading.Lock()

    @property
    def name(self) -> str:
        return "local-cross-encoder"

    def is_available(self, config: dict[str, Any], context: WorkspacePluginContext) -> bool:
        try:
            import sentence_transformers  # noqa: F401
            import torch  # noqa: F401
            return True
        except ImportError:
            return False

    def healthcheck(self, config: dict[str, Any], context: WorkspacePluginContext) -> PluginHealth:
        if not self.is_available(config, context):
            return PluginHealth(healthy=False, message="sentence_transformers or torch not installed")
        return PluginHealth(healthy=True)

    def signature(self, config: dict[str, Any]) -> str:
        model = str(config.get("model", "bge-reranker-v2-m3") or "bge-reranker-v2-m3")
        return f"local-cross-encoder:{model}"

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
        model = self._get_model(config)
        if model is None:
            return list(candidates)
        pairs = [(query, hit.content) for hit in candidates]
        try:
            scores = model.predict(pairs)
        except Exception:
            return list(candidates)
        if hasattr(scores, "tolist"):
            scores = scores.tolist()
        scored: list[WorkspaceHit] = []
        for candidate, score in zip(candidates, scores):
            hit = WorkspaceHit(
                chunk_id=candidate.chunk_id,
                relative_path=candidate.relative_path,
                content=candidate.content,
                metadata=candidate.metadata,
                sparse_score=candidate.sparse_score,
                dense_score=candidate.dense_score,
                fusion_score=candidate.fusion_score,
                rerank_score=float(score),
            )
            scored.append(hit)
        scored.sort(
            key=lambda h: (h.rerank_score or 0.0, h.fusion_score or 0.0, h.dense_score or 0.0),
            reverse=True,
        )
        return scored

    def _get_model(self, config: dict[str, Any]) -> Any:
        try:
            import torch
            from sentence_transformers import CrossEncoder
        except ImportError:
            return None
        if torch.cuda.is_available():
            device = "cuda"
        elif getattr(getattr(torch, "backends", None), "mps", None) and torch.backends.mps.is_available():
            device = "mps"
        else:
            device = "cpu"
        model_name = str(config.get("model", "bge-reranker-v2-m3") or "bge-reranker-v2-m3")
        cache_key = (model_name, device)
        with self._MODEL_CACHE_LOCK:
            cached = self._MODEL_CACHE.get(cache_key)
            if cached is not None:
                return cached
            try:
                model = CrossEncoder(model_name, device=device)
            except TypeError:
                model = CrossEncoder(model_name)
            except Exception:
                return None
            self._MODEL_CACHE[cache_key] = model
            return model


def register(ctx) -> None:
    ctx.register_workspace_reranker(LocalCrossEncoderReranker())
