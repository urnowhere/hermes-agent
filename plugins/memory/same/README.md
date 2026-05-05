# SAME Memory Provider for Hermes Agent

[SAME](https://github.com/sgx-labs/statelessagent) (Stateless Agent Memory Engine)
is a self-hosted, offline-first knowledge store for AI agents — a Go binary that
indexes markdown notes into a local semantic vault with provenance and trust tracking.

This is the native [Hermes Agent](https://github.com/NousResearch/hermes-agent)
memory provider plugin. It gives Hermes automatic recall, session handoffs, and
decision logging without any cloud dependency.

## What it does

| Hook / Tool | What happens |
|---|---|
| `prefetch()` | Vault searched before every turn; relevant notes injected with trust tags |
| `on_session_end()` | Handoff note created automatically (what was requested, done, pending) |
| `on_pre_compress()` | Context saved to vault before compression discards it |
| `on_memory_write()` | MEMORY.md / USER.md writes mirrored to the vault |
| `same_search` | Semantic search with trust state metadata |
| `same_save_note` | Save a markdown note with provenance tracking |
| `same_save_decision` | Log a project decision with attribution |
| `same_get_note` | Read full note content by path |
| `same_health` | Vault health and index status |

## vs using SAME as an MCP server

You can also add SAME to `config.yaml` under `mcp.servers` and use it as a
plain MCP tool server. The native plugin is better because:

- **Automatic prefetch** before every turn (MCP requires explicit tool calls)
- **Session handoffs** generated on exit without agent intervention
- **Memory mirroring** — built-in Hermes memory writes sync to vault automatically
- **Compression safety** — facts saved before the context window is compressed
- **No race conditions** — plugin runs in Hermes's sequential execution path

Use the MCP approach if you want SAME available as tools only, without automatic hooks.

## Prerequisites

- [SAME](https://github.com/sgx-labs/statelessagent) installed (`same` on PATH)
- A vault initialized with `same init`
- Hermes Agent v0.7.0+

## Install

**If this plugin has been merged into hermes-agent:**

```bash
# Already included — just configure
hermes config set memory.provider same
export SAME_VAULT_PATH=/path/to/your/vault
```

**If installing standalone (from the SAME repo):**

```bash
# Install SAME (pick one)
brew install sgx-labs/tap/same
curl -fsSL https://statelessagent.com/install.sh | bash
npm install -g @sgx-labs/same

# Link the plugin into Hermes
ln -s /path/to/statelessagent/integrations/hermes ~/.hermes/plugins/memory/same
```

## Configure

```bash
# Via Hermes wizard
hermes memory setup

# Or manually
hermes config set memory.provider same
export SAME_VAULT_PATH=/path/to/your/vault
```

Or create `~/.hermes/same/config.json` for profile-scoped config:

```json
{
  "vault_path": "/path/to/your/vault",
  "binary": "same",
  "agent": "hermes"
}
```

## Vault Setup

Before indexing, add a `.sameignore` to your vault root. Without it,
framework docs and build artifacts dominate search results and bury your
actual project knowledge.

```bash
cp /path/to/statelessagent/templates/sameignore/hermes-agent.sameignore \
   $SAME_VAULT_PATH/.sameignore
same reindex
```

The `hermes-agent.sameignore` template excludes Hermes internals (`.hermes/`),
Claude Code session data (`.claude/`), test fixtures, `node_modules/`, and Python
bytecode — the files most likely to drown out real results.

For other stacks, see `templates/sameignore/` in the
[SAME repo](https://github.com/sgx-labs/statelessagent/tree/main/templates/sameignore).

## Tools

This plugin exposes 5 of SAME's 19 MCP tools — the ones most useful for agent
memory workflows:

| Tool | Description |
|------|-------------|
| `same_search` | Search knowledge vault with semantic search |
| `same_save_note` | Save a markdown note with provenance tracking |
| `same_save_decision` | Log a project decision with attribution |
| `same_get_note` | Read full note content by path |
| `same_health` | Vault health and index status |

## Architecture

The plugin starts `same mcp` as a persistent subprocess and communicates via
JSON-RPC 2.0 over stdio — one process, no repeated startup cost, serialized
through a threading lock. If the subprocess dies between turns, `on_turn_start()`
restarts it automatically before the next call.

No new pip dependencies. The only external requirement is the `same` binary.

## Support

- [GitHub Issues](https://github.com/sgx-labs/statelessagent/issues)
- [Discord](https://discord.gg/Qg8AXavNWu)
