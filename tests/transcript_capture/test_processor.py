
import pytest

from agent.transcript_capture.config import TranscriptCaptureConfig
from agent.transcript_capture.processor import TranscriptProcessor


def test_rejects_non_approved_model_string():
    with pytest.raises(ValueError):
        TranscriptProcessor.validate_model_policy("gemini/free")


def test_accepts_only_approved_model_string():
    assert TranscriptProcessor.validate_model_policy("openai-codex/gpt-5.4-mini") == "openai-codex/gpt-5.4-mini"


def test_processing_requires_external_and_paid_gates(tmp_path):
    cfg = TranscriptCaptureConfig(active_dir=tmp_path/"a", corpus_dir=tmp_path/"c", state_dir=tmp_path/"s", external_synthesis_enabled=True, paid_provider_allowed=False)
    assert not TranscriptProcessor(cfg).is_enabled()
    cfg = TranscriptCaptureConfig(active_dir=tmp_path/"a", corpus_dir=tmp_path/"c", state_dir=tmp_path/"s", external_synthesis_enabled=True, paid_provider_allowed=True)
    assert TranscriptProcessor(cfg).is_enabled()
