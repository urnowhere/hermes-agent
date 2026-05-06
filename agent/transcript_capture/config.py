from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import FrozenSet, Optional

APPROVED_PROCESSING_MODEL = "openai-codex/gpt-5.4-mini"

_TRUE_VALUES = {"1", "true", "yes", "on", "y"}


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in _TRUE_VALUES


def _csv_env(name: str) -> FrozenSet[str]:
    value = os.getenv(name, "")
    return frozenset(part.strip() for part in value.split(",") if part.strip())


def _env_int(name: str, default: int, *, minimum: int = 1, maximum: Optional[int] = None) -> int:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        parsed = int(value.strip())
    except (TypeError, ValueError):
        return default
    parsed = max(minimum, parsed)
    if maximum is not None:
        parsed = min(maximum, parsed)
    return parsed


@dataclass(frozen=True)
class TranscriptCaptureConfig:
    active_dir: Path = field(default_factory=lambda: Path.home() / ".gbrain-runtime" / "transcript-capture-active")
    corpus_dir: Path = field(default_factory=lambda: Path.home() / ".gbrain-runtime" / "transcript-corpus")
    state_dir: Path = field(default_factory=lambda: Path.home() / ".gbrain-runtime" / "transcript-capture-state")
    capture_enabled: bool = False
    ingest_enabled: bool = False
    external_synthesis_enabled: bool = False
    paid_provider_allowed: bool = False
    platform_allowlist: FrozenSet[str] = frozenset()
    session_allowlist: FrozenSet[str] = frozenset()
    chat_allowlist: FrozenSet[str] = frozenset()
    denylist: FrozenSet[str] = frozenset()
    processing_model: str = APPROVED_PROCESSING_MODEL
    max_artifact_age_days: int = 7

    @classmethod
    def from_env(cls) -> "TranscriptCaptureConfig":
        runtime_root = Path(os.getenv("HERMES_TRANSCRIPT_RUNTIME_ROOT", str(Path.home() / ".gbrain-runtime"))).expanduser()
        return cls(
            active_dir=Path(os.getenv("HERMES_TRANSCRIPT_ACTIVE_DIR", str(runtime_root / "transcript-capture-active"))).expanduser(),
            corpus_dir=Path(os.getenv("HERMES_TRANSCRIPT_CORPUS_DIR", str(runtime_root / "transcript-corpus"))).expanduser(),
            state_dir=Path(os.getenv("HERMES_TRANSCRIPT_STATE_DIR", str(runtime_root / "transcript-capture-state"))).expanduser(),
            capture_enabled=_env_bool("HERMES_TRANSCRIPT_CAPTURE_ENABLED"),
            ingest_enabled=_env_bool("HERMES_TRANSCRIPT_INGEST_ENABLED"),
            external_synthesis_enabled=_env_bool("HERMES_TRANSCRIPT_EXTERNAL_SYNTHESIS_ENABLED"),
            paid_provider_allowed=_env_bool("HERMES_TRANSCRIPT_PAID_PROVIDER_ALLOWED"),
            platform_allowlist=_csv_env("HERMES_TRANSCRIPT_PLATFORM_ALLOWLIST"),
            session_allowlist=_csv_env("HERMES_TRANSCRIPT_SESSION_ALLOWLIST"),
            chat_allowlist=_csv_env("HERMES_TRANSCRIPT_CHAT_ALLOWLIST"),
            denylist=_csv_env("HERMES_TRANSCRIPT_DENYLIST"),
            processing_model=os.getenv("HERMES_TRANSCRIPT_PROCESSING_MODEL", APPROVED_PROCESSING_MODEL).strip() or APPROVED_PROCESSING_MODEL,
            max_artifact_age_days=_env_int("HERMES_TRANSCRIPT_MAX_ARTIFACT_AGE_DAYS", 7, maximum=7),
        )

    def platform_allowed(self, platform: Optional[str]) -> bool:
        if not self.platform_allowlist:
            return True
        return (platform or "").strip() in self.platform_allowlist

    def session_allowed(self, session_id: Optional[str]) -> bool:
        if not self.session_allowlist:
            return True
        return (session_id or "").strip() in self.session_allowlist

    def chat_allowed(self, chat_id: Optional[str]) -> bool:
        if not self.chat_allowlist:
            return True
        return (chat_id or "").strip() in self.chat_allowlist

    def denied(self, *values: Optional[str]) -> bool:
        normalized = {str(v).strip() for v in values if v is not None}
        return bool(normalized & set(self.denylist))
