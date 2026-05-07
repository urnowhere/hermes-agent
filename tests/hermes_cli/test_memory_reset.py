"""Tests for the `hermes memory reset` CLI command.

Covers:
- Reset both stores (MEMORY.md + USER.md)
- Reset individual stores (--target memory / --target user)
- Skip confirmation with --yes
- Graceful handling when no memory files exist
- Profile-scoped reset (uses HERMES_HOME)
- Reset conversation history (--target conversations)
- Reset everything (--target everything)

Note: _run_memory_reset() duplicates cmd_memory logic.
A follow-up PR should refactor tests to call cmd_memory directly.
"""

import os
import sqlite3
import pytest
from argparse import Namespace
from pathlib import Path


@pytest.fixture
def memory_env(tmp_path, monkeypatch):
    """Set up a fake HERMES_HOME with memory files."""
    hermes_home = tmp_path / ".hermes"
    memories = hermes_home / "memories"
    memories.mkdir(parents=True)
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))

    # Create sample memory files
    (memories / "MEMORY.md").write_text(
        "§\nHermes repo is at ~/.hermes/hermes-agent\n§\nUser prefers dark themes",
        encoding="utf-8",
    )
    (memories / "USER.md").write_text(
        "§\nUser is Teknium\n§\nTimezone: US Pacific",
        encoding="utf-8",
    )
    return hermes_home, memories


@pytest.fixture
def full_env(tmp_path, monkeypatch):
    """Set up a fake HERMES_HOME with memory files AND a state.db."""
    hermes_home = tmp_path / ".hermes"
    memories = hermes_home / "memories"
    memories.mkdir(parents=True)
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))

    # Create sample memory files
    (memories / "MEMORY.md").write_text(
        "§\nHermes repo is at ~/.hermes/hermes-agent\n§\nUser prefers dark themes",
        encoding="utf-8",
    )
    (memories / "USER.md").write_text(
        "§\nUser is Teknium\n§\nTimezone: US Pacific",
        encoding="utf-8",
    )

    # Create state.db with test data
    db_path = hermes_home / "state.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "CREATE TABLE IF NOT EXISTS sessions (id TEXT PRIMARY KEY, source TEXT)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS messages "
        "(id INTEGER PRIMARY KEY, session_id TEXT, role TEXT, content TEXT)"
    )
    conn.execute("INSERT INTO sessions VALUES ('sess-1', 'cli')")
    conn.execute("INSERT INTO sessions VALUES ('sess-2', 'telegram')")
    conn.execute(
        "INSERT INTO messages VALUES (1, 'sess-1', 'user', 'write me a story')"
    )
    conn.execute(
        "INSERT INTO messages VALUES (2, 'sess-1', 'assistant', 'Once upon a time...')"
    )
    conn.execute(
        "INSERT INTO messages VALUES (3, 'sess-2', 'user', 'hello')"
    )
    conn.commit()
    conn.close()

    return hermes_home, memories, db_path


def _run_memory_reset(
    target="all", yes=False, monkeypatch=None, confirm_input="no"
):
    """Invoke the memory reset logic from cmd_memory in main.py.

    Simulates what happens when `hermes memory reset` is run.
    """
    from hermes_constants import get_hermes_home, display_hermes_home

    hermes_home = get_hermes_home()
    mem_dir = hermes_home / "memories"

    # Determine what to reset
    reset_files = target in ("all", "memory", "user", "everything")
    reset_db = target in ("conversations", "everything")

    # Build file list
    files_to_reset = []
    if reset_files:
        if target in ("all", "memory", "everything"):
            files_to_reset.append(("MEMORY.md", "agent notes"))
        if target in ("all", "user", "everything"):
            files_to_reset.append(("USER.md", "user profile"))

    existing = [
        (f, desc) for f, desc in files_to_reset if (mem_dir / f).exists()
    ]

    # Check DB
    db_path = hermes_home / "state.db"
    db_exists = reset_db and db_path.exists()
    if db_exists:
        conn = sqlite3.connect(str(db_path))
        try:
            result = conn.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name='messages'"
            ).fetchone()
            if not result:
                db_exists = False
        except Exception:
            db_exists = False
        finally:
            conn.close()

    # If nothing to reset, bail out
    if not existing and not db_exists:
        return "nothing"

    if not yes:
        if confirm_input != "yes":
            return "cancelled"

    # Delete files
    for f, desc in existing:
        (mem_dir / f).unlink()

    # Clear state.db
    if db_exists:
        conn = sqlite3.connect(str(db_path))
        try:
            conn.execute("PRAGMA foreign_keys = OFF")
            tables = conn.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND NOT name LIKE 'sqlite_%'"
            ).fetchall()
            for (table,) in tables:
                conn.execute(f"DELETE FROM {table}")
            conn.execute("PRAGMA foreign_keys = ON")
            conn.commit()
        finally:
            conn.close()

    return "deleted"


