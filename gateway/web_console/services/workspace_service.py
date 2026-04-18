"""Workspace and process helpers for the Hermes Web Console backend."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

from hermes_cli.config import get_project_root, load_config

_MAX_TREE_ENTRIES = 200
_MAX_FILE_LINES = 2000
_MAX_SEARCH_MATCHES = 200
_MAX_SEARCH_FILE_BYTES = 1_000_000
_MAX_SEARCH_FILES = 2000


class WorkspaceService:
    """Thin service layer for GUI workspace browsing and process inspection."""

    def __init__(
        self,
        *,
        workspace_root: str | Path | None = None,
        checkpoint_manager: Any = None,
        process_registry: Any = None,
    ) -> None:
        self.workspace_root = self._resolve_workspace_root(workspace_root)
        self.checkpoint_manager = checkpoint_manager
        self.process_registry = process_registry

    @staticmethod
    def _resolve_workspace_root(workspace_root: str | Path | None) -> Path:
        if workspace_root is not None:
            return Path(workspace_root).expanduser().resolve()

        configured_cwd = (os.getenv("TERMINAL_CWD") or "").strip()
        if configured_cwd and configured_cwd not in {".", "auto", "cwd"}:
            candidate = Path(configured_cwd).expanduser().resolve()
            if candidate.exists():
                return candidate

        return get_project_root().resolve()

    @staticmethod
    def _build_checkpoint_manager() -> Any:
        from tools.checkpoint_manager import CheckpointManager

        try:
            config = load_config()
        except Exception:
            config = {}
        checkpoint_cfg = config.get("checkpoints") or {}
        if isinstance(checkpoint_cfg, bool):
            enabled = checkpoint_cfg
            max_snapshots = 50
        else:
            enabled = bool(checkpoint_cfg.get("enabled", True))
            max_snapshots = int(checkpoint_cfg.get("max_snapshots", 50) or 50)
        return CheckpointManager(enabled=enabled, max_snapshots=max_snapshots)

    @staticmethod
    def _get_default_process_registry() -> Any:
        from tools.process_registry import process_registry

        return process_registry

    def _get_checkpoint_manager(self) -> Any:
        if self.checkpoint_manager is None:
            self.checkpoint_manager = self._build_checkpoint_manager()
        return self.checkpoint_manager

    def _get_process_registry(self) -> Any:
        if self.process_registry is None:
            self.process_registry = self._get_default_process_registry()
        return self.process_registry

    def _resolve_path(self, path: str | None, *, allow_root: bool = True) -> Path:
        raw_path = (path or "").strip()
        candidate = self.workspace_root if not raw_path else (self.workspace_root / raw_path)
        resolved = candidate.expanduser().resolve()
        try:
            resolved.relative_to(self.workspace_root)
        except ValueError as exc:
            raise ValueError("Path escapes the active workspace.") from exc
        if not allow_root and resolved == self.workspace_root:
            raise ValueError("This operation requires a path inside the workspace.")
        return resolved

    def _relative_path(self, path: Path) -> str:
        if path == self.workspace_root:
            return "."
        return path.relative_to(self.workspace_root).as_posix()

    @staticmethod
    def _is_binary_file(path: Path) -> bool:
        try:
            sample = path.read_bytes()[:4096]
        except OSError:
            return False
        return b"\x00" in sample

    def _build_tree_node(
        self,
        path: Path,
        *,
        depth: int,
        include_hidden: bool,
        remaining: list[int],
    ) -> dict[str, Any]:
        is_dir = path.is_dir()
        node: dict[str, Any] = {
            "name": path.name or self.workspace_root.name,
            "path": self._relative_path(path),
            "type": "directory" if is_dir else "file",
        }

        if not is_dir:
            try:
                node["size"] = path.stat().st_size
            except OSError:
                node["size"] = None
            return node

        if depth <= 0:
            node["children"] = []
            node["truncated"] = False
            return node

        children: list[dict[str, Any]] = []
        truncated = False
        try:
            entries = sorted(
                path.iterdir(),
                key=lambda entry: (not entry.is_dir(), entry.name.lower()),
            )
        except OSError as exc:
            node["children"] = []
            node["error"] = str(exc)
            return node

        for entry in entries:
            if not include_hidden and entry.name.startswith("."):
                continue
            if remaining[0] <= 0:
                truncated = True
                break
            remaining[0] -= 1
            children.append(
                self._build_tree_node(
                    entry,
                    depth=depth - 1,
                    include_hidden=include_hidden,
                    remaining=remaining,
                )
            )

        node["children"] = children
        node["truncated"] = truncated
        return node

    def get_tree(self, *, path: str | None = None, depth: int = 2, include_hidden: bool = False) -> dict[str, Any]:
        target = self._resolve_path(path)
        if not target.exists():
            raise FileNotFoundError("The requested workspace path does not exist.")
        if not target.is_dir():
            raise ValueError("The requested workspace path is not a directory.")
        safe_depth = max(0, min(int(depth), 6))
        remaining = [_MAX_TREE_ENTRIES]
        return {
            "workspace_root": str(self.workspace_root),
            "tree": self._build_tree_node(
                target,
                depth=safe_depth,
                include_hidden=include_hidden,
                remaining=remaining,
            ),
        }

    def get_file(self, *, path: str, offset: int = 1, limit: int = 500) -> dict[str, Any]:
        target = self._resolve_path(path, allow_root=False)
        if not target.exists():
            raise FileNotFoundError("The requested file does not exist.")
        if not target.is_file():
            raise ValueError("The requested path is not a file.")
        if self._is_binary_file(target):
            raise ValueError("Binary files are not supported by this endpoint.")

        safe_offset = max(1, int(offset))
        safe_limit = max(1, min(int(limit), _MAX_FILE_LINES))
        text = target.read_text(encoding="utf-8", errors="replace")
        lines = text.splitlines()
        start = safe_offset - 1
        end = start + safe_limit
        selected = lines[start:end]
        return {
            "workspace_root": str(self.workspace_root),
            "file": {
                "path": self._relative_path(target),
                "size": target.stat().st_size,
                "line_count": len(lines),
                "offset": safe_offset,
                "limit": safe_limit,
                "content": "\n".join(selected),
                "truncated": end < len(lines),
                "is_binary": False,
            },
        }

    def save_file(self, *, path: str, content: str) -> dict[str, Any]:
        """Write *content* to the file at *path* inside the workspace."""
        target = self._resolve_path(path, allow_root=False)
        if target.is_dir():
            raise ValueError("The requested path is a directory, not a file.")
        if self._is_binary_file(target) and target.exists():
            raise ValueError("Binary files cannot be saved through this endpoint.")

        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return {
            "workspace_root": str(self.workspace_root),
            "file": {
                "path": self._relative_path(target),
                "size": target.stat().st_size,
            },
        }

    def search_workspace(
        self,
        *,
        query: str,
        path: str | None = None,
        limit: int = 50,
        include_hidden: bool = False,
        regex: bool = False,
    ) -> dict[str, Any]:
        if not query or not query.strip():
            raise ValueError("The search query must be a non-empty string.")

        base = self._resolve_path(path)
        if not base.exists():
            raise FileNotFoundError("The requested search path does not exist.")
        if not base.is_dir():
            raise ValueError("The requested search path is not a directory.")

        safe_limit = max(1, min(int(limit), _MAX_SEARCH_MATCHES))
        matcher = re.compile(query) if regex else None
        matches: list[dict[str, Any]] = []
        scanned_files = 0

        for candidate in base.rglob("*"):
            if len(matches) >= safe_limit or scanned_files >= _MAX_SEARCH_FILES:
                break
            if candidate.is_dir():
                if not include_hidden and any(part.startswith(".") for part in candidate.relative_to(base).parts if part not in {"."}):
                    continue
                continue
            if not include_hidden and any(part.startswith(".") for part in candidate.relative_to(base).parts):
                continue
            try:
                if candidate.stat().st_size > _MAX_SEARCH_FILE_BYTES or self._is_binary_file(candidate):
                    continue
                scanned_files += 1
                with candidate.open("r", encoding="utf-8", errors="replace") as handle:
                    for line_number, line in enumerate(handle, start=1):
                        haystack = line.rstrip("\n")
                        found = bool(matcher.search(haystack)) if matcher else query in haystack
                        if found:
                            matches.append(
                                {
                                    "path": self._relative_path(candidate),
                                    "line": line_number,
                                    "content": haystack,
                                }
                            )
                            if len(matches) >= safe_limit:
                                break
            except OSError:
                continue

        return {
            "workspace_root": str(self.workspace_root),
            "query": query,
            "path": self._relative_path(base),
            "matches": matches,
            "truncated": len(matches) >= safe_limit,
            "scanned_files": scanned_files,
        }

    def list_checkpoints(self, *, path: str | None = None) -> dict[str, Any]:
        checkpoint_manager = self._get_checkpoint_manager()
        target = self._resolve_path(path)
        working_dir = target if target.is_dir() else Path(checkpoint_manager.get_working_dir_for_path(str(target)))
        checkpoints = checkpoint_manager.list_checkpoints(str(working_dir))
        return {
            "workspace_root": str(self.workspace_root),
            "working_dir": str(working_dir),
            "checkpoints": checkpoints,
        }

    def diff_checkpoint(self, *, checkpoint_id: str, path: str | None = None) -> dict[str, Any]:
        if not checkpoint_id:
            raise ValueError("A checkpoint_id is required.")
        checkpoint_manager = self._get_checkpoint_manager()
        target = self._resolve_path(path)
        working_dir = target if target.is_dir() else Path(checkpoint_manager.get_working_dir_for_path(str(target)))
        result = checkpoint_manager.diff(str(working_dir), checkpoint_id)
        if not result.get("success"):
            raise FileNotFoundError(result.get("error") or "Checkpoint diff failed.")
        return {
            "workspace_root": str(self.workspace_root),
            "working_dir": str(working_dir),
            "checkpoint_id": checkpoint_id,
            "stat": result.get("stat", ""),
            "diff": result.get("diff", ""),
        }

    def rollback(self, *, checkpoint_id: str, path: str | None = None, file_path: str | None = None) -> dict[str, Any]:
        if not checkpoint_id:
            raise ValueError("A checkpoint_id is required.")
        checkpoint_manager = self._get_checkpoint_manager()
        target = self._resolve_path(path)
        working_dir = target if target.is_dir() else Path(checkpoint_manager.get_working_dir_for_path(str(target)))

        restore_file: str | None = None
        if file_path:
            restore_target = self._resolve_path(file_path, allow_root=False)
            restore_file = str(restore_target.relative_to(working_dir).as_posix())

        result = checkpoint_manager.restore(str(working_dir), checkpoint_id, file_path=restore_file)
        if not result.get("success"):
            raise FileNotFoundError(result.get("error") or "Rollback failed.")
        return result

    def list_processes(self) -> dict[str, Any]:
        return {"processes": self._get_process_registry().list_sessions()}

    def get_process_log(self, process_id: str, *, offset: int = 0, limit: int = 200) -> dict[str, Any]:
        result = self._get_process_registry().read_log(process_id, offset=offset, limit=limit)
        if result.get("status") == "not_found":
            raise FileNotFoundError(result.get("error") or "Process not found.")
        return result

    def kill_process(self, process_id: str) -> dict[str, Any]:
        result = self._get_process_registry().kill_process(process_id)
        if result.get("status") == "not_found":
            raise FileNotFoundError(result.get("error") or "Process not found.")
        if result.get("status") == "error":
            raise RuntimeError(result.get("error") or "Could not kill process.")
        return result

    def execute_sync(self, command: str) -> dict[str, Any]:
        if not command or not command.strip():
            raise ValueError("Command cannot be empty.")
        import subprocess
        try:
            # Setting a 10s timeout so this doesn't hang the GUI thread if someone runs `sleep 100` or a repl.
            result = subprocess.run(
                command,
                shell=True,
                cwd=str(self.workspace_root),
                capture_output=True,
                text=True,
                timeout=10
            )
            return {
                "cwd": str(self.workspace_root),
                "stdout": result.stdout,
                "stderr": result.stderr,
                "returncode": result.returncode
            }
        except subprocess.TimeoutExpired as e:
            return {
                "cwd": str(self.workspace_root),
                "stdout": e.stdout or "",
                "stderr": (e.stderr or "") + f"\n[Process timed out after 10s]",
                "returncode": -1
            }
        except Exception as e:
            raise RuntimeError(f"Command execution failed: {e}")
