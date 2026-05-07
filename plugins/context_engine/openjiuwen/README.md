# OpenJiuwen Context Engine

Third-party context engine adapter for Hermes with lazy runtime loading and safe fallback to the built-in compressor.

## Requirements

- Python `>=3.11,<3.14` (current `openjiuwen` constraint in this repo)
- Recommended install (from project root):
  - `UV_PRERELEASE=allow uv pip install -e ".[openjiuwen]"`
  - With dev tools: `UV_PRERELEASE=allow uv pip install -e ".[dev,openjiuwen]"`
- Do **not** combine with `.[all]` for now (`daytona`/`aiofiles` conflict with current `openjiuwen` release line)

## Setup

```bash
hermes plugins    # Provider Plugins -> Context Engine -> openjiuwen
```

Or manually:
```yaml
context:
  engine: "openjiuwen"
```

## Config

| Key | Default | Description |
|-----|---------|-------------|
| `context.engine` | `compressor` | Set to `openjiuwen` to activate this adapter |
| `auxiliary.compression.*` | Hermes defaults | Standard Hermes compression/summarization settings still apply |
| OpenJiuwen runtime config | (runtime-defined) | Configure provider-specific behavior in the `openjiuwen` package |

If `openjiuwen` is missing or initialization fails, Hermes logs a warning and falls back to `compressor`.

## Tools

| Tool | Description |
|------|-------------|
| Runtime-defined tools | Forwarded from `openjiuwen` when its runtime exposes `get_tool_schemas()` / `handle_tool_call()` |
