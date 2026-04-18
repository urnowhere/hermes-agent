from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass(slots=True)
class StationConfigSection:
    name: str = "Hermes Live"
    tagline: str = "Autonomous AI broadcast"
    topics: list[str] = field(default_factory=lambda: ["AI news", "tools", "research"])
    output_dir: Path = Path("artifacts/youtube_station")


@dataclass(slots=True)
class GenerationConfig:
    voice: str = "en-US-AvaMultilingualNeural"
    language: str = "en-US"
    provider: str = "fallback"
    model: str = "gpt-4.1-mini"
    api_key_env: str = "OPENAI_API_KEY"
    segment_duration_seconds: int = 90
    words_per_segment: int = 180
    temperature: float = 0.8


@dataclass(slots=True)
class StreamingConfig:
    enabled: bool = False
    rtmp_url_env: str = "YOUTUBE_RTMP_URL"
    rtmp_key_env: str = "YOUTUBE_STREAM_KEY"

    def destination(self) -> str | None:
        base = os.getenv(self.rtmp_url_env, "").strip()
        key = os.getenv(self.rtmp_key_env, "").strip()
        if not base:
            return None
        if key and not base.endswith(key):
            return f"{base.rstrip('/')}/{key}"
        return base or None


@dataclass(slots=True)
class StationConfig:
    station: StationConfigSection = field(default_factory=StationConfigSection)
    generation: GenerationConfig = field(default_factory=GenerationConfig)
    streaming: StreamingConfig = field(default_factory=StreamingConfig)
    source_path: Path | None = None


def _interpolate_env(value: Any) -> Any:
    if isinstance(value, str):
        return os.path.expandvars(value)
    if isinstance(value, list):
        return [_interpolate_env(item) for item in value]
    if isinstance(value, dict):
        return {key: _interpolate_env(item) for key, item in value.items()}
    return value


def load_config(path: str | Path) -> StationConfig:
    source_path = Path(path)
    data = yaml.safe_load(source_path.read_text()) or {}
    data = _interpolate_env(data)

    station_data = data.get("station", {})
    generation_data = data.get("generation", {})
    streaming_data = data.get("streaming", {})

    station = StationConfigSection(
        name=station_data.get("name", StationConfigSection.name),
        tagline=station_data.get("tagline", StationConfigSection.tagline),
        topics=list(station_data.get("topics", StationConfigSection().topics)),
        output_dir=Path(station_data.get("output_dir", StationConfigSection.output_dir)),
    )
    generation = GenerationConfig(**generation_data)
    streaming = StreamingConfig(**streaming_data)

    if not station.output_dir.is_absolute():
        station.output_dir = (source_path.parent / station.output_dir).resolve()

    return StationConfig(
        station=station,
        generation=generation,
        streaming=streaming,
        source_path=source_path.resolve(),
    )
