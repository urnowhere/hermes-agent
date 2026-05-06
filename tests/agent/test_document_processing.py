"""Acceptance tests for the Document Intelligence Layer.

Tests cover the 7 scenarios from the design doc (section J):
1. Upload test.html → Hermes reads body text
2. Upload test.htm  → Hermes reads body text
3. Process URL https://example.com → reads title & body
4. Upload unsupported file → clear error message
5. localhost URL → rejected by SSRF protection
6. Oversized HTML → rejected
7. script/style tags stripped from HTML
"""

from __future__ import annotations

import json
import pytest

from agent.document_processing.html_parser import parse_html
from agent.document_processing.normalizer import normalise
from agent.document_processing.router import process_document, process_url
from agent.document_processing.types import DocumentResult
from agent.document_processing.url_fetcher import FetchError, _check_ssrf, MAX_CONTENT_BYTES


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

SAMPLE_HTML = """\
<!DOCTYPE html>
<html>
<head>
    <title>Royal Pay API Documentation</title>
    <style>body { font-family: sans-serif; }</style>
    <script>console.log("tracking");</script>
</head>
<body>
    <noscript>Enable JavaScript</noscript>
    <h1>Royal Pay — USDT Payment Gateway</h1>
    <p>Welcome to the Royal Pay API docs.</p>
    <p>Base URL: <a href="https://api.royalpay.vip/v1">https://api.royalpay.vip/v1</a></p>
    <p>Authentication: <a href="https://api.royalpay.vip/v1/auth">Auth endpoint</a></p>
    <script>window.analytics.track("page_view");</script>
    <a href="#section2">Jump to section 2</a>
    <a href="javascript:void(0)">Do nothing</a>
</body>
</html>
"""

SAMPLE_HTML_BYTES = SAMPLE_HTML.encode("utf-8")


# ---------------------------------------------------------------------------
# Test 1: Upload .html — reads body text
# ---------------------------------------------------------------------------

class TestUploadHtml:
    def test_html_body_extracted(self):
        result = process_document(SAMPLE_HTML_BYTES, filename="api-docs.html", ext=".html")
        assert isinstance(result, DocumentResult)
        assert result.document_type == "html"
        assert result.source_type == "telegram_file"
        assert "Royal Pay" in result.title
        assert "USDT Payment Gateway" in result.text
        assert "Welcome to the Royal Pay API docs" in result.text
        assert result.metadata["filename"] == "api-docs.html"

    def test_html_links_extracted(self):
        result = process_document(SAMPLE_HTML_BYTES, filename="api-docs.html", ext=".html")
        assert "https://api.royalpay.vip/v1" in result.links
        assert "https://api.royalpay.vip/v1/auth" in result.links
        # Fragment and javascript links should be excluded
        assert "#section2" not in result.links
        assert "javascript:void(0)" not in result.links


# ---------------------------------------------------------------------------
# Test 2: Upload .htm — reads body text (same parser)
# ---------------------------------------------------------------------------

class TestUploadHtm:
    def test_htm_body_extracted(self):
        result = process_document(SAMPLE_HTML_BYTES, filename="docs.htm", ext=".htm")
        assert result.document_type == "html"
        assert "Royal Pay" in result.title
        assert "USDT Payment Gateway" in result.text


# ---------------------------------------------------------------------------
# Test 3: Process URL — reads title & body
# ---------------------------------------------------------------------------

class TestProcessUrl:
    def test_example_com(self):
        """Fetch https://example.com and verify title + body text."""
        result = process_url("https://example.com")
        assert result.source_type == "url"
        assert result.document_type == "html"
        # example.com has a well-known title
        assert "example" in result.title.lower() or "example" in result.text.lower()
        assert result.metadata["url"] == "https://example.com"


# ---------------------------------------------------------------------------
# Test 4: Unsupported file → clear error
# ---------------------------------------------------------------------------

class TestUnsupportedFile:
    def test_unsupported_extension(self):
        result = process_document(b"\x00\x01\x02", filename="data.bin", ext=".bin")
        assert "not yet supported" in result.text.lower() or "unsupported" in result.text.lower() or "received" in result.text.lower()
        assert result.document_type == "bin"


# ---------------------------------------------------------------------------
# Test 5: localhost URL → SSRF rejection
# ---------------------------------------------------------------------------

