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

formsy:
  base_url: "https://api.formsy.ai"
  memory_search_endpoint: "/api/v1/query"
  repo_id: "django__django-14053"
  revision: "latest"
  query_budget: 4000
  workspace_id: "ws_default"
  timeout_s: 30
```

When using the `memory_search` tool for SWE-bench tasks, pass the same
repository parameters accepted by the Formsy query API:

```json
{
  "repo_id": "django__django-14053",
  "query": "HashedFilesMixin post_process yields duplicate filenames",
  "revision": "latest",
  "budget": 4000
}
```

## Scene Routing

Scene routing is handled server-side by the Formsy Runtime:

- **coding**: Triggered when `repo_id` is present in context
- **vision_doc**: Triggered when `document_id` is present
- **general**: Default fallback

The engine sends `scene: "auto"` by default and lets the Runtime perform routing.
