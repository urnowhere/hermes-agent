# zoom_meeting plugin

Official-surface-first Zoom meeting intelligence for Hermes.

## What this first version does

- Fetches Zoom meeting metadata using server-to-server OAuth
- Stores durable local state under `~/.hermes/cache/zoom/meetings/<meeting_id>/`
- Runs a local webhook receiver for meeting lifecycle / RTMS-like events
- Normalizes event payloads into JSONL
- Extracts transcript-like fragments into `transcript.txt`
- Extracts action items, decisions, and open questions from transcript text
- Exports deterministic markdown / JSON artifacts for follow-up work

## What it does not do yet

- Join a Zoom call as a participant
- Speak inside the meeting
- Control meeting UI
- Act as a Team Chat adapter

Those are later phases. This plugin is the substrate they will build on.

## Required environment

For REST metadata fetches:

- `ZOOM_ACCOUNT_ID`
- `ZOOM_CLIENT_ID`
- `ZOOM_CLIENT_SECRET`

For webhook validation:

- `ZOOM_WEBHOOK_SECRET_TOKEN` (preferred) or `ZOOM_WEBHOOK_SECRET`

## CLI

```bash
hermes plugins enable zoom_meeting

hermes zoom auth-check
hermes zoom watch 123456789
hermes zoom serve --port 8754
hermes zoom status 123456789
hermes zoom transcript 123456789 --last 40
hermes zoom summary 123456789
hermes zoom action-items 123456789
hermes zoom export 123456789 --format markdown
```

## Files per meeting

Each meeting gets a workspace:

```text
~/.hermes/cache/zoom/meetings/<meeting_id>/
  state.json
  events.jsonl
  transcript.txt
  summary.md
  artifact.md / artifact.json
```
