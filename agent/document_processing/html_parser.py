"""HTML parser — extracts clean text, title, and links from HTML content.

Uses BeautifulSoup with the stdlib html.parser backend so there is no
hard dependency on lxml.  If beautifulsoup4 is missing at runtime the
parser falls back to a regex-based lightweight extractor.
"""

from __future__ import annotations

import re
from typing import List, Optional, Tuple

# ---------------------------------------------------------------------------
# Try importing BeautifulSoup; provide a graceful fallback.
# ---------------------------------------------------------------------------
try:
    from bs4 import BeautifulSoup, Comment  # type: ignore[import-untyped]

    _HAS_BS4 = True
except ImportError:
    _HAS_BS4 = False

# Tags whose *content* should be removed entirely (not just the tag).
_REMOVE_TAGS = {"script", "style", "noscript", "svg", "canvas", "template", "iframe"}

# Maximum whitespace‐collapsed gap between blocks.
_MAX_BLANK_LINES = 2


def parse_html(raw_html: str) -> Tuple[str, str, List[str]]:
    """Parse *raw_html* and return ``(title, text, links)``.

    * ``title`` — content of ``<title>`` or first ``<h1>``, empty string if
      neither exists.
    * ``text`` — human-readable text with paragraph breaks preserved.
    * ``links`` — deduplicated list of ``href`` values from ``<a>`` tags.
    """
    if _HAS_BS4:
        return _parse_with_bs4(raw_html)
    return _parse_fallback(raw_html)


# ---------------------------------------------------------------------------
# Primary: BeautifulSoup
# ---------------------------------------------------------------------------


def _parse_with_bs4(raw_html: str) -> Tuple[str, str, List[str]]:
    soup = BeautifulSoup(raw_html, "html.parser")

    # 1. Remove unwanted elements ----------------------------------------
    for tag_name in _REMOVE_TAGS:
        for tag in soup.find_all(tag_name):
            tag.decompose()

    # Remove HTML comments
    for comment in soup.find_all(string=lambda t: isinstance(t, Comment)):
        comment.extract()

    # 2. Title -----------------------------------------------------------
    title = ""
    title_tag = soup.find("title")
    if title_tag:
        title = title_tag.get_text(strip=True)
    if not title:
        h1 = soup.find("h1")
        if h1:
            title = h1.get_text(strip=True)

    # 3. Links -----------------------------------------------------------
    seen_links: set[str] = set()
    links: List[str] = []
    for a_tag in soup.find_all("a", href=True):
        href = a_tag["href"].strip()
        if href and href not in seen_links and not href.startswith(("#", "javascript:")):
            seen_links.add(href)
            links.append(href)

    # 4. Text ------------------------------------------------------------
    # get_text with a separator that lets us collapse later.
    raw_text = soup.get_text(separator="\n")
    text = _normalise_whitespace(raw_text)

    return title, text, links


# ---------------------------------------------------------------------------
# Fallback: regex (no external deps)
# ---------------------------------------------------------------------------


def _parse_fallback(raw_html: str) -> Tuple[str, str, List[str]]:
    """Best-effort extraction when BeautifulSoup is unavailable."""
    # Remove unwanted blocks
    for tag_name in _REMOVE_TAGS:
        raw_html = re.sub(
            rf"<{tag_name}[^>]*>.*?</{tag_name}>",
            "",
            raw_html,
            flags=re.IGNORECASE | re.DOTALL,
        )
    # Remove all remaining HTML tags
    title_match = re.search(r"<title[^>]*>(.*?)</title>", raw_html, re.IGNORECASE | re.DOTALL)
    title = title_match.group(1).strip() if title_match else ""

    # Links
    links = list(dict.fromkeys(
        href
        for href in re.findall(r'<a[^>]+href=["\']([^"\']+)["\']', raw_html, re.IGNORECASE)
        if not href.startswith(("#", "javascript:"))
    ))

    text = re.sub(r"<[^>]+>", " ", raw_html)
    text = _normalise_whitespace(text)

    return title, text, links


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _normalise_whitespace(text: str) -> str:
    """Collapse runs of blank lines and trim each line."""
    lines = [line.strip() for line in text.splitlines()]
    result: List[str] = []
    blank_count = 0
    for line in lines:
        if not line:
            blank_count += 1
            if blank_count <= _MAX_BLANK_LINES:
                result.append("")
        else:
            blank_count = 0
            result.append(line)
    return "\n".join(result).strip()
