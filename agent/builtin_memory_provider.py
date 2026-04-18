"""Compatibility shim for the built-in memory provider.

Historically callers imported ``BuiltinMemoryProvider`` from this module.
The built-in memory backend is effectively a no-op provider here, but
restoring the class keeps MemoryManager tests and legacy imports working.
"""

from __future__ import annotations

from typing import Dict, List

from agent.memory_provider import MemoryProvider


class BuiltinMemoryProvider(MemoryProvider):
    """Minimal built-in provider used for registration and compatibility."""

    @property
    def name(self) -> str:
        return "builtin"

    def is_available(self) -> bool:
        return True

    def initialize(self, session_id: str, **kwargs) -> None:
        pass

    def get_tool_schemas(self) -> List[Dict[str, object]]:
        return []
