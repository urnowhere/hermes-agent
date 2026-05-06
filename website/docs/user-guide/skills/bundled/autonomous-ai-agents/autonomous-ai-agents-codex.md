---
title: "Codex — Delegate coding to OpenAI Codex CLI (features, PRs)"
sidebar_label: "Codex"
description: "Delegate coding to OpenAI Codex CLI (features, PRs)"
---

{/* This page is auto-generated from the skill's SKILL.md by website/scripts/generate-skill-docs.py. Edit the source SKILL.md, not this page. */}

# Codex

Delegate coding to OpenAI Codex CLI (features, PRs).

## Skill metadata

| | |
|---|---|
| Source | Bundled (installed by default) |
| Path | `skills/autonomous-ai-agents/codex` |
| Version | `1.0.0` |
| Author | Hermes Agent |
| License | MIT |
| Tags | `Coding-Agent`, `Codex`, `OpenAI`, `Code-Review`, `Refactoring` |
| Related skills | [`claude-code`](/docs/user-guide/skills/bundled/autonomous-ai-agents/autonomous-ai-agents-claude-code), [`hermes-agent`](/docs/user-guide/skills/bundled/autonomous-ai-agents/autonomous-ai-agents-hermes-agent) |

## Reference: full SKILL.md

:::info
The following is the complete skill definition that Hermes loads when this skill is triggered. This is what the agent sees as instructions when the skill is active.
:::

# Codex CLI

Delegate coding tasks to [Codex](https://github.com/openai/codex) via the Hermes terminal. Codex is OpenAI's autonomous coding agent CLI.

## When to use

- Building features
- Refactoring
- PR reviews
- Batch issue fixing

Requires the codex CLI and a git repository.

## Prerequisites

- Codex installed globally: `npm install -g @openai/codex`
- Or user-local install: `npm install -g @openai/codex --prefix "$HOME/.local"` and ensure `$HOME/.local/bin` is on `PATH`
- Codex authenticated separately from Hermes:
  - ChatGPT/Codex subscription / CLI OAuth: `codex login --device-auth`
  - API-key mode: configure Codex CLI's supported OpenAI API key environment
- **Must run inside a git repository** — Codex refuses to run outside one
- Use `pty=true` in terminal calls — Codex is an interactive terminal app

For Hermes itself, `model.provider: openai-codex` uses Hermes-managed Codex
OAuth from `~/.hermes/auth.json` after `hermes auth add openai-codex`. For the
standalone Codex CLI, a valid CLI OAuth session may live under
`~/.codex/auth.json`; do not treat a missing `OPENAI_API_KEY` alone as proof
that Codex auth is missing.

## One-Shot Tasks

```
terminal(command="codex exec 'Add dark mode toggle to settings'", workdir="~/project", pty=true)
```

For scratch work (Codex needs a git repo):
```
terminal(command="cd $(mktemp -d) && git init && codex exec 'Build a snake game in Python'", pty=true)
```

## Background Mode (Long Tasks)

```
# Start in background with PTY
terminal(command="codex exec --full-auto 'Refactor the auth module'", workdir="~/project", background=true, pty=true)
# Returns session_id

# Monitor progress
process(action="poll", session_id="<id>")
process(action="log", session_id="<id>")

# Send input if Codex asks a question
process(action="submit", session_id="<id>", data="yes")

# Kill if needed
process(action="kill", session_id="<id>")
```

## Key Flags

| Flag | Effect |
|------|--------|
| `exec "prompt"` | One-shot execution, exits when done |
| `--sandbox workspace-write` | Allows writes only inside the workspace sandbox |
| `--ask-for-approval never` | Non-interactive mode for trusted workspace-scoped automation |
| `--cd <workspace>` | Pins Codex execution to the intended repository/worktree |

Do not use dangerous bypass flags for Hermes-managed Kanban work.

## PR Reviews

Clone to a temp directory for safe review:

```
terminal(command="REVIEW=$(mktemp -d) && git clone https://github.com/user/repo.git $REVIEW && cd $REVIEW && gh pr checkout 42 && codex review --base origin/main", pty=true)
```

## Parallel Issue Fixing with Worktrees

```
# Create worktrees
terminal(command="git worktree add -b fix/issue-78 /tmp/issue-78 main", workdir="~/project")
terminal(command="git worktree add -b fix/issue-99 /tmp/issue-99 main", workdir="~/project")

# Launch Codex in each
terminal(command="codex --sandbox workspace-write --ask-for-approval never exec 'Fix issue #78: <description>. Commit when done.'", workdir="/tmp/issue-78", background=true, pty=true)
terminal(command="codex --sandbox workspace-write --ask-for-approval never exec 'Fix issue #99: <description>. Commit when done.'", workdir="/tmp/issue-99", background=true, pty=true)

# Monitor
process(action="list")

# After completion, push and create PRs
terminal(command="cd /tmp/issue-78 && git push -u origin fix/issue-78")
terminal(command="gh pr create --repo user/repo --head fix/issue-78 --title 'fix: ...' --body '...'")

# Cleanup
terminal(command="git worktree remove /tmp/issue-78", workdir="~/project")
```

## Batch PR Reviews

```
# Fetch all PR refs
terminal(command="git fetch origin '+refs/pull/*/head:refs/remotes/origin/pr/*'", workdir="~/project")

# Review multiple PRs in parallel
terminal(command="codex exec 'Review PR #86. git diff origin/main...origin/pr/86'", workdir="~/project", background=true, pty=true)
terminal(command="codex exec 'Review PR #87. git diff origin/main...origin/pr/87'", workdir="~/project", background=true, pty=true)

# Post results
terminal(command="gh pr comment 86 --body '<review>'", workdir="~/project")
```

## Hermes Kanban Worker Lane

Hermes can dispatch Kanban tasks to the standalone Codex CLI while keeping the Hermes Kanban board as the only task database, log store, workspace registry, and lifecycle authority.

Assign a ready task to any of these names to trigger Codex worker mode:

- `codex`
- `codex-cli`
- `codex-worker`
- `openai-codex`

Lifecycle:

1. Hermes claims the task and moves it `ready -> running`.
2. Hermes starts `python -m hermes_cli.codex_worker` in the resolved Kanban workspace.
3. The runner invokes `codex --cd <workspace> --sandbox workspace-write --ask-for-approval never exec`.
4. On Codex exit 0, Hermes moves the task to `blocked` with `Codex completed; Hermes review required`.
5. A Hermes or human reviewer inspects the workspace diff and task log, then marks the task `done` or unblocks it with fix instructions.
6. On nonzero Codex exit, Hermes moves the task to `blocked` with `Codex failed` plus exit-code/output context.

Security posture:

- Codex runs in workspace-write sandbox mode by default.
- Hermes does not pass dangerous bypass flags in the Kanban worker path.
- Codex CLI auth is separate from Hermes OpenAI/Codex OAuth or provider credentials. Do not copy or mutate Hermes tokens for Codex.
- Successful Codex work intentionally blocks for review instead of completing dependent Kanban tasks automatically.

## Rules

1. **Always use `pty=true`** — Codex is an interactive terminal app and hangs without a PTY
2. **Git repo required** — Codex won't run outside a git directory. Use `mktemp -d && git init` for scratch
3. **Use `exec` for one-shots** — `codex exec "prompt"` runs and exits cleanly
4. **Use workspace-write sandboxing for automation** — keep Codex pinned to the intended workspace
5. **Background for long tasks** — use `background=true` and monitor with `process` tool
6. **Don't interfere** — monitor with `poll`/`log`, be patient with long-running tasks
7. **Parallel is fine** — run multiple Codex processes at once for batch work
