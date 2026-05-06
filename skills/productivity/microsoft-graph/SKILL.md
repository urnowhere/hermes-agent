---
name: microsoft-graph
description: Operate Microsoft Graph subscriptions and the Teams meeting pipeline. Use when the user needs Graph subscription setup, token health checks, Teams meeting artifact inspection, or failed-job replay.
version: 1.0.0
metadata:
  author: community
  license: MIT
  tags: [microsoft-graph, teams, meetings, webhook, productivity]
---

# Microsoft Graph

Use the built-in Microsoft Graph tools and CLI surfaces to operate Hermes' Teams meeting pipeline.

## Setup

Store Graph credentials in `~/.hermes/.env`:

```bash
MSGRAPH_TENANT_ID=...
MSGRAPH_CLIENT_ID=...
MSGRAPH_CLIENT_SECRET=...
MSGRAPH_WEBHOOK_ENABLED=true
MSGRAPH_WEBHOOK_PORT=8646
MSGRAPH_WEBHOOK_CLIENT_STATE=random-secret
```

Then start the gateway:

```bash
hermes gateway
```

## Operator Commands

CLI:

```bash
hermes teams-pipeline list
hermes teams-pipeline show <job_id>
hermes teams-pipeline run <job_id>
hermes teams-pipeline fetch --meeting-id <meeting_id>
hermes teams-pipeline subscriptions
hermes teams-pipeline subscribe --resource communications/onlineMeetings/getAllTranscripts --notification-url https://example.com/webhooks/msgraph
hermes teams-pipeline renew-subscription <subscription_id>
hermes teams-pipeline delete-subscription <subscription_id>
hermes teams-pipeline maintain-subscriptions --dry-run
hermes teams-pipeline token-health --force-refresh
hermes teams-pipeline validate
```

Agent tools:

- `microsoft_graph_list_subscriptions`
- `microsoft_graph_create_subscription`
- `microsoft_graph_renew_subscription`
- `microsoft_graph_delete_subscription`
- `microsoft_graph_inspect_token_health`
- `teams_pipeline_list_jobs`
- `teams_pipeline_replay_job`
- `teams_pipeline_dry_run_fetch`

## Common Workflows

### Check auth and subscriptions

1. Inspect token health.
2. List current subscriptions.
3. Renew or recreate the subscription if expiration is near.
4. Use `hermes teams-pipeline maintain-subscriptions` for bulk renewal and local store sync.

### Investigate a failed meeting job

1. `hermes teams-pipeline list --status failed`
2. `hermes teams-pipeline show <job_id>`
3. `hermes teams-pipeline fetch --meeting-id <meeting_id>`
4. `hermes teams-pipeline run <job_id>`

### Validate webhook ingestion

Confirm:

- `MSGRAPH_WEBHOOK_ENABLED=true`
- the gateway is running
- `MSGRAPH_WEBHOOK_CLIENT_STATE` matches the subscription
- the Graph subscription points at the Hermes webhook URL

## Notes

- Hermes deduplicates Graph notifications by receipt key.
- The pipeline prefers native transcripts over STT fallback.
- Teams outbound delivery is best-effort via incoming webhook unless you already have a delegated Graph posting token.
- When `msgraph_webhook` is enabled, the gateway also runs a background subscription maintenance loop and warns when local subscription state looks stale.
