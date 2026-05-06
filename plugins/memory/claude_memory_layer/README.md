# Claude Memory Layer Memory Provider

Read-only Hermes memory provider that automatically calls the project-aware
`mem-context-pack` MCP tool before each model turn and injects the returned
compact context through Hermes' existing `<memory-context>` prefetch path.

## Enable

1. Configure the claude-memory-layer MCP server so Hermes has the tool
   `mcp_claude_memory_layer_mem_context_pack`.
2. Set the memory provider:

```yaml
memory:
  provider: claude_memory_layer
  claude_memory_layer:
    context_tool: mcp_claude_memory_layer_mem_context_pack
    # Optional override. If empty, Hermes uses an explicit per-session cwd/project_path,
    # then infers an existing local path from gateway thread title/channel topic,
    # then falls back to TERMINAL_CWD/cwd.
    project_path: ""
    top_k: 5
    recent_limit: 30
    session_limit: 5
    max_chars: 6000
    # Optional claude-memory-layer source-session filter; empty by default.
    session_id: ""
```

Environment overrides are also supported:

- `CLAUDE_MEMORY_LAYER_CONTEXT_TOOL`
- `CLAUDE_MEMORY_LAYER_PROJECT_PATH`
- `CLAUDE_MEMORY_LAYER_TOP_K`
- `CLAUDE_MEMORY_LAYER_RECENT_LIMIT`
- `CLAUDE_MEMORY_LAYER_SESSION_LIMIT`
- `CLAUDE_MEMORY_LAYER_MAX_CHARS`
- `CLAUDE_MEMORY_LAYER_SESSION_ID`

## Privacy notes

- This provider does not write memories or mirror Hermes turns.
- It delegates retrieval to `mem-context-pack`, which should use the
  claude-memory-layer read-only `recordTrace: false` path.
- It does not pass the live Hermes `session_id` as a claude-memory-layer
  `sessionId` filter unless explicitly configured, because those identifiers
  live in different namespaces and default filtering would hide project context.
