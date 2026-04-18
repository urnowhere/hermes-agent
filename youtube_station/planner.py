from __future__ import annotations

import os
from dataclasses import asdict, dataclass

from .config import GenerationConfig, StationConfigSection, StreamingConfig

try:
    from openai import OpenAI
except Exception:  # pragma: no cover - optional dependency resolution
    OpenAI = None


@dataclass(slots=True)
class PlannedSegment:
    title: str
    topic: str
    script: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


class SegmentPlanner:
    def __init__(self, station: StationConfigSection, generation: GenerationConfig, streaming: StreamingConfig):
        self.station = station
        self.generation = generation
        self.streaming = streaming

    def plan_segments(self, count: int = 1) -> list[PlannedSegment]:
        api_key = os.getenv(self.generation.api_key_env, "").strip()
        if self.generation.provider != "fallback" and api_key and OpenAI is not None:
            try:
                return self._plan_with_openai(count=count, api_key=api_key)
            except Exception:
                pass
        return self._fallback_plan(count=count)

    def _fallback_plan(self, count: int) -> list[PlannedSegment]:
        topics = self.station.topics or ["AI updates"]
        segments: list[PlannedSegment] = []
        for index in range(count):
            topic = topics[index % len(topics)]
            title = f"{self.station.name} — {topic.title()} Brief #{index + 1}"
            script = (
                f"Welcome to {self.station.name}, {self.station.tagline}. "
                f"Tonight's segment is about {topic}. "
                f"We summarize the signal, explain why it matters, and leave the audience with one practical takeaway. "
                f"Stay tuned for the next autonomous broadcast."
            )
            segments.append(PlannedSegment(title=title, topic=topic, script=script))
        return segments

    def _plan_with_openai(self, count: int, api_key: str) -> list[PlannedSegment]:
        client = OpenAI(api_key=api_key)
        prompt = (
            f"You are planning concise YouTube livestream segments for {self.station.name}. "
            f"Station tagline: {self.station.tagline}. "
            f"Topics: {', '.join(self.station.topics)}. "
            f"Return exactly {count} segments as JSON with keys title, topic, script. "
            f"Each script should be around {self.generation.words_per_segment} words."
        )
        response = client.responses.create(
            model=self.generation.model,
            input=prompt,
            temperature=self.generation.temperature,
        )
        text = getattr(response, "output_text", "") or ""
        import json

        payload = json.loads(text)
        return [PlannedSegment(**item) for item in payload]
