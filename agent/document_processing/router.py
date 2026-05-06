"""Router — single entry-point for the Document Intelligence Layer.

Gateway platforms call :func:`process_document` (for uploaded files) or
:func:`process_url` (for URLs detected in messages).  The router picks the
right parser, normalises the output, and returns a :class:`DocumentResult`.
"""

from __future__ import annotations

import logging
from typing import Optional

from agent.document_processing.html_parser import parse_html
from agent.document_processing.normalizer import normalise
from agent.document_processing.types import DocumentResult
from agent.document_processing.url_fetcher import FetchError, fetch_url

logger = logging.getLogger(__name__)

# Extensions that can be decoded as plain text and injected directly.
_PLAINTEXT_EXTENSIONS = {".txt", ".md", ".log", ".ini", ".cfg", ".csv", ".json", ".xml", ".yaml", ".yml", ".toml"}

# Extensions handled by the HTML parser.
_HTML_EXTENSIONS = {".html", ".htm"}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def process_document(
    raw_bytes: bytes,
    *,
    filename: str = "",
    ext: str = "",
    mime_type: str = "",
    source_type: str = "telegram_file",
) -> DocumentResult:
    """Parse an uploaded file and return a normalised :class:`DocumentResult`.

    Parameters
    ----------
    raw_bytes:
        The raw file content.
    filename:
        Original filename (e.g. ``"api-docs.html"``).
    ext:
        Lowercase extension including the dot (e.g. ``".html"``).
    mime_type:
        MIME type reported by the platform (informational).
    source_type:
        One of ``"telegram_file"``, ``"local_file"``, etc.
    """
    ext = ext.lower() if ext else ""

    if ext in _HTML_EXTENSIONS:
        try:
            html_text = raw_bytes.decode("utf-8", errors="replace")
        except Exception:
            html_text = raw_bytes.decode("latin-1")
        title, text, links = parse_html(html_text)
        return normalise(
            source_type=source_type,
            document_type="html",
            title=title,
            text=text,
            links=links,
            filename=filename,
            mime_type=mime_type,
            size=len(raw_bytes),
        )

    if ext in _PLAINTEXT_EXTENSIONS:
        try:
            text = raw_bytes.decode("utf-8")
        except UnicodeDecodeError:
            text = raw_bytes.decode("latin-1")
        doc_type = ext.lstrip(".")
        # Normalise some extensions to canonical type names
        type_map = {
            "yml": "yaml",
            "log": "txt",
            "ini": "txt",
            "cfg": "txt",
        }
        doc_type = type_map.get(doc_type, doc_type)
        return normalise(
            source_type=source_type,
            document_type=doc_type,
            text=text,
            filename=filename,
            mime_type=mime_type,
            size=len(raw_bytes),
        )

    # Fallback — unsupported (PDF, DOCX, etc. can be added later)
    return normalise(
        source_type=source_type,
        document_type=ext.lstrip(".") or "unknown",
        text=f"[Document received: {filename or 'unnamed'} ({ext or 'unknown type'}). "
             f"Automatic text extraction for this format is not yet supported. "
             f"The file has been cached for manual inspection.]",
        filename=filename,
        mime_type=mime_type,
        size=len(raw_bytes),
    )


def process_url(url: str, *, source_type: str = "url") -> DocumentResult:
    """Fetch a URL and return a normalised :class:`DocumentResult`.

    Raises nothing — errors are captured into the result text.
    """
    try:
        html_text = fetch_url(url)
    except FetchError as exc:
        logger.warning("URL fetch failed for %s: %s", url, exc)
        return normalise(
            source_type=source_type,
            document_type="html",
            text=f"[Failed to fetch URL: {exc}]",
            url=url,
        )
    except Exception as exc:
        logger.warning("Unexpected error fetching %s: %s", url, exc, exc_info=True)
        return normalise(
            source_type=source_type,
            document_type="html",
            text=f"[Failed to fetch URL: {exc}]",
            url=url,
        )

    title, text, links = parse_html(html_text)
    return normalise(
        source_type=source_type,
        document_type="html",
        title=title,
        text=text,
        links=links,
        url=url,
        size=len(html_text.encode("utf-8", errors="replace")),
    )
