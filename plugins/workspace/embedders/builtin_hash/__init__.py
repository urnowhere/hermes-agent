from __future__ import annotations

import hashlib
import math
import re
from typing import Any

from agent.workspace_contracts import WorkspaceEmbedderPlugin
from agent.workspace_types import WorkspacePluginContext


class BuiltinHashEmbedder(WorkspaceEmbedderPlugin):

    @property
    def name(self) -> str:
        return "builtin-hash"

    def is_available(self, config: dict[str, Any], context: WorkspacePluginContext) -> bool:
        return True

    def signature(self, config: dict[str, Any]) -> str:
        dims = int(config.get("dimensions", 768) or 768)
        return f"builtin-hash:{dims}"

    def dimensions(self, config: dict[str, Any]) -> int:
        return int(config.get("dimensions", 768) or 768)

    def embed_documents(
        self, texts: list[str], *, config: dict[str, Any], context: WorkspacePluginContext
    ) -> list[list[float]]:
        return [self._hash_embed(text, config) for text in texts]

    def embed_query(
        self, text: str, *, config: dict[str, Any], context: WorkspacePluginContext
    ) -> list[float]:
        return self._hash_embed(text, config)

    def _hash_embed(self, text: str, config: dict[str, Any]) -> list[float]:
        dims = max(32, min(self.dimensions(config), 1024))
        vec = [0.0] * dims
        tokens = re.findall(r"[A-Za-z0-9_./:-]+", text.lower())
        if not tokens:
            return vec
        for token in tokens:
            digest = hashlib.sha256(token.encode("utf-8")).digest()
            idx = int.from_bytes(digest[:4], "big") % dims
            sign = 1.0 if digest[4] % 2 == 0 else -1.0
            vec[idx] += sign
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        return [v / norm for v in vec]


def register(ctx) -> None:
    ctx.register_workspace_embedder(BuiltinHashEmbedder())
