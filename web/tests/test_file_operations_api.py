"""
Test suite for file operations API endpoints.

Tests cover:
- GET /api/list - List files in workspace
- GET /api/file - Get file content
"""
import pytest
import json
from pathlib import Path
from typing import Any


class TestFileOperationsApi:
    """Test file operations API endpoints."""

    @pytest.fixture
    def temp_workspace(self, tmp_path):
        """Create a temporary workspace with test files."""
        workspace = tmp_path / "workspace"
        workspace.mkdir(parents=True, exist_ok=True)

        # Create test files
        (workspace / "test.py").write_text("print('hello')")
        (workspace / "config.json").write_text('{"key": "value"}')

        # Create subdirectory with files
        subdir = workspace / "src"
        subdir.mkdir(exist_ok=True)
        (subdir / "main.py").write_text("def main(): pass")

        return workspace

    def test_list_files_root_directory(self, make_request: Any, auth_headers: dict, temp_workspace: Path):
        """GET /api/list should return files in root directory."""
        resp = make_request(
            "GET",
            f"/api/list?session_id=test-session&path={temp_workspace}",
            headers=auth_headers
        )

        assert resp.status_code == 200
        data = resp.json()
        assert "files" in data
        assert isinstance(data["files"], list)

        # Check for expected files
        file_names = [f["name"] for f in data["files"]]
        assert "test.py" in file_names
        assert "config.json" in file_names
        assert "src" in file_names

    def test_list_files_subdirectory(self, make_request: Any, auth_headers: dict, temp_workspace: Path):
        """GET /api/list should return files in subdirectory."""
        subdir = temp_workspace / "src"

        resp = make_request(
            "GET",
            f"/api/list?session_id=test-session&path={subdir}",
            headers=auth_headers
        )

        assert resp.status_code == 200
        data = resp.json()
        assert "files" in data

        file_names = [f["name"] for f in data["files"]]
        assert "main.py" in file_names

    def test_list_files_nonexistent_directory(self, make_request: Any, auth_headers: dict):
        """GET /api/list should return 404 for nonexistent directory."""
        resp = make_request(
            "GET",
            "/api/list?session_id=test-session&path=/nonexistent/path",
            headers=auth_headers
        )

        assert resp.status_code == 404

    def test_get_file_content(self, make_request: Any, auth_headers: dict, temp_workspace: Path):
        """GET /api/file should return file content."""
        resp = make_request(
            "GET",
            f"/api/file?session_id=test-session&path={temp_workspace / 'test.py'}",
            headers=auth_headers
        )

        assert resp.status_code == 200
        data = resp.json()
        assert "content" in data
        assert data["content"] == "print('hello')"

    def test_get_json_file_content(self, make_request: Any, auth_headers: dict, temp_workspace: Path):
        """GET /api/file should return JSON file content."""
        resp = make_request(
            "GET",
            f"/api/file?session_id=test-session&path={temp_workspace / 'config.json'}",
            headers=auth_headers
        )

        assert resp.status_code == 200
        data = resp.json()
        assert "content" in data
        assert json.loads(data["content"]) == {"key": "value"}

    def test_get_file_nonexistent(self, make_request: Any, auth_headers: dict):
        """GET /api/file should return 404 for nonexistent file."""
        resp = make_request(
            "GET",
            "/api/file?session_id=test-session&path=/nonexistent/file.txt",
            headers=auth_headers
        )

        assert resp.status_code == 404

    def test_list_files_returns_file_metadata(self, make_request: Any, auth_headers: dict, temp_workspace: Path):
        """GET /api/list should return file metadata (type, size, modified)."""
        resp = make_request(
            "GET",
            f"/api/list?session_id=test-session&path={temp_workspace}",
            headers=auth_headers
        )

        assert resp.status_code == 200
        data = resp.json()

        # Check metadata for each file
        for file_info in data["files"]:
            assert "name" in file_info
            assert "type" in file_info  # "file" or "directory"
            assert file_info["type"] in ["file", "directory"]

    def test_list_files_distinguishes_directories(self, make_request: Any, auth_headers: dict, temp_workspace: Path):
        """GET /api/list should distinguish directories from files."""
        resp = make_request(
            "GET",
            f"/api/list?session_id=test-session&path={temp_workspace}",
            headers=auth_headers
        )

        assert resp.status_code == 200
        data = resp.json()

        # Find the src directory
        src_info = next((f for f in data["files"] if f["name"] == "src"), None)
        assert src_info is not None
        assert src_info["type"] == "directory"

        # Find a file
        test_py_info = next((f for f in data["files"] if f["name"] == "test.py"), None)
        assert test_py_info is not None
        assert test_py_info["type"] == "file"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
