"""Tests for the A2A server adapter.

All tests use mocks -- no real servers are started.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helper to check if server deps are available
# ---------------------------------------------------------------------------

def _server_available() -> bool:
    try:
        from a2a_adapter.server import is_server_available
        return is_server_available()
    except ImportError:
        return False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_mock_message(text="Hello agent"):
    """Create a mock A2A message with text parts."""
    part = SimpleNamespace(text=text, root=None)
    return SimpleNamespace(parts=[part])


def _make_mock_context(task_id="task-123", context_id="ctx-456", text="Hello agent"):
    """Create a mock RequestContext."""
    message = _make_mock_message(text)
    return SimpleNamespace(
        task_id=task_id,
        context_id=context_id,
        message=message,
        current_task=None,
    )


# ---------------------------------------------------------------------------
# Server availability
# ---------------------------------------------------------------------------

class TestServerAvailability:
    def test_is_server_available_returns_bool(self):
        """is_server_available returns a boolean."""
        from a2a_adapter.server import is_server_available
        result = is_server_available()
        assert isinstance(result, bool)


# ---------------------------------------------------------------------------
# Agent Card builder
# ---------------------------------------------------------------------------

class TestBuildAgentCard:
    @pytest.mark.skipif(
        not _server_available(),
        reason="a2a-sdk[http-server] not installed",
    )
    def test_card_has_required_fields(self):
        """Agent Card contains name, description, skills, capabilities."""
        from a2a_adapter.server import build_agent_card
        card = build_agent_card(host="localhost", port=9990)
        assert card.name == "Hermes Agent"
        assert "self-improving" in card.description.lower() or "hermes" in card.description.lower()
        assert len(card.skills) >= 1
        assert card.capabilities.streaming is True

    @pytest.mark.skipif(
        not _server_available(),
        reason="a2a-sdk[http-server] not installed",
    )
    def test_card_custom_name(self):
        """Custom name is reflected in the card."""
        from a2a_adapter.server import build_agent_card
        card = build_agent_card(name="My Custom Agent")
        assert card.name == "My Custom Agent"

    @pytest.mark.skipif(
        not _server_available(),
        reason="a2a-sdk[http-server] not installed",
    )
    def test_card_url_uses_host_port(self):
        """Card URL reflects host:port."""
        from a2a_adapter.server import build_agent_card
        card = build_agent_card(host="127.0.0.1", port=8080)
        assert "127.0.0.1:8080" in card.url

    @pytest.mark.skipif(
        not _server_available(),
        reason="a2a-sdk[http-server] not installed",
    )
    def test_card_url_resolves_wildcard(self):
        """0.0.0.0 bind address is resolved to 127.0.0.1 in the Agent Card."""
        from a2a_adapter.server import build_agent_card
        card = build_agent_card(host="0.0.0.0", port=9990)
        assert "0.0.0.0" not in card.url
        assert "127.0.0.1:9990" in card.url

    @pytest.mark.skipif(
        not _server_available(),
        reason="a2a-sdk[http-server] not installed",
    )
    def test_card_skills_have_ids(self):
        """Each skill has an id, name, and description."""
        from a2a_adapter.server import build_agent_card
        card = build_agent_card()
        for skill in card.skills:
            assert skill.id
            assert skill.name
            assert skill.description


# ---------------------------------------------------------------------------
# Session Manager
# ---------------------------------------------------------------------------

class TestSessionManager:
    def test_create_session(self):
        """Creating a session returns a SessionState with an agent."""
        from a2a_adapter.session import SessionManager
        with patch("a2a_adapter.session.SessionManager._make_agent") as mock_make:
            mock_make.return_value = MagicMock()
            mgr = SessionManager()
            state = mgr.create_session(cwd="/tmp")
            assert state.session_id
            assert state.agent is not None
            assert state.cwd == "/tmp"

    def test_get_session(self):
        """Can retrieve a session by ID."""
        from a2a_adapter.session import SessionManager
        with patch("a2a_adapter.session.SessionManager._make_agent") as mock_make:
            mock_make.return_value = MagicMock()
            mgr = SessionManager()
            state = mgr.create_session()
            retrieved = mgr.get_session(state.session_id)
            assert retrieved is state

    def test_get_nonexistent_session(self):
        """Getting a nonexistent session returns None."""
        from a2a_adapter.session import SessionManager
        mgr = SessionManager()
        assert mgr.get_session("nonexistent") is None

    def test_get_or_create_session(self):
        """get_or_create creates on first call, returns same on second."""
        from a2a_adapter.session import SessionManager
        with patch("a2a_adapter.session.SessionManager._make_agent") as mock_make:
            mock_make.return_value = MagicMock()
            mgr = SessionManager()
            s1 = mgr.get_or_create_session("task-1")
            s2 = mgr.get_or_create_session("task-1")
            assert s1.session_id == s2.session_id
            assert mock_make.call_count == 1  # Only created once

    def test_cancel_session(self):
        """Cancelling a session sets the cancel event."""
        from a2a_adapter.session import SessionManager
        with patch("a2a_adapter.session.SessionManager._make_agent") as mock_make:
            mock_make.return_value = MagicMock()
            mgr = SessionManager()
            state = mgr.create_session()
            assert not state.cancel_event.is_set()
            result = mgr.cancel_session(state.session_id)
            assert result is True
            assert state.cancel_event.is_set()

    def test_cancel_nonexistent(self):
        """Cancelling nonexistent session returns False."""
        from a2a_adapter.session import SessionManager
        mgr = SessionManager()
        assert mgr.cancel_session("nonexistent") is False

    def test_list_sessions(self):
        """list_sessions returns info dicts."""
        from a2a_adapter.session import SessionManager
        with patch("a2a_adapter.session.SessionManager._make_agent") as mock_make:
            mock_make.return_value = MagicMock()
            mgr = SessionManager()
            mgr.create_session(cwd="/tmp")
            mgr.create_session(cwd="/home")
            sessions = mgr.list_sessions()
            assert len(sessions) == 2
            assert all("session_id" in s for s in sessions)

    def test_get_or_create_no_dual_storage(self):
        """get_or_create_session stores exactly one entry per task (no duplicates)."""
        from a2a_adapter.session import SessionManager
        with patch("a2a_adapter.session.SessionManager._make_agent") as mock_make:
            mock_make.return_value = MagicMock()
            mgr = SessionManager()
            mgr.get_or_create_session("task-123")
            sessions = mgr.list_sessions()
            assert len(sessions) == 1, f"Expected 1 session, got {len(sessions)}"

    def test_session_manager_accepts_toolset(self):
        """SessionManager passes custom toolset to _make_agent."""
        from a2a_adapter.session import SessionManager
        with patch("a2a_adapter.session.SessionManager._make_agent") as mock_make:
            mock_make.return_value = MagicMock()
            mgr = SessionManager(toolset="hermes-acp")
            assert mgr._toolset == "hermes-acp"


# ---------------------------------------------------------------------------
# HermesAgentExecutor
# ---------------------------------------------------------------------------

class TestHermesAgentExecutor:
    @pytest.mark.skipif(
        not _server_available(),
        reason="a2a-sdk[http-server] not installed",
    )
    def test_extract_text_from_message(self):
        """Text extraction works from protobuf-style parts."""
        from a2a_adapter.server import HermesAgentExecutor
        executor = HermesAgentExecutor()
        context = _make_mock_context(text="Hello world")
        result = executor._extract_text(context)
        assert result == "Hello world"

    @pytest.mark.skipif(
        not _server_available(),
        reason="a2a-sdk[http-server] not installed",
    )
    def test_extract_text_empty_message(self):
        """Empty message returns empty string."""
        from a2a_adapter.server import HermesAgentExecutor
        executor = HermesAgentExecutor()
        context = SimpleNamespace(
            task_id="t1", context_id="c1", message=None, current_task=None,
        )
        result = executor._extract_text(context)
        assert result == ""

    @pytest.mark.skipif(
        not _server_available(),
        reason="a2a-sdk[http-server] not installed",
    )
    def test_extract_text_pydantic_parts(self):
        """Text extraction works from pydantic-style parts (root.text)."""
        from a2a_adapter.server import HermesAgentExecutor
        executor = HermesAgentExecutor()
        part = SimpleNamespace(text=None, root=SimpleNamespace(text="Pydantic text"))
        message = SimpleNamespace(parts=[part])
        context = SimpleNamespace(
            task_id="t1", context_id="c1", message=message, current_task=None,
        )
        result = executor._extract_text(context)
        assert result == "Pydantic text"


# ---------------------------------------------------------------------------
# Application builder
# ---------------------------------------------------------------------------

class TestBuildApplication:
    @pytest.mark.skipif(
        not _server_available(),
        reason="a2a-sdk[http-server] not installed",
    )
    def test_build_application_returns_app(self):
        """build_application returns an A2AStarletteApplication."""
        with patch("a2a_adapter.server.SessionManager._make_agent") as mock_make:
            mock_make.return_value = MagicMock()
            from a2a_adapter.server import build_application
            app = build_application(host="localhost", port=9990)
            assert app is not None


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

class TestEntryPoint:
    def test_import_entry(self):
        """Entry module is importable."""
        import a2a_adapter.entry
        assert hasattr(a2a_adapter.entry, "main")

    def test_import_main(self):
        """__main__ module is importable."""
        import a2a_adapter.__main__
