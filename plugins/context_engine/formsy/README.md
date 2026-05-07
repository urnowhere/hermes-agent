# Formsy Context Engine

Context engine plugin for Hermes Agent that integrates with the Formsy Runtime API for compiler-grade context compression.

## Features

- Replaces Hermes built-in context compressor
- Server-side scene routing (coding / vision / general)
- Focus topic support for targeted compression
- Advisory messages from Formsy Runtime
- Graceful degradation on API unavailability

## Configuration

Set the following environment variable:

```bash
export FORMALCC_API_KEY=fsy_live_your_key_here
```

Optional configuration in Hermes `config.yaml`:

```yaml
context:
  engine: "formsy"

formalcc:
  base_url: "https://api.formsy.ai"
  workspace_id: "ws_default"
  timeout_s: 30
```

## Scene Routing

Scene routing is handled server-side by the Formsy Runtime:

- **coding**: Triggered when `repo_id` is present in context
- **vision_doc**: Triggered when `document_id` is present
- **general**: Default fallback

The engine sends `scene: "auto"` by default and lets the Runtime perform routing.
