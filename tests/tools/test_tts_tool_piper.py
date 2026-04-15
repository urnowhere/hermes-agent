import json
from pathlib import Path

from piper_catalog import PIPER_VOICE_CATALOG_REF
from tools import tts_tool


def test_resolve_piper_binary_falls_back_to_active_venv(monkeypatch, tmp_path):
    venv_bin = tmp_path / "venv" / "bin"
    venv_bin.mkdir(parents=True)
    python_path = venv_bin / "python"
    python_path.write_text("")
    piper_path = venv_bin / "piper"
    piper_path.write_text("")

    monkeypatch.setattr(tts_tool.shutil, "which", lambda _name: None)
    monkeypatch.setattr(tts_tool.sys, "executable", str(python_path))

    assert tts_tool._resolve_piper_binary({"binary_path": "piper"}) == str(piper_path)


def test_resolve_piper_binary_uses_venv_symlink_parent_without_resolving(monkeypatch, tmp_path):
    base_python = tmp_path / "uv" / "python3.11"
    base_python.parent.mkdir(parents=True)
    base_python.write_text("")

    venv_bin = tmp_path / "venv" / "bin"
    venv_bin.mkdir(parents=True)
    python_link = venv_bin / "python"
    python_link.symlink_to(base_python)
    piper_path = venv_bin / "piper"
    piper_path.write_text("")

    monkeypatch.setattr(tts_tool.shutil, "which", lambda _name: None)
    monkeypatch.setattr(tts_tool.sys, "executable", str(python_link))

    assert tts_tool._resolve_piper_binary({"binary_path": "piper"}) == str(piper_path)


def test_check_tts_requirements_accepts_piper_with_downloaded_named_model(monkeypatch, tmp_path):
    monkeypatch.setattr(
        tts_tool,
        "_load_tts_config",
        lambda: {"provider": "piper", "piper": {"binary_path": "piper", "model": "pl_PL-gosia-medium"}},
    )
    monkeypatch.setattr(tts_tool, "_import_edge_tts", lambda: (_ for _ in ()).throw(ImportError()))
    monkeypatch.setattr(tts_tool, "_import_elevenlabs", lambda: (_ for _ in ()).throw(ImportError()))
    monkeypatch.setattr(tts_tool, "_import_openai_client", lambda: (_ for _ in ()).throw(ImportError()))
    monkeypatch.setattr(tts_tool, "_check_neutts_available", lambda: False)
    monkeypatch.setattr(tts_tool, "_resolve_piper_binary", lambda cfg: "/usr/local/bin/piper")
    monkeypatch.setattr(
        tts_tool,
        "_resolve_piper_model_paths",
        lambda cfg, allow_download=False: (tmp_path / "voice.onnx", tmp_path / "voice.onnx.json", "pl_PL-gosia-medium", tmp_path),
    )

    assert tts_tool.check_tts_requirements() is True


def test_check_tts_requirements_rejects_piper_without_binary(monkeypatch):
    monkeypatch.setattr(
        tts_tool,
        "_load_tts_config",
        lambda: {"provider": "piper", "piper": {"binary_path": "missing", "model": "pl_PL-gosia-medium"}},
    )
    monkeypatch.setattr(tts_tool, "_import_edge_tts", lambda: (_ for _ in ()).throw(ImportError()))
    monkeypatch.setattr(tts_tool, "_import_elevenlabs", lambda: (_ for _ in ()).throw(ImportError()))
    monkeypatch.setattr(tts_tool, "_import_openai_client", lambda: (_ for _ in ()).throw(ImportError()))
    monkeypatch.setattr(tts_tool, "_check_neutts_available", lambda: False)
    monkeypatch.setattr(
        tts_tool,
        "_resolve_piper_binary",
        lambda cfg: (_ for _ in ()).throw(FileNotFoundError("Piper binary not found: missing")),
    )

    assert tts_tool.check_tts_requirements() is False


def test_check_tts_requirements_rejects_piper_when_model_not_downloaded(monkeypatch):
    monkeypatch.setattr(
        tts_tool,
        "_load_tts_config",
        lambda: {"provider": "piper", "piper": {"binary_path": "piper", "model": "pl_PL-gosia-medium"}},
    )
    monkeypatch.setattr(tts_tool, "_import_edge_tts", lambda: (_ for _ in ()).throw(ImportError()))
    monkeypatch.setattr(tts_tool, "_import_elevenlabs", lambda: (_ for _ in ()).throw(ImportError()))
    monkeypatch.setattr(tts_tool, "_import_openai_client", lambda: (_ for _ in ()).throw(ImportError()))
    monkeypatch.setattr(tts_tool, "_check_neutts_available", lambda: False)
    monkeypatch.setattr(tts_tool, "_resolve_piper_binary", lambda cfg: "/usr/local/bin/piper")
    monkeypatch.setattr(
        tts_tool,
        "_resolve_piper_model_paths",
        lambda cfg, allow_download=False: (_ for _ in ()).throw(
            ValueError("Piper model is not downloaded yet: /tmp/pl_PL-gosia-medium.onnx")
        ),
    )

    assert tts_tool.check_tts_requirements() is False


def test_check_tts_requirements_prefers_configured_piper_over_edge(monkeypatch):
    monkeypatch.setattr(
        tts_tool,
        "_load_tts_config",
        lambda: {"provider": "piper", "piper": {"binary_path": "missing", "model": "pl_PL-gosia-medium"}},
    )
    monkeypatch.setattr(tts_tool, "_import_edge_tts", lambda: object())
    monkeypatch.setattr(
        tts_tool,
        "_resolve_piper_binary",
        lambda cfg: (_ for _ in ()).throw(FileNotFoundError("Piper binary not found: missing")),
    )

    assert tts_tool.check_tts_requirements() is False


