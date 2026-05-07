# Realtime Activity Ledger Requirements

> **For Hermes / Codex:** Evaluate the design first, then implement the smallest production-quality MVP that satisfies the acceptance criteria. Keep it lightweight and do not create a second memory system.

**Goal:** Add a lightweight, realtime, append-only activity ledger for Hermes turns and important events so daily reports and audits can use structured facts instead of relying primarily on keyword-based `session_search`.

**Architecture:** The ledger is a sidecar factual index, not a memory provider and not a replacement for the existing session DB/transcripts. It should write small JSONL records at conversation time via existing gateway lifecycle hooks where possible. Daily report jobs may consume it later, but the ledger itself must not require a daily backfill cron.

**Tech Stack:** Python, existing Hermes gateway hooks, existing `get_hermes_home()` path helpers, JSONL files, pytest.

---

## Background

Hermes already has several persistence layers:

- persistent memory / user profile: small durable facts that are injected into future sessions;
- session DB and legacy transcripts: full conversation history;
- logs: operational debugging;
- domain-specific event logs such as PR auto-review events.

The missing piece is not another memory store. The missing piece is a small factual ledger for questions like:

- What meaningful work happened today?
- Which turns used tools and produced artifacts?
- Which decisions, configuration changes, skill updates, PR reviews, cron edits, or integration work should be considered by the daily report?
- Where should the daily report look before falling back to brittle keyword search?

Recent daily-report gaps showed that keyword recall is unreliable. Important work can exist in sessions but still be missed because queries are too narrow, because PR events dominate the report, or because a content/design outcome is compressed into a technical PR status.

## Non-Goals

This feature must **not**:

1. Replace or compete with Hermes persistent memory.
2. Automatically inject ledger records into the system prompt or memory context.
3. Store full transcripts, secrets, raw tool outputs, credentials, tokens, cookies, OAuth payloads, or connection strings.
4. Add a daily cron job that scans all sessions after the fact as the primary collection mechanism.
5. Build topic clustering, daily summarization, semantic search, or a second session index in the MVP.
6. Require users to configure a database service.
7. Break prompt caching by mutating tools/system prompt mid-conversation.

## Core Design Principles

### 1. Sidecar, not memory

The activity ledger is only a structured factual index for reporting/audit. It should not be loaded into normal conversations unless explicitly read by a user/tool/report job.

### 2. Realtime append

Collection should happen at turn time, preferably on existing events such as:

- `session:start`
- `agent:start`
- `agent:step`
- `agent:end`
- `command:*`

The MVP should avoid any design that depends on a nightly collector to discover what happened.

### 3. Small and boring JSONL

Prefer date-partitioned JSONL over SQLite or a new service for the MVP:

```text
$HERMES_HOME/activity-ledger/YYYY-MM-DD/turns.jsonl
$HERMES_HOME/activity-ledger/YYYY-MM-DD/events.jsonl
```

The date should be derived from local time in a predictable way. If the project already has a timezone helper/config convention, use it. Otherwise use system local time and keep the implementation simple.

### 4. Redacted previews, not full content

Turn records may store short previews of user message and assistant response, but must not store raw full message bodies by default.

### 5. Explicit events beat inference

The MVP may start with turn skeletons from hooks. Future work can add explicit event logging from tools such as skill management, cron management, PR review, config edits, and a possible `activity_log` tool. Do not overbuild this in the first pass unless it is clearly cheap and well-contained.

## Required MVP Behavior

### A. Realtime turn ledger

On each completed gateway agent turn, append one record to:

```text
$HERMES_HOME/activity-ledger/YYYY-MM-DD/turns.jsonl
```

Suggested schema:

```json
{
  "schema_version": 1,
  "id": "turn_20260506T160112Z_<short-random-or-stable-suffix>",
  "time": "2026-05-07T00:01:12+08:00",
  "date": "2026-05-07",
  "type": "turn",
  "session_id": "20260506_...",
  "platform": "discord",
  "user_id": "<stable platform id if already available; omit or hash if privacy config requires it>",
  "chat_id": "<if already available and safe under existing gateway privacy rules>",
  "thread_id": "<if already available>",
  "message_preview": "short redacted preview, max 500 chars",
  "response_preview": "short redacted preview, max 500 chars",
  "tool_names": ["read_file", "terminal"],
  "reportable": "unknown",
  "visibility": "private"
}
```

Implementation notes:

- Existing `agent:start` hook context includes platform/user/session/message preview.
- Existing `agent:step` hook context includes tool names.
- Existing `agent:end` hook context includes response preview.
- If hook contexts do not share state directly, implement a small process-local accumulator keyed by `session_id` and/or another safe turn key.
- Hook failures must never block the user turn.

### B. Optional explicit event helper, if cheap

Evaluate whether it is low-risk to add a small internal helper such as:

```python
record_activity_event(type, title, summary, session_id=None, artifacts=None, visibility="private", reportable=True, metadata=None)
```

If implemented, append records to:

```text
$HERMES_HOME/activity-ledger/YYYY-MM-DD/events.jsonl
```

Suggested schema:

