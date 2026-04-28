"""HKTMemory enterprise knowledge recall plugin — MemoryProvider adapter.

Phase 1 is read-only: prefetch returns fenced recall results from the
HKTMemory vector store; sync_turn is a no-op that logs a skip reason.

External package (hktmemory-provider-hermes) is lazily imported inside
initialize() so the plugin loads cleanly when the package is absent.
"""

from __future__ import annotations

import hashlib
import importlib.util
import logging
import os
import shutil
from pathlib import Path
from typing import Any, Dict, List

from agent.memory_provider import MemoryProvider

logger = logging.getLogger(__name__)

# Token budget: ~4 chars/token, so 2048 tokens ~ 8192 chars.
_MAX_CONTEXT_CHARS = 8192


class HKTMemoryProvider(MemoryProvider):
    """Enterprise knowledge recall via HKTMemory vector store."""

    def __init__(self) -> None:
        self._provider = None  # HKTMemoryHermesProvider instance
        self._available = False

    @property
    def name(self) -> str:
        return "hktmemory"

    # -- Availability ---------------------------------------------------------

    def is_available(self) -> bool:
        """True when configured, importable, and backed by CLI or vector_store.db."""
        hkt_dir = os.getenv("HERMES_HKTMEMORY_DIR")
        if not hkt_dir:
            return False

        if importlib.util.find_spec("hktmemory_provider_hermes") is None:
            return False

        # CLI on PATH (hermes-hktmemory or fallback hkt-memory)
        if shutil.which("hermes-hktmemory") or shutil.which("hkt-memory"):
            return True

        # SQLite vector store exists
        db_path = Path(hkt_dir).expanduser() / "vector_store.db"
        if db_path.is_file():
            return True

        return False

    # -- Lifecycle ------------------------------------------------------------

    def initialize(self, session_id: str, **kwargs) -> None:
        """Lazy-import HKTMemoryHermesProvider; mark unavailable on ImportError."""
        try:
            from hktmemory_provider_hermes import HKTMemoryHermesProvider
        except ImportError:
            logger.warning(
                "hktmemory-provider-hermes package not installed — "
                "HKTMemory plugin unavailable"
            )
            self._available = False
            return

        config = {
            "memory_dir": os.getenv("HERMES_HKTMEMORY_DIR"),
            "namespace": os.getenv("HERMES_HKTMEMORY_NAMESPACE", "ai_infra_enterprise_knowledge"),
            "read_only": True,
        }
        self._provider = HKTMemoryHermesProvider(config=config)
        self._provider.initialize()
        self._available = True

    def system_prompt_block(self) -> str:
        return (
            "# HKTMemory Enterprise Memory\n"
            "Active (read-only recall). Relevant enterprise knowledge may be "
            "injected in <enterprise-memory>...</enterprise-memory> blocks."
        )

    # -- Prefetch / recall ----------------------------------------------------

    def prefetch(self, query: str, *, session_id: str = "") -> str:
        """Recall from HKTMemory, serialize as fenced text, truncate if over budget."""
        if not self._available or not self._provider:
            return ""

        hkt_dir = os.getenv("HERMES_HKTMEMORY_DIR", "")
        query_hash = hashlib.sha256(query.encode()).hexdigest()[:8]
        logger.info("enterprise_memory.recall.start query_hash=%s dir=%s", query_hash, hkt_dir)

        if not query:
            return ""

        try:
            results = self._provider.prefetch(query)
        except Exception as exc:
            logger.warning("enterprise_memory.recall.done results=0 emitted=0 error=%s", exc)
            return ""

        if not results:
            return ""

        serialized, emitted = self._serialize_results(results)
        truncated = len(serialized.encode()) > _MAX_CONTEXT_CHARS

        if truncated:
            serialized, emitted = self._truncate_serialized(serialized, results, emitted)
            logger.info(
                "enterprise_memory.recall.done results=%d emitted=%d truncated=true",
                len(results), emitted,
            )
        else:
            logger.info(
                "enterprise_memory.recall.done results=%d emitted=%d truncated=false",
                len(results), emitted,
            )

        return serialized

    # -- Serialization --------------------------------------------------------

    @staticmethod
    def _format_result(index: int, result: Any) -> str:
        source = getattr(result, "source", None) or ""
        scope = getattr(result, "scope", None) or ""
        confidence = getattr(result, "confidence", None)
        conf_str = f"{confidence:.2f}" if isinstance(confidence, (int, float)) else str(confidence or "")
        text = getattr(result, "text", "")
        # Escape literal --- in text when it appears as a record delimiter.
        text = text.replace("\n---", "\n\\---")
        header = f"[{index}] source: {source} | scope: {scope} | confidence: {conf_str}"
        return f"{header}\n{text}\n---\n"

    @staticmethod
    def _serialize_results(results: list) -> tuple[str, int]:
        """Serialize RecallResult list to fenced string.

        Returns (serialized_text, number_of_records_emitted).
        Empty result list returns ("", 0).
        """
        if not results:
            return "", 0

        output = f"<enterprise-memory>\nRecall Results ({len(results)}):\n\n"
        for i, result in enumerate(results, 1):
            output += HKTMemoryProvider._format_result(i, result)
        output += "</enterprise-memory>"
        return output, len(results)

    @staticmethod
    def _truncate_serialized(serialized: str, results: list, total: int) -> tuple[str, int]:
        """Truncate serialized output at the last complete record that fits."""
        if not results:
            return "", 0

        prefix = f"<enterprise-memory>\nRecall Results ({total}):\n\n"
        suffix = "\n... (truncated)\n</enterprise-memory>"
        current = prefix
        emitted = 0

        for i, result in enumerate(results, 1):
            record = HKTMemoryProvider._format_result(i, result)
            candidate = current + record
            if len((candidate.rstrip() + suffix).encode()) > _MAX_CONTEXT_CHARS:
                break
            current = candidate
            emitted = i

        if emitted == 0:
            return "", 0

        return current.rstrip() + suffix, emitted

    # -- Sync (read-only) -----------------------------------------------------

    def sync_turn(self, user_content: str, assistant_content: str, *, session_id: str = "") -> None:
        """Phase 1: read-only — log skip, do not raise."""
        logger.info("enterprise_memory.capture.skipped reason=read_only")

    # -- Tools (none in Phase 1) ----------------------------------------------

    def get_tool_schemas(self) -> List[Dict[str, Any]]:
        return []

    def shutdown(self) -> None:
        pass


# ---------------------------------------------------------------------------
# Plugin entry point
# ---------------------------------------------------------------------------

def register(ctx) -> None:
    """Register HKTMemory as a memory provider plugin."""
    ctx.register_memory_provider(HKTMemoryProvider())
