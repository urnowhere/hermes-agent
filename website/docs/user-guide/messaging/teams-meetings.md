---
sidebar_position: 18
title: "Teams Meeting Pipeline"
description: "Process completed Microsoft Teams meetings through Microsoft Graph, summarize them, and deliver the result to Notion, Linear, and Teams"
---

# Teams Meeting Pipeline

Hermes can run a production-grade post-meeting pipeline for Microsoft Teams:

1. Microsoft Teams meeting ends
2. Microsoft Graph sends a change notification
3. Hermes resolves meeting metadata, transcripts, recordings, and call records
4. Hermes prefers the native transcript when available
5. Hermes falls back to recording download + STT when needed
6. Hermes generates a structured summary with decisions, action items, and risks
7. Hermes writes the result to Notion and/or Linear
8. Hermes posts the summary to a Teams channel

This is not a full conversational Teams bot. It is an event-driven meeting processing pipeline.

## Overview

| Component | Purpose |
|-----------|---------|
| `platforms.msgraph_webhook` | Receives Graph change notifications |
| Microsoft Graph app credentials | Auth for meeting, transcript, recording, and call-record fetches |
| `tools/teams_pipeline.py` | Orchestrates transcript-first execution and STT fallback |
| `platforms.teams` | Sends the final summary to a Teams channel |
| `teams_pipeline_store.json` | Durable local state for subscriptions, receipts, jobs, and sink records |

## Quick Setup

Use the interactive setup wizard:

```bash
hermes gateway setup
```

Select **Microsoft Teams Meeting Pipeline**.

The wizard can store:

- `MSGRAPH_TENANT_ID`
- `MSGRAPH_CLIENT_ID`
- `MSGRAPH_CLIENT_SECRET`
- `MSGRAPH_WEBHOOK_ENABLED`
- `MSGRAPH_WEBHOOK_PORT`
- `MSGRAPH_WEBHOOK_CLIENT_STATE`
- `TEAMS_DELIVERY_MODE`
- `TEAMS_INCOMING_WEBHOOK_URL` or delegated Graph posting values
- optional `NOTION_API_KEY`
- optional `LINEAR_API_KEY`

## Azure App Registration

Create an Azure app registration for Hermes and grant the Microsoft Graph permissions your tenant requires for:

- online meeting metadata
- meeting transcripts
- meeting recordings
- call records

At minimum, verify the scopes needed for your tenant’s transcript and recording endpoints before creating the subscription.

Store the credentials in `~/.hermes/.env`:

```bash
MSGRAPH_TENANT_ID=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
MSGRAPH_CLIENT_ID=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
MSGRAPH_CLIENT_SECRET=super-secret
```

## Webhook Listener

Enable the dedicated Graph webhook listener:

```bash
MSGRAPH_WEBHOOK_ENABLED=true
MSGRAPH_WEBHOOK_PORT=8646
MSGRAPH_WEBHOOK_CLIENT_STATE=replace-with-a-random-secret
```

Then start the gateway:

```bash
hermes gateway
```

The Graph validation endpoint is served by the `msgraph_webhook` adapter. Use the listener URL when you create the Graph subscription.

## Teams Delivery Modes

Hermes supports two outbound Teams delivery modes.

### Incoming Webhook

Recommended for most deployments.

```bash
TEAMS_ENABLED=true
TEAMS_DELIVERY_MODE=incoming_webhook
TEAMS_INCOMING_WEBHOOK_URL=https://outlook.office.com/webhook/...
TEAMS_CHANNEL_ID=19:channel-id@thread.tacv2
TEAMS_HOME_CHANNEL=19:channel-id@thread.tacv2
```

### Graph Channel Posting

Supported when you already have a valid delegated bearer token for channel posting:

```bash
TEAMS_ENABLED=true
TEAMS_DELIVERY_MODE=graph
TEAMS_GRAPH_ACCESS_TOKEN=delegated-bearer-token
TEAMS_TEAM_ID=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
TEAMS_CHANNEL_ID=19:channel-id@thread.tacv2
TEAMS_HOME_CHANNEL=19:channel-id@thread.tacv2
```

:::warning
The core Graph foundation in Hermes is app-auth oriented. Generic Teams channel posting through Graph usually requires delegated semantics. If you do not already have that token path, use the incoming webhook mode.
:::

## Optional Sink Credentials

```bash
NOTION_API_KEY=ntn_...
LINEAR_API_KEY=lin_api_...
```

The pipeline runtime already includes deterministic Notion and Linear writers. Destination IDs still need to be supplied in the pipeline configuration used by your runtime.

## Operator Commands

