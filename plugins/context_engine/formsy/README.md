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
export FORMSY_API_KEY=fsy_live_your_key_here
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
  "budget": 4000,
  "metadata": {
    "retrieval_mode": "symbolic",
    "grounding_phase": "seed",
    "response_format": "bundle",
    "trace_id": "django__django-14053"
  }
}
```

Hermes-based E2E runs use the existing `context_search` and `context_read`
tool surface. Seed and grounded retrieval both call the configured query API;
the mode is controlled by a nested `metadata` object on `context_search`:

- `metadata.retrieval_mode`: `symbolic` or `legacy`
- `metadata.grounding_phase`: `seed` or `grounded`
- `metadata.response_format`: `bundle` or `legacy`
- optional `metadata.trace_id` / `metadata.case_id`
- optional grounded evidence metadata:
  `metadata.grounded_symbols`, `metadata.grounded_files`,
  `metadata.retrieval_feedback`
- if seed search returns `coverage: poor` or `matches: []`, retry before editing
- when `context_search` returns candidate files or spans, use `context_read`
- if `context_search` returns `suggested_queries`, follow them before shell grep/find
- the plugin marks search results with `retrieval_state` and `preferred_next_step`

Recommended sequence:

1. Call `context_search` with `metadata.retrieval_mode: "symbolic"` and
   `metadata.grounding_phase: "seed"`.
2. Inspect returned grounding candidates and diagnostics.
3. Call `context_read` for exact file/span inspection as needed.
4. Call `context_search` again with `metadata.retrieval_mode: "symbolic"` and
   `metadata.grounding_phase: "grounded"`.
5. Fall back to `metadata.retrieval_mode: "legacy"` only when symbolic grounding is weak
   or contradictory.

## Scene Routing

Scene routing is handled server-side by the Formsy Runtime:

- **coding**: Triggered when `repo_id` is present in context
- **vision_doc**: Triggered when `document_id` is present
- **general**: Default fallback

The engine sends `scene: "auto"` by default and lets the Runtime perform routing.
