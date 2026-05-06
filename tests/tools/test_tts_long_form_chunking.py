"""Tests for long-form TTS chunking and platform-aware delivery packing."""

import json
from pathlib import Path


from tools.tts_tool import (
    _pack_audio_files_for_delivery,
    _resolve_audio_delivery_profile,
    _split_text_for_tts,
    text_to_speech_tool,
)


def test_split_text_for_tts_preserves_all_text_and_keeps_chunks_under_limit():
    text = (
        "First sentence has enough content to be meaningful. "
        "Second sentence should stay intact where possible.\n\n"
        "- Bullet items need punctuation for speech pauses.\n"
        "Final sentence closes the thought cleanly."
    )

    chunks = _split_text_for_tts(text, max_chars=70)

    assert len(chunks) > 1
    assert all(len(chunk) <= 70 for chunk in chunks)
    assert " ".join(chunks).replace("\n", " ") == " ".join(text.split())
    assert all(chunk[-1] in ".,;:!?" for chunk in chunks)


def test_resolve_audio_delivery_profile_uses_conservative_discord_limit():
    profile = _resolve_audio_delivery_profile("discord", {})

    assert profile.platform == "discord"
    assert profile.max_file_bytes <= 10 * 1024 * 1024
    assert profile.target_file_bytes < profile.max_file_bytes
    assert profile.preferred_format in {"ogg", "opus", "mp3"}


def test_pack_audio_files_for_delivery_keeps_groups_under_platform_target(tmp_path):
    files = []
    for idx, size in enumerate([400, 400, 400, 900]):
        path = tmp_path / f"chunk_{idx}.ogg"
        path.write_bytes(b"x" * size)
        files.append(str(path))

    profile = _resolve_audio_delivery_profile(
        "discord", {"delivery_profiles": {"discord": {"max_file_bytes": 1000, "safety_ratio": 0.8}}}
    )
    groups = _pack_audio_files_for_delivery(files, profile)

    assert groups == [[files[0], files[1]], [files[2]], [files[3]]]
    assert all(sum(Path(p).stat().st_size for p in group) <= profile.max_file_bytes for group in groups)


def test_text_to_speech_tool_chunks_long_openai_input_instead_of_truncating(tmp_path, monkeypatch):
    calls = []

    def fake_import_openai_client():
        return object

    def fake_openai(text, output_path, cfg):
        calls.append(text)
        Path(output_path).write_bytes(("audio:" + text).encode())
        return output_path

    def fake_concat_audio_files(paths, output_path):
        Path(output_path).write_bytes(b"".join(Path(p).read_bytes() for p in paths))
        return output_path

    monkeypatch.setattr("tools.tts_tool._import_openai_client", fake_import_openai_client)
    monkeypatch.setattr("tools.tts_tool._generate_openai_tts", fake_openai)
    monkeypatch.setattr("tools.tts_tool._concat_audio_files", fake_concat_audio_files)
    monkeypatch.setattr("tools.tts_tool._load_tts_config", lambda: {
        "provider": "openai",
        "openai": {"max_text_length": 120},
        "delivery_profiles": {"discord": {"max_file_bytes": 1_000_000}},
    })
    monkeypatch.setattr("gateway.session_context.get_session_env", lambda name, default="": "discord")

    text = " ".join(f"Sentence {i} has enough words to be a natural chunk." for i in range(20))
    output_path = tmp_path / "out.mp3"

    result = json.loads(text_to_speech_tool(text=text, output_path=str(output_path)))

    assert result["success"] is True
    assert result["chunk_count"] > 1
    assert len(calls) == result["chunk_count"]
    assert all(len(call) <= 120 for call in calls)
    assert " ".join(calls) == " ".join(text.split())
    assert output_path.exists()
