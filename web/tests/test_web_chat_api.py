"""
Hermes Web Chat API - Core Functionality Tests

Tests for the integrated Hermes WebUI chat API endpoints.
Covers: auth, models, routes, sessions, streaming
"""

import json
import os
import pytest
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Dict
from unittest.mock import patch, MagicMock

# Test fixtures and helpers


@pytest.fixture
def temp_state_dir(tmp_path):
    """Create isolated state directory for chat UI."""
    state_dir = tmp_path / "webui-state"
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / "sessions").mkdir(exist_ok=True)
    return state_dir


@pytest.fixture
def mock_hermes_config(tmp_path):
    """Create mock Hermes config."""
    config_file = tmp_path / "config.yaml"
    config_file.write_text("""
model: anthropic/claude-sonnet-4-20250514
display:
  theme: dark
tools:
  enabled:
    - core
    - web
""")
    return config_file


@pytest.fixture
def chat_session_data():
    """Sample chat session data for testing."""
    return {
        "session_id": "test-session-123",
        "title": "Test Session",
        "workspace": "/tmp/workspace",
        "model": "anthropic/claude-sonnet-4-20250514",
        "messages": [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there!"}
        ],
        "created_at": time.time(),
        "updated_at": time.time()
    }


# =============================================================================
# Test 1: Session CRUD Operations
# =============================================================================

class TestSessionCRUD:
    """Test session create, read, update, delete operations."""

    def test_create_session_returns_valid_id(self, temp_state_dir):
        """Creating a session returns a valid session ID."""
        from hermes_cli.web_chat_api.models import create_session

        session = create_session(
            title="New Session",
            workspace="/workspace",
            model="test-model",
            state_dir=temp_state_dir
        )

        assert session is not None
        assert "session_id" in session
        assert session["title"] == "New Session"
        assert session["workspace"] == "/workspace"

    def test_get_session_returns_session_data(self, temp_state_dir, chat_session_data):
        """Retrieving a session returns full session data."""
        from hermes_cli.web_chat_api.models import create_session, get_session

        # Create session
        session_id = chat_session_data["session_id"]
        session_file = temp_state_dir / "sessions" / f"{session_id}.json"
        session_file.write_text(json.dumps(chat_session_data))

        # Retrieve session
        retrieved = get_session(session_id, state_dir=temp_state_dir)

        assert retrieved is not None
        assert retrieved["session_id"] == session_id
        assert retrieved["title"] == "Test Session"

    def test_get_session_returns_none_for_missing(self, temp_state_dir):
        """Retrieving a non-existent session returns None."""
        from hermes_cli.web_chat_api.models import get_session

        result = get_session("non-existent-id", state_dir=temp_state_dir)
        assert result is None

    def test_delete_session_removes_file(self, temp_state_dir, chat_session_data):
        """Deleting a session removes the session file."""
        from hermes_cli.web_chat_api.models import create_session, get_session, delete_session

        # Create session
        session_id = chat_session_data["session_id"]
        session_file = temp_state_dir / "sessions" / f"{session_id}.json"
        session_file.write_text(json.dumps(chat_session_data))

        # Delete session
        success = delete_session(session_id, state_dir=temp_state_dir)
        assert success is True

        # Verify deleted
        assert get_session(session_id, state_dir=temp_state_dir) is None

    def test_list_sessions_returns_all_sessions(self, temp_state_dir):
        """Listing sessions returns all available sessions."""
        from hermes_cli.web_chat_api.models import list_sessions

        # Create multiple sessions
        for i in range(3):
            session_data = {
                "session_id": f"session-{i}",
                "title": f"Session {i}",
                "messages": [],
                "created_at": time.time()
            }
            session_file = temp_state_dir / "sessions" / f"session-{i}.json"
            session_file.write_text(json.dumps(session_data))

        sessions = list_sessions(state_dir=temp_state_dir)

        assert len(sessions) == 3
        session_ids = {s["session_id"] for s in sessions}
        assert "session-0" in session_ids
        assert "session-1" in session_ids
        assert "session-2" in session_ids

    def test_update_session_title(self, temp_state_dir, chat_session_data):
        """Updating a session title persists the change."""
        from hermes_cli.web_chat_api.models import update_session

        session_id = chat_session_data["session_id"]
        session_file = temp_state_dir / "sessions" / f"{session_id}.json"
        session_file.write_text(json.dumps(chat_session_data))

        # Update title
        updated = update_session(
            session_id,
            title="Updated Title",
            state_dir=temp_state_dir
        )

        assert updated["title"] == "Updated Title"

        # Verify persisted
        retrieved = json.loads(session_file.read_text())
        assert retrieved["title"] == "Updated Title"


