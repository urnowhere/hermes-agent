"""Tests for MemPalace memory provider — covering edge cases in KG/ChromaDB correctness."""

import json
import sqlite3
from pathlib import Path

import pytest

# Must import after sys.path setup (handled by conftest)
from plugins.memory.mempalace import (
    MemPalaceMemoryProvider,
    _store_triple_with_inverse,
)


@pytest.fixture
def mempalace_provider(tmp_path, monkeypatch):
    """Create a MemPalace provider backed by temporary palace/KG files."""
    palace_dir = tmp_path / "palace"
    palace_dir.mkdir()
    kg_path = tmp_path / "kg.sqlite3"
    identity_path = tmp_path / "identity.txt"
    identity_path.write_text("Test identity\n")

    # Seed minimal KG schema
    conn = sqlite3.connect(str(kg_path))
    conn.execute(
        "CREATE TABLE IF NOT EXISTS triples ("
        "id TEXT PRIMARY KEY, subject TEXT, predicate TEXT, object TEXT, "
        "valid_from TEXT, valid_to TEXT, confidence REAL, source_closet TEXT, "
        "source_file TEXT, extracted_at TEXT, inverse_predicate TEXT, context TEXT, "
        "source TEXT, subject_type TEXT, object_type TEXT)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS belief_history ("
        "belief_id TEXT PRIMARY KEY, entity TEXT, predicate TEXT, old_value TEXT, "
        "new_value TEXT, change_type TEXT, changed_at TEXT, source TEXT, "
        "confidence REAL, context TEXT, valid_from TEXT, valid_to TEXT)"
    )
    conn.commit()
    conn.close()

    # Patch module-level paths so all disk operations stay inside tmp_path
    monkeypatch.setattr("plugins.memory.mempalace._HERMES_PALACE", str(palace_dir))
    monkeypatch.setattr("plugins.memory.mempalace._HERMES_KG", str(kg_path))
    monkeypatch.setattr("plugins.memory.mempalace._IDENTITY_PATH", str(identity_path))
    monkeypatch.setattr("plugins.memory.mempalace._DEFAULT_PALACE_PATH", str(tmp_path))
    monkeypatch.setattr("plugins.memory.mempalace._OPENCLAW_PALACE", str(tmp_path / "openclaw_palace"))
    monkeypatch.setattr("plugins.memory.mempalace._OPENCLAW_KG", str(tmp_path / "openclaw_kg.sqlite3"))
    monkeypatch.setattr("plugins.memory.mempalace._OPENCLAW_IDENTITY", str(tmp_path / "openclaw_identity.txt"))

    provider = MemPalaceMemoryProvider()
    provider.initialize("test-session", hermes_home=str(tmp_path))
    return provider


class TestStoreTripleWithInverse:
    """Edge-case coverage for contradiction detection and inverse triple invalidation."""

    def test_inverse_invalidation_is_scoped_to_object(self, mempalace_provider, tmp_path):
        """
        When a new triple contradicts an old one, only the *specific* inverse
        of the old triple should be invalidated — not every inverse triple with
        the same subject+predicate.
        """
        # Setup: two projects both depend on LibraryX
        _store_triple_with_inverse("ProjectA", "depends_on", "LibraryX", "2026-04-16")
        _store_triple_with_inverse("ProjectB", "depends_on", "LibraryX", "2026-04-16")

        # Contradiction: ProjectA now depends on LibraryY instead of LibraryX
        _store_triple_with_inverse("ProjectA", "depends_on", "LibraryY", "2026-04-16")

        conn = sqlite3.connect(str(tmp_path / "kg.sqlite3"))
        cur = conn.cursor()
        cur.execute(
            "SELECT object FROM triples WHERE subject='LibraryX' AND predicate='depended_on_by' "
            "AND (valid_to IS NULL OR valid_to='')"
        )
        remaining = {r[0] for r in cur.fetchall()}
        conn.close()

        assert "ProjectB" in remaining, "ProjectB's inverse triple must remain active"
        assert "ProjectA" not in remaining, "ProjectA's old inverse triple must be invalidated"


