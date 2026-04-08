# Nowledge Mem Memory Provider

Cross-tool knowledge graph memory for Hermes Agent. Decisions, procedures,
lessons, and conversation context can stay available across Hermes, Cursor,
Claude Code, Codex, Gemini, and other Nowledge Mem integrations.

## Requirements

- Nowledge Mem desktop app or reachable server
- `nmem` CLI on PATH

If the desktop app is installed on the same machine, `nmem` is already
bundled. Otherwise:

```bash
pip install nmem-cli
```

## Setup

```bash
hermes memory setup
```

Select `nowledge-mem` from the provider picker.

Or configure manually:

```bash
hermes config set memory.provider nowledge-mem
```

## What It Does

- injects Working Memory into the system prompt
- prefetches relevant memories before each turn
- mirrors Hermes user-profile writes into Nowledge Mem
- exposes native tools for search, save, update, delete, and thread lookup

The provider does not claim transcript-backed session import or silent
background distillation. Durable saves still happen through the native
`nmem_` tools, with guidance in the provider prompt telling Hermes when to
use them.

## Tools

| Tool | Purpose |
|------|---------|
| `nmem_search` | Search durable memories |
| `nmem_save` | Save a decision, learning, or durable fact |
| `nmem_update` | Refine an existing memory |
| `nmem_delete` | Delete one or more memories |
| `nmem_thread_search` | Search past conversations |
| `nmem_thread_messages` | Fetch messages from a thread |

## Configuration

The only provider-specific setting is the request timeout:

```json
{
  "timeout": 30
}
```

Stored at:

```text
$HERMES_HOME/nowledge-mem.json
```

Server URL and API key are managed by `nmem`, not the provider.

For remote Mem, configure the machine running Hermes with:

```bash
nmem config client set url https://your-server:14242
nmem config client set api-key your-key
```

That writes the shared local client config `nmem` reads. It is separate from
server-side Access Anywhere or bind/allowlist settings on the Mem host.

## Verify

Ask Hermes:

> Search my memories for recent decisions.

The provider should call `nmem_search` and return results from Nowledge Mem.

Then ask:

> Save a memory that the Hermes Nowledge Mem plugin test passed.

The provider should call `nmem_save`. If Hermes instead falls back to its
built-in `memory` tool, you are running a build older than `0.5.5`.
