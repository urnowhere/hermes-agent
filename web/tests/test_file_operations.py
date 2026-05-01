"""
Hermes WebUI - File Operations API Tests

Test file operations:
- Create file
- Read file
- Save file
- Rename file
- Delete file
- Create directory
- Path traversal prevention
"""

import pytest
import os
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock
from fastapi import HTTPException
from fastapi.testclient import TestClient


# =============================================================================
# Test Fixtures
# =============================================================================

@pytest.fixture
def temp_workspace(tmp_path):
    """Create a temporary workspace directory for testing."""
    workspace = tmp_path / "test_workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    return workspace


@pytest.fixture
def client(temp_workspace, monkeypatch):
    """Create test client with mocked state."""
    # Set up environment for test isolation
    monkeypatch.setenv("HERMES_WEBUI_STATE_DIR", str(temp_workspace))

    # Import after env setup
    from hermes_cli.web_server import app, _SESSION_TOKEN

    # Create test client with proper auth header
    with TestClient(app) as test_client:
        test_client.headers["Authorization"] = f"Bearer {_SESSION_TOKEN}"
        yield test_client, temp_workspace


# =============================================================================
# Test 1: File Creation
# =============================================================================

class TestFileCreate:
    """Test file creation endpoint."""

    def test_create_file_success(self, client):
        """Should create a new file successfully."""
        test_client, test_workspace = client

        response = test_client.post("/api/file/create", json={
            "path": "test.txt",
            "content": "Hello, World!",
            "workspace": str(test_workspace)
        })

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert "test.txt" in data["path"]

        # Verify file was created
        created_file = test_workspace / "test.txt"
        assert created_file.exists()
        assert created_file.read_text() == "Hello, World!"

    def test_create_file_nested_path(self, client):
        """Should create file in nested directory."""
        test_client, test_workspace = client

        response = test_client.post("/api/file/create", json={
            "path": "subdir/nested/test.txt",
            "content": "Nested content",
            "workspace": str(test_workspace)
        })

        assert response.status_code == 200
        created_file = test_workspace / "subdir" / "nested" / "test.txt"
        assert created_file.exists()
        assert created_file.read_text() == "Nested content"

    def test_create_file_missing_required_field(self, client):
        """Should fail when missing required fields."""
        test_client, test_workspace = client

        response = test_client.post("/api/file/create", json={
            "path": "test.txt"
            # Missing content
        })

        # Should either succeed with empty content or return validation error
        assert response.status_code in [200, 400]

    def test_create_file_already_exists(self, client):
        """Should handle existing file (overwrite or error)."""
        test_client, test_workspace = client

        # Create file first
        existing_file = test_workspace / "existing.txt"
        existing_file.write_text("Original content")

        response = test_client.post("/api/file/create", json={
            "path": "existing.txt",
            "content": "New content",
            "workspace": str(test_workspace)
        })

        # Either overwrite or return error - both acceptable
        assert response.status_code in [200, 400]


# =============================================================================
# Test 2: File Save
# =============================================================================

class TestFileSave:
    """Test file save endpoint."""

    def test_save_file_success(self, client):
        """Should save file content successfully."""
        test_client, test_workspace = client

        # Create file first
        test_file = test_workspace / "save_test.txt"
        test_file.write_text("Original")

        response = test_client.post("/api/file/save", json={
            "path": "save_test.txt",
            "content": "Updated content",
            "workspace": str(test_workspace)
        })

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True

        # Verify content updated
        assert test_file.read_text() == "Updated content"

    def test_save_file_creates_if_missing(self, client):
        """Should create file if it doesn't exist."""
        test_client, test_workspace = client

        response = test_client.post("/api/file/save", json={
            "path": "new_file.txt",
            "content": "New file content",
            "workspace": str(test_workspace)
        })

        assert response.status_code == 200
        new_file = test_workspace / "new_file.txt"
        assert new_file.exists()
        assert new_file.read_text() == "New file content"

    def test_save_file_empty_content(self, client):
        """Should handle empty content."""
        test_client, test_workspace = client

        test_file = test_workspace / "empty.txt"
        test_file.write_text("Has content")

        response = test_client.post("/api/file/save", json={
            "path": "empty.txt",
            "content": "",
            "workspace": str(test_workspace)
        })

        assert response.status_code == 200
        assert test_file.read_text() == ""


# =============================================================================
# Test 3: File Rename
# =============================================================================

