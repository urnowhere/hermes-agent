"""
Test suite for low-priority API endpoints.

Tests cover:
- Session import/export
- Profiles management
- Auth endpoints
- Memory endpoints
- Personalities
"""
import pytest
import json
from pathlib import Path
from typing import Any


class TestSessionImportExport:
    """Test session import/export API endpoints."""

    @pytest.fixture
    def session_token(self) -> str:
        """Return the session token from web_server."""
        from hermes_cli.web_server import _SESSION_TOKEN
        return _SESSION_TOKEN

    @pytest.fixture
    def auth_headers(self, session_token: str) -> dict:
        """Return authentication headers."""
        return {"Authorization": f"Bearer {session_token}"}

    @pytest.fixture
    def make_request(self, session_token: str):
        """Make HTTP requests to the test server."""
        from starlette.testclient import TestClient
        from hermes_cli.web_server import app

        def _request(method: str, path: str, headers: dict = None, json: dict = None):
            with TestClient(app) as client:
                if headers is None:
                    headers = {"Authorization": f"Bearer {session_token}"}
                if json:
                    return client.request(method, path, json=json, headers=headers)
                return client.request(method, path, headers=headers)

        return _request

    def test_export_session_returns_data(self, make_request: Any, auth_headers: dict):
        """GET /api/session/export should return session data."""
        resp = make_request(
            "GET",
            "/api/session/export?session_id=test-session",
            headers=auth_headers
        )

        # Should return session data or 404 if not found
        assert resp.status_code in [200, 404]

    def test_import_cli_session(self, make_request: Any, auth_headers: dict):
        """POST /api/session/import_cli should import a session."""
        resp = make_request(
            "POST",
            "/api/session/import_cli",
            headers=auth_headers,
            json={"session_id": "test-session"}
        )

        # Should succeed or indicate session not found
        assert resp.status_code in [200, 404]


class TestProfilesApi:
    """Test profiles management API endpoints."""

    @pytest.fixture
    def session_token(self) -> str:
        """Return the session token from web_server."""
        from hermes_cli.web_server import _SESSION_TOKEN
        return _SESSION_TOKEN

    @pytest.fixture
    def auth_headers(self, session_token: str) -> dict:
        """Return authentication headers."""
        return {"Authorization": f"Bearer {session_token}"}

    @pytest.fixture
    def make_request(self, session_token: str):
        """Make HTTP requests to the test server."""
        from starlette.testclient import TestClient
        from hermes_cli.web_server import app

        def _request(method: str, path: str, headers: dict = None, json: dict = None):
            with TestClient(app) as client:
                if headers is None:
                    headers = {"Authorization": f"Bearer {session_token}"}
                if json:
                    return client.request(method, path, json=json, headers=headers)
                return client.request(method, path, headers=headers)

        return _request

    def test_get_profiles_returns_list(self, make_request: Any, auth_headers: dict):
        """GET /api/profiles should return list of profiles."""
        resp = make_request(
            "GET",
            "/api/profiles",
            headers=auth_headers
        )

        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, (list, dict))

    def test_profile_create(self, make_request: Any, auth_headers: dict):
        """POST /api/profile/create should create a profile."""
        resp = make_request(
            "POST",
            "/api/profile/create",
            headers=auth_headers,
            json={"name": "Test Profile", "config": {}}
        )

        # Should succeed or already exists
        assert resp.status_code in [200, 201, 409]

    def test_profile_switch(self, make_request: Any, auth_headers: dict):
        """POST /api/profile/switch should switch active profile."""
        resp = make_request(
            "POST",
            "/api/profile/switch",
            headers=auth_headers,
            json={"name": "default"}
        )

        # Should succeed or profile not found
        assert resp.status_code in [200, 404]

    def test_profile_delete(self, make_request: Any, auth_headers: dict):
        """POST /api/profile/delete should delete a profile."""
        resp = make_request(
            "POST",
            "/api/profile/delete",
            headers=auth_headers,
            json={"name": "Test Profile"}
        )

        # Should succeed or profile not found
        assert resp.status_code in [200, 404]


