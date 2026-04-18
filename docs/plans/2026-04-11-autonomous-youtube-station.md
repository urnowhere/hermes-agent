# Autonomous AI YouTube Streaming Station Implementation Plan

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.

**Goal:** Build a standalone MVP that can autonomously plan short segments, generate narration, render simple branded video clips, and optionally stream them to YouTube RTMP.

**Architecture:** Create a small standalone Python package (`youtube_station/`) that is isolated from the existing Hermes web-console code. The station will load YAML config, generate a segment plan via an LLM provider with a deterministic fallback, synthesize narration with Edge TTS, render MP4 clips with ffmpeg, and optionally push the clip to a YouTube RTMP endpoint. Default behavior is safe dry-run/local rendering.

**Tech Stack:** Python 3.11, pytest, YAML, Edge TTS, ffmpeg, optional OpenAI-compatible chat provider.

---

### Task 1: Add package skeleton and config tests
- Create `youtube_station/` package and `tests/youtube_station/`.
- Write failing tests for config loading, env interpolation, and defaults.
- Implement `StationConfig` dataclasses and YAML loader.

### Task 2: Add autonomous segment planner
- Write failing tests for deterministic fallback planning.
- Implement a planner that emits title/topic/script prompts and uses a fallback when no API key/provider is configured.

### Task 3: Add renderer command builder
- Write failing tests for ffmpeg command generation.
- Implement helpers to create audio/video output paths and ffmpeg invocations for local render and RTMP publish.

### Task 4: Add station runner orchestration
- Write failing tests for one-shot station execution in dry-run mode.
- Implement the runner that ties together config, planner, TTS, rendering, and optional streaming.

### Task 5: Add CLI and sample config
- Write failing tests for CLI argument handling where practical.
- Implement `python -m youtube_station` entrypoint with `plan`, `render`, and `run` commands.
- Add `configs/youtube_station.example.yaml`.

### Task 6: Add docs and verification
- Add `youtube_station/README.md` with setup, dry-run, local render, and YouTube streaming instructions.
- Run targeted tests and a smoke dry-run command.

## Verification
- `pytest tests/youtube_station -q`
- `python -m youtube_station plan --config configs/youtube_station.example.yaml --count 2`
- `python -m youtube_station run --config configs/youtube_station.example.yaml --once --dry-run`

## Notes
- Do not modify the user’s existing dirty GUI files.
- Keep all new code in isolated new paths.
- Default to local artifacts unless RTMP settings are explicitly provided.
