
import os
import time
from pathlib import Path

from agent.transcript_capture.config import TranscriptCaptureConfig
from agent.transcript_capture.retention import cleanup_old_artifacts


def test_default_retention_is_at_most_one_week(monkeypatch):
    monkeypatch.delenv("HERMES_TRANSCRIPT_MAX_ARTIFACT_AGE_DAYS", raising=False)
    cfg = TranscriptCaptureConfig.from_env()
    assert cfg.max_artifact_age_days == 7


def test_retention_days_can_be_tightened_by_env(monkeypatch):
    monkeypatch.setenv("HERMES_TRANSCRIPT_MAX_ARTIFACT_AGE_DAYS", "3")
    cfg = TranscriptCaptureConfig.from_env()
    assert cfg.max_artifact_age_days == 3


def test_cleanup_removes_only_old_runtime_artifacts(tmp_path):
    cfg = TranscriptCaptureConfig(
        active_dir=tmp_path / "active",
        corpus_dir=tmp_path / "corpus",
        state_dir=tmp_path / "state",
        max_artifact_age_days=7,
    )
    for d in (cfg.active_dir, cfg.corpus_dir, cfg.state_dir):
        d.mkdir(parents=True)
    old_part = cfg.active_dir / "old.part"
    new_part = cfg.active_dir / "new.part"
    old_txt = cfg.corpus_dir / "old.txt"
    new_txt = cfg.corpus_dir / "new.txt"
    nested_txt = cfg.corpus_dir / "nested" / "old.txt"
    nested_txt.parent.mkdir()
    for path in (old_part, new_part, old_txt, new_txt, nested_txt):
        path.write_text("x")
    old_ts = time.time() - 8 * 24 * 3600
    for path in (old_part, old_txt, nested_txt):
        os.utime(path, (old_ts, old_ts))

    removed = cleanup_old_artifacts(cfg)

    assert old_part in removed
    assert old_txt in removed
    assert nested_txt not in removed
    assert not old_part.exists()
    assert not old_txt.exists()
    assert new_part.exists()
    assert new_txt.exists()
    assert nested_txt.exists()
