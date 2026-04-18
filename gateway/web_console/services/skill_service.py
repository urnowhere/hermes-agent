"""Skill data access helpers for the Hermes Web Console."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from hermes_state import SessionDB


class SkillService:
    """Thin wrapper around Hermes skills discovery and session-scoped in-memory skill state."""

    def __init__(self, db: SessionDB | None = None) -> None:
        self.db = db or SessionDB()
        self._session_loaded_skills: dict[str, dict[str, dict[str, Any]]] = {}

    def _resolve_session_id(self, session_id: str) -> str:
        return self.db.resolve_session_id(session_id) or session_id

    def _require_session(self, session_id: str) -> str:
        resolved = self._resolve_session_id(session_id)
        if not self.db.get_session(resolved):
            raise LookupError("session_not_found")
        return resolved

    @staticmethod
    def _parse_tool_payload(raw_payload: str) -> dict[str, Any]:
        payload = json.loads(raw_payload)
        if not isinstance(payload, dict):
            raise ValueError("invalid_skill_payload")
        return payload

    @staticmethod
    def _build_source_indexes() -> tuple[dict[str, dict[str, Any]], set[str]]:
        from tools.skills_hub import HubLockFile
        from tools.skills_sync import _read_manifest

        hub_entries = {entry["name"]: entry for entry in HubLockFile().list_installed()}
        builtin_names = set(_read_manifest())
        return hub_entries, builtin_names

    @staticmethod
    def _merge_source_metadata(
        skill: dict[str, Any],
        *,
        hub_entries: dict[str, dict[str, Any]],
        builtin_names: set[str],
    ) -> dict[str, Any]:
        merged = dict(skill)
        name = str(skill.get("name") or "")
        hub_entry = hub_entries.get(name)
        if hub_entry:
            merged.update(
                {
                    "source_type": "hub",
                    "source": hub_entry.get("source", "hub"),
                    "trust_level": hub_entry.get("trust_level", "community"),
                    "identifier": hub_entry.get("identifier"),
                    "install_path": hub_entry.get("install_path"),
                    "scan_verdict": hub_entry.get("scan_verdict"),
                    "installed_at": hub_entry.get("installed_at"),
                    "updated_at": hub_entry.get("updated_at"),
                    "installed_metadata": hub_entry.get("metadata") or {},
                }
            )
        elif name in builtin_names:
            merged.update(
                {
                    "source_type": "builtin",
                    "source": "builtin",
                    "trust_level": "builtin",
                    "identifier": None,
                    "install_path": None,
                    "scan_verdict": None,
                    "installed_at": None,
                    "updated_at": None,
                    "installed_metadata": {},
                }
            )
        else:
            merged.update(
                {
                    "source_type": "local",
                    "source": "local",
                    "trust_level": "local",
                    "identifier": None,
                    "install_path": None,
                    "scan_verdict": None,
                    "installed_at": None,
                    "updated_at": None,
                    "installed_metadata": {},
                }
            )
        return merged

    def list_skills(self) -> dict[str, Any]:
        from tools.skills_tool import skills_list

        payload = self._parse_tool_payload(skills_list())
        if not payload.get("success"):
            raise RuntimeError(payload.get("error") or "Failed to list skills.")

        hub_entries, builtin_names = self._build_source_indexes()
        skills = [
            self._merge_source_metadata(skill, hub_entries=hub_entries, builtin_names=builtin_names)
            for skill in payload.get("skills", [])
        ]
        return {
            "skills": skills,
            "categories": payload.get("categories", []),
            "count": len(skills),
            "hint": payload.get("hint"),
        }

    def get_skill(self, name: str) -> dict[str, Any]:
        from tools.skills_tool import skill_view

        payload = self._parse_tool_payload(skill_view(name))
        if not payload.get("success"):
            error_message = str(payload.get("error") or "Failed to load skill.")
            lowered = error_message.lower()
            if "not found" in lowered:
                raise FileNotFoundError(error_message)
            raise ValueError(error_message)

        hub_entries, builtin_names = self._build_source_indexes()
        return self._merge_source_metadata(payload, hub_entries=hub_entries, builtin_names=builtin_names)

    def load_skill_for_session(self, session_id: str, name: str) -> dict[str, Any]:
        resolved_session_id = self._require_session(session_id)
        skill = self.get_skill(name)
        loaded_for_session = self._session_loaded_skills.setdefault(resolved_session_id, {})
        resolved_name = skill["name"]
        if resolved_name in loaded_for_session:
            return {
                "session_id": resolved_session_id,
                "skill": loaded_for_session[resolved_name],
                "loaded": True,
                "already_loaded": True,
            }

        session_skill = {
            "name": resolved_name,
            "description": skill.get("description", ""),
            "path": skill.get("path"),
            "source": skill.get("source"),
            "source_type": skill.get("source_type"),
            "trust_level": skill.get("trust_level"),
            "readiness_status": skill.get("readiness_status"),
            "setup_needed": bool(skill.get("setup_needed", False)),
            "loaded_at": datetime.now(timezone.utc).isoformat(),
        }
        loaded_for_session[resolved_name] = session_skill
        return {
            "session_id": resolved_session_id,
            "skill": session_skill,
            "loaded": True,
            "already_loaded": False,
        }

    def list_session_skills(self, session_id: str) -> dict[str, Any]:
        resolved_session_id = self._require_session(session_id)
        skills = list(self._session_loaded_skills.get(resolved_session_id, {}).values())
        skills.sort(key=lambda item: item["name"])
        return {
            "session_id": resolved_session_id,
            "skills": skills,
            "count": len(skills),
        }

    def unload_skill_for_session(self, session_id: str, name: str) -> dict[str, Any]:
        resolved_session_id = self._require_session(session_id)
        loaded_for_session = self._session_loaded_skills.get(resolved_session_id, {})

        canonical_name = name
        if canonical_name not in loaded_for_session:
            for loaded_name in loaded_for_session:
                if loaded_name.lower() == name.lower():
                    canonical_name = loaded_name
                    break

        if canonical_name not in loaded_for_session:
            try:
                canonical_name = self.get_skill(name)["name"]
            except (FileNotFoundError, ValueError, LookupError):
                canonical_name = name

        removed = loaded_for_session.pop(canonical_name, None)
        if not loaded_for_session and resolved_session_id in self._session_loaded_skills:
            self._session_loaded_skills.pop(resolved_session_id, None)
        return {
            "session_id": resolved_session_id,
            "name": canonical_name,
            "removed": removed is not None,
        }
