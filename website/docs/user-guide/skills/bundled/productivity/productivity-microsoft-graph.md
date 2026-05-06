---
title: "Microsoft Graph"
sidebar_label: "Microsoft Graph"
description: "Operate Microsoft Graph subscriptions and the Teams meeting pipeline from Hermes"
---

{/* This page mirrors the bundled skill source at `skills/productivity/microsoft-graph/SKILL.md`. */}

# Microsoft Graph

Operate Microsoft Graph subscriptions and the Teams meeting pipeline from Hermes.

## What It Covers

- Graph subscription setup and lifecycle
- token health inspection
- subscription maintenance and renewal
- Teams meeting artifact inspection
- Teams pipeline job listing and replay

## Fast Commands

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

## References

- Skill source: `skills/productivity/microsoft-graph/SKILL.md`
- Sample env: `skills/productivity/microsoft-graph/references/sample-env.md`
- Sample subscription body: `skills/productivity/microsoft-graph/references/sample-subscription.json`
- Main feature docs: [Teams Meeting Pipeline](/docs/user-guide/features/teams-meeting-pipeline)
