from __future__ import annotations

import time
from pathlib import Path
from typing import Iterable, List

from .config import TranscriptCaptureConfig


_RUNTIME_PATTERNS = (
    ("active_dir", "*.part"),
    ("corpus_dir", "*.txt"),
    ("state_dir", "*.json"),
)


def _old_enough(path: Path, cutoff: float) -> bool:
    try:
        return path.stat().st_mtime < cutoff
    except OSError:
        return False


def _iter_runtime_artifacts(config: TranscriptCaptureConfig) -> Iterable[Path]:
    for attr, pattern in _RUNTIME_PATTERNS:
        root = Path(getattr(config, attr))
        if not root.is_dir():
            continue
        # Non-recursive by design: this preserves the flat-corpus invariant and
        # avoids deleting unexpected nested files the verifier should flag.
        yield from (p for p in root.glob(pattern) if p.is_file())


def cleanup_old_artifacts(config: TranscriptCaptureConfig, *, now: float | None = None) -> List[Path]:
    """Delete transcript-runtime artifacts older than the configured TTL.

    The default TTL is seven days to meet the <=1 week retention requirement.
    Cleanup is deliberately scoped to known runtime file extensions in known
    directories and does not recurse into nested corpus directories.
    """
    days = max(1, int(getattr(config, "max_artifact_age_days", 7) or 7))
    cutoff = (time.time() if now is None else now) - days * 24 * 3600
    removed: List[Path] = []
    for path in _iter_runtime_artifacts(config):
        if not _old_enough(path, cutoff):
            continue
        try:
            path.unlink()
        except OSError:
            continue
        removed.append(path)
    return removed
