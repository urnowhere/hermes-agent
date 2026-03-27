---
sidebar_position: 20
---

# Plugins

Hermes has a plugin system for adding custom tools, hooks, slash commands, and integrations without modifying core code.

**→ [Build a Hermes Plugin](/docs/guides/build-a-hermes-plugin)** — step-by-step guide with a complete working example.

## Quick overview

Drop a directory into `~/.hermes/plugins/` with a `plugin.yaml` and Python code:

```
~/.hermes/plugins/my-plugin/
├── plugin.yaml      # manifest
├── __init__.py      # register() — wires schemas to handlers
├── schemas.py       # tool schemas (what the LLM sees)
└── tools.py         # tool handlers (what runs when called)
```

Start Hermes — your tools appear alongside built-in tools. The model can call them immediately.

Project-local plugins under `./.hermes/plugins/` are disabled by default. Enable them only for trusted repositories by setting `HERMES_ENABLE_PROJECT_PLUGINS=true` before starting Hermes.

## What plugins can do

| Capability | How |
|-----------|-----|
| Add tools | `ctx.register_tool(name, schema, handler)` |
| Add hooks | `ctx.register_hook("post_tool_call", callback)` |
| Add slash commands | `ctx.register_command("mycommand", handler)` |
| Ship data files | `Path(__file__).parent / "data" / "file.yaml"` |
| Bundle skills | Copy `skill.md` to `~/.hermes/skills/` at load time |
| Gate on env vars | `requires_env: [API_KEY]` in plugin.yaml |
| Distribute via pip | `[project.entry-points."hermes_agent.plugins"]` |

## Plugin discovery

| Source | Path | Use case |
|--------|------|----------|
| User | `~/.hermes/plugins/` | Personal plugins |
| Project | `.hermes/plugins/` | Project-specific plugins (requires `HERMES_ENABLE_PROJECT_PLUGINS=true`) |
| pip | `hermes_agent.plugins` entry_points | Distributed packages |

## Available hooks

Plugins can register callbacks for these lifecycle events. See the **[Event Hooks page](/docs/user-guide/features/hooks#plugin-hooks)** for full details, callback signatures, and examples.

| Hook | Fires when |
|------|-----------|
| `pre_tool_call` | Before any tool executes |
| `post_tool_call` | After any tool returns |
| `pre_llm_call` | Before LLM API request *(planned)* |
| `post_llm_call` | After LLM API response *(planned)* |
| `on_session_start` | Session begins *(planned)* |
| `on_session_end` | Session ends *(planned)* |

## Slash commands

Plugins can register slash commands that work in both CLI and messaging platforms:

```python
def register(ctx):
    ctx.register_command(
        name="greet",
        handler=lambda args: f"Hello, {args or 'world'}!",
        description="Greet someone",
        args_hint="[name]",
        aliases=("hi",),
    )
```

The handler receives the argument string (everything after `/greet`) and may
also accept optional runtime context:

- `handler(args)`
- `handler(args, context)`
- `handler(args, *, context=...)`

Registered commands automatically appear in `/help`, tab autocomplete,
Telegram bot menu, and Slack subcommand mapping.

| Parameter | Description |
|-----------|-------------|
| `name` | Command name without slash |
| `handler` | Callable that takes `args: str` and optionally `context`, returning `str | None` |
| `description` | Shown in `/help` |
| `args_hint` | Usage hint, e.g. `"[name]"` |
| `aliases` | Tuple of alternative names |
| `cli_only` | Only available in CLI |
| `gateway_only` | Only available in messaging platforms |
| `gateway_config_gate` | Config dotpath (e.g. `"display.my_option"`). When set on a `cli_only` command, the command becomes available in the gateway if the config value is truthy. |

Example `/sampling` command (CLI-only, live session updates):

```python
def set_sampling(args, context=None):
    context = context or {}
    if context.get("surface") != "cli":
        return "This command is CLI-only"

    cli = context.get("cli")
    if not cli:
        return "Missing CLI context"

    temp_raw, top_p_raw = args.split()
    temp = None if temp_raw == "default" else float(temp_raw)
    top_p = None if top_p_raw == "default" else float(top_p_raw)

    cli.temperature = temp
    cli.top_p = top_p
    if getattr(cli, "agent", None):
        cli.agent.temperature = temp
        cli.agent.top_p = top_p

    return f"Sampling updated: temperature={temp}, top_p={top_p}"


def register(ctx):
    ctx.register_command(
        name="sampling",
        handler=set_sampling,
        description="Set session temperature/top_p",
        args_hint="<temperature|default> <top_p|default>",
        cli_only=True,
    )
```

A full working plugin implementation is included at:

- `optional-plugins/sampling-command/plugin.yaml`
- `optional-plugins/sampling-command/__init__.py`

Install it into your user plugin directory:

```bash
mkdir -p ~/.hermes/plugins/sampling-command
cp optional-plugins/sampling-command/plugin.yaml ~/.hermes/plugins/sampling-command/
cp optional-plugins/sampling-command/__init__.py ~/.hermes/plugins/sampling-command/
```

## Managing plugins

```
/plugins              # list loaded plugins in a session
hermes config set display.show_cost true  # show cost in status bar
```

See the **[full guide](/docs/guides/build-a-hermes-plugin)** for handler contracts, schema format, hook behavior, error handling, and common mistakes.
