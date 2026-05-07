# Agent Command Hub

> This document describes the running Hermes Agent instance — its configuration, capabilities, source identity, and how to extend it.

---

## Deployment Identity

| Property         | Value |
|---|---|
| **Source Repo**  | `kiranvk-code/hermes-agent` (private) |
| **Upstream**     | `NousResearch/hermes-agent` |
| **Running Branch** | `running` — pinned to exact deployed commit |
| **Upstream Branch** | `upstream/main` — verbatim NousResearch main |
| **Current Commit** | *(see DEPLOYED_COMMIT below)* |
| **Deploy Path**  | `/root/.hermes/hermes-agent/` |
| **Binary Entry** | `/root/.local/bin/hermes` → `hermes-agent/venv/bin/hermes` |
| **Platform**     | Telegram (DM with pkd11) |

**DEPLOYED_COMMIT:** `f54067c53`
**DEPLOYED_VERSION:** `v0.12.0+f54067c`
**DEPLOYED_DATE:** `2026-05-05`

---

## LLM Provider Configuration

### Primary: Bailian (Alibaba Cloud)
- **Provider:** `alibaba`
- **Default Model:** `qwen3.6-plus`
- **Base URL:** `https://coding-intl.dashscope.aliyuncs.com/v1`
- **Purpose:** Long-running sessions, general tasks

### Fallback: GitHub Copilot
- **Provider:** `copilot`
- **Fallback Model:** `claude-sonnet-4.6`
- **Base URL:** `https://api.business.githubcopilot.com` (set via `COPILOT_API_BASE_URL` in `.env`)
- **Purpose:** Strategic tasks, high-quality reasoning
- **Token Source:** Copilot Account Manager at `localhost:5111`
- **Rotation:** Every 5 min via `/root/.hermes/scripts/hermes-token-rotation.sh`

### Available Models (switch via `/model`)

**Copilot Models:**
- `copilot/claude-opus-4.6`, `copilot/claude-opus-4.7`
- `copilot/claude-sonnet-4.6`, `copilot/claude-sonnet-4.5`, `copilot/claude-sonnet-4`
- `copilot/claude-haiku-4.5`
- `copilot/gpt-5.5`, `copilot/gpt-5.4`, `copilot/gpt-5.4-mini`, `copilot/gpt-5.2-codex`, `copilot/gpt-5.3-codex`
- `copilot/gemini-3.1-pro-preview`
- `copilot/grok-code-fast-1`

**Bailian Models:**
- `alibaba/qwen3.6-plus`
- `alibaba/glm-5`
- `alibaba/kimi-k2.5`
- `alibaba/qwen3-coder-plus`
- `alibaba/qwen3-max`
- `alibaba/minimax-m2.5`

### Switching Models
- **Telegram:** Type `/model` for picker, or `/model copilot/claude-sonnet-4.6` for instant switch
- **CLI:** `hermes model` interactive picker
- **Fallback:** Automatic when primary fails (rate-limit, 5xx, connection errors)

---

## System Prompt Architecture

The agent's behavior is composed of layered prompts, assembled at runtime:

```
System Prompt = [Base system prompt]
              + [SOUL.md — personality/tone]
              + [Memory — persistent facts (user prefs, env, conventions)]
              + [Skills — procedural knowledge (task-specific instructions)]
              + [Conversation history — current session context]
```

### SOUL.md (`~/.hermes/SOUL.md`)
Defines the agent's personality and communication tone. Loaded fresh each message.

### Memory (`~/.hermes/memory/`)
Two stores:
- **user** — who the user is (preferences, habits, pet peeves)
- **memory** — agent's notes (environment facts, conventions, tool quirks)

### Skills (`~/.hermes/skills/`)
Procedural knowledge for recurring task types. Each skill has:
- `SKILL.md` — trigger conditions, numbered steps, pitfalls, verification
- Optional: `references/`, `templates/`, `scripts/`, `assets/`

---

## Tool Configuration

### Enabled Toolsets
- `hermes-cli` — full tool suite

### Core Tools
| Tool | Purpose |
|---|---|
| `terminal` | Shell execution (foreground/background/PTY) |
| `read_file` / `write_file` / `patch` | File operations |
| `search_files` | Grep/find replacement |
| `browser_*` | Browser automation |
| `web_*` | Web search and content extraction |
| `delegate_task` | Spawn subagents for parallel/isolated work |
| `execute_code` | Python sandbox with Hermes tool access |
| `memory` | Persistent memory read/write |
| `skill_manage` / `skill_view` | Skill lifecycle |
| `cronjob` | Scheduled task management |
| `send_message` | Cross-platform messaging |
| `session_search` | Cross-session recall |

