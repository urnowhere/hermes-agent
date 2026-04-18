from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .config import GenerationConfig, StationConfigSection
from .planner import PlannedSegment


@dataclass(slots=True)
class RenderArtifacts:
    script_path: Path
    audio_path: Path
    video_path: Path
    metadata_path: Path


def slugify(value: str) -> str:
    cleaned = "".join(ch.lower() if ch.isalnum() else "-" for ch in value)
    while "--" in cleaned:
        cleaned = cleaned.replace("--", "-")
    return cleaned.strip("-") or "segment"


def escape_drawtext(value: str) -> str:
    return value.replace("\\", r"\\").replace(":", r"\:").replace("'", r"\'")


def build_render_command(
    *,
    segment: PlannedSegment,
    station: StationConfigSection,
    generation: GenerationConfig,
    artifacts: RenderArtifacts,
) -> list[str]:
    title_text = escape_drawtext(segment.title)
    return [
        "ffmpeg",
        "-y",
        "-f",
        "lavfi",
        "-i",
        f"color=c=0x111827:s=1280x720:d={generation.segment_duration_seconds}",
        "-i",
        str(artifacts.audio_path),
        "-vf",
        (
            "drawtext=font=Sans:text='"
            + title_text
            + "':fontcolor=white:fontsize=38:x=(w-text_w)/2:y=70,"
            + f"drawtext=textfile={artifacts.script_path}:reload=1:font=Sans:fontcolor=white:fontsize=26:"
            + "box=1:boxcolor=black@0.45:boxborderw=18:x=80:y=h-230"
        ),
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-shortest",
        str(artifacts.video_path),
    ]


def build_stream_command(video_path: Path, destination: str) -> list[str]:
    return [
        "ffmpeg",
        "-re",
        "-stream_loop",
        "-1",
        "-i",
        str(video_path),
        "-c",
        "copy",
        "-f",
        "flv",
        destination,
    ]
