# Hermes Web Console API and Event Schema

Date: 2026-03-30

## Purpose

This document defines the Hermes-native backend contract for a fully featured GUI. The existing `/v1/*` OpenAI-compatible API remains unchanged. The GUI uses `/api/gui/*` plus SSE endpoints for structured, inspectable state.

## Design rules

1. Never leak raw secrets.
2. Keep `/v1/*` compatibility untouched.
3. Prefer thin wrappers around existing Hermes runtime/services.
4. Return capability metadata when actions are unsupported.
5. Use one event envelope format everywhere.

## API namespaces

- `/api/gui/health`
- `/api/gui/meta`
- `/api/gui/chat/*`
- `/api/gui/sessions/*`
- `/api/gui/workspace/*`
- `/api/gui/processes/*`
- `/api/gui/human/*`
- `/api/gui/memory/*`
- `/api/gui/user-profile/*`
- `/api/gui/session-search`
- `/api/gui/skills/*`
- `/api/gui/cron/*`
- `/api/gui/gateway/*`
- `/api/gui/settings/*`
- `/api/gui/logs/*`
- `/api/gui/browser/*`
- `/api/gui/media/*`
- `/api/gui/stream/*`

## Common envelope

### Success envelope

```json
{
  "ok": true,
  "data": {}
}
```

### Error envelope

```json
{
  "ok": false,
  "error": {
    "code": "not_found",
    "message": "Session not found",
    "details": {}
  }
}
```

### Capability envelope

```json
{
  "ok": true,
  "data": {
    "supported": false,
    "reason": "gateway_service_control_unavailable",
    "details": {
      "platform": "darwin"
    }
  }
}
```

## Authentication model

Default:
- localhost-only bind
- GUI respects API server bearer auth when configured

Headers:
- `Authorization: Bearer <token>` when `API_SERVER_KEY` or GUI auth is enabled

## Core resource shapes

### Session summary

```json
{
  "session_id": "sess_123",
  "title": "Build Hermes GUI",
  "source": "cli",
  "workspace": "/home/glitch/.hermes/hermes-agent",
  "model": "gpt-5.4",
  "provider": "openai-codex",
  "last_active": "2026-03-30T02:10:00Z",
  "token_summary": {
    "input": 12000,
    "output": 3400,
    "total": 15400
  },
  "parent_session_id": null,
  "has_tools": true,
  "has_attachments": false,
  "has_subagents": true
}
```

### Transcript item

```json
{
  "id": "msg_123",
  "type": "assistant_message",
  "role": "assistant",
  "content": "Done — I gave Hermes a browser GUI.",
  "created_at": "2026-03-30T02:10:00Z",
  "metadata": {
    "run_id": "run_123",
    "tool_call_ids": []
  }
}
```

### Tool timeline item

```json
{
  "id": "tool_123",
  "tool_name": "search_files",
  "status": "completed",
  "started_at": "2026-03-30T02:10:02Z",
  "completed_at": "2026-03-30T02:10:03Z",
  "duration_ms": 940,
  "arguments_preview": {
    "pattern": "api_server",
    "path": "/home/glitch/.hermes/hermes-agent"
  },
  "result_preview": {
    "match_count": 12
  },
  "error": null
}
```

### Pending human request

```json
{
  "request_id": "human_123",
  "kind": "approval",
  "session_id": "sess_123",
  "run_id": "run_123",
  "title": "Approve terminal command",
  "prompt": "Allow Hermes to run git status in /repo?",
  "choices": [
    "approve_once",
    "approve_session",
    "approve_always",
    "deny"
  ],
  "expires_at": "2026-03-30T02:15:00Z",
  "sensitive": false
}
```

### Workspace checkpoint

```json
{
  "checkpoint_id": "cp_123",
  "label": "Before destructive file patch",
  "created_at": "2026-03-30T02:11:00Z",
  "session_id": "sess_123",
  "run_id": "run_123",
  "file_count": 3
}
```

### Cron job summary

```json
{
  "job_id": "cron_123",
  "name": "Morning summary",
  "schedule": "0 9 * * *",
  "deliver": "telegram",
  "paused": false,
  "next_run_at": "2026-03-31T09:00:00Z",
  "last_run_at": "2026-03-30T09:00:00Z"
}
```

### Platform summary