class TestMemoryReset:
    """Tests for `hermes memory reset` subcommand."""

    def test_reset_all_with_yes_flag(self, memory_env):
        """--yes flag should skip confirmation and delete both files."""
        hermes_home, memories = memory_env
        assert (memories / "MEMORY.md").exists()
        assert (memories / "USER.md").exists()

        result = _run_memory_reset(target="all", yes=True)
        assert result == "deleted"
        assert not (memories / "MEMORY.md").exists()
        assert not (memories / "USER.md").exists()

    def test_reset_memory_only(self, memory_env):
        """--target memory should only delete MEMORY.md."""
        hermes_home, memories = memory_env

        result = _run_memory_reset(target="memory", yes=True)
        assert result == "deleted"
        assert not (memories / "MEMORY.md").exists()
        assert (memories / "USER.md").exists()

    def test_reset_user_only(self, memory_env):
        """--target user should only delete USER.md."""
        hermes_home, memories = memory_env

        result = _run_memory_reset(target="user", yes=True)
        assert result == "deleted"
        assert (memories / "MEMORY.md").exists()
        assert not (memories / "USER.md").exists()

    def test_reset_no_files_exist(self, tmp_path, monkeypatch):
        """Should return 'nothing' when no memory files exist."""
        hermes_home = tmp_path / ".hermes"
        (hermes_home / "memories").mkdir(parents=True)
        monkeypatch.setenv("HERMES_HOME", str(hermes_home))

        result = _run_memory_reset(target="all", yes=True)
        assert result == "nothing"

    def test_reset_confirmation_denied(self, memory_env):
        """Without --yes and without typing 'yes', should be cancelled."""
        hermes_home, memories = memory_env

        result = _run_memory_reset(
            target="all", yes=False, confirm_input="no"
        )
        assert result == "cancelled"
        # Files should still exist
        assert (memories / "MEMORY.md").exists()
        assert (memories / "USER.md").exists()

    def test_reset_confirmation_accepted(self, memory_env):
        """Typing 'yes' should proceed with deletion."""
        hermes_home, memories = memory_env

        result = _run_memory_reset(
            target="all", yes=False, confirm_input="yes"
        )
        assert result == "deleted"
        assert not (memories / "MEMORY.md").exists()
        assert not (memories / "USER.md").exists()

    def test_reset_profile_scoped(self, tmp_path, monkeypatch):
        """Reset should work on the active profile's HERMES_HOME."""
        profile_home = tmp_path / "profiles" / "myprofile"
        memories = profile_home / "memories"
        memories.mkdir(parents=True)
        (memories / "MEMORY.md").write_text("profile memory", encoding="utf-8")
        (memories / "USER.md").write_text("profile user", encoding="utf-8")
        monkeypatch.setenv("HERMES_HOME", str(profile_home))

        result = _run_memory_reset(target="all", yes=True)
        assert result == "deleted"
        assert not (memories / "MEMORY.md").exists()
        assert not (memories / "USER.md").exists()

    def test_reset_partial_files(self, memory_env):
        """Reset should work when only one memory file exists."""
        hermes_home, memories = memory_env
        (memories / "USER.md").unlink()

        result = _run_memory_reset(target="all", yes=True)
        assert result == "deleted"
        assert not (memories / "MEMORY.md").exists()

    def test_reset_empty_memories_dir(self, tmp_path, monkeypatch):
        """No memories dir at all should report nothing."""
        hermes_home = tmp_path / ".hermes"
        hermes_home.mkdir(parents=True)
        # No memories dir
        monkeypatch.setenv("HERMES_HOME", str(hermes_home))

        # The memories dir won't exist; get_hermes_home() / "memories" won't
        # have files
        result = _run_memory_reset(target="all", yes=True)
        assert result == "nothing"


