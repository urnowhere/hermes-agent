"""Tests for the web console workspace and process APIs."""

from __future__ import annotations

from pathlib import Path

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from gateway.web_console.api.workspace import WORKSPACE_SERVICE_APP_KEY
from gateway.web_console.routes import register_web_console_routes
from gateway.web_console.services.workspace_service import WorkspaceService


class FakeWorkspaceService:
    def __init__(self) -> None:
        self.rollback_requests: list[dict[str, object]] = []
        self.killed: list[str] = []

    def get_tree(self, *, path=None, depth=2, include_hidden=False):
        if path == "missing":
            raise FileNotFoundError("missing path")
        if path == "bad":
            raise ValueError("bad path")
        return {
            "workspace_root": "/workspace",
            "tree": {
                "name": "workspace",
                "path": path or ".",
                "type": "directory",
                "children": [{"name": "src", "path": "src", "type": "directory", "children": []}],
                "truncated": False,
            },
        }

    def get_file(self, *, path, offset=1, limit=500):
        if path == "missing.txt":
            raise FileNotFoundError("missing file")
        if path == "binary.bin":
            raise ValueError("Binary files are not supported by this endpoint.")
        return {
            "workspace_root": "/workspace",
            "file": {
                "path": path,
                "size": 12,
                "line_count": 3,
                "offset": offset,
                "limit": limit,
                "content": "line1\nline2",
                "truncated": True,
                "is_binary": False,
            },
        }

    def search_workspace(self, *, query, path=None, limit=50, include_hidden=False, regex=False):
        if query == "bad":
            raise ValueError("bad query")
        return {
            "workspace_root": "/workspace",
            "query": query,
            "path": path or ".",
            "matches": [{"path": "src/app.py", "line": 3, "content": "needle here"}],
            "truncated": False,
            "scanned_files": 1,
        }

    def diff_checkpoint(self, *, checkpoint_id, path=None):
        if checkpoint_id == "missing":
            raise FileNotFoundError("missing checkpoint")
        return {
            "workspace_root": "/workspace",
            "working_dir": "/workspace",
            "checkpoint_id": checkpoint_id,
            "stat": "1 file changed",
            "diff": "@@ -1 +1 @@",
        }

    def list_checkpoints(self, *, path=None):
        return {
            "workspace_root": "/workspace",
            "working_dir": "/workspace",
            "checkpoints": [{"hash": "abc123", "short_hash": "abc123", "reason": "auto"}],
        }

    def rollback(self, *, checkpoint_id, path=None, file_path=None):
        if checkpoint_id == "missing":
            raise FileNotFoundError("missing checkpoint")
        payload = {"checkpoint_id": checkpoint_id, "path": path, "file_path": file_path}
        self.rollback_requests.append(payload)
        return {"success": True, "restored_to": checkpoint_id[:8], "file": file_path}

    def list_processes(self):
        return {
            "processes": [
                {
                    "session_id": "proc_123",
                    "command": "pytest",
                    "status": "running",
                    "pid": 42,
                    "cwd": "/workspace",
                    "output_preview": "running",
                }
            ]
        }

    def get_process_log(self, process_id, *, offset=0, limit=200):
        if process_id == "missing":
            raise FileNotFoundError("missing process")
        return {
            "session_id": process_id,
            "status": "running",
            "output": "a\nb",
            "total_lines": 2,
            "showing": "2 lines",
        }

    def kill_process(self, process_id):
        if process_id == "missing":
            raise FileNotFoundError("missing process")
        self.killed.append(process_id)
        return {"status": "killed", "session_id": process_id}


