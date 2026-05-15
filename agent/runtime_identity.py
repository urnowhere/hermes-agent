"""Shared runtime identity snapshot for FormSy integrations."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ResolvedIdentitySnapshot:
    workspace_id: str
    session_id: str
    turn_id: Optional[str] = None

    user_id: Optional[str] = None
    profile_id: Optional[str] = None

    repo_id: Optional[str] = None
    branch: Optional[str] = None
    revision: Optional[str] = None

    document_id: Optional[str] = None
    document_version: Optional[str] = None

    source_flags: dict[str, str] = field(default_factory=dict)
    limited_scope_flags: set[str] = field(default_factory=set)

    def to_runtime_identity(self) -> dict[str, str]:
        return {
            key: value
            for key, value in {
                "user_id": self.user_id,
                "profile_id": self.profile_id,
                "repo_id": self.repo_id,
                "branch": self.branch,
                "revision": self.revision,
                "document_id": self.document_id,
                "document_version": self.document_version,
            }.items()
            if value is not None
        }

    def set_source(self, field_name: str, source: str) -> None:
        if source:
            self.source_flags[field_name] = source

    def mark_limited(self, flag: str) -> None:
        if flag:
            self.limited_scope_flags.add(flag)

    def clear_limited(self, flag: str) -> None:
        self.limited_scope_flags.discard(flag)