```json
{
  "platform": "telegram",
  "enabled": true,
  "configured": true,
  "connected": true,
  "error": null,
  "home_channel": "12345678",
  "allowed_mode": "pair"
}
```

## Endpoint definitions

### GET /api/gui/health

Returns GUI/backend health.

```json
{
  "ok": true,
  "data": {
    "status": "ok",
    "product": "hermes-web-console"
  }
}
```

### GET /api/gui/meta

Returns version/build/runtime metadata.

```json
{
  "ok": true,
  "data": {
    "product": "hermes-web-console",
    "version": "0.1.0",
    "gui_mount_path": "/app",
    "api_base": "/api/gui",
    "stream_base": "/api/gui/stream",
    "v1_base": "/v1",
    "features": {
      "workspace": true,
      "gateway_admin": true,
      "voice": true
    }
  }
}
```

### POST /api/gui/chat/send

Request:

```json
{
  "session_id": "sess_123",
  "conversation": "browser-chat",
  "message": "Inspect the API server and summarize its routes.",
  "instructions": "Be concise.",
  "attachments": [],
  "model": "hermes-agent"
}
```

Response:

```json
{
  "ok": true,
  "data": {
    "session_id": "sess_123",
    "run_id": "run_987",
    "status": "started"
  }
}
```

### POST /api/gui/chat/stop

```json
{
  "run_id": "run_987"
}
```

### POST /api/gui/chat/retry

```json
{
  "session_id": "sess_123"
}
```

### POST /api/gui/chat/undo

```json
{
  "session_id": "sess_123"
}
```

### GET /api/gui/sessions

Query params:
- `q`
- `source`
- `workspace`
- `model`
- `has_tools`
- `limit`
- `offset`

### GET /api/gui/sessions/{session_id}

Returns metadata, recap, lineage.

### GET /api/gui/sessions/{session_id}/transcript

Returns normalized transcript and tool timeline.

### POST /api/gui/sessions/{session_id}/title

```json
{
  "title": "Hermes Web Console planning"
}
```

### DELETE /api/gui/sessions/{session_id}

Deletes a session if supported by Hermes storage.

### GET /api/gui/workspace/tree

Query params:
- `root`
- `depth`

### GET /api/gui/workspace/file

Query params:
- `path`

### GET /api/gui/workspace/search

Query params:
- `pattern`
- `path`
- `file_glob`

### GET /api/gui/workspace/diff

Query params:
- `path`
- `session_id`
- `run_id`

### GET /api/gui/workspace/checkpoints

Query params:
- `workspace`
- `session_id`

### POST /api/gui/workspace/rollback

```json
{
  "checkpoint_id": "cp_123",
  "path": null
}
```

### GET /api/gui/processes

Returns background process summaries.

### GET /api/gui/processes/{process_id}/log

Query params:
- `offset`
- `limit`

### POST /api/gui/processes/{process_id}/kill

Kills a tracked process.

### GET /api/gui/human/pending

Returns pending approvals and clarifications.

### POST /api/gui/human/approve

```json
{
  "request_id": "human_123",
  "scope": "once"
}
```

### POST /api/gui/human/deny

```json
{
  "request_id": "human_123"
}
```

### POST /api/gui/human/clarify

```json
{
  "request_id": "human_124",
  "response": "A web app you can open in a browser"
}
```

### GET /api/gui/memory

Query params:
- `target=memory|user`

### POST /api/gui/memory

```json
{
  "target": "memory",
  "action": "add",
  "content": "User prefers concise terminal-renderable responses."
}
```

### GET /api/gui/session-search

Query params:
- `query`
- `limit`

### GET /api/gui/skills

Query params:
- `category`

### GET /api/gui/skills/{name}

Returns skill markdown + metadata.

### POST /api/gui/skills/{name}/install

Optional admin action.

### GET /api/gui/cron/jobs

### POST /api/gui/cron/jobs

```json
{
  "name": "Morning summary",
  "prompt": "Summarize the latest issues.",
  "schedule": "0 9 * * *",
  "deliver": "telegram",
  "skills": ["github-issues"]
}
```

### PATCH /api/gui/cron/jobs/{job_id}

### POST /api/gui/cron/jobs/{job_id}/run

### POST /api/gui/cron/jobs/{job_id}/pause

### POST /api/gui/cron/jobs/{job_id}/resume

