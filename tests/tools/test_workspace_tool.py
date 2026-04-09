from __future__ import annotations

import json
from pathlib import Path

from tests.conftest import make_workspace_config


def _config(tmp_path: Path) -> dict:
    return make_workspace_config(tmp_path)


class TestWorkspaceTool:
    def test_status_reports_workspace_roots(self, tmp_path, monkeypatch):
        from tools.workspace_tool import workspace_tool

        monkeypatch.setattr("tools.workspace_tool.load_config", lambda: _config(tmp_path))

        result = json.loads(workspace_tool(action="status"))

        assert result["success"] is True
        assert result["workspace_root"].endswith("workspace")
        assert result["knowledgebase_root"].endswith("knowledgebase")

    def test_index_search_and_retrieve_round_trip(self, tmp_path, monkeypatch):
        from tools.workspace_tool import workspace_tool

        cfg = _config(tmp_path)
        workspace = Path(cfg["workspace"]["path"])
        (workspace / "docs").mkdir(parents=True)
        (workspace / "docs" / "deploy.md").write_text("deployment checklist and rollback plan\n", encoding="utf-8")
        monkeypatch.setattr("tools.workspace_tool.load_config", lambda: cfg)

        indexed = json.loads(workspace_tool(action="index"))
        assert indexed["success"] is True
        assert indexed["file_count"] == 1
        assert indexed["chunk_count"] >= 1

        searched = json.loads(workspace_tool(action="search", query="deployment"))
        assert searched["success"] is True
        assert searched["count"] == 1
        assert searched["matches"][0]["relative_path"] == "docs/deploy.md"

        retrieved = json.loads(workspace_tool(action="retrieve", query="rollback plan"))
        assert retrieved["success"] is True
        assert retrieved["count"] >= 1
        assert retrieved["results"][0]["relative_path"] == "docs/deploy.md"

    def test_delete_removes_file_from_index(self, tmp_path, monkeypatch):
        from tools.workspace_tool import workspace_tool

        cfg = _config(tmp_path)
        workspace = Path(cfg["workspace"]["path"])
        (workspace / "docs").mkdir(parents=True)
        (workspace / "docs" / "deploy.md").write_text("deployment checklist and rollback plan\n", encoding="utf-8")
        monkeypatch.setattr("tools.workspace_tool.load_config", lambda: cfg)

        # Index the file
        indexed = json.loads(workspace_tool(action="index"))
        assert indexed["success"] is True
        assert indexed["file_count"] == 1

        # Verify file is searchable
        searched = json.loads(workspace_tool(action="search", query="deployment"))
        assert searched["success"] is True
        assert searched["count"] == 1

        # Delete from index
        deleted = json.loads(workspace_tool(action="delete", path="docs/deploy.md"))
        assert deleted["success"] is True
        assert deleted["deleted"] == "docs/deploy.md"

        # Verify file is gone from retrieval results
        retrieved = json.loads(workspace_tool(action="retrieve", query="deployment"))
        assert retrieved["success"] is True
        assert retrieved["count"] == 0

    def test_list_returns_relative_paths(self, tmp_path, monkeypatch):
        from tools.workspace_tool import workspace_tool

        cfg = _config(tmp_path)
        workspace = Path(cfg["workspace"]["path"])
        (workspace / "notes").mkdir(parents=True)
        (workspace / "notes" / "todo.txt").write_text("ship it\n", encoding="utf-8")
        monkeypatch.setattr("tools.workspace_tool.load_config", lambda: cfg)

        listed = json.loads(workspace_tool(action="list"))
        assert listed["success"] is True
        assert listed["entries"][0]["relative_path"] == "notes/todo.txt"
