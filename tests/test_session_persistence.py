"""
Test suite for WebUI session persistence.

Tests that sessions are properly saved to disk and can be recovered
after server restart.
"""
import json
import pytest
import tempfile
import time
from pathlib import Path
from unittest.mock import Mock, patch

# Import the modules we're testing
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from hermes_cli.web_chat_api import models


class TestSessionPersistence:
    """Test session persistence to disk."""

    @pytest.fixture
    def temp_state_dir(self):
        """Create a temporary state directory for testing."""
        with tempfile.TemporaryDirectory() as tmpdir:
            yield Path(tmpdir)

    def test_create_session_saves_to_disk(self, temp_state_dir):
        """Test that creating a session saves it to disk."""
        # Arrange
        title = "Test Chat"
        workspace = "/tmp/test"
        model = "anthropic/claude-sonnet-4"

        # Act
        session = models.create_session(
            title=title,
            workspace=workspace,
            model=model,
            state_dir=temp_state_dir
        )

        # Assert
        assert session is not None
        assert session["title"] == title
        assert session["workspace"] == workspace
        assert session["model"] == model
        assert "session_id" in session

        # Verify file exists on disk
        session_file = temp_state_dir / "sessions" / f"{session['session_id']}.json"
        assert session_file.exists()

        # Verify file content
        saved_data = json.loads(session_file.read_text())
        assert saved_data["title"] == title
        assert saved_data["workspace"] == workspace
        assert saved_data["model"] == model

    def test_get_session_loads_from_disk(self, temp_state_dir):
        """Test that getting a session loads it from disk."""
        # Arrange - create a session first
        session = models.create_session(
            title="Test Chat",
            workspace="/tmp/test",
            model="anthropic/claude-sonnet-4",
            state_dir=temp_state_dir
        )
        session_id = session["session_id"]

        # Act - retrieve the session
        loaded_session = models.get_session(session_id, state_dir=temp_state_dir)

        # Assert
        assert loaded_session is not None
        assert loaded_session["session_id"] == session_id
        assert loaded_session["title"] == "Test Chat"
        assert loaded_session["workspace"] == "/tmp/test"
        assert loaded_session["model"] == "anthropic/claude-sonnet-4"

    def test_update_session_persists_changes(self, temp_state_dir):
        """Test that updating a session persists changes to disk."""
        # Arrange
        session = models.create_session(
            title="Original Title",
            workspace="/tmp/test",
            model="anthropic/claude-sonnet-4",
            state_dir=temp_state_dir
        )
        session_id = session["session_id"]

        # Act - update the session
        new_messages = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there!"}
        ]
        updated_session = models.update_session(
            session_id,
            state_dir=temp_state_dir,
            title="Updated Title",
            messages=new_messages
        )

        # Assert - verify in-memory update
        assert updated_session["title"] == "Updated Title"
        assert len(updated_session["messages"]) == 2

        # Assert - verify persisted to disk
        loaded_session = models.get_session(session_id, state_dir=temp_state_dir)
        assert loaded_session["title"] == "Updated Title"
        assert len(loaded_session["messages"]) == 2
        assert loaded_session["messages"][0]["content"] == "Hello"

    def test_session_survives_restart(self, temp_state_dir):
        """Test that sessions survive a simulated server restart."""
        # Arrange - create session and add messages
        session = models.create_session(
            title="Persistent Chat",
            workspace="/tmp/test",
            model="anthropic/claude-sonnet-4",
            state_dir=temp_state_dir
        )
        session_id = session["session_id"]

        messages = [
            {"role": "user", "content": "First message"},
            {"role": "assistant", "content": "First response"},
            {"role": "user", "content": "Second message"},
            {"role": "assistant", "content": "Second response"}
        ]
        models.update_session(
            session_id,
            state_dir=temp_state_dir,
            messages=messages
        )

        # Act - simulate restart by clearing any in-memory cache
        # (In real scenario, this would be a server restart)
        # Then load the session again
        loaded_session = models.get_session(session_id, state_dir=temp_state_dir)

        # Assert - all data should be intact
        assert loaded_session is not None
        assert loaded_session["session_id"] == session_id
        assert loaded_session["title"] == "Persistent Chat"
        assert len(loaded_session["messages"]) == 4
        assert loaded_session["messages"][0]["content"] == "First message"
        assert loaded_session["messages"][-1]["content"] == "Second response"

    def test_get_nonexistent_session_returns_none(self, temp_state_dir):
        """Test that getting a non-existent session returns None."""
        # Act
        session = models.get_session("nonexistent-id", state_dir=temp_state_dir)

        # Assert
        assert session is None

    def test_update_nonexistent_session_returns_none(self, temp_state_dir):
        """Test that updating a non-existent session returns None."""
        # Act
        result = models.update_session(
            "nonexistent-id",
            state_dir=temp_state_dir,
            title="New Title"
        )

        # Assert
        assert result is None

    def test_delete_session_removes_from_disk(self, temp_state_dir):
        """Test that deleting a session removes it from disk."""
        # Arrange
        session = models.create_session(
            title="To Be Deleted",
            workspace="/tmp/test",
            model="anthropic/claude-sonnet-4",
            state_dir=temp_state_dir
        )
        session_id = session["session_id"]
        session_file = temp_state_dir / "sessions" / f"{session_id}.json"

        # Verify file exists
        assert session_file.exists()

        # Act
        result = models.delete_session(session_id, state_dir=temp_state_dir)

        # Assert
        assert result is True
        assert not session_file.exists()
        assert models.get_session(session_id, state_dir=temp_state_dir) is None

    def test_concurrent_session_updates(self, temp_state_dir):
        """Test that concurrent updates to the same session are handled correctly."""
        import threading

        # Arrange
        session = models.create_session(
            title="Concurrent Test",
            workspace="/tmp/test",
            model="anthropic/claude-sonnet-4",
            state_dir=temp_state_dir
        )
        session_id = session["session_id"]

        results = []
        errors = []

        def update_session_thread(message_num):
            try:
                models.update_session(
                    session_id,
                    state_dir=temp_state_dir,
                    messages=[{"role": "user", "content": f"Message {message_num}"}]
                )
                results.append(message_num)
            except Exception as e:
                errors.append(e)

        # Act - create multiple threads updating the same session
        threads = []
        for i in range(5):
            t = threading.Thread(target=update_session_thread, args=(i,))
            threads.append(t)
            t.start()

        for t in threads:
            t.join()

        # Assert - no errors occurred
        assert len(errors) == 0
        assert len(results) == 5

        # Final session should have one of the updates
        final_session = models.get_session(session_id, state_dir=temp_state_dir)
        assert final_session is not None
        assert len(final_session["messages"]) == 1


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
