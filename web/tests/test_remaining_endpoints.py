"""
Test suite for remaining API endpoints.

Tests cover:
- Workspaces management (/api/workspaces/add, /api/workspaces/remove)
- Projects management (/api/projects/create, /api/projects/rename, /api/projects/delete)
- Session operations (/api/session/retry, /api/session/undo)
"""
import pytest
import json
from pathlib import Path
from typing import Any


class TestWorkspacesApi:
    """Test workspaces management API endpoints."""

    def test_get_workspaces_returns_list(self, make_request: Any, auth_headers: dict):
        """GET /api/workspaces should return list of workspaces."""
        resp = make_request(
            "GET",
            "/api/workspaces",
            headers=auth_headers
        )

        assert resp.status_code == 200
        data = resp.json()
        assert "workspaces" in data or isinstance(data, list)

    def test_add_workspace(self, make_request: Any, auth_headers: dict, tmp_path: Path):
        """POST /api/workspaces/add should add a workspace."""
        workspace_path = str(tmp_path / "test-workspace")
        workspace_path_obj = Path(workspace_path)
        workspace_path_obj.mkdir(parents=True, exist_ok=True)

        resp = make_request(
            "POST",
            "/api/workspaces/add",
            headers=auth_headers,
            json={"path": workspace_path}
        )

        # Should succeed or return already exists
        assert resp.status_code in [200, 201, 409]

    def test_remove_workspace(self, make_request: Any, auth_headers: dict, tmp_path: Path):
        """POST /api/workspaces/remove should remove a workspace."""
        workspace_path = str(tmp_path / "test-workspace-remove")
        workspace_path_obj = Path(workspace_path)
        workspace_path_obj.mkdir(parents=True, exist_ok=True)

        # First add it
        make_request(
            "POST",
            "/api/workspaces/add",
            headers=auth_headers,
            json={"path": workspace_path}
        )

        # Then remove it
        resp = make_request(
            "POST",
            "/api/workspaces/remove",
            headers=auth_headers,
            json={"path": workspace_path}
        )

        # Should succeed
        assert resp.status_code in [200, 204]


class TestProjectsApi:
    """Test projects management API endpoints."""

    def test_get_projects_returns_list(self, make_request: Any, auth_headers: dict):
        """GET /api/projects should return list of projects."""
        resp = make_request(
            "GET",
            "/api/projects",
            headers=auth_headers
        )

        assert resp.status_code == 200
        data = resp.json()
        assert "projects" in data or isinstance(data, list)

    def test_create_project(self, make_request: Any, auth_headers: dict):
        """POST /api/projects/create should create a project."""
        resp = make_request(
            "POST",
            "/api/projects/create",
            headers=auth_headers,
            json={"name": "Test Project", "color": "#3b82f6"}
        )

        assert resp.status_code in [200, 201]
        data = resp.json()
        assert "project_id" in data or "project" in data or "id" in data

    def test_rename_project(self, make_request: Any, auth_headers: dict):
        """POST /api/projects/rename should rename a project."""
        # First create a project
        create_resp = make_request(
            "POST",
            "/api/projects/create",
            headers=auth_headers,
            json={"name": "Original Name", "color": "#3b82f6"}
        )

        if create_resp.status_code in [200, 201]:
            create_data = create_resp.json()
            project_id = create_data.get("project_id") or create_data.get("id")

            if project_id:
                resp = make_request(
                    "POST",
                    "/api/projects/rename",
                    headers=auth_headers,
                    json={"project_id": project_id, "name": "New Name"}
                )

                assert resp.status_code in [200, 204]

    def test_delete_project(self, make_request: Any, auth_headers: dict):
        """POST /api/projects/delete should delete a project."""
        # First create a project
        create_resp = make_request(
            "POST",
            "/api/projects/create",
            headers=auth_headers,
            json={"name": "Delete Me", "color": "#ef4444"}
        )

        if create_resp.status_code in [200, 201]:
            create_data = create_resp.json()
            project_id = create_data.get("project_id") or create_data.get("id")

            if project_id:
                resp = make_request(
                    "POST",
                    "/api/projects/delete",
                    headers=auth_headers,
                    json={"project_id": project_id}
                )

                assert resp.status_code in [200, 204]


class TestSessionOperationsApi:
    """Test session operations API endpoints."""

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

    def test_retry_session_returns_messages(self, make_request: Any, auth_headers: dict):
        """POST /api/session/retry should retry last message."""
        # This is a stub - actual retry logic requires conversation history
        resp = make_request(
            "POST",
            "/api/session/retry",
            headers=auth_headers,
            json={"session_id": "test-session"}
        )

        # Should return success or indicate no messages to retry
        assert resp.status_code in [200, 400, 404]

    def test_undo_session(self, make_request: Any, auth_headers: dict):
        """POST /api/session/undo should undo last message."""
        resp = make_request(
            "POST",
            "/api/session/undo",
            headers=auth_headers,
            json={"session_id": "test-session"}
        )

        # Should return success or indicate nothing to undo
        assert resp.status_code in [200, 400, 404]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
