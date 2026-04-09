from __future__ import annotations

import logging
import os
from typing import Any

from agent.workspace_contracts import WorkspaceEmbedderPlugin
from agent.workspace_types import PluginHealth, WorkspacePluginContext

logger = logging.getLogger(__name__)


class OpenAIEmbedder(WorkspaceEmbedderPlugin):

    @property
    def name(self) -> str:
        return "openai"

    def is_available(self, config: dict[str, Any], context: WorkspacePluginContext) -> bool:
        return bool(os.getenv("OPENAI_API_KEY", "").strip())

    def healthcheck(self, config: dict[str, Any], context: WorkspacePluginContext) -> PluginHealth:
        if not self.is_available(config, context):
            return PluginHealth(healthy=False, message="OPENAI_API_KEY not set")
        return PluginHealth(healthy=True)

    def signature(self, config: dict[str, Any]) -> str:
        model = str(config.get("model", "text-embedding-3-small") or "text-embedding-3-small")
        dims = self.dimensions(config)
        return f"openai:{model}:{dims}"

    def dimensions(self, config: dict[str, Any]) -> int:
        return int(config.get("dimensions", 1536) or 1536)

    def embed_documents(
        self, texts: list[str], *, config: dict[str, Any], context: WorkspacePluginContext
    ) -> list[list[float]]:
        return self._embed(texts, config)

    def embed_query(
        self, text: str, *, config: dict[str, Any], context: WorkspacePluginContext
    ) -> list[float]:
        results = self._embed([text], config)
        return results[0]

    def _embed(self, texts: list[str], config: dict[str, Any]) -> list[list[float]]:
        from openai import OpenAI

        api_key = os.getenv("OPENAI_API_KEY", "").strip()
        kwargs: dict[str, Any] = {"api_key": api_key}
        base_url = os.getenv("OPENAI_BASE_URL", "").strip()
        if base_url:
            kwargs["base_url"] = base_url
        model = str(config.get("model", "text-embedding-3-small") or "text-embedding-3-small")
        client = OpenAI(**kwargs)
        resp = client.embeddings.create(model=model, input=texts)
        return [list(item.embedding) for item in resp.data]


def register(ctx) -> None:
    ctx.register_workspace_embedder(OpenAIEmbedder())
