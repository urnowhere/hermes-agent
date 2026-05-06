
from pathlib import Path

from agent.transcript_capture.config import APPROVED_PROCESSING_MODEL, TranscriptCaptureConfig


def test_default_paths_resolve_under_home_gbrain_runtime(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    cfg = TranscriptCaptureConfig.from_env()
    root = tmp_path / ".gbrain-runtime"
    assert cfg.active_dir == root / "transcript-capture-active"
    assert cfg.corpus_dir == root / "transcript-corpus"
    assert cfg.state_dir == root / "transcript-capture-state"


def test_operational_gates_default_disabled(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    for key in (
        "HERMES_TRANSCRIPT_CAPTURE_ENABLED",
        "HERMES_TRANSCRIPT_INGEST_ENABLED",
        "HERMES_TRANSCRIPT_EXTERNAL_SYNTHESIS_ENABLED",
        "HERMES_TRANSCRIPT_PAID_PROVIDER_ALLOWED",
    ):
        monkeypatch.delenv(key, raising=False)
    cfg = TranscriptCaptureConfig.from_env()
    assert not cfg.capture_enabled
    assert not cfg.ingest_enabled
    assert not cfg.external_synthesis_enabled
    assert not cfg.paid_provider_allowed


def test_allowlists_and_denylists_parse(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("HERMES_TRANSCRIPT_PLATFORM_ALLOWLIST", " discord,telegram ,, slack ")
    monkeypatch.setenv("HERMES_TRANSCRIPT_SESSION_ALLOWLIST", "s1, s2")
    monkeypatch.setenv("HERMES_TRANSCRIPT_CHAT_ALLOWLIST", "c1,c2")
    monkeypatch.setenv("HERMES_TRANSCRIPT_DENYLIST", "private, blocked")
    cfg = TranscriptCaptureConfig.from_env()
    assert cfg.platform_allowlist == frozenset({"discord", "telegram", "slack"})
    assert cfg.session_allowlist == frozenset({"s1", "s2"})
    assert cfg.chat_allowlist == frozenset({"c1", "c2"})
    assert cfg.denylist == frozenset({"private", "blocked"})


def test_model_constant_is_exactly_approved_model():
    assert APPROVED_PROCESSING_MODEL == "openai-codex/gpt-5.4-mini"


def test_max_artifact_age_days_is_capped_at_privacy_limit(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("HERMES_TRANSCRIPT_MAX_ARTIFACT_AGE_DAYS", "365")
    cfg = TranscriptCaptureConfig.from_env()
    assert cfg.max_artifact_age_days == 7


def test_max_artifact_age_days_allows_shorter_retention(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("HERMES_TRANSCRIPT_MAX_ARTIFACT_AGE_DAYS", "3")
    cfg = TranscriptCaptureConfig.from_env()
    assert cfg.max_artifact_age_days == 3
