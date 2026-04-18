from __future__ import annotations

import json
import subprocess
from dataclasses import asdict, dataclass
from datetime import datetime, UTC
from pathlib import Path

from .config import StationConfig
from .planner import PlannedSegment, SegmentPlanner
from .renderer import RenderArtifacts, build_render_command, build_stream_command, slugify
from .tts import synthesize_to_file


@dataclass(slots=True)
class RunResult:
    segment: PlannedSegment
    script_path: Path
    audio_path: Path
    video_path: Path
    metadata_path: Path
    render_command: list[str]
    stream_command: list[str] | None


class StationRunner:
    def __init__(self, config: StationConfig):
        self.config = config
        self.planner = SegmentPlanner(config.station, config.generation, config.streaming)

    def run_once(self, dry_run: bool = True, publish: bool = False) -> RunResult:
        segment = self.planner.plan_segments(count=1)[0]
        artifacts = self._prepare_artifacts(segment)
        artifacts.script_path.parent.mkdir(parents=True, exist_ok=True)
        artifacts.script_path.write_text(segment.script)

        render_command = build_render_command(
            segment=segment,
            station=self.config.station,
            generation=self.config.generation,
            artifacts=artifacts,
        )
        stream_command = None
        if publish:
            destination = self.config.streaming.destination()
            if destination:
                stream_command = build_stream_command(artifacts.video_path, destination)

        metadata = {
            "timestamp": datetime.now(UTC).isoformat(),
            "dry_run": dry_run,
            "publish": publish,
            "segment": asdict(segment),
            "artifacts": {
                "script_path": str(artifacts.script_path),
                "audio_path": str(artifacts.audio_path),
                "video_path": str(artifacts.video_path),
                "metadata_path": str(artifacts.metadata_path),
            },
            "render_command": render_command,
            "stream_command": stream_command,
        }

        if not dry_run:
            synthesize_to_file(segment.script, self.config.generation.voice, artifacts.audio_path)
            subprocess.run(render_command, check=True)
            if stream_command:
                subprocess.run(stream_command, check=True)
        artifacts.metadata_path.write_text(json.dumps(metadata, indent=2))

        return RunResult(
            segment=segment,
            script_path=artifacts.script_path,
            audio_path=artifacts.audio_path,
            video_path=artifacts.video_path,
            metadata_path=artifacts.metadata_path,
            render_command=render_command,
            stream_command=stream_command,
        )

    def _prepare_artifacts(self, segment: PlannedSegment) -> RenderArtifacts:
        stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        slug = slugify(segment.title)
        base = self.config.station.output_dir / f"{stamp}-{slug}"
        return RenderArtifacts(
            script_path=base.with_suffix(".txt"),
            audio_path=base.with_suffix(".mp3"),
            video_path=base.with_suffix(".mp4"),
            metadata_path=base.with_suffix(".json"),
        )
