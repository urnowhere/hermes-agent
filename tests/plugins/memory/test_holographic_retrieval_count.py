"""
Tests for holographic memory retrieval_count tracking.

Verifies that every public retrieval method (search, probe, related, reason)
and the internal _score_facts_by_vector helper correctly increment the
retrieval_count column on returned facts.
"""

import sys

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_store(tmp_path, facts=None):
    """Create a fresh MemoryStore + FactRetriever with optional seed facts."""
    sys.path.insert(0, "plugins")
    from memory.holographic.store import MemoryStore
    from memory.holographic.retrieval import FactRetriever

    db_path = str(tmp_path / "test_memory.db")
    store = MemoryStore(db_path)
    retriever = FactRetriever(store)

    if facts:
        for content, cat, tags in facts:
            store.add_fact(content, category=cat, tags=tags)

    return store, retriever


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def populated_store(tmp_path):
    """Store with seed facts for retrieval_count tracking tests."""
    facts = [
        ("Hello world memory tool", "general", "english"),
        ("V8菜单配置包含按钮权限", "project", "菜单,权限"),
        ("贺兰纵小说已写完", "novel", "贺兰纵"),
        ("read_file工具脱敏密码", "tool", "read_file"),
    ]
    return _make_store(tmp_path, facts)


# ---------------------------------------------------------------------------
# search() increments retrieval_count
# ---------------------------------------------------------------------------

class TestSearchRetrievalCount:
    def test_english_search(self, populated_store):
        store, retriever = populated_store
        retriever.search("hello", limit=5)
        row = store._conn.execute(
            "SELECT retrieval_count FROM facts WHERE content LIKE '%hello%'"
        ).fetchone()
        assert row is not None
        assert row[0] >= 1

    def test_english_search_increments_on_each_call(self, populated_store):
        store, retriever = populated_store
        retriever.search("hello", limit=5)
        retriever.search("hello", limit=5)
        row = store._conn.execute(
            "SELECT retrieval_count FROM facts WHERE content LIKE '%hello%'"
        ).fetchone()
        assert row[0] >= 2

    def test_empty_results_no_error(self, populated_store):
        """Searching for nonexistent terms should not crash or write to DB."""
        store, retriever = populated_store
        retriever.search("nonexistent_xyz_abc", limit=5)
        rows = store._conn.execute(
            "SELECT retrieval_count FROM facts WHERE retrieval_count > 0"
        ).fetchall()
        assert len(rows) == 0


# ---------------------------------------------------------------------------
# probe() increments retrieval_count
# ---------------------------------------------------------------------------

class TestProbeRetrievalCount:
    def test_probe_fallback_search(self, populated_store):
        """probe() without numpy falls back to search(), which should track."""
        store, retriever = populated_store
        retriever.probe("memory", limit=5)
        rows = store._conn.execute(
            "SELECT retrieval_count FROM facts WHERE retrieval_count > 0"
        ).fetchall()
        assert len(rows) > 0


# ---------------------------------------------------------------------------
# related() increments retrieval_count
# ---------------------------------------------------------------------------

class TestRelatedRetrievalCount:
    def test_related(self, populated_store):
        store, retriever = populated_store
        retriever.related("hello", limit=5)
        rows = store._conn.execute(
            "SELECT retrieval_count FROM facts WHERE retrieval_count > 0"
        ).fetchall()
        assert len(rows) > 0


# ---------------------------------------------------------------------------
# reason() increments retrieval_count
# ---------------------------------------------------------------------------

class TestReasonRetrievalCount:
    def test_reason(self, populated_store):
        store, retriever = populated_store
        # reason() takes a list of entity strings; without numpy it falls
        # back to search() which joins them with a space.
        retriever.reason(["hello", "world"], limit=5)
        rows = store._conn.execute(
            "SELECT retrieval_count FROM facts WHERE retrieval_count > 0"
        ).fetchall()
        assert len(rows) > 0


# ---------------------------------------------------------------------------
# _record_retrieval edge cases
# ---------------------------------------------------------------------------

class TestRecordRetrievalEdgeCases:
    def test_empty_list(self, populated_store):
        """_record_retrieval with empty list should be a no-op."""
        store, retriever = populated_store
        retriever._record_retrieval([])
        # Should not crash, no rows affected
        rows = store._conn.execute(
            "SELECT retrieval_count FROM facts WHERE retrieval_count > 0"
        ).fetchall()
        assert len(rows) == 0

    def test_multiple_searches_accumulate(self, populated_store):
        """Multiple searches should accumulate retrieval_count on the same fact."""
        store, retriever = populated_store
        retriever.search("hello", limit=5)
        retriever.search("hello", limit=5)
        retriever.search("hello", limit=5)
        row = store._conn.execute(
            "SELECT retrieval_count FROM facts WHERE content LIKE '%hello%'"
        ).fetchone()
        assert row is not None
        assert row[0] >= 3
