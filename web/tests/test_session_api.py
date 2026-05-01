"""
Test suite for session API endpoints.

Tests cover:
- GET /api/session - Get session details
- POST /api/session/update - Update session model/workspace
- POST /api/session/delete - Delete session
- POST /api/session/pin - Pin/unpin session
- POST /api/session/archive - Archive/unarchive session
"""
import pytest
import json
import time
from pathlib import Path
from typing import Any, Dict


class TestSessionApi:
    """Test session management API endpoints."""

    @pytest.fixture
    def temp_state_dir(self, tmp_path):
        """Create isolated state directory for chat UI."""
        state_dir = tmp_path / "webui-state"
        state_dir.mkdir(parents=True, exist_ok=True)
        (state_dir / "sessions").mkdir(exist_ok=True)
        return state_dir

    @pytest.fixture
    def test_session(self, temp_state_dir, session_token: str, make_request) -> dict:
        """Create a test session via API."""
        resp = make_request(
            "POST",
            "/api/session/new",
            json={"model": "test-model", "workspace": "/tmp/workspace"}
        )
        return resp.json().get("session", resp.json())
    def auth_headers(self, session_token: str) -> dict:
        """Return authentication headers."""
        return {"Authorization": f"Bearer {session_token}"}

    def test_get_session_details(self, make_request: Any, auth_headers: dict, test_session: dict):
        """GET /api/session?session_id=X should return session details."""
        session_id = test_session["session_id"]

        resp = make_request(
            "GET",
            f"/api/session?session_id={session_id}",
            headers=auth_headers
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["session_id"] == session_id
        assert "messages" in data
        assert "title" in data
        assert "model" in data
        assert "workspace" in data
        assert "created_at" in data

    def test_get_session_not_found(self, make_request: Any, auth_headers: dict):
        """GET /api/session with invalid session_id should return 404."""
        resp = make_request(
            "GET",
            "/api/session?session_id=nonexistent-12345",
            headers=auth_headers
        )

        # Should return 404 for non-existent session
        assert resp.status_code == 404

    def test_update_session_model(self, make_request: Any, auth_headers: dict, test_session: dict):
        """POST /api/session/update should update session model."""
        session_id = test_session["session_id"]

        resp = make_request(
            "POST",
            "/api/session/update",
            headers=auth_headers,
            json={
                "session_id": session_id,
                "model": "qwen-max",
                "workspace": "/new/workspace"
            }
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["model"] == "qwen-max"
        assert data["workspace"] == "/new/workspace"

    def test_update_session_missing_fields(self, make_request: Any, auth_headers: dict):
        """POST /api/session/update with missing session_id should return 422 (validation error)."""
        resp = make_request(
            "POST",
            "/api/session/update",
            headers=auth_headers,
            json={"model": "test"}
        )

        # Pydantic validation returns 422 for missing required field
        assert resp.status_code == 422

    def test_delete_session(self, make_request: Any, auth_headers: dict, test_session: dict):
        """POST /api/session/delete should delete a session."""
        session_id = test_session["session_id"]

        # Delete
        resp = make_request(
            "POST",
            "/api/session/delete",
            headers=auth_headers,
            json={"session_id": session_id}
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["session_id"] == session_id

    def test_pin_session(self, make_request: Any, auth_headers: dict, test_session: dict):
        """POST /api/session/pin should pin/unpin a session."""
        session_id = test_session["session_id"]

        # Pin
        resp = make_request(
            "POST",
            "/api/session/pin",
            headers=auth_headers,
            json={"session_id": session_id, "pinned": True}
        )

        assert resp.status_code == 200
        data = resp.json()
        assert "pinned" in data
        assert data["pinned"] is True
        assert data["session_id"] == session_id

        # Unpin
        resp = make_request(
            "POST",
            "/api/session/pin",
            headers=auth_headers,
            json={"session_id": session_id, "pinned": False}
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["pinned"] is False

    def test_archive_session(self, make_request: Any, auth_headers: dict, test_session: dict):
        """POST /api/session/archive should archive/unarchive a session."""
        session_id = test_session["session_id"]

        # Archive
        resp = make_request(
            "POST",
            "/api/session/archive",
            headers=auth_headers,
            json={"session_id": session_id, "archived": True}
        )

        assert resp.status_code == 200
        data = resp.json()
        assert "archived" in data
        assert data["archived"] is True
        assert data["session_id"] == session_id

        # Unarchive
        resp = make_request(
            "POST",
            "/api/session/archive",
            headers=auth_headers,
            json={"session_id": session_id, "archived": False}
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["archived"] is False

    def test_session_status(self, make_request: Any, auth_headers: dict, test_session: dict):
        """GET /api/session/status should return session status."""
        session_id = test_session["session_id"]

        resp = make_request(
            "GET",
            f"/api/session/status?session_id={session_id}",
            headers=auth_headers
        )

        assert resp.status_code == 200
        data = resp.json()
        assert "session_id" in data
        assert "active" in data
        assert "message_count" in data


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
