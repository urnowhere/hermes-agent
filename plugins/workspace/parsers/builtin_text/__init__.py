from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from agent.workspace_contracts import WorkspaceParserPlugin
from agent.workspace_types import BINARY_SUFFIXES, PluginHealth, WorkspaceDocument, WorkspacePluginContext




class BuiltinTextParser(WorkspaceParserPlugin):

    @property
    def name(self) -> str:
        return "builtin-text"

    def supported_suffixes(self) -> set[str]:
        return set()  # handles all non-binary files

    def is_available(self, config: dict[str, Any], context: WorkspacePluginContext) -> bool:
        return True

    def signature(self, config: dict[str, Any]) -> str:
        return "builtin-text:1"

    def parse(
        self,
        path: Path,
        *,
        config: dict[str, Any],
        context: WorkspacePluginContext,
    ) -> WorkspaceDocument | None:
        if path.suffix.lower() in BINARY_SUFFIXES:
            return None
        try:
            chunk = path.read_bytes()[:1024]
        except OSError:
            return None
        if b"\x00" in chunk:
            return None
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            return None
        if not text.strip():
            return None

        ext = path.suffix.lower()
        if ext in {".md", ".markdown", ".rst"}:
            media_type = "text/markdown"
        elif ext in {".py", ".js", ".ts", ".tsx", ".jsx", ".rs", ".go", ".java", ".c", ".cpp", ".h", ".hpp"}:
            media_type = "text/x-code"
        else:
            media_type = "text/plain"

        return WorkspaceDocument(
            source_path=str(path),
            relative_path=str(path),
            media_type=media_type,
            text=text,
        )


def register(ctx) -> None:
    ctx.register_workspace_parser(BuiltinTextParser())
