---
title: "Teams Meeting Pipeline"
sidebar_position: 26
description: "Transcript-first Microsoft Teams post-meeting automation with Graph webhooks, STT fallback, and sink delivery"
---

# Teams Meeting Pipeline

The Teams meeting pipeline is Hermes’ event-driven workflow for turning completed Microsoft Teams meetings into structured follow-up outputs.

## What It Does

- receives Microsoft Graph change notifications
- resolves the meeting, transcript, recording, and call-record artifacts
- prefers native transcripts over STT
- falls back to recording download + transcription when needed
- summarizes the meeting with structured decisions, action items, and risks
- writes results to Notion and/or Linear
- posts the summary back to a Teams channel

## Runtime Components

| Component | File |
|-----------|------|
| Graph auth | `tools/microsoft_graph_auth.py` |
| Graph client | `tools/microsoft_graph_client.py` |
| Meeting artifact helpers | `tools/teams_meetings.py` |
| Durable store | `tools/teams_pipeline_store.py` |
| Orchestrator | `tools/teams_pipeline.py` |
| Graph notification adapter | `gateway/platforms/msgraph_webhook.py` |
| Teams delivery adapter | `gateway/platforms/teams.py` |
| Operator tools | `tools/teams_pipeline_operator_tools.py` |

## Pipeline States

Hermes persists explicit lifecycle state for each job:

- `received`
- `resolving_meeting`
- `fetching_transcript`
- `downloading_recording`
- `transcribing_audio`
- `summarizing`
- `writing_notion`
- `writing_linear`
- `sending_teams`
- `completed`
- `failed`
- `retry_scheduled`

## Idempotency

The pipeline stores:

- subscription metadata
- notification receipts for dedupe
- per-job state
- sink records for upsert behavior

Duplicate Graph notifications reuse the existing job instead of creating a second run.

## Operator Surface

CLI:

```bash
hermes teams-pipeline list
hermes teams-pipeline show <job_id>
hermes teams-pipeline run <job_id>
hermes teams-pipeline fetch --meeting-id <meeting_id>
```

Agent tools:

- `teams_pipeline_list_jobs`
- `teams_pipeline_replay_job`
- `teams_pipeline_dry_run_fetch`

## Delivery Scope

Teams outbound delivery currently supports:

- incoming webhook posting
- explicit delegated Graph bearer-token posting

It does not yet turn Hermes into a general-purpose interactive Teams bot.

## Related Docs

- [Teams Meeting Pipeline Setup](/docs/user-guide/messaging/teams-meetings)
- [Messaging Gateway](/docs/user-guide/messaging/index)