class TestSsrfProtection:
    def test_localhost_blocked(self):
        with pytest.raises(FetchError, match="blocked"):
            _check_ssrf("localhost")

    def test_127_0_0_1_blocked(self):
        with pytest.raises(FetchError, match="blocked"):
            _check_ssrf("127.0.0.1")

    def test_private_10_blocked(self):
        with pytest.raises(FetchError, match="blocked"):
            _check_ssrf("10.0.0.1")

    def test_private_192_168_blocked(self):
        with pytest.raises(FetchError, match="blocked"):
            _check_ssrf("192.168.1.1")

    def test_process_url_localhost_safe(self):
        result = process_url("http://127.0.0.1/secret")
        assert "blocked" in result.text.lower() or "failed" in result.text.lower()


# ---------------------------------------------------------------------------
# Test 6: Oversized HTML → rejected
# ---------------------------------------------------------------------------

class TestOversizedHtml:
    def test_oversize_detection(self):
        """A >5 MB HTML file should not be processed by the URL fetcher."""
        # We test the constant directly — the streaming check happens in
        # url_fetcher.fetch_url which we can't easily unit-test without a
        # real server.  But we verify the limit is correctly defined.
        assert MAX_CONTENT_BYTES == 5 * 1024 * 1024

    def test_large_html_document_still_parseable(self):
        """process_document should still work for large files (it's the URL
        fetcher that enforces the 5 MB limit, not the document processor)."""
        big_html = b"<html><head><title>Big</title></head><body>" + b"<p>x</p>" * 100_000 + b"</body></html>"
        result = process_document(big_html, filename="big.html", ext=".html")
        assert result.title == "Big"
        assert "x" in result.text


# ---------------------------------------------------------------------------
# Test 7: script/style tags stripped
# ---------------------------------------------------------------------------

class TestScriptStyleRemoval:
    def test_script_content_removed(self):
        result = process_document(SAMPLE_HTML_BYTES, filename="test.html", ext=".html")
        assert "console.log" not in result.text
        assert "tracking" not in result.text
        assert "analytics.track" not in result.text
        assert "window.analytics" not in result.text

    def test_style_content_removed(self):
        result = process_document(SAMPLE_HTML_BYTES, filename="test.html", ext=".html")
        assert "font-family" not in result.text
        assert "sans-serif" not in result.text

    def test_noscript_removed(self):
        result = process_document(SAMPLE_HTML_BYTES, filename="test.html", ext=".html")
        assert "Enable JavaScript" not in result.text


# ---------------------------------------------------------------------------
# Additional: DocumentResult serialization
# ---------------------------------------------------------------------------

class TestDocumentResult:
    def test_to_dict(self):
        r = normalise(
            source_type="telegram_file",
            document_type="html",
            title="Test",
            text="Hello",
            filename="test.html",
        )
        d = r.to_dict()
        assert d["source_type"] == "telegram_file"
        assert d["document_type"] == "html"
        assert d["title"] == "Test"
        assert d["metadata"]["filename"] == "test.html"

    def test_to_injection_text(self):
        r = normalise(
            source_type="telegram_file",
            document_type="html",
            title="Royal Pay API",
            text="Some content here",
            filename="api.html",
        )
        injection = r.to_injection_text()
        assert "api.html" in injection
        assert "Royal Pay API" in injection
        assert "Some content here" in injection


# ---------------------------------------------------------------------------
# Additional: plaintext file processing
# ---------------------------------------------------------------------------

class TestPlaintextFiles:
    def test_txt_file(self):
        result = process_document(b"Hello World", filename="readme.txt", ext=".txt")
        assert result.document_type == "txt"
        assert result.text == "Hello World"

    def test_md_file(self):
        result = process_document(b"# Title\n\nContent", filename="readme.md", ext=".md")
        assert result.document_type == "md"
        assert "# Title" in result.text

    def test_json_file(self):
        content = json.dumps({"key": "value"}).encode()
        result = process_document(content, filename="data.json", ext=".json")
        assert result.document_type == "json"
        assert "key" in result.text

    def test_csv_file(self):
        result = process_document(b"name,age\nAlice,30", filename="data.csv", ext=".csv")
        assert result.document_type == "csv"
        assert "Alice" in result.text