def test_generate_piper_uses_native_cli_speaker_flag(tmp_path, monkeypatch):
    wav_path = tmp_path / "sample.wav"
    captured = {}

    def fake_run(cmd, input=None, capture_output=None, text=None, timeout=None):
        captured["cmd"] = cmd
        captured["input"] = input
        wav_path.write_bytes(b"RIFFfake")

        class Result:
            returncode = 0
            stderr = ""
            stdout = ""

        return Result()

    monkeypatch.setattr(tts_tool, "_resolve_piper_binary", lambda cfg: "/usr/local/bin/piper")
    monkeypatch.setattr(
        tts_tool,
        "_resolve_piper_model_paths",
        lambda cfg, allow_download=True: (tmp_path / "voice.onnx", tmp_path / "voice.onnx.json", "pl_PL-gosia-medium", tmp_path),
    )
    monkeypatch.setattr(tts_tool.subprocess, "run", fake_run)

    result = tts_tool._generate_piper(
        "hello",
        str(wav_path),
        {"piper": {"speaker": "2"}},
    )

    assert result == str(wav_path)
    assert "--speaker" in captured["cmd"]
    assert "2" in captured["cmd"]
    assert captured["input"] == "hello\n"


def test_build_piper_voice_urls_percent_encodes_unicode_model_names():
    model_url, config_url = tts_tool._build_piper_voice_urls("pt_PT-tugão-medium")

    assert f"/resolve/{PIPER_VOICE_CATALOG_REF}/" in model_url
    assert f"/resolve/{PIPER_VOICE_CATALOG_REF}/" in config_url
    assert "tug%C3%A3o" in model_url
    assert "tug%C3%A3o" in config_url
    assert model_url.endswith("pt_PT-tug%C3%A3o-medium.onnx?download=true")
    assert config_url.endswith("pt_PT-tug%C3%A3o-medium.onnx.json?download=true")


def test_text_to_speech_tool_uses_piper_and_converts_for_telegram(tmp_path, monkeypatch):
    wav_path = tmp_path / "tts.wav"
    ogg_path = tmp_path / "tts.ogg"

    def fake_generate(text, output_path, _tts_config):
        Path(output_path).write_bytes(b"RIFFfake")
        return output_path

    monkeypatch.setenv("HERMES_SESSION_PLATFORM", "telegram")
    monkeypatch.setattr(tts_tool, "_load_tts_config", lambda: {"provider": "piper", "piper": {"model": "pl_PL-gosia-medium"}})
    monkeypatch.setattr(tts_tool, "_generate_piper", fake_generate)
    monkeypatch.setattr(tts_tool, "_convert_to_opus", lambda _path: str(ogg_path))
    monkeypatch.setattr(tts_tool, "_probe_audio_codec", lambda _path: "opus")
    ogg_path.write_bytes(b"OggSfake")

    result = json.loads(tts_tool.text_to_speech_tool("Czesc", output_path=str(wav_path)))

    assert result["success"] is True
    assert result["provider"] == "piper"
    assert result["voice_compatible"] is True
    assert result["file_path"] == str(ogg_path)
    assert result["media_tag"].startswith("[[audio_as_voice]]")


def test_text_to_speech_tool_does_not_mark_non_opus_ogg_as_voice(tmp_path, monkeypatch):
    ogg_path = tmp_path / "tts.ogg"
    ogg_path.write_bytes(b"OggSfake")

    monkeypatch.setenv("HERMES_SESSION_PLATFORM", "telegram")
    monkeypatch.setattr(
        tts_tool,
        "_load_tts_config",
        lambda: {"provider": "openai", "openai": {"model": "gpt-4o-mini-tts", "voice": "alloy"}},
    )
    monkeypatch.setattr(tts_tool, "_generate_openai_tts", lambda *_args, **_kwargs: str(ogg_path))
    monkeypatch.setattr(tts_tool, "_probe_audio_codec", lambda _path: "vorbis")

    result = json.loads(tts_tool.text_to_speech_tool("Czesc", output_path=str(ogg_path)))

    assert result["success"] is True
    assert result["voice_compatible"] is False
    assert result["media_tag"] == f"MEDIA:{ogg_path}"


def test_text_to_speech_tool_requires_telegram_opus_for_piper(tmp_path, monkeypatch):
    wav_path = tmp_path / "tts.wav"

    def fake_generate(text, output_path, _tts_config):
        Path(output_path).write_bytes(b"RIFFfake")
        return output_path

    monkeypatch.setenv("HERMES_SESSION_PLATFORM", "telegram")
    monkeypatch.setattr(tts_tool, "_load_tts_config", lambda: {"provider": "piper", "piper": {"model": "pl_PL-gosia-medium"}})
    monkeypatch.setattr(tts_tool, "_generate_piper", fake_generate)
    monkeypatch.setattr(tts_tool, "_convert_to_opus", lambda _path: None)

    result = json.loads(tts_tool.text_to_speech_tool("Czesc", output_path=str(wav_path)))

    assert result["success"] is False
    assert "Telegram-compatible OGG/Opus voice note" in result["error"]


def test_text_to_speech_tool_reports_missing_piper_binary(monkeypatch, tmp_path):
    monkeypatch.setattr(tts_tool, "_load_tts_config", lambda: {"provider": "piper", "piper": {"model": "pl_PL-gosia-medium"}})
    monkeypatch.setattr(
        tts_tool,
        "_generate_piper",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            FileNotFoundError("Piper binary not found: piper. Install Piper CLI and re-run 'hermes setup tts'.")
        ),
    )

    result = json.loads(tts_tool.text_to_speech_tool("Hello", output_path=str(tmp_path / "out.wav")))

    assert result["success"] is False
    assert "TTS dependency missing (piper)" in result["error"]