class TestAuthApi:
    """Test auth API endpoints."""

    @pytest.fixture
    def session_token(self) -> str:
        """Return the session token from web_server."""
        from hermes_cli.web_server import _SESSION_TOKEN
        return _SESSION_TOKEN

    @pytest.fixture
    def auth_headers(self, session_token: str) -> dict:
        """Return authentication headers."""
        return {"Authorization": f"Bearer {session_token}"}

    @pytest.fixture
    def make_request(self, session_token: str):
        """Make HTTP requests to the test server."""
        from starlette.testclient import TestClient
        from hermes_cli.web_server import app

        def _request(method: str, path: str, headers: dict = None, json: dict = None):
            with TestClient(app) as client:
                if headers is None:
                    headers = {"Authorization": f"Bearer {session_token}"}
                if json:
                    return client.request(method, path, json=json, headers=headers)
                return client.request(method, path, headers=headers)

        return _request

    def test_auth_status(self, make_request: Any, auth_headers: dict):
        """GET /api/auth/status should return auth status."""
        resp = make_request(
            "GET",
            "/api/auth/status",
            headers=auth_headers
        )

        assert resp.status_code == 200
        data = resp.json()
        assert "authenticated" in data or "status" in data

    def test_logout(self, make_request: Any, auth_headers: dict):
        """POST /api/auth/logout should logout user."""
        resp = make_request(
            "POST",
            "/api/auth/logout",
            headers=auth_headers,
            json={}
        )

        assert resp.status_code in [200, 204]


class TestMemoryApi:
    """Test memory API endpoints."""

    @pytest.fixture
    def session_token(self) -> str:
        """Return the session token from web_server."""
        from hermes_cli.web_server import _SESSION_TOKEN
        return _SESSION_TOKEN

    @pytest.fixture
    def auth_headers(self, session_token: str) -> dict:
        """Return authentication headers."""
        return {"Authorization": f"Bearer {session_token}"}

    @pytest.fixture
    def make_request(self, session_token: str):
        """Make HTTP requests to the test server."""
        from starlette.testclient import TestClient
        from hermes_cli.web_server import app

        def _request(method: str, path: str, headers: dict = None, json: dict = None):
            with TestClient(app) as client:
                if headers is None:
                    headers = {"Authorization": f"Bearer {session_token}"}
                if json:
                    return client.request(method, path, json=json, headers=headers)
                return client.request(method, path, headers=headers)

        return _request

    def test_get_memory(self, make_request: Any, auth_headers: dict):
        """GET /api/memory should return memory content."""
        resp = make_request(
            "GET",
            "/api/memory",
            headers=auth_headers
        )

        # Should return memory data or empty
        assert resp.status_code == 200

    def test_memory_write(self, make_request: Any, auth_headers: dict):
        """POST /api/memory/write should write memory."""
        resp = make_request(
            "POST",
            "/api/memory/write",
            headers=auth_headers,
            json={"section": "memory", "content": "test content"}
        )

        # Should succeed
        assert resp.status_code in [200, 204]


class TestPersonalitiesApi:
    """Test personalities API endpoints."""

    @pytest.fixture
    def session_token(self) -> str:
        """Return the session token from web_server."""
        from hermes_cli.web_server import _SESSION_TOKEN
        return _SESSION_TOKEN

    @pytest.fixture
    def auth_headers(self, session_token: str) -> dict:
        """Return authentication headers."""
        return {"Authorization": f"Bearer {session_token}"}

    @pytest.fixture
    def make_request(self, session_token: str):
        """Make HTTP requests to the test server."""
        from starlette.testclient import TestClient
        from hermes_cli.web_server import app

        def _request(method: str, path: str, headers: dict = None, json: dict = None):
            with TestClient(app) as client:
                if headers is None:
                    headers = {"Authorization": f"Bearer {session_token}"}
                if json:
                    return client.request(method, path, json=json, headers=headers)
                return client.request(method, path, headers=headers)

        return _request

    def test_get_personalities(self, make_request: Any, auth_headers: dict):
        """GET /api/personalities should return list of personalities."""
        resp = make_request(
            "GET",
            "/api/personalities",
            headers=auth_headers
        )

        # Should return list or empty
        assert resp.status_code == 200

    def test_set_personality(self, make_request: Any, auth_headers: dict):
        """POST /api/personality/set should set personality."""
        resp = make_request(
            "POST",
            "/api/personality/set",
            headers=auth_headers,
            json={"session_id": "test", "name": "assistant"}
        )

        # Should succeed or session not found
        assert resp.status_code in [200, 404]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