Hermes exposes a dedicated CLI surface:

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
hermes teams-pipeline validate --skip-remote
```

Use these to inspect recent jobs, replay a failed job, or dry-run Graph artifact resolution.
The same command family now covers routine subscription lifecycle checks, renewals, token validation, local store synchronization, and maintenance dry-runs before expiry.

## Subscription Maintenance

Hermes now has three layers of subscription hardening:

1. `hermes teams-pipeline validate`
   Syncs visible Graph subscriptions into the local Teams pipeline store and checks config completeness.
2. `hermes teams-pipeline maintain-subscriptions`
   Finds subscriptions nearing expiry and renews them, or shows candidates with `--dry-run`.
3. Gateway startup and runtime checks
   Hermes warns at startup when the local store is empty, expired, or close to expiry, and the gateway runs a background maintenance loop when `msgraph_webhook` is enabled.

Recommended operator sequence:

```bash
hermes teams-pipeline validate
hermes teams-pipeline maintain-subscriptions --dry-run
hermes teams-pipeline maintain-subscriptions
```

## Live Validation Runbook

Use this checklist when moving from mocked tests to a real Microsoft 365 tenant.

### 1. Create and consent the Azure app

Confirm all of the following before touching Hermes:

1. The Azure app registration exists.
2. Required Microsoft Graph application permissions are granted.
3. Admin consent has been completed for the tenant.

### 2. Configure Hermes

Populate `~/.hermes/.env` and restart the gateway:

```bash
hermes gateway
```

Then verify local config and Graph reachability:

```bash
hermes teams-pipeline validate
hermes teams-pipeline token-health --force-refresh
```

### 3. Create or sync the Graph subscription

If you have not created a subscription yet, create one with the Teams pipeline CLI. If it already exists, sync it into the local store:

```bash
hermes teams-pipeline subscriptions
hermes teams-pipeline validate
```

If startup warns that subscriptions are near expiry, run:

```bash
hermes teams-pipeline maintain-subscriptions --dry-run
hermes teams-pipeline maintain-subscriptions
```

### 4. Verify the Graph validation handshake

Create or renew the subscription against the live Hermes webhook URL and confirm Microsoft Graph accepts the validation token echo path. This proves the webhook listener is reachable from Graph.

### 5. Trigger a real notification

Use either:

1. a real meeting completion flow in Teams, or
2. a controlled tenant test that produces the same Graph notification resource you subscribed to

Then check that Hermes accepted the notification and created a pipeline job:

```bash
hermes teams-pipeline list
```

### 6. Verify artifact resolution

Inspect the resulting job and dry-run the underlying fetch path:

```bash
hermes teams-pipeline show <job_id>
hermes teams-pipeline fetch --meeting-id <meeting_id>
```

Confirm:

1. meeting metadata resolves correctly
2. transcript is present when expected
3. recording artifacts appear when transcript is absent
4. call-record enrichment behaves as expected for your tenant

### 7. Verify transcript-first and fallback behavior

Check which branch Hermes chose:

1. transcript-first path when a usable native transcript exists
2. recording + STT fallback when transcript is missing or unusable

If needed, replay the job after artifacts finish indexing:

```bash
hermes teams-pipeline run <job_id>
```

### 8. Verify sink delivery

Confirm the final output lands in every enabled sink:

1. Teams channel summary arrives once
2. Notion record is created or updated
3. Linear record is created or updated

### 9. Verify idempotency

Replay the same job or resend the same notification scenario and confirm Hermes does not duplicate sink output unexpectedly.

### 10. Verify logs and provenance

Review gateway logs and stored job JSON to confirm:

1. artifact provenance is visible
2. transcript vs recording branch is explicit
3. sink success/failure is visible
4. timing/latency information is present enough for operator debugging

## Go-Live Checklist

Use this as the final pre-production gate for a real tenant.

### Tenant and Identity

- [ ] Azure app registration exists for Hermes
- [ ] tenant admin consent has been granted
- [ ] `MSGRAPH_TENANT_ID`, `MSGRAPH_CLIENT_ID`, and `MSGRAPH_CLIENT_SECRET` are stored in `~/.hermes/.env`
- [ ] `hermes teams-pipeline token-health --force-refresh` succeeds

### Webhook and Subscription

- [ ] `MSGRAPH_WEBHOOK_ENABLED=true` is set
- [ ] Hermes webhook listener is reachable from Microsoft Graph
- [ ] subscription create or renew succeeds against the live webhook URL
- [ ] `MSGRAPH_WEBHOOK_CLIENT_STATE` is set to a tenant-specific secret
- [ ] `hermes teams-pipeline validate` returns `ok: true`
- [ ] `hermes teams-pipeline maintain-subscriptions --dry-run` shows the expected managed subscriptions only

### Pipeline Resolution

- [ ] a real Teams meeting completion event creates exactly one Hermes pipeline job
- [ ] `hermes teams-pipeline show <job_id>` contains the expected meeting reference
- [ ] transcript-first path works for a meeting with native transcript availability
- [ ] recording download + STT fallback works for a meeting without a usable transcript
- [ ] no duplicate sink writes occur when the same job is replayed

### Output Sinks

- [ ] Teams summary delivery works in the chosen delivery mode
- [ ] Notion write succeeds for the configured destination
- [ ] Linear write succeeds for the configured destination
- [ ] sink records in `teams_pipeline_store.json` reflect the final delivery state

### Runtime and Operations

- [ ] gateway startup shows no fatal preflight errors
- [ ] startup freshness warnings are understood or cleared
- [ ] background maintenance runs only when `msgraph_webhook` is actually connected
- [ ] subscription renewals only target Hermes-managed subscriptions
- [ ] operator knows the replay path: `hermes teams-pipeline run <job_id>`

## Tenant Validation Worksheet

Copy this into your deployment notes and fill it out during the first live tenant run.

| Field | Value |
|-------|-------|
| Validation date | |
| Environment | dev / staging / prod |
| Microsoft 365 tenant | |
| Azure app display name | |
| Hermes host / base URL | |
| Webhook URL | |
| Delivery mode | incoming_webhook / graph |
| Teams target | |
| Notion target | |
| Linear target | |
| Validator | |

### Validation Commands

Run and capture the result of each:

```bash
hermes teams-pipeline validate
hermes teams-pipeline token-health --force-refresh
hermes teams-pipeline subscriptions
hermes teams-pipeline maintain-subscriptions --dry-run
```

Record:

- validation output summary:
- token refresh result:
- visible subscription count:
- managed renewal candidate count:

### Live Meeting Samples

Record at least two meetings:

| Sample | Meeting ID | Transcript path | Recording fallback used | Final job ID | Teams sent | Notion sent | Linear sent |
|--------|------------|-----------------|--------------------------|--------------|------------|-------------|-------------|
| A | | native transcript | no | | yes / no | yes / no | yes / no |
| B | | STT fallback | yes | | yes / no | yes / no | yes / no |

### Sign-Off

- [ ] subscription ownership behavior verified
- [ ] webhook validation handshake verified
- [ ] transcript-first behavior verified
- [ ] recording/STT fallback verified
- [ ] sink idempotency verified
- [ ] operator handoff complete

## Transcript-First Behavior

Hermes prefers the native Graph transcript when it is available and long enough to be trustworthy. If no usable transcript exists:

1. Hermes lists recording artifacts
2. Hermes downloads the preferred recording
3. Hermes extracts audio with `ffmpeg` when required
4. Hermes runs the existing STT runtime
5. Hermes summarizes the derived transcript with an explicit confidence note

## Sample Environment

```bash
MSGRAPH_TENANT_ID=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
MSGRAPH_CLIENT_ID=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
MSGRAPH_CLIENT_SECRET=super-secret
MSGRAPH_WEBHOOK_ENABLED=true
MSGRAPH_WEBHOOK_PORT=8646
MSGRAPH_WEBHOOK_CLIENT_STATE=replace-with-a-random-secret

