from pathlib import Path

from youtube_station.config import GenerationConfig, StationConfigSection
from youtube_station.planner import PlannedSegment
from youtube_station.renderer import RenderArtifacts, build_render_command, build_stream_command


def test_build_render_command_contains_text_overlay_and_output_path(tmp_path):
    segment = PlannedSegment(title="Autonomous Markets", topic="AI markets", script="Hello world")
    station = StationConfigSection(name="Signal FM", tagline="AI radio", topics=["AI markets"], output_dir=tmp_path)
    generation = GenerationConfig()
    artifacts = RenderArtifacts(
        script_path=tmp_path / "segment.txt",
        audio_path=tmp_path / "segment.mp3",
        video_path=tmp_path / "segment.mp4",
        metadata_path=tmp_path / "segment.json",
    )

    command = build_render_command(segment=segment, station=station, generation=generation, artifacts=artifacts)

    joined = " ".join(command)
    assert "ffmpeg" in command[0]
    assert str(artifacts.video_path) in joined
    assert "drawtext=textfile=" in joined
    assert str(artifacts.script_path) in joined


def test_build_stream_command_targets_rtmp_endpoint(tmp_path):
    destination = "rtmp://a.rtmp.youtube.com/live2/stream-key"
    command = build_stream_command(Path("clip.mp4"), destination)

    joined = " ".join(command)
    assert "-re" in joined
    assert destination in joined
    assert "-f flv" in joined