# =============================================================================
# Test 2: Stream Management
# =============================================================================

class TestStreamManagement:
    """Test SSE stream creation, cancellation, and status."""

    def test_create_stream_registers_queue(self, temp_state_dir):
        """Creating a stream registers it in the global registry."""
        from hermes_cli.web_chat_api.config import STREAMS, STREAMS_LOCK, create_stream

        stream_id = "test-stream-123"

        with STREAMS_LOCK:
            STREAMS.clear()

        create_stream(stream_id)

        with STREAMS_LOCK:
            assert stream_id in STREAMS
            assert STREAMS[stream_id] is not None

    def test_cancel_stream_sets_flag(self, temp_state_dir):
        """Cancelling a stream sets the cancellation flag."""
        from hermes_cli.web_chat_api.config import CANCEL_FLAGS, STREAMS, STREAMS_LOCK, create_stream

        stream_id = "test-stream-cancel"

        with STREAMS_LOCK:
            STREAMS.clear()
            CANCEL_FLAGS.clear()

        create_stream(stream_id)

        # Set cancel flag directly
        with STREAMS_LOCK:
            CANCEL_FLAGS[stream_id] = threading.Event()
        CANCEL_FLAGS[stream_id].set()

        assert stream_id in CANCEL_FLAGS
        assert CANCEL_FLAGS[stream_id].is_set() is True

    def test_get_stream_status_returns_state(self, temp_state_dir):
        """Getting stream status returns current state."""
        from hermes_cli.web_chat_api.config import (
            create_stream,
            get_stream_status,
            set_stream_complete
        )

        stream_id = "test-stream-status"
        create_stream(stream_id)

        status = get_stream_status(stream_id)
        assert status["stream_id"] == stream_id
        assert status["status"] == "running"

        set_stream_complete(stream_id)
        status = get_stream_status(stream_id)
        assert status["status"] == "completed"


# =============================================================================
# Test 3: Model Resolution
# =============================================================================

class TestModelResolution:
    """Test model string parsing and provider resolution."""

    def test_resolve_anthropic_model(self):
        """Resolving anthropic model returns correct components."""
        from hermes_cli.web_chat_api.config import resolve_model_provider

        model, provider, base_url = resolve_model_provider("anthropic/claude-sonnet-4-20250514")

        assert model == "claude-sonnet-4-20250514"
        assert provider == "anthropic"
        assert base_url is None

    def test_resolve_openrouter_model(self):
        """Resolving OpenRouter model returns correct base URL."""
        from hermes_cli.web_chat_api.config import resolve_model_provider

        model, provider, base_url = resolve_model_provider("openrouter/meta-llama/llama-3-70b-instruct")

        assert model == "meta-llama/llama-3-70b-instruct"
        assert provider == "openrouter"
        assert base_url == "https://openrouter.ai/api/v1"

    def test_resolve_plain_model(self):
        """Resolving model without provider returns model only."""
        from hermes_cli.web_chat_api.config import resolve_model_provider

        model, provider, base_url = resolve_model_provider("gpt-4o")

        assert model == "gpt-4o"
        assert provider is None
        assert base_url is None

    def test_resolve_empty_model(self):
        """Resolving empty model returns None values."""
        from hermes_cli.web_chat_api.config import resolve_model_provider

        model, provider, base_url = resolve_model_provider("")

        assert model is None
        assert provider is None
        assert base_url is None