### Tool Registry
All tools defined in `tools/registry.py` — schemas, handlers, dispatch.

---

## Current Configuration (`~/.hermes/config.yaml`)

### Model
- **Default:** `qwen3.6-plus`
- **Provider:** `alibaba`
- **Base URL:** `https://coding-intl.dashscope.aliyuncs.com/v1`
- **Fallback:** `copilot/claude-sonnet-4.6`

### Agent Settings
- **Max turns:** 60
- **Gateway timeout:** 1800s (30 min)
- **Reasoning effort:** medium
- **Verbose:** false

### Terminal
- **Backend:** local (VPS filesystem)
- **Timeout:** 180s
- **Persistent shell:** true

### Compression
- **Enabled:** true
- **Threshold:** 85% context usage
- **Target ratio:** 20%
- **Protect last N:** 20 messages

---

## Local Patches & Modifications (Restore After Update)

These are all code modifications made on top of upstream `NousResearch/hermes-agent` v0.12.0 (`0ce1b9fe2`). After any `hermes update --yes`, these patches must be re-applied and committed.

### Patch 1: Copilot Provider Model List (`hermes_cli/models.py`)

**File:** `hermes_cli/models.py`, line ~205-223
**Commit:** `a2d1c363e` → squashed into `60e9bde48`
**Problem:** The static `_PROVIDER_MODELS["copilot"]` list was missing Claude Opus models and newer GPT versions, causing the `/model` picker to not show them.
**Change:** Added the following models to `_PROVIDER_MODELS["copilot"]`:

```python
"copilot": [
    "gpt-5.5",
    "gpt-5.4",
    "gpt-5.4-mini",
    "gpt-5-mini",
    "gpt-5.3-codex",
    "gpt-5.2-codex",
    "gpt-4.1",
    "gpt-4o",
    "gpt-4o-mini",
    "claude-opus-4.7",
    "claude-opus-4.6",
    "claude-opus-4.5",
    "claude-sonnet-4.6",
    "claude-sonnet-4",
    "claude-sonnet-4.5",
    "claude-haiku-4.5",
    "gemini-3.1-pro-preview",
    "gemini-3-pro-preview",
    "gemini-3-flash-preview",
    "gemini-2.5-pro",
    # ... rest of list
]
```

**Re-apply after update:** Patch `_PROVIDER_MODELS["copilot"]` in `hermes_cli/models.py` to include `claude-opus-4.5`, `claude-opus-4.6`, `claude-opus-4.7`, `gpt-5.5`, `gpt-5.4`, `gpt-5.4-mini`.

**Note:** The picker uses `provider_model_ids("copilot")` which fetches the LIVE GitHub Copilot API (not the static list). The static list is used as fallback when the API is unreachable. Both must be kept in sync.

### Patch 2: Copilot Provider Config (`~/.hermes/config.yaml`)

**File:** `~/.hermes/config.yaml`
**Change:** Added `copilot` provider block:

```yaml
providers:
  copilot:
    provider_type: openai
    base_url: https://api.business.githubcopilot.com
    api_key_env: COPILOT_GITHUB_TOKEN
```

**Re-apply after update:** Edit `~/.hermes/config.yaml` — the Hermes update does NOT touch user config, so this persists automatically. But verify it's still present.

### Patch 3: Token Rotation Script

**File:** `/root/.hermes/scripts/hermes-token-rotation.sh`
**Also in repo:** `scripts/hermes-token-rotation.sh` (committed to `running` branch)
**Commit:** `86b45e935`
**Purpose:** Calls `GET http://localhost:5111/api/best-token?tool=hermes` with Basic Auth, updates `COPILOT_GITHUB_TOKEN` in `/root/.hermes/.env`, logs rotation events.
**Cron:** Host crontab (NOT Hermes scheduler) — `*/5 * * * *` in `/etc/crontab` or `crontab -e`.
**Log:** `/var/log/hermes-token-rotation.log`

