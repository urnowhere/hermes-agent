from youtube_station.config import GenerationConfig, StationConfigSection, StreamingConfig
from youtube_station.planner import SegmentPlanner


def test_fallback_planner_creates_requested_number_of_segments(tmp_path):
    planner = SegmentPlanner(
        station=StationConfigSection(
            name="Signal FM",
            tagline="AI radio",
            topics=["AI policy", "robotics", "indie hackers"],
            output_dir=tmp_path,
        ),
        generation=GenerationConfig(),
        streaming=StreamingConfig(),
    )

    segments = planner.plan_segments(count=3)

    assert len(segments) == 3
    assert segments[0].topic == "AI policy"
    assert segments[1].topic == "robotics"
    assert segments[2].topic == "indie hackers"
    assert all(segment.script for segment in segments)
    assert all("Signal FM" in segment.script for segment in segments)