TEAMS_ENABLED=true
TEAMS_DELIVERY_MODE=incoming_webhook
TEAMS_INCOMING_WEBHOOK_URL=https://outlook.office.com/webhook/...
TEAMS_CHANNEL_ID=19:channel-id@thread.tacv2

NOTION_API_KEY=ntn_...
LINEAR_API_KEY=lin_api_...
```

## Troubleshooting

1. `hermes teams-pipeline fetch --meeting-id ...` fails immediately:
   Check `MSGRAPH_TENANT_ID`, `MSGRAPH_CLIENT_ID`, and `MSGRAPH_CLIENT_SECRET`.
2. Webhook notifications do not arrive:
   Verify `MSGRAPH_WEBHOOK_CLIENT_STATE`, the subscription URL, and that the gateway is running.
3. Startup warns that subscriptions are expiring or missing:
   Run `hermes teams-pipeline validate` and then `hermes teams-pipeline maintain-subscriptions --dry-run`.
4. Transcript missing:
   This is often a timing issue. Re-run the job later with `hermes teams-pipeline run <job_id>`.
5. Recording fallback fails:
   Confirm `ffmpeg` is installed and that the recording endpoint is permitted in your tenant.
6. Teams delivery fails:
   Prefer `incoming_webhook` unless you already have a delegated Graph posting path.