class TestKgInvalidate:
    """Edge-case coverage for kg_invalidate belief_history correctness."""

    def test_kg_invalidate_records_correct_old_value_with_multiple_active(
        self, mempalace_provider, tmp_path
    ):
        """
        Even when multiple active triples share the same subject+predicate,
        invalidating a specific (subject, predicate, object) must record the
        *target* object's old_value in belief_history.
        """
        _store_triple_with_inverse("EdgeSubject", "knows", "ValueA", "2026-04-16")
        _store_triple_with_inverse("EdgeSubject", "knows", "ValueB", "2026-04-16")

        result = mempalace_provider.handle_tool_call(
            "mempalace_kg_invalidate",
            {"subject": "EdgeSubject", "predicate": "knows", "object": "ValueB"},
        )
        data = json.loads(result)
        assert data["result"] == "Triple invalidated"

        conn = sqlite3.connect(str(tmp_path / "kg.sqlite3"))
        cur = conn.cursor()
        cur.execute(
            "SELECT old_value, new_value FROM belief_history WHERE entity='EdgeSubject' "
            "AND predicate='knows' AND change_type='invalidated' ORDER BY changed_at DESC LIMIT 1"
        )
        row = cur.fetchone()
        conn.close()

        assert row is not None, "belief_history entry should exist"
        assert row[0] == "ValueB", "old_value must match the invalidated object"
        assert row[1] == "", "new_value must be empty for an invalidation"


class TestMemoryReflection:
    """Edge-case coverage for memory_reflection duplicate detection."""

    def test_reflection_ignores_episodes_duplicates(self, mempalace_provider, tmp_path):
        """
        The 'episodes' collection legitimately contains repeated system prompts
        and greetings across sessions. Reflection must NOT flag these as
        duplicate drawers.
        """
        import chromadb

        client = chromadb.PersistentClient(path=str(tmp_path / "palace"))
        col = client.get_or_create_collection("episodes")
        col.add(
            ids=["ep1", "ep2"],
            documents=[
                "Review the conversation above and consider saving or updating a skill.",
                "Review the conversation above and consider saving or updating a skill.",
            ],
            metadatas=[
                {"speaker": "mars", "role": "user", "timestamp": "2026-04-16T10:00:00"},
                {"speaker": "mars", "role": "user", "timestamp": "2026-04-16T11:00:00"},
            ],
        )

        result = mempalace_provider.handle_tool_call(
            "mempalace_memory_reflection", {"fix": False}
        )
        data = json.loads(result)

        dup_issue = next(
            (i for i in data.get("issues", []) if i["type"] == "duplicate_drawers"), None
        )
        assert dup_issue is None, "episodes duplicates should be ignored by reflection"

    def test_reflection_detects_real_duplicates_in_drawers(self, mempalace_provider, tmp_path):
        """
        Non-episodes collections (e.g. 'facts') should still be checked for duplicates.
        """
        import chromadb

        client = chromadb.PersistentClient(path=str(tmp_path / "palace"))
        col = client.get_or_create_collection("facts")
        col.add(
            ids=["f1", "f2"],
            documents=[
                "Mars prefers dark mode for all applications.",
                "Mars prefers dark mode for all applications.",
            ],
            metadatas=[
                {"wing": "facts", "room": "prefs", "created_at": "2026-04-16T10:00:00"},
                {"wing": "facts", "room": "prefs", "created_at": "2026-04-16T10:01:00"},
            ],
        )

        result = mempalace_provider.handle_tool_call(
            "mempalace_memory_reflection", {"fix": False}
        )
        data = json.loads(result)

        dup_issue = next(
            (i for i in data.get("issues", []) if i["type"] == "duplicate_drawers"), None
        )
        assert dup_issue is not None, "real duplicates in facts should be detected"
        assert any(
            g["col"] == "facts" for group in dup_issue.get("groups", []) for g in group
        ), "duplicate should be reported inside the facts collection"
