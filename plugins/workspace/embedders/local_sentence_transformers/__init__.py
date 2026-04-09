from __future__ import annotations

import logging
import threading
from typing import Any

from agent.workspace_contracts import WorkspaceEmbedderPlugin
from agent.workspace_types import PluginHealth, WorkspacePluginContext

logger = logging.getLogger(__name__)


class LocalSentenceTransformersEmbedder(WorkspaceEmbedderPlugin):

    _MODEL_CACHE: dict[tuple[str, str], Any] = {}
    _MODEL_CACHE_LOCK = threading.Lock()

    @property
    def name(self) -> str:
        return "local-sentence-transformers"

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
        model = str(config.get("model", "google/embeddinggemma-300m") or "google/embeddinggemma-300m")
        dims = int(config.get("dimensions", 768) or 768)
        return f"local-st:{model}:{dims}"

    def dimensions(self, config: dict[str, Any]) -> int:
        return int(config.get("dimensions", 768) or 768)

    def embed_documents(
        self, texts: list[str], *, config: dict[str, Any], context: WorkspacePluginContext
    ) -> list[list[float]]:
        model = self._get_model(config)
        if model is None:
            raise RuntimeError("SentenceTransformer model not available")
        kwargs = self._encode_kwargs(config)
        try:
            if hasattr(model, "encode_document"):
                return self._vectors_to_lists(model.encode_document(texts, **kwargs))
            return self._vectors_to_lists(model.encode(texts, prompt_name="Retrieval-document", **kwargs))
        except Exception as e:
            raise RuntimeError(f"Embedding failed: {e}") from e

    def embed_query(
        self, text: str, *, config: dict[str, Any], context: WorkspacePluginContext
    ) -> list[float]:
        model = self._get_model(config)
        if model is None:
            raise RuntimeError("SentenceTransformer model not available")
        kwargs = self._encode_kwargs(config)
        try:
            if hasattr(model, "encode_query"):
                return self._vector_to_list(model.encode_query(text, **kwargs))
            return self._vector_to_list(model.encode(text, prompt_name="Retrieval-query", **kwargs))
        except Exception as e:
            raise RuntimeError(f"Query embedding failed: {e}") from e

    def _get_model(self, config: dict[str, Any]) -> Any:
        try:
            import torch
            from sentence_transformers import SentenceTransformer
        except ImportError:
            return None

        if torch.cuda.is_available():
            device = "cuda"
        elif getattr(getattr(torch, "backends", None), "mps", None) and torch.backends.mps.is_available():
            device = "mps"
        else:
            device = "cpu"

        model_name = str(config.get("model", "google/embeddinggemma-300m") or "google/embeddinggemma-300m")
        cache_key = (model_name, device)
        with self._MODEL_CACHE_LOCK:
            cached = self._MODEL_CACHE.get(cache_key)
            if cached is not None:
                return cached
            try:
                model = SentenceTransformer(model_name, device=device)
            except TypeError:
                model = SentenceTransformer(model_name)
                if hasattr(model, "to"):
                    model = model.to(device)
            except Exception:
                return None
            self._MODEL_CACHE[cache_key] = model
            return model

    def _encode_kwargs(self, config: dict[str, Any]) -> dict[str, Any]:
        kwargs: dict[str, Any] = {"normalize_embeddings": True}
        dims = self.dimensions(config)
        if 0 < dims < 768:
            kwargs["truncate_dim"] = dims
        return kwargs

    @staticmethod
    def _vector_to_list(vector: Any) -> list[float]:
        if hasattr(vector, "tolist"):
            vector = vector.tolist()
        return [float(v) for v in vector]

    def _vectors_to_lists(self, vectors: Any) -> list[list[float]]:
        if hasattr(vectors, "tolist"):
            vectors = vectors.tolist()
        if not vectors:
            return []
        first = vectors[0]
        if isinstance(first, (int, float)):
            return [self._vector_to_list(vectors)]
        return [self._vector_to_list(v) for v in vectors]


def register(ctx) -> None:
    ctx.register_workspace_embedder(LocalSentenceTransformersEmbedder())
