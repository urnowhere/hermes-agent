---
title: "Teams Meeting Pipeline"
sidebar_label: "Teams Meeting Pipeline"
description: "Troubleshoot and operate Hermes' Teams meeting summary pipeline"
---

{/* This page mirrors the bundled skill source at `skills/productivity/teams-meeting-pipeline/SKILL.md`. */}

# Teams Meeting Pipeline

Use this skill when the task is operational rather than conversational: inspect meeting artifacts, replay a failed job, validate sink delivery, or understand why Hermes chose transcript-first versus recording fallback.

## Operator Commands

```bash
hermes teams-pipeline list
hermes teams-pipeline show <job_id>
hermes teams-pipeline run <job_id>
hermes teams-pipeline fetch --meeting-id <meeting_id>
hermes teams-pipeline validate
hermes teams-pipeline maintain-subscriptions --dry-run
```

## Typical Flow

1. List recent jobs and filter failures.
2. Validate Graph config and subscription state.
3. Dry-run artifact resolution against Graph.
4. Replay the job once transcript or recording artifacts are ready.
5. Use subscription maintenance when startup warnings say expiry is near.

## References

- Skill source: `skills/productivity/teams-meeting-pipeline/SKILL.md`
- Sample config: `skills/productivity/teams-meeting-pipeline/references/sample-config.yaml`
- Sample webhook payload: `skills/productivity/teams-meeting-pipeline/references/sample-webhook-payload.json`
- Sample summary payload: `skills/productivity/teams-meeting-pipeline/references/sample-summary-payload.json`
- Main setup doc: [Teams Meeting Pipeline Setup](/docs/user-guide/messaging/teams-meetings)
