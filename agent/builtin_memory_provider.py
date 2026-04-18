"""Compatibility shim for the built-in memory provider.

Some tests and older code import ``BuiltinMemoryProvider`` from this module.
The current memory system keeps the abstract interface in ``memory_provider``
and relies on the built-in MEMORY.md/USER.md flow elsewhere, so this shim
provides a lightweight built-in provider for registration/threading tests.
"""

from __future__ import annotations

from typing import Any, Dict, List

from agent.memory_provider import MemoryProvider


class BuiltinMemoryProvider(MemoryProvider):
    """Minimal built-in provider used for manager compatibility tests."""

    def __init__(self) -> None:
        self._session_id: str | None = None
        self._init_kwargs: Dict[str, Any] = {}

    @property
    def name(self) -> str:
        return "builtin"

    def is_available(self) -> bool:
        return True

    def initialize(self, session_id: str, **kwargs) -> None:
        self._session_id = session_id
        self._init_kwargs = dict(kwargs)

    def get_tool_schemas(self) -> List[Dict[str, Any]]:
        return []

    def shutdown(self) -> None:
        return None
