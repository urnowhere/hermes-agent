# FormSy Constraint Keeper Hermes Plugin

This plugin connects Hermes Agent to FormSy Constraint Keeper through plugin hooks only.
It observes task evidence, asks the FormSy Runtime API for recovery and completion
verification, and returns FormSy guidance without requiring Hermes agent loop changes.

Important boundary:

```text
Never modify the Hermes agent loop for this integration.
Only plugin, context provider, memory provider, or tool-facing code should change.
```

## Enable In `~/.hermes/config.yaml`

Merge the following settings into `~/.hermes/config.yaml`:

```yaml
plugins:
  enabled:
    - formsy-constraint-keeper
    - formsy-observability

context:
  engine: formsy

formsy:
  base_url: http://127.0.0.1:8000
  api_key_env: FORMSY_API_KEY
  timeout_s: 180
  memory_search_endpoint: /api/v1/query
  workspace_id: ws_default
  policy:
    mode: advisory
    retrieval_gate: observe_only
    final_submit_gate: warn_only
    block_destructive_commands: true
    block_forbidden_paths: true
    warning_budget:
      enabled: true

platform_toolsets:
  cli:
    - hermes-cli
    - plugin_formsy_constraint_keeper
```

If `plugins.enabled` or `platform_toolsets.cli` already exists, append the new
entries instead of replacing the existing list. The required plugin toolset is:

```text
plugin_formsy_constraint_keeper
```

When launching Hermes with explicit `-t` / `--toolsets`, include the plugin
toolset because explicit toolsets override `platform_toolsets.cli`:

```bash
hermes chat -t hermes-cli,plugin_formsy_constraint_keeper
```

## Runtime API Auth

When the FormSy Runtime API uses authentication, export the key before starting
Hermes:

```bash
export FORMSY_API_KEY='your FormSy runtime api key'
```

Start the authenticated runtime API in the FormSy repo as usual:

```bash
RUNTIME_API_PORT=8000 uv run --package formsy-runtime-api python -m formsy.runtime_api.main
```

If the local key cannot use `FORMSY_API_KEY`, configure `formsy.api_key` directly
or set `formsy.api_key_env` to the environment variable Hermes should read.

## Optional Environment Overrides

The plugin is enabled by `plugins.enabled`, so the following variables are only
needed for local override or debugging:

```bash
export FORMSY_CONSTRAINT_KEEPER_ENABLED=true
export FORMSY_CONSTRAINT_KEEPER_TIMEOUT=180
export FORMSY_CONSTRAINT_KEEPER_SPOOL_DIR=~/.hermes/formsy/constraint-keeper/spool
```

Local default policy is advisory for ordinary tools. Final submit is the
verifier boundary: by default the plugin calls the FormSy Completion Gate before
allowing `COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT`. If the server returns
`NEED_MORE_VALIDATION`, `REJECT_DONE`, or `NEED_HUMAN_REVIEW`, the submit is
stopped and the server-provided next actions are shown. If the server is
unavailable, the plugin fails open, records the unavailable verification, and
allows Hermes' original submit path. Set
`FORMSY_CONSTRAINT_KEEPER_FAIL_CLOSED_ON_SUBMIT=false` only for debugging or
temporary local bypass.

## Exposed Tools

After enabling the plugin toolset, Hermes can see these tools:

```text
formsy_constraint_status
formsy_recover
formsy_verify_completion
formsy_request_human_review
```

The hooks still observe tool activity when the plugin is enabled, but these tools
are only visible to the agent when `plugin_formsy_constraint_keeper` is included
in the effective toolsets.