class TestWorkspaceApi:
    @staticmethod
    async def _make_client(service: FakeWorkspaceService) -> TestClient:
        app = web.Application()
        app[WORKSPACE_SERVICE_APP_KEY] = service
        register_web_console_routes(app)
        client = TestClient(TestServer(app))
        await client.start_server()
        return client

    @pytest.mark.asyncio
    async def test_workspace_endpoints_return_structured_payloads(self):
        client = await self._make_client(FakeWorkspaceService())
        try:
            tree_resp = await client.get("/api/gui/workspace/tree?path=src&depth=1")
            assert tree_resp.status == 200
            tree_payload = await tree_resp.json()
            assert tree_payload["ok"] is True
            assert tree_payload["tree"]["path"] == "src"

            file_resp = await client.get("/api/gui/workspace/file?path=README.md&offset=2&limit=2")
            assert file_resp.status == 200
            file_payload = await file_resp.json()
            assert file_payload["file"]["path"] == "README.md"
            assert file_payload["file"]["offset"] == 2
            assert file_payload["file"]["limit"] == 2

            search_resp = await client.get("/api/gui/workspace/search?query=needle")
            assert search_resp.status == 200
            search_payload = await search_resp.json()
            assert search_payload["matches"][0]["path"] == "src/app.py"

            diff_resp = await client.get("/api/gui/workspace/diff?checkpoint_id=abc123")
            assert diff_resp.status == 200
            diff_payload = await diff_resp.json()
            assert diff_payload["checkpoint_id"] == "abc123"
            assert "@@" in diff_payload["diff"]

            checkpoints_resp = await client.get("/api/gui/workspace/checkpoints")
            assert checkpoints_resp.status == 200
            checkpoints_payload = await checkpoints_resp.json()
            assert checkpoints_payload["checkpoints"][0]["hash"] == "abc123"
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_rollback_and_process_endpoints(self):
        service = FakeWorkspaceService()
        client = await self._make_client(service)
        try:
            rollback_resp = await client.post(
                "/api/gui/workspace/rollback",
                json={"checkpoint_id": "abc123", "file_path": "src/app.py"},
            )
            assert rollback_resp.status == 200
            rollback_payload = await rollback_resp.json()
            assert rollback_payload["ok"] is True
            assert rollback_payload["result"]["restored_to"] == "abc123"
            assert service.rollback_requests[0]["file_path"] == "src/app.py"

            list_resp = await client.get("/api/gui/processes")
            assert list_resp.status == 200
            list_payload = await list_resp.json()
            assert list_payload["processes"][0]["session_id"] == "proc_123"

            log_resp = await client.get("/api/gui/processes/proc_123/log?offset=0&limit=10")
            assert log_resp.status == 200
            log_payload = await log_resp.json()
            assert log_payload["session_id"] == "proc_123"
            assert log_payload["total_lines"] == 2

            kill_resp = await client.post("/api/gui/processes/proc_123/kill")
            assert kill_resp.status == 200
            kill_payload = await kill_resp.json()
            assert kill_payload["result"]["status"] == "killed"
            assert service.killed == ["proc_123"]
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_workspace_api_returns_structured_errors(self):
        client = await self._make_client(FakeWorkspaceService())
        try:
            missing_path_resp = await client.get("/api/gui/workspace/file")
            assert missing_path_resp.status == 400
            assert (await missing_path_resp.json())["error"]["code"] == "missing_path"

            invalid_depth_resp = await client.get("/api/gui/workspace/tree?depth=abc")
            assert invalid_depth_resp.status == 400
            assert (await invalid_depth_resp.json())["error"]["code"] == "invalid_path"

            missing_checkpoint_resp = await client.get("/api/gui/workspace/diff")
            assert missing_checkpoint_resp.status == 400
            assert (await missing_checkpoint_resp.json())["error"]["code"] == "missing_checkpoint_id"

            invalid_json_resp = await client.post(
                "/api/gui/workspace/rollback",
                data="not json",
                headers={"Content-Type": "application/json"},
            )
            assert invalid_json_resp.status == 400
            assert (await invalid_json_resp.json())["error"]["code"] == "invalid_json"

            missing_process_resp = await client.get("/api/gui/processes/missing/log")
            assert missing_process_resp.status == 404
            missing_process_payload = await missing_process_resp.json()
            assert missing_process_payload["error"]["code"] == "process_not_found"
        finally:
            await client.close()


class TestWorkspaceService:
    def test_workspace_service_reads_tree_file_and_searches(self, tmp_path: Path):
        class StubCheckpointManager:
            def list_checkpoints(self, working_dir):
                return []

            def get_working_dir_for_path(self, file_path):
                return str(tmp_path)

            def diff(self, working_dir, commit_hash):
                return {"success": False, "error": "unused"}

            def restore(self, working_dir, commit_hash, file_path=None):
                return {"success": False, "error": "unused"}

        class StubProcessRegistry:
            def list_sessions(self):
                return []

        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "app.py").write_text("alpha\nneedle value\nomega\n", encoding="utf-8")
        (tmp_path / "README.md").write_text("one\ntwo\nthree\n", encoding="utf-8")

        service = WorkspaceService(
            workspace_root=tmp_path,
            checkpoint_manager=StubCheckpointManager(),
            process_registry=StubProcessRegistry(),
        )

        tree = service.get_tree(path="src", depth=1)
        assert tree["tree"]["type"] == "directory"
        assert tree["tree"]["children"][0]["path"] == "src/app.py"

        file_payload = service.get_file(path="README.md", offset=2, limit=2)
        assert file_payload["file"]["content"] == "two\nthree"
        assert file_payload["file"]["truncated"] is False

        search_payload = service.search_workspace(query="needle")
        assert search_payload["matches"] == [{"path": "src/app.py", "line": 2, "content": "needle value"}]
