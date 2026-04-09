from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from agent.model_metadata import estimate_tokens_rough
from agent.workspace_contracts import WorkspaceChunkerPlugin
from agent.workspace_types import WorkspaceChunk, WorkspaceDocument, WorkspacePluginContext


class BuiltinStructuralChunker(WorkspaceChunkerPlugin):

    @property
    def name(self) -> str:
        return "builtin-structural"

    def is_available(self, config: dict[str, Any], context: WorkspacePluginContext) -> bool:
        return True

    def signature(self, config: dict[str, Any]) -> str:
        dt = int(config.get("default_tokens", 512) or 512)
        ot = int(config.get("overlap_tokens", 80) or 80)
        return f"builtin-structural:{dt}:{ot}"

    def chunk(
        self,
        document: WorkspaceDocument,
        *,
        path: Path,
        config: dict[str, Any],
        context: WorkspacePluginContext,
    ) -> list[WorkspaceChunk]:
        target_chars = max(256, int(config.get("default_tokens", 512) or 512) * 4)
        overlap_chars = max(0, int(config.get("overlap_tokens", 80) or 80) * 4)
        text = document.text.replace("\r\n", "\n").strip()
        if not text:
            return []

        if document.media_type == "text/markdown":
            raw_chunks = self._chunk_markdown(text, path, target_chars, overlap_chars)
        elif document.media_type == "text/x-code":
            raw_chunks = self._chunk_code(text, path, target_chars, overlap_chars)
        else:
            raw_chunks = self._chunk_generic(text, path, target_chars, overlap_chars)

        if not raw_chunks:
            raw_chunks = [self._build_chunk(path, text, "text")]

        return raw_chunks

    def _build_chunk(self, path: Path, content: str, kind: str, section: str = "") -> WorkspaceChunk:
        prefix_lines = [f"Path: {path.as_posix()}"]
        if section:
            prefix_lines.append(f"Section: {section}")
        if kind:
            prefix_lines.append(f"Kind: {kind}")
        body = "\n".join(prefix_lines) + "\n\n" + content.strip()
        return WorkspaceChunk(
            content=body,
            token_estimate=estimate_tokens_rough(body),
            kind=kind,
            section_title=section,
        )

    def _yield_chunk_windows(self, text: str, target_chars: int, overlap_chars: int) -> list[str]:
        normalized = text.replace("\r\n", "\n").strip()
        if not normalized:
            return []
        windows: list[str] = []
        start = 0
        text_len = len(normalized)
        while start < text_len:
            end = min(text_len, start + target_chars)
            if end < text_len:
                boundary = normalized.rfind("\n\n", max(start + 1, end - 200), end)
                if boundary == -1:
                    boundary = normalized.rfind("\n", max(start + 1, end - 120), end)
                if boundary != -1 and boundary > start:
                    end = boundary
            chunk = normalized[start:end].strip()
            if chunk:
                windows.append(chunk)
            if end >= text_len:
                break
            next_start = max(start + 1, end - overlap_chars)
            if next_start <= start:
                next_start = end
            start = next_start
        return windows

    def _chunk_markdown(self, text: str, path: Path, target_chars: int, overlap_chars: int) -> list[WorkspaceChunk]:
        lines = text.splitlines()
        sections: list[tuple[str, str]] = []
        current_heading = ""
        current_lines: list[str] = []
        for line in lines:
            if re.match(r"^#{1,6}\s+", line.strip()):
                if current_lines:
                    sections.append((current_heading, "\n".join(current_lines).strip()))
                current_heading = line.strip().lstrip("#").strip()
                current_lines = [line]
            else:
                current_lines.append(line)
        if current_lines:
            sections.append((current_heading, "\n".join(current_lines).strip()))

        chunks: list[WorkspaceChunk] = []
        for heading, section_text in sections:
            for window in self._yield_chunk_windows(section_text, target_chars, overlap_chars):
                chunks.append(self._build_chunk(path, window, "markdown", heading))
        return chunks

    def _chunk_code(self, text: str, path: Path, target_chars: int, overlap_chars: int) -> list[WorkspaceChunk]:
        lines = text.splitlines()
        marker_re = re.compile(
            r"^\s*(?:async\s+def|def|class)\s+|^\s*(?:export\s+)?(?:async\s+)?function\s+|"
            r"^\s*(?:export\s+)?class\s+|^\s*(?:const|let|var)\s+\w+\s*=\s*(?:async\s*)?\("
        )
        blocks: list[str] = []
        current: list[str] = []
        for line in lines:
            if marker_re.match(line) and current:
                blocks.append("\n".join(current).strip())
                current = [line]
            else:
                current.append(line)
        if current:
            blocks.append("\n".join(current).strip())

        chunks: list[WorkspaceChunk] = []
        for block in blocks:
            first_line = next((ln.strip() for ln in block.splitlines() if ln.strip()), "")
            section = first_line[:120]
            for window in self._yield_chunk_windows(block, target_chars, overlap_chars):
                chunks.append(self._build_chunk(path, window, "code", section))
        return chunks

    def _chunk_generic(self, text: str, path: Path, target_chars: int, overlap_chars: int) -> list[WorkspaceChunk]:
        for_paragraphs = [part.strip() for part in re.split(r"\n\s*\n", text) if part.strip()]
        aggregated: list[str] = []
        current = ""
        for paragraph in for_paragraphs:
            candidate = f"{current}\n\n{paragraph}".strip() if current else paragraph
            if current and len(candidate) > target_chars:
                aggregated.append(current)
                current = paragraph
            else:
                current = candidate
        if current:
            aggregated.append(current)

        chunks: list[WorkspaceChunk] = []
        for block in aggregated or [text]:
            for window in self._yield_chunk_windows(block, target_chars, overlap_chars):
                chunks.append(self._build_chunk(path, window, "text"))
        return chunks


def register(ctx) -> None:
    ctx.register_workspace_chunker(BuiltinStructuralChunker())
