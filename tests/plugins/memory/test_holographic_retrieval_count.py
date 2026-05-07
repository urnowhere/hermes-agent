"""Test that retrieval.search() increments retrieval_count (#17899)."""

from __future__ import annotations

import sqlite3
import types
from unittest.mock import MagicMock

import pytest


@pytest.fixture()
def _memory_store(tmp_path):
    """Create a minimal in-memory MemoryStore-like object with a real SQLite DB."""
    db_path = tmp_path / "facts.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("CREATE VIRTUAL TABLE IF NOT EXISTS facts_fts USING fts5(content, tags, fact_id UNINDEXED)")
    conn.execute(
        """CREATE TABLE IF NOT EXISTS facts (
            fact_id TEXT PRIMARY KEY,
            content TEXT,
            tags TEXT DEFAULT '',
            category TEXT DEFAULT 'general',
            trust_score REAL DEFAULT 0.8,
            retrieval_count INTEGER DEFAULT 0,
            helpful_count INTEGER DEFAULT 0,
            created_at TEXT,
            updated_at TEXT,
            hrr_vector BLOB DEFAULT NULL
        )"""
    )
    # Insert a test fact
    conn.execute(
        "INSERT INTO facts (fact_id, content, tags, category, trust_score, retrieval_count) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ("fact-1", "Python is a programming language", "python coding", "general", 0.9, 0),
    )
    conn.execute(
        "INSERT INTO facts_fts (fact_id, content, tags) VALUES (?, ?, ?)",
        ("fact-1", "Python is a programming language", "python coding"),
    )
    conn.commit()

    store = MagicMock()
    store._conn = conn
    return store


def test_search_increments_retrieval_count(_memory_store):
    """retrieval.search() must increment retrieval_count for returned facts."""
    from plugins.memory.holographic.retrieval import FactRetriever

    retriever = FactRetriever(store=_memory_store)

    # Patch _fts_candidates to return our test fact directly
    retriever._fts_candidates = lambda query, category, min_trust, limit: [
        {
            "fact_id": "fact-1",
            "content": "Python is a programming language",
            "tags": "python coding",
            "category": "general",
            "trust_score": 0.9,
            "retrieval_count": 0,
            "helpful_count": 0,
            "created_at": None,
            "updated_at": None,
            "fts_rank": 0.8,
            "hrr_vector": None,
        }
    ]

    results = retriever.search("Python programming")
    assert len(results) == 1
    assert results[0]["fact_id"] == "fact-1"

    # Verify retrieval_count was incremented in the DB
    row = _memory_store._conn.execute(
        "SELECT retrieval_count FROM facts WHERE fact_id = ?", ("fact-1",)
    ).fetchone()
    assert row[0] == 1, f"Expected retrieval_count=1, got {row[0]}"

    # Search again — should increment to 2
    results = retriever.search("Python programming")
    row = _memory_store._conn.execute(
        "SELECT retrieval_count FROM facts WHERE fact_id = ?", ("fact-1",)
    ).fetchone()
    assert row[0] == 2, f"Expected retrieval_count=2, got {row[0]}"
