# Autonomous AI YouTube Streaming Station

This is a standalone MVP for an autonomous YouTube streaming station.

## What it does
- plans short segments from a topic list
- falls back to deterministic offline copy when no LLM provider is configured
- synthesizes narration with Edge TTS
- renders a simple branded MP4 with ffmpeg
- can optionally push a rendered clip to a YouTube RTMP destination

## Quick start
```bash
source venv/bin/activate
python -m youtube_station plan --config configs/youtube_station.example.yaml --count 2
python -m youtube_station run --config configs/youtube_station.example.yaml --once --dry-run
```

## Local render
```bash
source venv/bin/activate
python -m youtube_station render --config configs/youtube_station.example.yaml
```

## Stream to YouTube
1. Create a YouTube live stream and copy the RTMP URL + stream key.
2. Export:
```bash
export YOUTUBE_RTMP_URL='rtmp://a.rtmp.youtube.com/live2'
export YOUTUBE_STREAM_KEY='your-stream-key'
```
3. Enable streaming in the YAML or pass `--stream` on `run`.
4. Run:
```bash
source venv/bin/activate
python -m youtube_station run --config configs/youtube_station.example.yaml --once --stream
```

## Notes
- `--dry-run` is the safest mode and writes a JSON manifest with the planned commands.
- For true 24/7 operation, wrap `python -m youtube_station run ... --stream` in a supervisor like systemd, pm2, or Docker restart policies.
- The current renderer is intentionally simple: background, title, and script text overlay. It is meant as a working base you can expand into scenes, B-roll, news ingestion, or live chat reactions.
