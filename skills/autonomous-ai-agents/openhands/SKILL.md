---
name: openhands
description: "Delegate coding to OpenHands CLI (model-agnostic sandboxed coding agent)."
version: 1.0.0
author: MountainLabs UG + Hermes Agent
license: MIT
metadata:
  hermes:
    tags: [Coding-Agent, OpenHands, Model-Agnostic, Docker, Sandboxed, Code-Review, Refactoring, Browser-Automation]
    related_skills: [claude-code, codex, hermes-agent, opencode]
---

# OpenHands — Hermes Orchestration Guide

Delegate coding tasks to [OpenHands](https://github.com/All-Hands-AI/OpenHands) via the Hermes terminal. OpenHands (formerly OpenDevin) is a model-agnostic autonomous coding agent with Docker sandboxing, browser automation, and multi-provider LLM support (100+ providers via LiteLLM).

**Key advantage over Claude Code / Codex:** Works with ANY LLM provider — Claude, GPT, DeepSeek, Qwen, Nous models, local Ollama/vLLM, etc.

## Prerequisites

- **Install (recommended):** `pip install openhands-ai` (requires Python 3.11+)
- **Install (Docker):** `docker run -it ghcr.io/all-hands-ai/openhands:latest`
- **Install (conda):** `conda install -c conda-forge openhands`
- **Auth:** Set LLM provider environment variables:
  ```
  export LLM_API_KEY="your-api-key"
  export LLM_BASE_URL="https://api.your-provider.com/v1"  # if using non-OpenAI provider
  ```
- **Health check:** `openhands --version`
- **List models:** `openhands models`

## When to Use OpenHands

- Model-agnostic coding tasks (any provider, any model)
- Tasks requiring Docker-sandboxed execution
- Browser automation (QA, scraping, form filling)
- Multi-agent task delegation
- When Claude Code (Anthropic-only) or Codex (OpenAI-only) don't fit

## One-Shot Tasks

### Basic one-shot (headless mode)

```
terminal(command="openhands --headless -t 'Add error handling to all API calls in src/'", workdir="/path/to/project", timeout=300)
```

### With specific model

```
terminal(command="openhands --headless -t 'Fix the login bug' --model anthropic/claude-sonnet-4-5", workdir="/path/to/project", timeout=300)
```

### With JSON output for structured parsing

```
terminal(command="openhands --headless --json -t 'Refactor the auth module to use JWT'", workdir="/path/to/project", timeout=300)
```

### With environment overrides (recommended for automation)

```
terminal(command="LLM_MODEL=anthropic/claude-sonnet-4-5 LLM_API_KEY=$API_KEY LLM_BASE_URL=$BASE_URL openhands --headless --override-with-envs -t 'Build a REST API for user management'", workdir="/path/to/project", timeout=600)
```

**`--override-with-envs`** prevents OpenHands from writing settings files — always use it for automated runs.

### Scratch work (no existing project)

```
terminal(command="cd $(mktemp -d) && openhands --headless -t 'Build a snake game in Python with Pygame'", timeout=300)
```

## Background Mode (Long Tasks)

```
# Start in background
terminal(command="openhands --headless --override-with-envs -t 'Refactor the entire auth module to use JWT tokens, add tests, and update the API docs'", workdir="/path/to/project", background=true, notify_on_complete=true)

# Returns session_id — monitor progress
process(action="poll", session_id="<id>")
process(action="log", session_id="<id>")

# Kill if needed
process(action="kill", session_id="<id>")
```

## Session Resume

OpenHands supports session resumption via `--resume`:

```
# First run — captures session ID from output
terminal(command="openhands --headless --json -t 'Analyze the codebase structure'", workdir="/path/to/project")

# Resume with session ID (32-char hex from "Conversation ID:" output)
terminal(command="openhands --headless --json --resume <session-id> -t 'Now fix the issues you found'", workdir="/path/to/project")
```

## Key Flags

| Flag | Effect |
|------|--------|
| `--headless` | Non-interactive mode (no GUI) |
| `--json` | JSON event stream output |
| `-t "prompt"` | Task prompt (one-shot) |
| `--model provider/model` | Select LLM (e.g. `anthropic/claude-sonnet-4-5`) |
| `--resume <id>` | Resume a previous session |
| `--override-with-envs` | Use env vars only, don't write config files |
| `--exit-without-confirmation` | Exit automatically when done |
| `--max-iterations N` | Limit agent iterations (default: varies by model) |
| `--workspace <path>` | Set workspace directory |
| `--sandbox docker` | Force Docker sandbox (default when available) |
| `--sandbox local` | Use local filesystem (no isolation) |

## Model Configuration

OpenHands supports 100+ providers via LiteLLM. Set environment variables:

```
# OpenAI / compatible
LLM_MODEL=openai/gpt-4o
LLM_API_KEY=sk-...
LLM_BASE_URL=https://api.openai.com/v1

# Anthropic
LLM_MODEL=anthropic/claude-sonnet-4-5
LLM_API_KEY=sk-ant-...

# Google
LLM_MODEL=google/gemini-2.5-pro
LLM_API_KEY=...

# Local / self-hosted (Ollama, vLLM, etc.)
LLM_MODEL=ollama/llama3
LLM_BASE_URL=http://localhost:11434/v1

# DeepSeek
LLM_MODEL=deepseek/deepseek-chat
LLM_API_KEY=...
LLM_BASE_URL=https://api.deepseek.com/v1
```

OpenHands also requires `OPENAI_API_KEY` and `OPENAI_API_BASE` for compatibility — set these in addition to `LLM_API_KEY`/`LLM_BASE_URL` when using non-OpenAI providers.

## Docker Sandbox Configuration

OpenHands defaults to Docker sandboxing when Docker is available:

```
# Ensure Docker is running
terminal(command="docker info > /dev/null 2>&1 && echo 'Docker available' || echo 'Docker not available'")

# Force Docker sandbox
terminal(command="openhands --headless --sandbox docker -t 'Run the test suite and fix failures'", workdir="/path/to/project", timeout=600)

# Use local execution (no Docker, faster but no isolation)
terminal(command="openhands --headless --sandbox local -t 'Fix the CSS layout bug'", workdir="/path/to/project", timeout=300)
```

## Browser Automation

OpenHands has built-in Playwright support for web interaction:

```
# QA a web application
terminal(command="openhands --headless -t 'Navigate to http://localhost:3000, test the login flow, and report any bugs'", workdir="/path/to/project", timeout=600)

# Web scraping
terminal(command="openhands --headless -t 'Go to https://example.com and extract all product prices from the catalog page'", timeout=300)
```

## PR Reviews

```
# Clone PR and review
terminal(command="REVIEW=$(mktemp -d) && git clone https://github.com/user/repo.git $REVIEW && cd $REVIEW && gh pr checkout 42 && openhands --headless -t 'Review this PR. Check for bugs, security issues, and code quality. Provide a detailed review.'", timeout=600)
```

## Parallel Issue Fixing

```
# Create worktrees for parallel work
terminal(command="git worktree add -b fix/issue-78 /tmp/oh-78 main", workdir="~/project")
terminal(command="git worktree add -b fix/issue-99 /tmp/oh-99 main", workdir="~/project")

# Launch OpenHands in each (background)
terminal(command="openhands --headless --override-with-envs -t 'Fix issue #78: <description>. Commit when done.'", workdir="/tmp/oh-78", background=true, notify_on_complete=true)
terminal(command="openhands --headless --override-with-envs -t 'Fix issue #99: <description>. Commit when done.'", workdir="/tmp/oh-99", background=true, notify_on_complete=true)

# Monitor
process(action="list")

# After completion, push and create PRs
terminal(command="cd /tmp/oh-78 && git push -u origin fix/issue-78")
terminal(command="gh pr create --repo user/repo --head fix/issue-78 --title 'fix: resolve #78' --body 'Closes #78'")

# Cleanup
terminal(command="git worktree remove /tmp/oh-78", workdir="~/project")
```

## JSON Output Parsing

When using `--json`, OpenHands emits `--JSON Event--` markers followed by JSON event objects:

- **MessageEvent**: Agent messages (check `source === "agent"` for assistant text)
- **ObservationEvent**: Tool observations (includes `is_error`, `tool_name`)
- **ActionEvent**: Agent actions (`run`, `run_ipython`, `message`)
- **step_completion / step_finish**: Token usage and cost data
- **Conversation ID**: Session ID at end of output (32-char hex or UUID)

## Rules

1. **Always use `--headless`** for automated tasks — no GUI needed in Hermes
2. **Use `--override-with-envs`** for automation — prevents config file side effects
3. **Use `--exit-without-confirmation`** to avoid hanging on completion
4. **Set `LLM_MODEL`** explicitly — OpenHands requires a model selection
5. **Background for long tasks** — use `background=true` + `notify_on_complete=true`
6. **Session resume for multi-step** — use `--resume <id>` to continue previous work
7. **Docker sandbox when available** — safer for untrusted code execution
8. **Don't interfere** — monitor with `process(action="poll")`, be patient
9. **Parallel is fine** — run multiple OpenHands processes for batch work

## Pitfalls

- **Heavy install:** `pip install openhands-ai` pulls 70+ packages. Consider a separate venv or Docker.
- **Docker required for sandbox:** If Docker isn't running, OpenHands falls back to local execution (no isolation).
- **V0 to V1 migration:** OpenHands is transitioning architectures. Pin your version for stability.
- **Model quality varies:** Quality depends heavily on the configured LLM. Claude/GPT perform best on complex tasks.
- **Timeout budget:** Complex tasks can take 10+ minutes. Set generous timeouts.
- **Environment variable conflicts:** If OpenHands is installed in the same venv as Hermes, dependency conflicts may occur. Use Docker or a separate venv.

## Related

- [OpenHands GitHub](https://github.com/All-Hands-AI/OpenHands)
- [OpenHands Documentation](https://docs.openhands.dev/overview/introduction)
- Related skills: `claude-code` (Claude-only), `codex` (OpenAI-only), `hermes-agent` (Hermes subagents)
