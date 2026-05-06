
from agent.transcript_capture.formatting import TranscriptEvent, TranscriptMetadata, render_transcript


def test_render_transcript_has_stable_header_and_end_session():
    meta = TranscriptMetadata(
        platform="discord",
        source_type="gateway",
        session_key_hash="abc123",
        session_id_hash="def456",
        started_at="2026-05-03T00:00:00Z",
        finalized_at="2026-05-03T00:01:00Z",
    )
    text = render_transcript(meta, [TranscriptEvent(role="user", content="hello", timestamp="2026-05-03T00:00:01Z")])
    assert "schema_version: 1" in text
    assert "redaction_version:" in text
    assert "platform: discord" in text
    assert "source_type: gateway" in text
    assert "session_key_hash: abc123" in text
    assert "session_id_hash: def456" in text
    assert text.rstrip().endswith("END_SESSION")


def test_render_transcript_renders_user_assistant_and_filters_reasoning_and_secrets():
    secret = "sk-" + "C" * 28
    meta = TranscriptMetadata("slack", "gateway", "k", "s", "start", "end")
    text = render_transcript(meta, [
        TranscriptEvent(role="user", content=f"hi {secret}", timestamp="t1", metadata={"reasoning": "drop"}),
        TranscriptEvent(role="assistant", content="hello", timestamp="t2"),
    ])
    assert "[t1] USER:" in text
    assert "[t2] ASSISTANT:" in text
    assert secret not in text
    assert "reasoning" not in text.lower()