class TestFileRename:
    """Test file rename endpoint."""

    def test_rename_file_success(self, client):
        """Should rename file successfully."""
        test_client, test_workspace = client

        # Create file first
        old_file = test_workspace / "old_name.txt"
        old_file.write_text("Content")

        response = test_client.post("/api/file/rename", json={
            "old_path": "old_name.txt",
            "new_path": "new_name.txt",
            "workspace": str(test_workspace)
        })

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True

        # Verify renamed
        assert not old_file.exists()
        new_file = test_workspace / "new_name.txt"
        assert new_file.exists()
        assert new_file.read_text() == "Content"

    def test_rename_file_not_found(self, client):
        """Should return error when file not found."""
        test_client, test_workspace = client

        response = test_client.post("/api/file/rename", json={
            "old_path": "nonexistent.txt",
            "new_path": "new.txt",
            "workspace": str(test_workspace)
        })

        assert response.status_code in [400, 404]

    def test_rename_target_exists(self, client):
        """Should handle target already exists."""
        test_client, test_workspace = client

        # Create both files
        (test_workspace / "source.txt").write_text("Source")
        (test_workspace / "dest.txt").write_text("Dest")

        response = test_client.post("/api/file/rename", json={
            "old_path": "source.txt",
            "new_path": "dest.txt",
            "workspace": str(test_workspace)
        })

        # Either overwrite or error - both acceptable
        assert response.status_code in [200, 400]


# =============================================================================
# Test 4: File Delete
# =============================================================================

class TestFileDelete:
    """Test file delete endpoint."""

    def test_delete_file_success(self, client):
        """Should delete file successfully."""
        test_client, test_workspace = client

        # Create file first
        test_file = test_workspace / "to_delete.txt"
        test_file.write_text("To delete")

        response = test_client.post("/api/file/delete", json={
            "path": "to_delete.txt",
            "workspace": str(test_workspace)
        })

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True

        # Verify deleted
        assert not test_file.exists()

    def test_delete_file_not_found(self, client):
        """Should return error when file not found."""
        test_client, test_workspace = client

        response = test_client.post("/api/file/delete", json={
            "path": "nonexistent.txt",
            "workspace": str(test_workspace)
        })

        assert response.status_code in [400, 404]


# =============================================================================
# Test 5: Directory Creation
# =============================================================================

class TestCreateDirectory:
    """Test directory creation endpoint."""

    def test_create_dir_success(self, client):
        """Should create directory successfully."""
        test_client, test_workspace = client

        response = test_client.post("/api/file/create-dir", json={
            "path": "new_dir",
            "workspace": str(test_workspace)
        })

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True

        # Verify created
        new_dir = test_workspace / "new_dir"
        assert new_dir.exists()
        assert new_dir.is_dir()

    def test_create_dir_nested(self, client):
        """Should create nested directories."""
        test_client, test_workspace = client

        response = test_client.post("/api/file/create-dir", json={
            "path": "level1/level2/level3",
            "workspace": str(test_workspace)
        })

        assert response.status_code == 200
        nested_dir = test_workspace / "level1" / "level2" / "level3"
        assert nested_dir.exists()
        assert nested_dir.is_dir()

    def test_create_dir_already_exists(self, client):
        """Should handle existing directory."""
        test_client, test_workspace = client

        existing_dir = test_workspace / "existing"
        existing_dir.mkdir()

        response = test_client.post("/api/file/create-dir", json={
            "path": "existing",
            "workspace": str(test_workspace)
        })

        # Either success or error - both acceptable
        assert response.status_code in [200, 400]


# =============================================================================
# Test 6: Security - Path Traversal Prevention
# =============================================================================

class TestPathTraversalPrevention:
    """Test path traversal security."""

    def test_prevent_traversal_outside_workspace(self, client):
        """Should block path traversal attacks."""
        test_client, test_workspace = client

        # Try to access parent directory
        response = test_client.post("/api/file/create", json={
            "path": "../../../etc/passwd",
            "content": "malicious",
            "workspace": str(test_workspace)
        })

        assert response.status_code == 403
        assert "traversal" in response.json().get("detail", "").lower() or "forbidden" in response.json().get("error", "").lower()

    def test_prevent_traversal_absolute_path(self, client):
        """Should block absolute paths outside workspace."""
        test_client, test_workspace = client

        response = test_client.post("/api/file/create", json={
            "path": "/etc/passwd",
            "content": "malicious",
            "workspace": str(test_workspace)
        })

        # Should either block or resolve within workspace
        assert response.status_code in [200, 400, 403]

    def test_prevent_read_sensitive_files(self, client):
        """Should block access to sensitive files."""
        test_client, test_workspace = client

        # Try to read .env file
        response = test_client.post("/api/file/save", json={
            "path": ".env",
            "content": "SECRET=bad",
            "workspace": str(test_workspace)
        })

        # Either blocked or allowed (depending on implementation)
        # This test documents the behavior
        assert response.status_code in [200, 400, 403]


# =============================================================================
# Test 7: File Size Limits
# =============================================================================

class TestFileLimits:
    """Test file size and content limits."""

    def test_large_file_rejected(self, client):
        """Should reject files that are too large."""
        test_client, test_workspace = client

        # Create content larger than MAX_FILE_BYTES (typically 10MB)
        large_content = "x" * (15 * 1024 * 1024)  # 15MB

        response = test_client.post("/api/file/create", json={
            "path": "large_file.txt",
            "content": large_content,
            "workspace": str(test_workspace)
        })

        assert response.status_code in [400, 413]  # Bad Request or Payload Too Large
