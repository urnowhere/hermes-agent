# Keep Memory Provider

Reflective memory via the `keep-skill` daemon. Keep adds semantic search, versioned session capture, tagging, and prompt-driven reflection on top of Hermes's built-in memory.

## Requirements

- `keep-skill` Python package
- one embedding provider
- optionally, one summarization provider

## Setup

```bash
hermes memory setup    # select "keep", choose providers
```

`hermes memory setup` is the normal install path. The setup wizard installs `keep-skill` into the Hermes environment if needed, then bootstraps the Keep store config and writes `KEEP_STORE_PATH` to `.env`.

Or manually:

```bash
hermes config set memory.provider keep
pip install keep-skill
```

If you install manually, still run `hermes memory setup` afterward to initialize the Keep store.

## Config

Store location: `$HERMES_HOME/keep` (for example `~/.hermes/keep`).

If `KEEP_STORE_PATH` is already set in the environment, Keep uses that path instead.

Provider availability is determined by `keep-skill`. Common options include:

| Purpose | Common options |
|---------|----------------|
| Embeddings | `Ollama`, `OpenAI` (`OPENAI_API_KEY`), `OpenRouter` (`OPENROUTER_API_KEY`), `Gemini` (`GEMINI_API_KEY`), `Voyage` (`VOYAGE_API_KEY`), `Mistral` (`MISTRAL_API_KEY`) |
| Summarization | `Ollama`, `Anthropic` (`ANTHROPIC_API_KEY`), `OpenAI`, `OpenRouter`, `Gemini`, `Mistral` |

`hermes memory setup` only offers providers that are available in the current environment.

## Tools

| Tool | Description |
|------|-------------|
| `keep_flow` | Full Keep state-doc flow surface: search, get, put, tag, move, stats, and more |
| `keep_prompt` | Render Keep agent prompts like `reflect`, `session-start`, and `conversation` |
| `keep_help` | Read Keep help topics and operation docs |

For `keep_flow`, prefer the normal `state + params` form in prompts, docs, and examples:

```text
keep_flow(state="get", params={"id": "now"})
keep_flow(state="list", params={"prefix": ".library", "include_hidden": true})
keep_flow(state="query-resolve", params={"query": "recent work"})
```

`state_doc_yaml` exists for advanced custom inline flows, but it should not be the default example shape for Hermes agents. Lighter models are noticeably more reliable with `state + params`.
