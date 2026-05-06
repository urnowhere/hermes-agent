"""Canonical types for the Document Intelligence Layer."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class DocumentResult:
    """Standardised output produced by every parser in the pipeline.

    All fields follow the schema specified in the design doc (section C).
    """

    source_type: str  # "telegram_file" | "url" | "local_file"
    document_type: str  # "html" | "pdf" | "docx" | "txt" | "md" | "json" | "csv"
    title: str = ""
    text: str = ""
    links: List[str] = field(default_factory=list)
    metadata: Dict[str, object] = field(default_factory=dict)

    # Convenience helpers --------------------------------------------------

    def to_dict(self) -> dict:
        return {
            "source_type": self.source_type,
            "document_type": self.document_type,
            "title": self.title,
            "text": self.text,
            "links": self.links,
            "metadata": self.metadata,
        }

    def to_injection_text(self, max_chars: int = 100_000) -> str:
        """Return a compact text representation for injecting into the LLM context."""
        parts: List[str] = []
        source_label = self.metadata.get("filename") or self.metadata.get("url") or self.source_type
        parts.append(f"[Document: {source_label} ({self.document_type})]")
        if self.title:
            parts.append(f"Title: {self.title}")
        if self.text:
            text = self.text[:max_chars]
            if len(self.text) > max_chars:
                text += f"\n… (truncated, {len(self.text):,} chars total)"
            parts.append(text)
        if self.links:
            parts.append(f"\nLinks ({len(self.links)}):")
            for link in self.links[:50]:  # cap at 50 links
                parts.append(f"  - {link}")
        return "\n".join(parts)
