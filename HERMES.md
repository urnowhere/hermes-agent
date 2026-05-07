# Workspace Map — Jordan Blake / Express Tire de Mexico

This file orients any new session to the full workspace: what exists, where it lives,
how the pieces connect. Read this before any non-trivial task. Details live in the
documents referenced here — this is the index, not the manual.

---

## Two Codebases, One Workspace

### 1. Hermes Fork (`~/.hermes/hermes-agent-feat/`)
A maintained fork of NousResearch/hermes-agent with local patches and one open PR.

- **Live branch:** `feat/delegate-task-model-provider-override` — this is production.
  The gateway launchd plist (`~/Library/LaunchAgents/ai.hermes.gateway.plist`) runs
  from this directory. Restart: `launchctl kickstart -k gui/$(id -u)/ai.hermes.gateway`
- **Tracking branch:** `local/main` — mirrors upstream main, rebased periodically
- **Upstream remote:** `https://github.com/NousResearch/hermes-agent.git`
- **Open PR:** #12794 — `feat(tools): per-call model/provider override for delegate_task
  + observability plugin`. Status: open, 0 reviews, mergeable.

**Three-copy sync rule:** Components that exist both locally and in the PR must be
kept in sync manually. After any edit:

| Component | Live location | PR location |
|---|---|---|
| `model_observability` plugin | `~/.hermes/plugins/model_observability/` | `feat/plugins/model_observability/` |
| `subagent-model-routing` skill | `~/.hermes/skills/autonomous-ai-agents/subagent-model-routing/` | `feat/skills/autonomous-ai-agents/subagent-model-routing/` |
| `refresh_openrouter_models.py` | `~/.hermes/scripts/refresh_openrouter_models.py` | `feat/scripts/refresh_openrouter_models.py` |

Post-merge cleanup: delete `~/.hermes/scripts/refresh_openrouter_models.py`, repoint
the OpenRouter Model Refresh cron to `feat/scripts/` (or main once merged).

### 2. Ops Scripts (`~/.hermes/scripts/`)
The Express Tire de Mexico operations automation layer. Full detail in
`~/.hermes/scripts/AGENTS.md` — read that before touching any script here.

**Core entry point:** `daily_briefing.py` — assembles the morning briefing from all
`ops/` modules. The `ops/` directory is a modular data layer; each module is
independently queryable via `python3 -m ops.<module>`.

**Tests:** `~/.hermes/scripts/tests/` — run with `cd ~/.hermes/scripts && python -m pytest`

---

## Plugins

| Plugin | Location | Purpose |
|---|---|---|
| `model_observability` | `~/.hermes/plugins/model_observability/` | Logs model usage to `~/.hermes/logs/model_usage.jsonl`; verifies per-task model pinning in `delegate_task`; injects MISMATCH warnings inline. Query: `python ~/.hermes/scripts/model_usage.py` |
| `hermes-lcm` | `~/.hermes/plugins/hermes-lcm/` | Lossless Context Management — hierarchical session compression. Current version: v0.8.0.8.0.8.0.8.0.8.0.8.0.8.0.8.0.8.0.8.0.8.0.8.0.8.0.8.0.8.0.8.0.8.0. Auto-updates Mondays 4AM via cron `f0e72a4cd297`. |

---

## Cron Schedule

| Job | ID | Schedule | Script | Purpose |
|---|---|---|---|---|
| Daily Briefing | `47b46fa1a4c9` | 11:00 Mon–Sat | `cron_briefing_prerunner.py` | Assembles + delivers ops briefing |
| Email Intel Scanner | `55c4bc89d0a7` | 10:05 Mon–Sat | `cron_email_scanner.py` | Caches email intel before briefing |
| Corte Fetch + File | `cac5e5b62876` | 10:00 Mon–Sat | — | Downloads Corte PDF from email → OneDrive |
| Invoice Fetch + File | `c89c31ace8b9` | 19:15 Mon–Sat | — | Files invoices to OneDrive |
| Vault Tunnel Scanner | `c622954bd411` | 10:50 Mon–Sat | — | Detects broken cross-wing vault links |
| Ops Sentinel (Noon) | `1b82ecb0fed1` | 12:00 Mon–Sat | `cron_ops_sentinel.py` | Silent health check; alerts on failures |
| Ops Sentinel (Evening) | `b03ea75fff89` | 20:15 Mon–Sat | `cron_ops_sentinel.py` | Silent health check; alerts on failures |
| OpenRouter Model Refresh | `6d0271a4d5cb` | 11:00 Sundays | `refresh_openrouter_models.py` | Updates model whitelists in script + skill |
| Taxonomy Digest | `07802e5f4fb3` | 08:00 Mondays | — | Vault taxonomy compliance report |
| Workspace Auto-Commit | `4b90efea66fb` | 03:00 daily | — | Commits + pushes `~/.hermes` workspace |
| Daily Memory Log | `48ac898893bf` | 04:30 daily | `cron_daily_memory_log.py` | Writes daily memory snapshot |
| hermes-lcm Sentinel | `ace3b0d0d7de` | 06:00 daily | — | LCM health check |
| hermes-lcm Autoupdate | `f0e72a4cd297` | 04:00 Mondays | — | Pulls latest hermes-lcm release |

---

## Key State Files / Caches

| File | Purpose |
|---|---|
| `~/.hermes/caches/email_intel.json` | Pre-built email cache; briefing reads from here, never IMAP directly |
| `~/.hermes/caches/intel_synthesis_cache.json` | Intel synthesis bridge between `/intel` skill and briefing |
| `~/.hermes/caches/intel_state.json` | Persistent intel state across briefing runs |
| `~/.hermes/caches/sentinel_state.json` | Sentinel last-known-good state |
| `~/.hermes/caches/openrouter_prices_last.json` | Price delta cache for model refresh (20% threshold alerts) |
| `~/.hermes/caches/snapshots/YYYY-MM-DD.json` | Daily ops snapshots |
| `~/.hermes/logs/model_usage.jsonl` | Per-call model usage log (query via `model_usage.py`) |

---

## Active Upstream Issues to Watch

### hermes-agent

| Issue | Title | Why It Matters |
|---|---|---|
| #14009 | `hermes-session-client.py` — cross-profile ACP sessions | Persistent inter-profile communication |
| #18493 | `agent_control` — durable profile-agent orchestration | Manager/team orchestration layer; complements delegate_task + Kanban |
| #12794 | Per-task model/provider override + observability (our PR) | Monitor for reviews/merge |

### hermes-lcm

| Issue | Title | Why It Matters |
|---|---|---|
| #109 | Gateway restart mid-session resets `_ingest_cursor` to 0, silently dropping tool results | Confirmed in prod: ~1,900 tool result rows lost after `hermes restart`; same root cause as #1 (fixed 4bc6b9c) but triggered by restart not compaction. Blocks post-mortem forensics. |

---

## Vault

Obsidian vault at `~/Documents/Obsidian/JordanBlake/` (iCloud-synced).

**Read first before any briefing or ops work:**
`vault/Operations/References/Express-Tire-Ops-Intelligence-Blueprint.md`

**Multi-agent research:**
`vault/Research/concepts/hermes-multi-agent-team.md`
`vault/Research/concepts/next-steps-post-v0110.md`

---

## Skills (Business-System)

Key skills for ops work — load before touching the relevant domain:

| Skill | When to load |
|---|---|
| `express-tire-intel` | Briefing, intel scan, ops state reconciliation |
| `express-tire-ap-filing` | AP invoice filing, OneDrive taxonomy |
| `express-tire-fetch-and-file` | Email → OneDrive fetch scripts |
| `ops-code-review-workflow` | Any ops script change |
| `ops-silent-sentinel` | Sentinel architecture changes |
| `daily-business-briefing-system` | Briefing system changes |
| `subagent-model-routing` | Any `delegate_task` call — model selection |

---

## Key Conventions

- **OpenRouter model slugs use dots:** `claude-haiku-4.5`, `claude-sonnet-4.6`, `claude-opus-4.7` — never hyphens in version numbers
- **Ops file naming (COGS):** `FACTURAS [VENDOR] SEM[NN] DDMMYY #INV (N) - ACTION` — ALL CAPS keys
- **Fiscal week:** Thu → Wed. Payroll: Thu → Wed. AP bill-pay: Fri → Thu
- **Jordan's email:** `jordan@1959.mx` (Zoho). `jordan@expresstire.mx` is wrong.
- **Ricardo:** WhatsApp text primary, English only. **Samantha:** Email primary, Spanish only.
- **Sentinel clean signal:** `SENTINEL_OK` in cron output — no Telegram delivery on clean runs
