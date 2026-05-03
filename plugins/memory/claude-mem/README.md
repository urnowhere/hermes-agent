# claude-mem — automatic hook-driven memory for Hermes

Cross-session memory backed by a local worker that captures observations automatically, indexes them with SQLite FTS5, and serves semantic recall via Chroma.

## What it is

Claude-mem runs as a local worker on `http://127.0.0.1:37777` and captures observations automatically (via hooks) as you work. It provides keyword search over SQLite FTS5 and semantic search over Chroma embeddings. Because the same worker is shared with Claude Code sessions, memory follows you across tools — what you did in a Hermes session is recallable from Claude Code and vice versa.

## Prerequisites

Install the worker globally and start it:

```bash
npm install -g @thedotmack/claude-mem
claude-mem worker start
```

Verify it's running:

```bash
curl http://127.0.0.1:37777/health
# {"status":"ok",...}
```

## Setup

```bash
hermes memory setup    # select "claude-mem" and accept the defaults
```

## Tools

| Tool | Description |
|------|-------------|
| `claude_mem_recall(query, limit?, obs_type?)` | Search past observations, decisions, and session summaries. |
| `claude_mem_save(text, title?)` | Store a durable fact the user explicitly wants remembered. |
| `claude_mem_timeline(anchor_id, depth_before?, depth_after?)` | Pull context around a specific observation. |

Most capture is automatic via lifecycle hooks — `claude_mem_save` is for the rare case where the user asks you to remember something that tool use alone wouldn't have captured.

## Lifecycle integration

The provider wires into the standard `MemoryProvider` lifecycle:

- `initialize` — registers the Hermes session with the worker.
- `sync_turn` — posts each user/assistant turn as an observation.
- `prefetch` / `queue_prefetch` — background semantic recall; results are injected into the next LLM prompt.
- `on_pre_compress` — queues a session summary before compression.
- `on_session_end` — marks the session complete so the worker can finalize it.

All writes run on daemon threads. The main loop never blocks on the worker.

## Configuration

Config file: `$HERMES_HOME/claude-mem.json`

| Key | Default | Description |
|-----|---------|-------------|
| `base_url` | `http://127.0.0.1:37777` | Worker URL. |
| `default_project` | `""` | Project name for scoping. Auto-detected from `agent_workspace` if empty. |

Environment overrides:

- `CLAUDE_MEM_WORKER_URL` — overrides `base_url`.
- `CLAUDE_MEM_DEFAULT_PROJECT` — overrides `default_project`.

## Troubleshooting

**Worker not running?** Check health:

```bash
curl http://127.0.0.1:37777/health
```

Should return `{"status":"ok",...}`. If it doesn't, run `claude-mem worker start`.

**Port conflict on 37777?** Start the worker on a different port (`CLAUDE_MEM_WORKER_PORT=NNNN claude-mem worker start`) and point the plugin at it via `base_url` in `$HERMES_HOME/claude-mem.json` or `CLAUDE_MEM_WORKER_URL`.

**Short queries return nothing from semantic recall.** The worker's `/api/context/semantic` endpoint silently returns empty for queries under 20 characters. The plugin falls back to FTS5 keyword search via `/api/search` in that case — this is expected behavior, not a bug.
