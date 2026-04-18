"""Log service helpers for the Hermes Web Console backend."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from hermes_cli.config import ensure_hermes_home, get_hermes_home


class LogService:
    """Thin wrapper for reading Hermes log files from ~/.hermes/logs."""

    def __init__(
        self,
        *,
        hermes_home_getter: Callable[[], Path] = get_hermes_home,
        ensure_home: Callable[[], None] = ensure_hermes_home,
    ) -> None:
        self._hermes_home_getter = hermes_home_getter
        self._ensure_home = ensure_home

    def get_logs(self, *, file_name: str | None = None, limit: int = 200) -> dict[str, Any]:
        self._ensure_home()
        log_dir = self._hermes_home_getter() / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        files = self._list_log_files(log_dir)
        selected = self._resolve_selected_file(log_dir, files, file_name)
        lines = self._tail_lines(selected, limit=limit) if selected is not None else []
        return {
            "directory": str(log_dir),
            "file": selected.name if selected is not None else None,
            "available_files": [item.name for item in files],
            "line_count": len(lines),
            "lines": lines,
        }

    @staticmethod
    def _list_log_files(log_dir: Path) -> list[Path]:
        return sorted(
            [item for item in log_dir.iterdir() if item.is_file()],
            key=lambda item: item.stat().st_mtime,
            reverse=True,
        )

    @staticmethod
    def _resolve_selected_file(log_dir: Path, files: list[Path], file_name: str | None) -> Path | None:
        if file_name:
            candidate = (log_dir / file_name).resolve()
            if candidate.parent != log_dir.resolve() or not candidate.is_file():
                raise FileNotFoundError(file_name)
            return candidate
        if not files:
            return None
        for preferred in ("gateway.log", "gateway.error.log"):
            for item in files:
                if item.name == preferred:
                    return item
        return files[0]

    @staticmethod
    def _tail_lines(path: Path, *, limit: int) -> list[str]:
        if limit <= 0:
            return []
        try:
            content = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            return []
        return content[-limit:]
