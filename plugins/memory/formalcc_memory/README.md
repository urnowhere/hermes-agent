# FormalCC Memory Provider

Memory provider plugin for Hermes Agent that integrates with FormalCC Runtime API.

## Features

- Memory prefetch before model invocations
- Asynchronous turn synchronization
- Session management
- Memory search tools
- Graceful degradation on API unavailability

## Configuration

Set the following environment variable:

```bash
export FORMALCC_API_KEY=fsy_live_your_key_here
```

Optional configuration in Hermes `config.yaml`:

```yaml
memory:
  provider: "formalcc-memory"

formalcc:
  base_url: "https://api.formsy.ai"
  workspace_id: "ws_default"
  timeout_s: 30
```

## CLI Commands

```bash
# Check provider status
hermes formalcc-memory status

# Test connectivity
hermes formalcc-memory test

# Show configuration
hermes formalcc-memory config
```

## Memory Tools

When enabled, the provider exposes these tools to the model:

- `cc_memory_search`: Search memory for relevant context
- `cc_memory_profile`: Get memory statistics
