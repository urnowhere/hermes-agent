import json
from pathlib import Path

from youtube_station.config import load_config
from youtube_station.runner import StationRunner


def test_runner_dry_run_writes_segment_manifest(tmp_path):
    config_path = tmp_path / "station.yaml"
    config_path.write_text(
        """
station:
  name: Test Station
  tagline: Dry run radio
  topics:
    - synthetic media
  output_dir: renders
generation:
  voice: en-US-AvaMultilingualNeural
""".strip()
    )

    config = load_config(config_path)
    runner = StationRunner(config=config)

    result = runner.run_once(dry_run=True)

    assert result.video_path.suffix == ".mp4"
    assert result.metadata_path.exists()
    payload = json.loads(result.metadata_path.read_text())
    assert payload["dry_run"] is True
    assert payload["segment"]["title"]
    assert Path(payload["artifacts"]["script_path"]).exists()
