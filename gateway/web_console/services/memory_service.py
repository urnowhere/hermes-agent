"""Memory and session-search helpers for the Hermes Web Console backend."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

from hermes_cli.config import load_config
from hermes_state import SessionDB


def _load_module_from_tools(module_name: str):
    root = Path(__file__).resolve().parents[3]
    module_path = root / "tools" / f"{module_name}.py"
    cache_key = f"gateway.web_console._lazy_{module_name}"
    module = sys.modules.get(cache_key)
    if module is not None:
        return module

    spec = importlib.util.spec_from_file_location(cache_key, module_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Could not load {module_name} from {module_path}.")

    module = importlib.util.module_from_spec(spec)
    sys.modules[cache_key] = module
    spec.loader.exec_module(module)
    return module


def _get_memory_store_class():
    return _load_module_from_tools("memory_tool").MemoryStore


def session_search(**kwargs):
    return _load_module_from_tools("session_search_tool").session_search(**kwargs)


class MemoryService:
    """Thin wrapper around Hermes memory files and session-search logic."""

    def __init__(
        self,
        *,
        store: Any = None,
        db: SessionDB | None = None,
    ) -> None:
        self._store = store
        self.db = db or SessionDB()

    @staticmethod
    def _memory_config() -> dict[str, Any]:
        try:
            config = load_config()
        except Exception:
            config = {}
        memory_cfg = config.get("memory") or {}
        if not isinstance(memory_cfg, dict):
            return {}
        return memory_cfg

    @classmethod
    def _target_enabled(cls, target: str) -> bool:
        memory_cfg = cls._memory_config()
        if target == "user":
            return bool(memory_cfg.get("user_profile_enabled", True))
        return bool(memory_cfg.get("memory_enabled", True))

    @classmethod
    def _build_store(cls) -> Any:
        memory_cfg = cls._memory_config()
        memory_store_class = _get_memory_store_class()
        store = memory_store_class(
            memory_char_limit=int(memory_cfg.get("memory_char_limit", 2200) or 2200),
            user_char_limit=int(memory_cfg.get("user_char_limit", 1375) or 1375),
        )
        store.load_from_disk()
        return store

    def _get_store(self) -> Any:
        if self._store is None:
            self._store = self._build_store()
        return self._store

    @staticmethod
    def _validate_target(target: str) -> str:
        normalized = (target or "memory").strip().lower()
        if normalized in {"user-profile", "user_profile"}:
            normalized = "user"
        if normalized not in {"memory", "user"}:
            raise ValueError("The 'target' field must be 'memory' or 'user'.")
        return normalized

    @staticmethod
    def _usage_payload(usage: str) -> dict[str, Any]:
        percent = None
        current_chars = None
        char_limit = None
        raw = usage or ""
        if "—" in raw:
            percent_part, _, counts_part = raw.partition("—")
            percent_part = percent_part.strip().rstrip("%")
            counts_part = counts_part.strip().replace(" chars", "")
            try:
                percent = int(percent_part)
            except ValueError:
                percent = None
            if "/" in counts_part:
                current_raw, _, limit_raw = counts_part.partition("/")
                try:
                    current_chars = int(current_raw.replace(",", "").strip())
                    char_limit = int(limit_raw.replace(",", "").strip())
                except ValueError:
                    current_chars = None
                    char_limit = None
        return {
            "text": raw,
            "percent": percent,
            "current_chars": current_chars,
            "char_limit": char_limit,
        }

    def get_memory(self, *, target: str = "memory") -> dict[str, Any]:
        target = self._validate_target(target)
        store = self._get_store()
        entries = list(store.user_entries if target == "user" else store.memory_entries)
        current_chars = store._char_count(target)
        char_limit = store._char_limit(target)
        usage_text = f"{int((current_chars / char_limit) * 100) if char_limit > 0 else 0}% — {current_chars:,}/{char_limit:,} chars"
        return {
            "target": target,
            "enabled": self._target_enabled(target),
            "entries": entries,
            "entry_count": len(entries),
            "usage": self._usage_payload(usage_text),
            "path": str(Path(store._path_for(target)).resolve()),
        }

    def mutate_memory(
        self,
        *,
        action: str,
        target: str = "memory",
        content: str | None = None,
        old_text: str | None = None,
    ) -> dict[str, Any]:
        target = self._validate_target(target)
        if not self._target_enabled(target):
            label = "user profile" if target == "user" else "memory"
            raise PermissionError(f"Local {label} is disabled in config.")

        store = self._get_store()
        if action == "add":
            result = store.add(target, content or "")
        elif action == "replace":
            result = store.replace(target, old_text or "", content or "")
        elif action == "remove":
            result = store.remove(target, old_text or "")
        else:
            raise ValueError("Unsupported action.")

        payload = self.get_memory(target=target)
        payload["message"] = result.get("message")
        payload["success"] = bool(result.get("success"))
        if not result.get("success"):
            payload["error"] = result.get("error")
            if "matches" in result:
                payload["matches"] = result["matches"]
        return payload

    def search_sessions(
        self,
        *,
        query: str,
        role_filter: str | None = None,
        limit: int = 3,
        current_session_id: str | None = None,
    ) -> dict[str, Any]:
        result = session_search(
            query=query,
            role_filter=role_filter,
            limit=limit,
            db=self.db,
            current_session_id=current_session_id,
        )
        try:
            payload = json.loads(result)
        except (TypeError, json.JSONDecodeError) as exc:
            raise RuntimeError("Session search returned invalid JSON.") from exc
        if not isinstance(payload, dict):
            raise RuntimeError("Session search returned an invalid payload.")
        return payload