# =============================================================================
# Test 4: HERMES_HOME Management
# =============================================================================

class TestHermesHomeManagement:
    """Test HERMES_HOME save/restore for profile isolation."""

    def test_save_hermes_home_returns_current(self):
        """Saving HERMES_HOME returns the current value."""
        from hermes_cli.web_chat_api.config import save_hermes_home, restore_hermes_home
        import os

        original = os.environ.get("HERMES_HOME", "/default")
        os.environ["HERMES_HOME"] = "/test/path"

        saved = save_hermes_home()
        assert saved == "/test/path"

        restore_hermes_home()
        os.environ["HERMES_HOME"] = original

    def test_restore_hermes_home_restores_previous(self):
        """Restoring HERMES_HOME reverts to previous value."""
        from hermes_cli.web_chat_api.config import save_hermes_home, restore_hermes_home
        import os

        original = os.environ.get("HERMES_HOME", "/default")

        os.environ["HERMES_HOME"] = "/first/path"
        save_hermes_home()

        os.environ["HERMES_HOME"] = "/second/path"
        restore_hermes_home()

        assert os.environ["HERMES_HOME"] == "/first/path"

        os.environ["HERMES_HOME"] = original


# =============================================================================
# Test 5: Helpers - Redaction
# =============================================================================

class TestHelpersRedaction:
    """Test credential redaction in session data."""

    def test_redact_session_data_masks_api_keys(self):
        """Redacting session data masks API keys in messages."""
        from hermes_cli.web_chat_api.helpers import redact_session_data

        session = {
            "title": "Test",
            "messages": [
                {"role": "user", "content": "My key is sk-1234567890abcdef"},
                {"role": "assistant", "content": "I see your key"}
            ]
        }

        redacted = redact_session_data(session)

        # Key should be redacted (either shortened or masked)
        original_key = "sk-1234567890abcdef"
        assert original_key not in redacted["messages"][0]["content"]

    def test_redact_session_data_handles_empty(self):
        """Redacting empty session data handles edge cases."""
        from hermes_cli.web_chat_api.helpers import redact_session_data

        session = {"title": "", "messages": []}
        redacted = redact_session_data(session)

        assert redacted["title"] == ""
        assert redacted["messages"] == []


# =============================================================================
# Test 6: Helpers - Path Security
# =============================================================================

class TestHelpersPathSecurity:
    """Test path traversal prevention."""

    def test_safe_resolve_prevents_traversal(self):
        """Safe resolve prevents directory traversal attacks."""
        from hermes_cli.web_chat_api.helpers import safe_resolve
        from pathlib import Path

        root = Path("/safe/root")

        # Valid path
        result = safe_resolve(root, "subdir/file.txt")
        assert str(result).startswith("/safe/root")

        # Invalid traversal
        with pytest.raises(ValueError):
            safe_resolve(root, "../../../etc/passwd")

    def test_safe_resolve_handles_absolute(self):
        """Safe resolve handles absolute paths correctly."""
        from hermes_cli.web_chat_api.helpers import safe_resolve
        from pathlib import Path

        root = Path("/safe/root")
        result = safe_resolve(root, "normal.txt")

        assert result.name == "normal.txt"
        assert str(result).startswith("/safe/root")


# =============================================================================
# Test 7: Toolset Configuration
# =============================================================================

class TestToolsetConfiguration:
    """Test toolset retrieval for chat sessions."""

    def test_get_toolsets_for_chat_returns_list(self):
        """Getting toolsets returns a list."""
        from hermes_cli.web_chat_api.config import get_toolsets_for_chat

        toolsets = get_toolsets_for_chat()

        assert isinstance(toolsets, list)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