**Re-apply after update:**
1. Verify `/root/.hermes/scripts/hermes-token-rotation.sh` still exists (it's outside the repo, survives updates)
2. Copy to repo: `cp /root/.hermes/scripts/hermes-token-rotation.sh /root/.hermes/hermes-agent/scripts/`
3. Re-add to host crontab: `*/5 * * * * /root/.hermes/scripts/hermes-token-rotation.sh --once >> /var/log/hermes-token-rotation.log 2>&1`

### Patch 4: Copilot Model Picker Bug Fix (2026-05-04)

**Issue:** User reported that selecting `claude-opus-4.6` in the Telegram `/model` picker was selecting `claude-opus-4.7` instead.

**Root Cause:** 
- `validate_requested_model()` in `hermes_cli/models.py` was auto-correcting `claude-opus-4.6` → `claude-opus-4.7` using `get_close_matches()` fuzzy matching (cutoff=0.9)
- This happened in the "custom" provider validation path (line ~3121-3130) when the probed model list contained both Opus models
- The `switch_model` function at line 941-942 applied the `corrected_model` from validation, silently rewriting the user's selection

**Fix:**
- The live API probe now correctly returns `claude-opus-4.6` as an exact match, so fuzzy correction is not triggered
- Verified that `claude-opus-4.6` exists in the probed model list before fuzzy matching kicks in
- Gateway was restarted (PID 617667) to clear stale Python bytecode cache

**Key files:**
- `hermes_cli/models.py` — `validate_requested_model()` (line 3024), `get_close_matches` auto-correction (line 3121)
- `hermes_cli/model_switch.py` — `switch_model()` applies `corrected_model` (line 941)
- `gateway/platforms/telegram.py` — picker builds `mm:{index}` callbacks (line 1647)

**If the bug recurs:** Check if `claude-opus-4.6` is present in the live API model list. If the API removes it or changes the name format, the fuzzy matcher will "correct" to the closest match (4.7).

### Patch 5: Copilot Compression Header Fix (2026-05-05)

**Issue:** Context compression summary failed with `HTTP 400: missing Editor-Version header for IDE auth` when falling back to Copilot.

**Root Cause:**
- `_try_custom_endpoint()` in `agent/auxiliary_client.py` creates an OpenAI client for custom endpoints (including Copilot)
- Unlike `_resolve_api_key_provider()` (which adds Copilot headers at lines 1165-1168, 1192-1195), `_try_custom_endpoint()` was NOT adding the required `Editor-Version`, `Copilot-Integration-Id`, etc. headers
- When the main provider (Alibaba) failed and compression fell back to Copilot via the custom endpoint path, the request was missing IDE auth headers

**Fix:**
Added Copilot header injection to `_try_custom_endpoint()`:
```python
if base_url_host_matches(custom_base, "api.githubcopilot.com"):
    from hermes_cli.models import copilot_default_headers
    _extra.setdefault("default_headers", {}).update(copilot_default_headers())
```

**File:** `agent/auxiliary_client.py`, line ~1461 (after `_extra` initialization)

**Re-apply after update:** Patch `_try_custom_endpoint()` in `agent/auxiliary_client.py` to add Copilot headers when `base_url` matches `api.githubcopilot.com`.

### Patch 6: Git Branch Status

**Remote setup:**
- `origin` → `NousResearch/hermes-agent` (upstream)
- `personal` → `kiranvk-code/hermes-agent` (private fork)

**Branches:**
- `upstream/main` — was intended as clean mirror, NOT created as local branch yet
- `running` — exists at `personal/running` (commit `60e9bde48`)
- `running-local` — local working branch (currently checked out)

**To create upstream/main mirror (one-time setup):**
```bash
cd /root/.hermes/hermes-agent
git fetch origin
git branch upstream/main origin/main
git push personal upstream/main
```

---

## Versioning Strategy

### Branch Model
```
upstream/main  ← clean mirror of NousResearch/hermes-agent main
running        ← pinned to exact deployed commit (+ custom scripts)
feature/*      ← extensions and customizations
```

### Update Flow
1. Fetch upstream: `git fetch origin`
2. Update upstream/main mirror: `git push personal refs/remotes/origin/main:refs/heads/upstream/main`
3. Reset running to latest: `git checkout running && git reset --hard origin/main`
4. Re-add custom scripts: `cp /root/.hermes/scripts/hermes-token-rotation.sh scripts/`
5. Add/update AGENT-HUB.md with new commit hash
6. Tag: `git tag deployed-YYYYMMDD-<sha7>`
7. Push: `git push personal running --tags --force`

### Important: Preserve Custom Scripts
Custom scripts in `/root/.hermes/scripts/` survive updates (outside git repo).
But they MUST be re-committed to the repo after each update:
```bash
cp /root/.hermes/scripts/hermes-token-rotation.sh /root/.hermes/hermes-agent/scripts/
git add scripts/hermes-token-rotation.sh
git commit -m "scripts: preserve hermes token rotation script"
git push personal running-local:refs/heads/running --force
```

### Adding Features
1. Branch off `running`: `git checkout -b feature/<name> running`
2. Make changes
3. Test locally
4. Merge to `running` when ready

---

## Token Rotation

### Script: `/root/.hermes/scripts/hermes-token-rotation.sh`
- Checks quota via Copilot Account Manager every 5 min
- Rotates token when exhausted or critical (<=5%)
- Updates `COPILOT_GITHUB_TOKEN` in `/root/.hermes/.env`
- Signals gateway USR1 for credential reload
- Telegram notifications on rotation
- Supports `--once`, `--status`, and daemon modes

### Cron Job
- **Job ID:** `0a1dc2ba0405`
- **Schedule:** Every 5 minutes
- **Name:** "Hermes Copilot Token Rotation"

### Copilot Account Manager
- **URL:** `http://localhost:5111`
- **Auth:** Basic (`759641:Kapuma@23`)
- **Accounts:** 34 GitHub accounts in rotation pool
- **API:** `GET /api/best-token?tool=hermes&reason=rotation-check`

---

## Project Structure

```
hermes-agent/
├── run_agent.py          # AIAgent class — core conversation loop
├── model_tools.py        # Tool orchestration & dispatch
├── toolsets.py           # Toolset definitions
├── cli.py                # HermesCLI — interactive CLI orchestrator
├── hermes_state.py       # SessionDB — SQLite session store (FTS5)
├── agent/                # Agent internals (prompts, compression, caching)
├── hermes_cli/           # CLI subcommands, setup, skin engine
├── tools/                # Tool implementations & registry
├── gateway/              # Messaging platform gateway
├── ui-tui/               # React TUI (Ink)
├── tui_gateway/          # Python JSON-RPC backend for TUI
├── acp_adapter/          # ACP server (VS Code / Zed integration)
├── cron/                 # Scheduler
├── scripts/              # Custom scripts (hermes-token-rotation.sh)
├── tests/                # Pytest suite (~3000 tests)
└── batch_runner.py       # Parallel batch processing
```

**Config files:**
- `~/.hermes/config.yaml` — settings
- `~/.hermes/.env` — API keys (includes `COPILOT_GITHUB_TOKEN`, `COPILOT_API_BASE_URL`)
- `~/.hermes/SOUL.md` — personality
- `~/.hermes/memory/` — persistent memory
- `~/.hermes/skills/` — procedural skills

---

## How to Extend This Agent

### 1. Modify behavior via SOUL.md
Edit `~/.hermes/SOUL.md` — changes apply immediately (loaded each message).

### 2. Switch models
Type `/model` in Telegram for interactive picker, or `/model provider/model-name` for instant switch.

### 3. Add/remove skills
- Create in `~/.hermes/skills/<name>/SKILL.md`
- Agent auto-discovers and loads on relevance

### 4. Update memory
Agent auto-saves durable facts via `memory` tool.

### 5. Patch source code
- Branch off `running`, modify Python source
- Restart gateway: `systemctl restart hermes-gateway`

### 6. Change model/provider
Edit `~/.hermes/config.yaml` under `model:` section, or use `/model` command.

---

## Self-Update Procedure

When the user requests "update yourself":

1. Run `hermes update --yes` — pulls latest upstream, restarts gateway
2. Verify: `hermes --version`, gateway PID, Telegram connected
3. Fetch upstream and push mirror: `git fetch origin && git push personal refs/remotes/origin/main:refs/heads/upstream/main`
4. Reset running: `git checkout running-local && git reset --hard origin/main`
5. Re-add custom scripts to repo
6. Update AGENT-HUB.md with new commit hash
7. Tag and push: `git tag deployed-$(date +%Y%m%d)-$(git rev-parse --short=7 HEAD) && git push personal running --tags --force`
8. Verify token rotation cron job is still active

---

*Last updated: 2026-05-04*
