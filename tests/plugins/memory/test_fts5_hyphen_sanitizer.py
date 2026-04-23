"""Tests for FTS5 hyphenated query sanitization in holographic retrieval."""

import sqlite3
import pytest

from plugins.memory.holographic.retrieval import _sanitize_fts5_query


class TestSanitizeFTS5Query:
    """Unit tests for the _sanitize_fts5_query helper."""

    def test_hyphenated_token_quoted(self):
        assert _sanitize_fts5_query("pve-01") == '"pve-01"'

    def test_multiple_hyphenated_tokens(self):
        result = _sanitize_fts5_query("lxc-103 AND pve-01")
        assert '"lxc-103"' in result
        assert '"pve-01"' in result
        assert "AND" in result

    def test_already_quoted_preserved(self):
        assert _sanitize_fts5_query('"pve-01"') == '"pve-01"'

    def test_no_hyphens_unchanged(self):
        assert _sanitize_fts5_query("hello world") == "hello world"

    def test_single_word_unchanged(self):
        assert _sanitize_fts5_query("hello") == "hello"

    def test_fts5_operators_preserved(self):
        result = _sanitize_fts5_query("pve-01 NOT lxc-103")
        assert "NOT" in result
        assert '"pve-01"' in result
        assert '"lxc-103"' in result

    def test_or_operator_preserved(self):
        result = _sanitize_fts5_query("pve-01 OR pve-02")
        assert "OR" in result

    def test_multi_hyphen_token(self):
        assert _sanitize_fts5_query("my-multi-part-name") == '"my-multi-part-name"'

    def test_mixed_plain_and_hyphenated(self):
        result = _sanitize_fts5_query("server pve-01 hardware")
        assert result == 'server "pve-01" hardware'

    def test_empty_string(self):
        assert _sanitize_fts5_query("") == ""


class TestFTS5Integration:
    """Integration tests using real SQLite FTS5 to verify queries don't crash."""

    @pytest.fixture()
    def fts_conn(self):
        conn = sqlite3.connect(":memory:")
        conn.execute(
            "CREATE VIRTUAL TABLE test_fts USING fts5(content, tags)"
        )
        conn.execute(
            "INSERT INTO test_fts(content, tags) VALUES (?, ?)",
            ("PVE-01 hardware: i5-13500T, IP 10.20.90.00", "pve-01,homelab"),
        )
        conn.execute(
            "INSERT INTO test_fts(content, tags) VALUES (?, ?)",
            ("LXC-103 running pihole DNS", "lxc-103,dns"),
        )
        conn.commit()
        yield conn
        conn.close()

    def test_raw_hyphen_raises(self, fts_conn):
        """The raw (unsanitized) query MUST fail — this is the bug."""
        with pytest.raises(sqlite3.OperationalError, match="no such column"):
            fts_conn.execute(
                "SELECT * FROM test_fts WHERE test_fts MATCH ?", ("pve-01",)
            )

    def test_sanitized_hyphen_succeeds(self, fts_conn):
        sanitized = _sanitize_fts5_query("pve-01")
        rows = fts_conn.execute(
            "SELECT * FROM test_fts WHERE test_fts MATCH ?", (sanitized,)
        ).fetchall()
        assert len(rows) >= 1
        assert "PVE-01" in rows[0][0]

    def test_sanitized_multiple_tokens(self, fts_conn):
        sanitized = _sanitize_fts5_query("lxc-103")
        rows = fts_conn.execute(
            "SELECT * FROM test_fts WHERE test_fts MATCH ?", (sanitized,)
        ).fetchall()
        assert len(rows) >= 1

    def test_plain_query_still_works(self, fts_conn):
        rows = fts_conn.execute(
            "SELECT * FROM test_fts WHERE test_fts MATCH ?", ("hardware",)
        ).fetchall()
        assert len(rows) >= 1
