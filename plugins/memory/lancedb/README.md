# LanceDB Memory Provider

Local-first semantic memory for Hermes Agent using LanceDB as the vector store and a small SQLite metadata index for profile views, dedupe, and query-time filtering.

## What It Does

- Stores durable memories explicitly via tools and mirrored built-in memory writes
- Captures conversation turns as lower-priority episodic memories
- Recalls relevant memories before each turn in `context` or `hybrid` mode
- Keeps all data inside `$HERMES_HOME` by default

## Setup

```bash
hermes memory setup    # select "lancedb"
```

Or manually:

```bash
hermes config set memory.provider lancedb
```

Default storage path:

```text
$HERMES_HOME/lancedb/
```

## Dependencies

Default backend:

```bash
uv pip install lancedb
export OPENAI_API_KEY=...
```

Optional local embedding backend:

```bash
uv pip install sentence-transformers
```

## Tools

- `lancedb_search` — semantic search over durable and episodic memories
- `lancedb_store` — store an explicit durable memory
- `lancedb_forget` — delete memories by id or top query matches
- `lancedb_profile` — inspect the current durable profile view

## Config

Config file: `$HERMES_HOME/lancedb.json`

```json
{
  "db_path": "$HERMES_HOME/lancedb",
  "table_name": "memories",
  "embedding_backend": "openai",
  "embedding_model": "text-embedding-3-small",
  "memory_mode": "hybrid",
  "auto_recall": true,
  "auto_capture": true,
  "max_prefetch_results": 6,
  "max_tool_results": 8
}
```

### Key options

| Key | Default | Description |
|-----|---------|-------------|
| `db_path` | `$HERMES_HOME/lancedb` | LanceDB directory |
| `table_name` | `memories` | Table used for vector search |
| `embedding_backend` | `openai` | `openai` or `sentence-transformers` |
| `embedding_model` | `text-embedding-3-small` | Embedding model name |
| `memory_mode` | `hybrid` | `hybrid`, `context`, or `tools` |
| `auto_recall` | `true` | Inject recalled memories before each turn |
| `auto_capture` | `true` | Store user/assistant turns as episodic memory |

## Storage Model

Hermes stores three logical record types in one table:

- `profile` — identity, preferences, stable user facts
- `memory` — durable project facts, decisions, constraints
- `episode` — lower-priority conversation snippets

Only durable records are surfaced by `lancedb_profile`. Prefetch blends durable memories with episodic context.
