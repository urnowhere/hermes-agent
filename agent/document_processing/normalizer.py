"""Normalizer — wraps parser output into a canonical :class:`DocumentResult`."""

from __future__ import annotations

from agent.document_processing.types import DocumentResult


def normalise(
    *,
    source_type: str,
    document_type: str,
    title: str = "",
    text: str = "",
    links: list[str] | None = None,
    filename: str = "",
    url: str = "",
    mime_type: str = "",
    size: int = 0,
) -> DocumentResult:
    """Build a :class:`DocumentResult` with a validated *metadata* dict."""
    return DocumentResult(
        source_type=source_type,
        document_type=document_type,
        title=title,
        text=text,
        links=links or [],
        metadata={
            "filename": filename,
            "url": url,
            "mime_type": mime_type,
            "size": size,
        },
    )
