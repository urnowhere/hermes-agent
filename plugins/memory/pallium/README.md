# Pallium Memory Provider

Local-first memory sidecar with automatic fact extraction, hybrid retrieval (lexical + vector RRF), and evidence provenance. No cloud dependency, no API key required.

Two extraction packages run in parallel over every ingested turn:

- **Work continuity** — decisions, investigation outcomes, task checkpoints ("why did we choose this?", "where did we leave off?")
- **Factual recall** — names, dates, preferences, events, relationships extracted from conversation threads, consolidated by subject across sessions

**Multilingual by design** — memory is preserved in the original language and cross-language recall works natively. A query in one language retrieves memory stored in another.

## Requirements

- Python 3.12+
- A running Pallium server
- An LLM API key for Pallium's extraction (Anthropic or OpenAI-compatible)

## Setup

**1. Install and start Pallium:**

```bash
git clone https://github.com/rotemhermon/pallium
cd pallium
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev,vector]"
cp pallium.example.toml pallium.local.toml
cp .env.example .env.local
# Set your LLM API key in .env.local

python -m app.run serve --host 127.0.0.1 --port 8000
```

**2. Configure Hermes:**

```bash
hermes memory setup    # select "pallium"
# Or manually:
hermes config set memory.provider pallium
```

**3. Verify:**

```bash
hermes memory status
curl http://localhost:8000/health
```

## Configuration

Config is stored in `$HERMES_HOME/pallium.json`:

| Key | Default | Description |
|-----|---------|-------------|
| `base_url` | `http://localhost:8000` | Pallium server URL |
| `actor_ref` | `hermes-user` | Stable user identifier for cross-session memory scoping |
| `container_ref` | `hermes` | Memory container identifier |

Example:

```json
{
  "base_url": "http://localhost:8000",
  "actor_ref": "your-name",
  "container_ref": "hermes"
}
```

## Tools

### `pallium_query`

Search Pallium's persistent memory for relevant context.

```
pallium_query(query="why did we choose event time for ordering?")
```

Returns compact evidence-backed cards: decisions, investigation outcomes, checkpoints, extracted facts.

### `pallium_remember`

Store an important piece of information explicitly.

```
pallium_remember(content="Decided to use PostgreSQL for the main store — SQLite won't scale past 10k users", kind="decision")
```

`kind` options: `note` (default), `decision`, `finding`.

## How Memory Is Built

Pallium runs automatic extraction in the background — the agent doesn't need to decide what to remember. Every ingested turn feeds two parallel packages:

1. **conversational_knowledge** — extracts atomic facts per thread (subject, statement, category), consolidates by subject across threads into `fact_summary` objects
2. **agent_conversation_memory** — extracts higher-level memories: decisions, investigation outcomes, task checkpoints, thread summaries

Retrieval combines IDF-weighted lexical search with vector similarity, fused via RRF (Reciprocal Rank Fusion). Every memory object is linked back to the source items it was derived from.

## Profile Isolation

Each Hermes profile gets isolated memory via `container_ref`. By default this is `hermes` — change it per profile to fully separate memory across profiles:

```json
{ "container_ref": "hermes-coder" }
```

## Debugging

Pallium exposes a debug endpoint that shows the full retrieval and routing trace:

```bash
curl -X POST http://localhost:8000/query/debug \
  -H 'Content-Type: application/json' \
  -d '{"query": "your question", "container_ref": "hermes"}'
```
