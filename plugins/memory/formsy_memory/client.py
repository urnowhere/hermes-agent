"""Client wrapper for memory provider."""

import logging
from typing import Optional, Any

from plugins.formsy import RuntimeClient
from plugins.formsy.models import (
    ArtifactRef,
    CodingSummary,
    MemoryPrefetchRequest,
    MemoryPrefetchResponse,
    MemorySyncTurnRequest,
    SyncMode,
)
from plugins.formsy.errors import RuntimeAPIError, TimeoutError as FormalCCTimeoutError

logger = logging.getLogger("formsy.memory.client")


class MemoryClient:
    """Client for memory-related Runtime API calls."""

    def __init__(self, runtime_client: RuntimeClient):
        self.runtime_client = runtime_client

    async def prefetch(
        self,
        workspace_id: str,
        session_id: str,
        turn_id: str,
        query: str,
        identity: Optional[dict[str, Any]] = None,
        budget: Optional[dict[str, Any]] = None,
        hints: Optional[dict[str, Any]] = None,
    ) -> MemoryPrefetchResponse | None:
        """Prefetch memory and return the Runtime response bundle."""
        try:
            request = MemoryPrefetchRequest(
                workspace_id=workspace_id,
                session_id=session_id,
                turn_id=turn_id,
                identity=identity,
                query=query,
                budget=budget or {"top_k": 10},
            )

            response = await self.runtime_client.memory_prefetch(request)
            logger.info(f"Memory prefetch completed: {response.elapsed_ms}ms")
            return response

        except (RuntimeAPIError, FormalCCTimeoutError) as e:
            logger.warning(f"Prefetch failed: {e}")
            return None
        except Exception as e:
            logger.error(f"Unexpected error in prefetch: {e}")
            return None

    async def sync_turn(
        self,
        workspace_id: str,
        session_id: str,
        turn_id: str,
        messages: list[dict[str, Any]],
        identity: Optional[dict[str, Any]] = None,
        sync_mode: SyncMode = SyncMode.ASYNC_BEST_EFFORT,
        coding_summary: Optional[CodingSummary] = None,
        artifacts: Optional[list[ArtifactRef]] = None,
    ) -> None:
        """Sync turn data (non-blocking)."""
        try:
            request = MemorySyncTurnRequest(
                workspace_id=workspace_id,
                session_id=session_id,
                turn_id=turn_id,
                messages=messages,
                identity=identity,
                sync_mode=sync_mode,
                coding_summary=coding_summary,
                artifacts=artifacts,
            )

            await self.runtime_client.memory_sync_turn(request)
            logger.info(f"Memory sync completed: turn={turn_id}")

        except Exception as e:
            logger.error(f"Sync turn failed: {e}")
