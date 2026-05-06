---
name: teams-meeting-pipeline
description: Troubleshoot and operate the Microsoft Teams meeting summary pipeline end-to-end. Use when the user wants to inspect meeting artifacts, replay jobs, verify sink delivery, or understand transcript-versus-recording behavior.
version: 1.0.0
metadata:
  author: community
  license: MIT
  tags: [teams, meetings, transcript, recording, notion, linear]
---

# Teams Meeting Pipeline

Operate Hermes' Teams meeting pipeline from Graph notification through sink delivery.

## What Hermes Does

1. Receives Graph change notifications
2. Resolves the meeting
3. Fetches the preferred transcript
4. Falls back to recording + STT if needed
5. Generates a structured summary
6. Writes to Notion / Linear
7. Posts the result to a Teams channel

## Fast Checks

### List recent jobs

```bash
hermes teams-pipeline list
hermes teams-pipeline list --status failed
```

### Inspect one job

```bash
hermes teams-pipeline show <job_id>
```

### Replay a failed or partial job

```bash
hermes teams-pipeline run <job_id>
```

### Dry-run artifact resolution

```bash
hermes teams-pipeline fetch --meeting-id <meeting_id>
```

### Validate and maintain subscriptions

```bash
hermes teams-pipeline validate
hermes teams-pipeline maintain-subscriptions --dry-run
```

## Troubleshooting Order

1. Verify Graph credentials and token health.
2. Validate and sync remote Graph subscriptions into the local store.
3. If startup warns about expiry, run `maintain-subscriptions` before waiting for failures.
4. Verify the Graph webhook is enabled and receiving notifications.
5. Verify transcript availability.
6. If transcript is missing, verify recording availability.
7. If recording fallback fails, verify `ffmpeg` and STT configuration.
8. Check Notion / Linear / Teams sink credentials and target IDs.

## Delivery Modes

Preferred Teams delivery:

```bash
TEAMS_DELIVERY_MODE=incoming_webhook
TEAMS_INCOMING_WEBHOOK_URL=...
```

Alternate Graph delivery:

```bash
TEAMS_DELIVERY_MODE=graph
TEAMS_GRAPH_ACCESS_TOKEN=...
TEAMS_TEAM_ID=...
TEAMS_CHANNEL_ID=...
```

## Notes

- Duplicate notifications are deduplicated before replaying the workflow.
- Transcript-first is the expected happy path.
- Recording fallback should be treated as normal degradation, not as a hard failure.
- If sink delivery partially fails, inspect the stored sink record before rerunning to avoid duplicate output.
- When `msgraph_webhook` is enabled, Hermes warns about stale subscription state at startup and runs background subscription maintenance.
