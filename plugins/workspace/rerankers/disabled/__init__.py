from __future__ import annotations

from typing import Any

from agent.workspace_contracts import WorkspaceRerankerPlugin
from agent.workspace_types import WorkspaceHit, WorkspacePluginContext


class DisabledReranker(WorkspaceRerankerPlugin):

    @property
    def name(self) -> str:
        return "disabled"

    def is_available(self, config: dict[str, Any], context: WorkspacePluginContext) -> bool:
        return True

    def signature(self, config: dict[str, Any]) -> str:
        return "disabled"

    def rerank(
        self,
        query: str,
        candidates: list[WorkspaceHit],
        *,
        config: dict[str, Any],
        context: WorkspacePluginContext,
    ) -> list[WorkspaceHit]:
        return list(candidates)


def register(ctx) -> None:
    ctx.register_workspace_reranker(DisabledReranker())
