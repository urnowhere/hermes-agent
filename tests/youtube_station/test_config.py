from pathlib import Path

from youtube_station.config import load_config


def test_load_config_interpolates_env_and_defaults(tmp_path, monkeypatch):
    monkeypatch.setenv("YT_OUTPUT_DIR", str(tmp_path / "renders"))
    config_path = tmp_path / "station.yaml"
    config_path.write_text(
        """
station:
  name: Night Signal
  tagline: Autonomous late-night tech radio
  topics:
    - AI news
    - open source tools
  output_dir: ${YT_OUTPUT_DIR}
generation:
  voice: en-US-AvaMultilingualNeural
streaming:
  enabled: true
  rtmp_url_env: YOUTUBE_RTMP_URL
  rtmp_key_env: YOUTUBE_STREAM_KEY
""".strip()
    )

    config = load_config(config_path)

    assert config.station.name == "Night Signal"
    assert config.station.output_dir == Path(tmp_path / "renders")
    assert config.generation.segment_duration_seconds == 90
    assert config.streaming.enabled is True
    assert config.streaming.rtmp_url_env == "YOUTUBE_RTMP_URL"