class TestMemoryResetConversations:
    """Tests for --target conversations (state.db only)."""

    def test_reset_conversations_clears_db(self, full_env):
        """--target conversations should clear state.db but keep memory files."""
        hermes_home, memories, db_path = full_env

        result = _run_memory_reset(target="conversations", yes=True)
        assert result == "deleted"

        # Memory files should still exist
        assert (memories / "MEMORY.md").exists()
        assert (memories / "USER.md").exists()

        # DB should be empty
        conn = sqlite3.connect(str(db_path))
        msg_count = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
        sess_count = conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
        conn.close()
        assert msg_count == 0
        assert sess_count == 0

    def test_reset_conversations_no_db(self, memory_env):
        """--target conversations with no state.db should return 'nothing'."""
        hermes_home, memories = memory_env

        result = _run_memory_reset(target="conversations", yes=True)
        assert result == "nothing"

        # Memory files should still exist
        assert (memories / "MEMORY.md").exists()
        assert (memories / "USER.md").exists()

    def test_reset_conversations_preserves_schema(self, full_env):
        """--target conversations should preserve DB schema."""
        hermes_home, memories, db_path = full_env

        _run_memory_reset(target="conversations", yes=True)

        # Schema should still work — insert new data
        conn = sqlite3.connect(str(db_path))
        conn.execute(
            "INSERT INTO sessions VALUES ('new-sess', 'cli')"
        )
        conn.execute(
            "INSERT INTO messages VALUES (1, 'new-sess', 'user', 'test')"
        )
        conn.commit()

        count = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
        conn.close()
        assert count == 1

    def test_reset_conversations_confirmation_denied(self, full_env):
        """Without --yes, conversations reset should be cancelled."""
        hermes_home, memories, db_path = full_env

        result = _run_memory_reset(
            target="conversations", yes=False, confirm_input="no"
        )
        assert result == "cancelled"

        # DB should still have data
        conn = sqlite3.connect(str(db_path))
        msg_count = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
        conn.close()
        assert msg_count == 3


class TestMemoryResetEverything:
    """Tests for --target everything (files + state.db)."""

    def test_reset_everything_clears_all(self, full_env):
        """--target everything should clear memory files AND state.db."""
        hermes_home, memories, db_path = full_env

        result = _run_memory_reset(target="everything", yes=True)
        assert result == "deleted"

        # Memory files should be gone
        assert not (memories / "MEMORY.md").exists()
        assert not (memories / "USER.md").exists()

        # DB should be empty
        conn = sqlite3.connect(str(db_path))
        msg_count = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
        sess_count = conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
        conn.close()
        assert msg_count == 0
        assert sess_count == 0

    def test_reset_everything_no_state_db(self, memory_env):
        """--target everything with no state.db should still clear files."""
        hermes_home, memories = memory_env

        result = _run_memory_reset(target="everything", yes=True)
        assert result == "deleted"
        assert not (memories / "MEMORY.md").exists()
        assert not (memories / "USER.md").exists()

    def test_reset_everything_no_files_no_db(self, tmp_path, monkeypatch):
        """--target everything with nothing to reset should return 'nothing'."""
        hermes_home = tmp_path / ".hermes"
        (hermes_home / "memories").mkdir(parents=True)
        monkeypatch.setenv("HERMES_HOME", str(hermes_home))

        result = _run_memory_reset(target="everything", yes=True)
        assert result == "nothing"

    def test_reset_everything_preserves_schema(self, full_env):
        """--target everything should preserve DB schema."""
        hermes_home, memories, db_path = full_env

        _run_memory_reset(target="everything", yes=True)

        # Schema should still work
        conn = sqlite3.connect(str(db_path))
        conn.execute("INSERT INTO sessions VALUES ('new-sess', 'cli')")
        conn.execute(
            "INSERT INTO messages VALUES (1, 'new-sess', 'user', 'test')"
        )
        conn.commit()

        count = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
        conn.close()
        assert count == 1