### DELETE /api/gui/cron/jobs/{job_id}

### GET /api/gui/gateway/overview

Returns:
- runtime state
- PID
- platform summary list
- delivery/home configuration summary

### GET /api/gui/gateway/platforms

Returns all platform config/status cards.

### GET /api/gui/gateway/pairing

Returns pending codes and approved identities.

### POST /api/gui/gateway/pairing/approve

```json
{
  "platform": "telegram",
  "code": "AB12CD34"
}
```

### POST /api/gui/gateway/pairing/revoke

```json
{
  "platform": "telegram",
  "user_id": "123456789"
}
```

### GET /api/gui/settings

Returns masked config snapshot split by sections.

### PATCH /api/gui/settings

Accepts partial updates for safe editable sections.

### GET /api/gui/logs

Query params:
- `kind=errors|gateway|cron|tool_debug`
- `offset`
- `limit`
- `follow`

### GET /api/gui/browser/status

Returns browser backend info, live connection state, recording paths if available.

### POST /api/gui/browser/connect

```json
{
  "mode": "live_chrome"
}
```

### POST /api/gui/browser/disconnect

### POST /api/gui/media/upload

Accepts multipart uploads and returns normalized attachment metadata.

### POST /api/gui/media/transcribe

Accepts uploaded audio reference and returns transcription.

### POST /api/gui/media/tts

Accepts text and returns generated media path metadata.

## SSE transport

### Endpoint

- `GET /api/gui/stream/session/{session_id}`

Optional query params:
- `run_id`
- `history=1` to replay recent buffered events

### Event envelope

```json
{
  "id": "evt_123",
  "type": "tool.started",
  "session_id": "sess_123",
  "run_id": "run_987",
  "ts": "2026-03-30T02:10:02.123Z",
  "payload": {}
}
```

### Required event types

#### run.started

```json
{
  "type": "run.started",
  "payload": {
    "message_id": "msg_1",
    "title": "Inspect API server routes"
  }
}
```

#### message.assistant.delta

```json
{
  "type": "message.assistant.delta",
  "payload": {
    "message_id": "msg_2",
    "delta": "The API server currently exposes"
  }
}
```

#### tool.started

```json
{
  "type": "tool.started",
  "payload": {
    "tool_call_id": "tool_1",
    "tool_name": "search_files",
    "arguments": {
      "pattern": "api_server"
    }
  }
}
```

#### tool.completed

```json
{
  "type": "tool.completed",
  "payload": {
    "tool_call_id": "tool_1",
    "duration_ms": 944,
    "summary": "12 matches"
  }
}
```

#### approval.requested

```json
{
  "type": "approval.requested",
  "payload": {
    "request_id": "human_1",
    "title": "Approve terminal command",
    "prompt": "Allow Hermes to run git status?",
    "choices": ["approve_once", "approve_session", "approve_always", "deny"]
  }
}
```

#### clarify.requested

```json
{
  "type": "clarify.requested",
  "payload": {
    "request_id": "human_2",
    "title": "Need clarification",
    "prompt": "What kind of GUI do you want?",
    "choices": [
      "A local desktop app",
      "A web app you can open in a browser",
      "A terminal UI (TUI)",
      "Something minimal: just a chat window"
    ]
  }
}
```

#### todo.updated

```json
{
  "type": "todo.updated",
  "payload": {
    "items": [
      {
        "id": "inspect",
        "content": "Inspect Hermes repo",
        "status": "completed"
      }
    ]
  }
}
```

#### subagent.completed

```json
{
  "type": "subagent.completed",
  "payload": {
    "subagent_id": "sub_1",
    "title": "Inspect gateway features",
    "summary": "Found pairing, status, and gateway config surfaces."
  }
}
```

## Security rules

1. Secrets from `.env`, auth stores, passwords, or secret prompts must never be returned.
2. Sensitive settings responses should return:
   - `present: true|false`
   - masked preview only when already displayed in CLI-equivalent UX
3. Service control endpoints should require capability checks and explicit confirmation in UI.
4. Remote GUI access should require auth if `host != 127.0.0.1` or if an API key is configured.

## Versioning

Add a `schema_version` field in `/api/gui/meta` and bump it whenever:
- event shape changes
- endpoint response shape changes
- required fields change

Suggested initial value:

```json
{
  "schema_version": 1
}
```