```json
{
  "schema_version": 1,
  "id": "evt_20260506T160112Z_<suffix>",
  "time": "2026-05-07T00:01:12+08:00",
  "date": "2026-05-07",
  "type": "design_decision",
  "source": "agent_or_tool_or_hook",
  "session_id": "20260506_...",
  "title": "Daily report should consume realtime ledger before session_search",
  "summary": "Ledger is a sidecar factual index and must not be injected as memory.",
  "artifacts": [],
  "visibility": "private",
  "reportable": true,
  "metadata": {}
}
```

Do not add broad automatic LLM summarization for event extraction in the MVP.

### C. Configuration

Add a small config surface if needed. Suggested defaults:

```yaml
activity_ledger:
  enabled: false
  capture_turns: true
  capture_commands: true
  max_preview_chars: 500
```

Default can be `false` if maintainers prefer opt-in, or `true` if the implementation is clearly safe and privacy-preserving. Codex should evaluate current project conventions and choose the safer default.

If config is added, document it briefly and add tests for defaults.

### D. Redaction / privacy

The ledger must apply conservative local redaction before writing previews. At minimum redact common secret-looking fields and values:

- `api_key`
- `token`
- `secret`
- `password`
- `private key`
- `client_secret`
- `cookie`
- bearer tokens
- GitHub PAT-looking values
- connection strings where obvious

Use existing redaction utilities if the codebase has them. If not, implement a small helper near the ledger module and keep it deliberately conservative.

Respect existing privacy settings where applicable. If gateway privacy config hashes or redacts user IDs before model context, do not bypass that policy in ledger writes.

### E. Atomic-ish append and robustness

- Ensure parent directories are created.
- Append one JSON object per line.
- Use UTF-8.
- Avoid throwing exceptions to the gateway main path.
- If a write fails, log debug/warning but do not fail the turn.
- Keep files profile-aware by using `get_hermes_home()` or the current canonical helper.

## Suggested Implementation Shape

Codex should evaluate the repository and choose exact paths. A likely approach:

1. Add a small module, for example:
   - `gateway/activity_ledger.py` or `agent/activity_ledger.py`
2. Register a built-in gateway hook or wire into existing hook emission in a minimal way.
3. Keep state small and process-local:
   - on `agent:start`, remember message preview and metadata;
   - on `agent:step`, accumulate tool names;
   - on `agent:end`, write one turn record and clear the accumulator.
4. Add tests under `tests/gateway/` or another existing suitable test area.
5. Add short documentation if config/user-facing behavior is introduced.

If the existing user hook system can solve this without code changes, Codex should still consider whether a built-in implementation is preferable for tests, config, and daily report integration. Avoid dumping this into a user-specific hook under `~/.hermes/hooks` as the final repo implementation unless there is a clear reason.

## Daily Report Integration Contract

This task does not need to rewrite the daily report job. It should provide a reliable file contract that a future report job can consume:

```text
$HERMES_HOME/activity-ledger/<date>/turns.jsonl
$HERMES_HOME/activity-ledger/<date>/events.jsonl
```

A daily report job should be able to read those files and treat `session_search` as fallback, not primary source.

## Acceptance Criteria

1. A completed gateway turn writes exactly one turn record when the ledger is enabled.
2. The record is date-partitioned under `$HERMES_HOME/activity-ledger/YYYY-MM-DD/turns.jsonl`.
3. The record includes session id, platform, timestamp/date, redacted message preview, redacted response preview, and accumulated tool names when available.
4. Hook/ledger write failures do not break the user turn.
5. The implementation is profile-aware and does not hardcode `/data00/home/huangbaixi` or `~/.hermes`.
6. It does not write full transcripts or raw tool outputs.
7. It does not modify or depend on Hermes persistent memory.
8. It does not add a daily collection cron as the main mechanism.
9. Tests cover successful write, disabled config behavior, redaction, and failure isolation.
10. Any config/docs changes are minimal and clearly describe that the ledger is not memory.

## Evaluation Questions for Codex

Before implementing, answer these briefly in the final response and/or commit notes:

1. Should the MVP be implemented as a built-in gateway hook, direct gateway integration, or a general module called from hook events?
2. Should default `activity_ledger.enabled` be true or false, given project privacy conventions?
3. Is an explicit `record_activity_event` helper cheap enough for this PR, or should it be deferred?
4. Which existing redaction/privacy utilities should be reused?
5. Which tests give the best confidence without running the entire huge suite?

## Verification Commands

Codex should run the narrowest relevant tests first, then a broader sanity check if feasible. Examples, adjust to actual files:

```bash
python -m pytest tests/gateway/test_activity_ledger.py -q -o 'addopts='
python -m pytest tests/gateway/test_hooks.py -q -o 'addopts='
python -m pytest tests/gateway -q -o 'addopts='  # if runtime is acceptable
```

Also run:

```bash
git diff --check
```

## Commit / PR Expectations

- Work on branch: `feat/realtime-activity-ledger`.
- Keep changes scoped to this feature.
- Commit the requirements document first, then implementation commits.
- Push the branch when complete.
- Do not include user-specific files, secrets, local sessions, logs, or generated caches.
