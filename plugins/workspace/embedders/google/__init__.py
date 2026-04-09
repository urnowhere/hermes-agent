from __future__ import annotations

import logging
import os
from typing import Any

from agent.workspace_contracts import WorkspaceEmbedderPlugin
from agent.workspace_types import PluginHealth, WorkspacePluginContext

logger = logging.getLogger(__name__)


class GoogleEmbedder(WorkspaceEmbedderPlugin):

    @property
    def name(self) -> str:
        return "google"

    def is_available(self, config: dict[str, Any], context: WorkspacePluginContext) -> bool:
        return bool(
            os.getenv("GEMINI_API_KEY", "").strip()
            or os.getenv("GOOGLE_API_KEY", "").strip()
        )

    def healthcheck(self, config: dict[str, Any], context: WorkspacePluginContext) -> PluginHealth:
        if not self.is_available(config, context):
            return PluginHealth(healthy=False, message="GEMINI_API_KEY or GOOGLE_API_KEY not set")
        return PluginHealth(healthy=True)

    def signature(self, config: dict[str, Any]) -> str:
        model = str(config.get("model", "text-embedding-004") or "text-embedding-004")
        dims = self.dimensions(config)
        return f"google:{model}:{dims}"

    def dimensions(self, config: dict[str, Any]) -> int:
        return int(config.get("dimensions", 768) or 768)

    def embed_documents(
        self, texts: list[str], *, config: dict[str, Any], context: WorkspacePluginContext
    ) -> list[list[float]]:
        return self._embed(texts, "RETRIEVAL_DOCUMENT", config)

    def embed_query(
        self, text: str, *, config: dict[str, Any], context: WorkspacePluginContext
    ) -> list[float]:
        results = self._embed([text], "RETRIEVAL_QUERY", config)
        return results[0]

    def _embed(self, texts: list[str], task_type: str, config: dict[str, Any]) -> list[list[float]]:
        import requests

        api_key = (
            os.getenv("GEMINI_API_KEY", "").strip()
            or os.getenv("GOOGLE_API_KEY", "").strip()
        )
        model = str(config.get("model", "text-embedding-004") or "text-embedding-004")
        dims = self.dimensions(config)

        # Use batchEmbedContents to embed all texts in a single API call
        # (Google supports up to 100 per batch).
        _BATCH_SIZE = 100
        results: list[list[float]] = []
        for batch_start in range(0, len(texts), _BATCH_SIZE):
            batch = texts[batch_start:batch_start + _BATCH_SIZE]
            response = requests.post(
                f"https://generativelanguage.googleapis.com/v1beta/models/{model}:batchEmbedContents",
                params={"key": api_key},
                json={
                    "requests": [
                        {
                            "model": f"models/{model}",
                            "content": {"parts": [{"text": text}]},
                            "taskType": task_type,
                            "outputDimensionality": dims,
                        }
                        for text in batch
                    ],
                },
                timeout=60,
            )
            response.raise_for_status()
            payload = response.json()
            embeddings = payload.get("embeddings", [])
            if len(embeddings) != len(batch):
                raise RuntimeError(
                    f"Expected {len(batch)} embeddings, got {len(embeddings)} from model {model}"
                )
            for emb in embeddings:
                values = emb.get("values")
                if not values:
                    raise RuntimeError(f"No embedding values in response for model {model}")
                results.append([float(v) for v in values])
        return results


def register(ctx) -> None:
    ctx.register_workspace_embedder(GoogleEmbedder())
