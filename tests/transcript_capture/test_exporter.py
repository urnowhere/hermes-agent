
from agent.transcript_capture.config import TranscriptCaptureConfig
from agent.transcript_capture.session_export import SessionTranscriptExporter, SessionFinalizeEntry


class FakeDB:
    def __init__(self):
        self.session = {"id": "raw-session-id", "source": "discord", "started_at": 1770000000.0, "ended_at": 1770000060.0}
        self.messages = [
            {"role": "user", "content": "hi", "timestamp": 1770000001.0},
            {"role": "user", "content": "hi", "timestamp": 1770000002.0},
            {"role": "assistant", "content": "hello", "timestamp": 1770000003.0, "reasoning": "private"},
            {"role": "tool", "tool_name": "terminal", "content": "raw result", "timestamp": 1770000004.0},
            {"role": "system", "content": "skip me", "timestamp": 1770000005.0},
        ]
    def get_session(self, session_id):
        assert session_id == "raw-session-id"
        return self.session
    def get_messages(self, session_id):
        assert session_id == "raw-session-id"
        return self.messages


def test_exporter_uses_hash_identity_and_preserves_repeated_messages(tmp_path):
    cfg = TranscriptCaptureConfig(active_dir=tmp_path/"active", corpus_dir=tmp_path/"corpus", state_dir=tmp_path/"state")
    exporter = SessionTranscriptExporter(FakeDB(), cfg)
    final = exporter.export_finalized(SessionFinalizeEntry(session_id="raw-session-id", session_key="raw-chat-id", platform="discord", source_type="gateway"))
    text = final.read_text()
    assert "raw-session-id" not in final.name
    assert "raw-chat-id" not in final.name
    assert "raw-session-id" not in text
    assert "raw-chat-id" not in text
    assert text.count("hi") == 2
    assert "private" not in text
    assert "system" not in text.lower()
    assert "TOOL terminal" in text
    assert "raw result" not in text
