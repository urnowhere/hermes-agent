# Hermes Web Console v1 Implementation Plan

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.

**Goal:** Build a fully featured browser-based Hermes GUI that exposes chat, sessions, tool activity, workspace operations, approvals, memory, skills, cron, gateway/platform administration, settings, and observability on top of the existing Hermes runtime.

**Architecture:** Keep the existing OpenAI-compatible API server and add a Hermes-native web-console backend under `gateway/web_console/` with structured REST + SSE endpoints. Build a dedicated React/TypeScript SPA under `web_console/`, compile it into static assets served by aiohttp, and reuse existing Hermes runtime/session/gateway/storage services instead of inventing parallel state stores.

**Tech Stack:** aiohttp, Python 3.11+, existing Hermes gateway/runtime/session stores, React 19, TypeScript 5, Vite, TanStack Router, TanStack Query, Zustand, Vitest, Playwright, pytest, SSE.

---

## 0. Repository baseline and guiding decisions

### Existing surfaces to build on

- OpenAI-compatible API server already exists in `gateway/platforms/api_server.py`.
- Minimal browser UI already exists in `gateway/platforms/api_server_ui.py`.
- Session persistence already exists in Hermes state/session storage and docs.
- Gateway runtime health/status already exists in `gateway/status.py`.
- Cron, pairing, skills, memory, tools, and config systems already exist.

### Decisions for this implementation

1. Keep `/v1/*` endpoints unchanged for compatibility.
2. Add Hermes-native endpoints under `/api/gui/*`.
3. Add SSE endpoint(s) under `/api/gui/stream/*` for run events.
4. Serve built SPA assets from `/app/*`, with `/` redirecting to `/app/` when GUI is enabled.
5. Continue using local file-backed/runtime-backed stores (`config.yaml`, `.env`, auth store, session DB, gateway state, cron outputs) as source of truth.
6. Never expose raw secrets in GUI responses.
7. Default GUI binding remains localhost-only unless the user intentionally configures remote access.

---

## 1. Target file layout

### Backend files to create

- Create: `gateway/web_console/__init__.py`
- Create: `gateway/web_console/app.py`
- Create: `gateway/web_console/routes.py`
- Create: `gateway/web_console/security.py`
- Create: `gateway/web_console/sse.py`
- Create: `gateway/web_console/event_bus.py`
- Create: `gateway/web_console/static.py`
- Create: `gateway/web_console/state.py`
- Create: `gateway/web_console/api/__init__.py`
- Create: `gateway/web_console/api/chat.py`
- Create: `gateway/web_console/api/sessions.py`
- Create: `gateway/web_console/api/workspace.py`
- Create: `gateway/web_console/api/approvals.py`
- Create: `gateway/web_console/api/memory.py`
- Create: `gateway/web_console/api/skills.py`
- Create: `gateway/web_console/api/cron.py`
- Create: `gateway/web_console/api/gateway_admin.py`
- Create: `gateway/web_console/api/settings.py`
- Create: `gateway/web_console/api/logs.py`
- Create: `gateway/web_console/api/browser.py`
- Create: `gateway/web_console/api/media.py`
- Create: `gateway/web_console/services/__init__.py`
- Create: `gateway/web_console/services/chat_service.py`
- Create: `gateway/web_console/services/session_service.py`
- Create: `gateway/web_console/services/workspace_service.py`
- Create: `gateway/web_console/services/approval_service.py`
- Create: `gateway/web_console/services/memory_service.py`
- Create: `gateway/web_console/services/skill_service.py`
- Create: `gateway/web_console/services/cron_service.py`
- Create: `gateway/web_console/services/gateway_service.py`
- Create: `gateway/web_console/services/settings_service.py`
- Create: `gateway/web_console/services/log_service.py`
- Create: `gateway/web_console/services/browser_service.py`
- Create: `gateway/web_console/schemas.py`

### Existing backend files to modify

- Modify: `gateway/platforms/api_server.py`
- Modify: `gateway/run.py`
- Modify: `run_agent.py`
- Modify: `hermes_cli/config.py`
- Modify: `pyproject.toml`
- Modify: `README.md`

### Frontend app files to create

- Create: `web_console/package.json`
- Create: `web_console/tsconfig.json`
- Create: `web_console/vite.config.ts`
- Create: `web_console/index.html`
- Create: `web_console/src/main.tsx`
- Create: `web_console/src/app/App.tsx`
- Create: `web_console/src/app/router.tsx`
- Create: `web_console/src/app/providers.tsx`
- Create: `web_console/src/app/theme.css`
- Create: `web_console/src/lib/api.ts`
- Create: `web_console/src/lib/events.ts`
- Create: `web_console/src/lib/types.ts`
- Create: `web_console/src/lib/utils.ts`
- Create: `web_console/src/store/uiStore.ts`
- Create: `web_console/src/store/sessionStore.ts`
- Create: `web_console/src/store/runStore.ts`
- Create: `web_console/src/store/workspaceStore.ts`
- Create: `web_console/src/components/layout/AppShell.tsx`
- Create: `web_console/src/components/layout/TopBar.tsx`
- Create: `web_console/src/components/layout/Sidebar.tsx`
- Create: `web_console/src/components/layout/Inspector.tsx`
- Create: `web_console/src/components/layout/BottomDrawer.tsx`
- Create: `web_console/src/components/chat/Transcript.tsx`
- Create: `web_console/src/components/chat/Composer.tsx`
- Create: `web_console/src/components/chat/MessageCard.tsx`
- Create: `web_console/src/components/chat/ToolTimeline.tsx`
- Create: `web_console/src/components/chat/ApprovalPrompt.tsx`
- Create: `web_console/src/components/chat/ClarifyPrompt.tsx`
- Create: `web_console/src/components/chat/RunStatusBar.tsx`
- Create: `web_console/src/components/sessions/SessionList.tsx`
- Create: `web_console/src/components/sessions/SessionPreview.tsx`
- Create: `web_console/src/components/workspace/FileTree.tsx`
- Create: `web_console/src/components/workspace/FileViewer.tsx`
- Create: `web_console/src/components/workspace/DiffViewer.tsx`
- Create: `web_console/src/components/workspace/CheckpointList.tsx`
- Create: `web_console/src/components/workspace/TerminalPanel.tsx`
- Create: `web_console/src/components/workspace/ProcessPanel.tsx`
- Create: `web_console/src/components/memory/MemoryList.tsx`
- Create: `web_console/src/components/skills/SkillList.tsx`
- Create: `web_console/src/components/cron/CronList.tsx`
- Create: `web_console/src/components/gateway/PlatformCards.tsx`
- Create: `web_console/src/components/settings/SettingsForm.tsx`
- Create: `web_console/src/components/logs/LogViewer.tsx`
- Create: `web_console/src/pages/ChatPage.tsx`
- Create: `web_console/src/pages/SessionsPage.tsx`
- Create: `web_console/src/pages/WorkspacePage.tsx`
- Create: `web_console/src/pages/AutomationsPage.tsx`
- Create: `web_console/src/pages/MemoryPage.tsx`
- Create: `web_console/src/pages/SkillsPage.tsx`
- Create: `web_console/src/pages/GatewayPage.tsx`
- Create: `web_console/src/pages/SettingsPage.tsx`
- Create: `web_console/src/pages/LogsPage.tsx`

### Tests to create

- Create: `tests/gateway/test_api_server_gui_mount.py`
- Create: `tests/web_console/test_event_bus.py`
- Create: `tests/web_console/test_chat_api.py`
- Create: `tests/web_console/test_sessions_api.py`
- Create: `tests/web_console/test_workspace_api.py`
- Create: `tests/web_console/test_approvals_api.py`
- Create: `tests/web_console/test_memory_api.py`
- Create: `tests/web_console/test_skills_api.py`
- Create: `tests/web_console/test_cron_api.py`
- Create: `tests/web_console/test_gateway_admin_api.py`
- Create: `tests/web_console/test_settings_api.py`
- Create: `tests/web_console/test_logs_api.py`
- Create: `tests/web_console/test_browser_api.py`
- Create: `tests/web_console/test_static_assets.py`
- Create: `web_console/src/**/*.test.tsx`
- Create: `web_console/playwright/chat.spec.ts`
- Create: `web_console/playwright/workspace.spec.ts`
- Create: `web_console/playwright/cron.spec.ts`

---

## 2. Delivery phases

1. Backend foundation and event model
2. Frontend shell and chat console MVP
3. Sessions, approvals, clarifications, and logs
4. Workspace, diffs, terminal, processes, checkpoints
5. Memory, skills, todos, subagents, session search
6. Cron, gateway/platform admin, pairing, delivery state
7. Settings, providers, auth status, plugins, skins, browser/media
8. Hardening, tests, docs, packaging, release gating

---

## 3. Task-by-task implementation plan

### Task 1: Add GUI config flags and serving mode

**Objective:** Define how Hermes enables, binds, and secures the web console.

**Files:**
- Modify: `hermes_cli/config.py`
- Modify: `pyproject.toml`
- Test: `tests/web_console/test_settings_api.py`

**Step 1: Write failing tests for GUI config defaults**

Add tests asserting defaults for:
- `gui.enabled`
- `gui.host`
- `gui.port`
- `gui.mount_path`
- `gui.require_api_key`
- `gui.open_browser`

**Step 2: Run test to verify failure**

Run: `python3 -m pytest tests/web_console/test_settings_api.py -q`
Expected: FAIL — missing GUI config keys.

**Step 3: Add config defaults**

Add a new section to `DEFAULT_CONFIG` in `hermes_cli/config.py`:

```python
after_gui = {
    "enabled": False,
    "host": "127.0.0.1",
    "port": 8642,
    "mount_path": "/app",
    "require_api_key": False,
    "open_browser": False,
    "dev_server_url": "",
}
```

Merge it under a top-level `gui` key.

**Step 4: Add optional frontend packaging/build hints**

Update `pyproject.toml` docs/comments and optional dev dependencies if needed, but keep runtime Python dependency footprint unchanged.

**Step 5: Run tests**

Run: `python3 -m pytest tests/web_console/test_settings_api.py -q`
Expected: PASS.

**Step 6: Commit**

```bash
git add hermes_cli/config.py pyproject.toml tests/web_console/test_settings_api.py
git commit -m "feat: add hermes gui config defaults"
```

### Task 2: Create a GUI backend package and router skeleton

**Objective:** Establish a single backend mounting point for the GUI.

**Files:**
- Create: `gateway/web_console/__init__.py`
- Create: `gateway/web_console/app.py`
- Create: `gateway/web_console/routes.py`
- Create: `gateway/web_console/static.py`
- Modify: `gateway/platforms/api_server.py`
- Test: `tests/gateway/test_api_server_gui_mount.py`

**Step 1: Write failing tests for GUI route mounting**

Test that when GUI is enabled the aiohttp app exposes:
- `/app/`
- `/api/gui/health`
- `/api/gui/meta`

**Step 2: Run test to verify failure**

Run: `python3 -m pytest tests/gateway/test_api_server_gui_mount.py -q`
Expected: FAIL — routes not found.

**Step 3: Create backend package skeleton**

Minimal example for `gateway/web_console/routes.py`:

```python
from aiohttp import web


def register_gui_routes(app: web.Application) -> None:
    app.router.add_get("/api/gui/health", lambda request: web.json_response({"status": "ok"}))
    app.router.add_get("/api/gui/meta", lambda request: web.json_response({"product": "hermes-web-console"}))
```

**Step 4: Add GUI mount helper to API server**

In `gateway/platforms/api_server.py`, call a helper such as:

```python
from gateway.web_console.app import maybe_register_web_console
```

Then in `_register_routes(app)`:

```python
maybe_register_web_console(app, adapter=self)
```

**Step 5: Run tests**

Run: `python3 -m pytest tests/gateway/test_api_server_gui_mount.py -q`
Expected: PASS.

**Step 6: Commit**

```bash
git add gateway/web_console gateway/platforms/api_server.py tests/gateway/test_api_server_gui_mount.py
git commit -m "feat: mount hermes web console routes"
```

### Task 3: Define the GUI event bus and SSE transport

**Objective:** Create one canonical streaming channel for run state, tools, approvals, clarifications, and system events.

**Files:**
- Create: `gateway/web_console/event_bus.py`
- Create: `gateway/web_console/sse.py`
- Create: `gateway/web_console/state.py`
- Test: `tests/web_console/test_event_bus.py`

**Step 1: Write failing tests for publish/subscribe behavior**

Test that:
- subscribers receive events in order
- subscribers can disconnect cleanly
- events are namespaced by session id / run id

**Step 2: Run test to verify failure**

Run: `python3 -m pytest tests/web_console/test_event_bus.py -q`
Expected: FAIL — module missing.

**Step 3: Implement minimal in-process event bus**

Suggested interface:

```python
@dataclass
class GuiEvent:
    type: str
    session_id: str
    run_id: str | None
    payload: dict[str, Any]
    ts: float
```

Methods:
- `subscribe(channel: str) -> asyncio.Queue[GuiEvent]`
- `unsubscribe(channel: str, queue: asyncio.Queue) -> None`
- `publish(channel: str, event: GuiEvent) -> None`

**Step 4: Implement SSE response helper**

Support `text/event-stream` with:
- ping/keepalive
- JSON event payloads
- event type field

**Step 5: Run tests**

Run: `python3 -m pytest tests/web_console/test_event_bus.py -q`
Expected: PASS.

**Step 6: Commit**

```bash
git add gateway/web_console/event_bus.py gateway/web_console/sse.py gateway/web_console/state.py tests/web_console/test_event_bus.py
git commit -m "feat: add web console event bus and sse transport"
```

### Task 4: Instrument agent runs for GUI events

**Objective:** Feed the GUI with live run state from the existing Hermes runtime.

**Files:**
- Modify: `run_agent.py`
- Modify: `gateway/run.py`
- Create: `gateway/web_console/services/chat_service.py`
- Test: `tests/web_console/test_chat_api.py`

**Step 1: Write failing tests for emitted run events**

Test expected event sequence for one prompt:
- `run.started`
- `message.user`
- `tool.started` / `tool.finished` as applicable
- `message.assistant.delta` (optional)
- `message.assistant.completed`
- `run.completed`

**Step 2: Run test to verify failure**

Run: `python3 -m pytest tests/web_console/test_chat_api.py -q`
Expected: FAIL — no emitted GUI events.

**Step 3: Add GUI event callback plumbing**

In `run_agent.py`, add optional callback hooks on agent lifecycle:
- run start/end
- assistant delta/final
- tool call start/end
- approval request
- clarification request
- background result

Keep callback optional and no-op when GUI is not active.

**Step 4: Bridge gateway/CLI runtime state into chat service**

Create a wrapper that runs Hermes and publishes GUI events to the bus.

**Step 5: Run tests**

Run: `python3 -m pytest tests/web_console/test_chat_api.py -q`
Expected: PASS.

**Step 6: Commit**

```bash
git add run_agent.py gateway/run.py gateway/web_console/services/chat_service.py tests/web_console/test_chat_api.py
git commit -m "feat: publish gui events from hermes runs"
```

### Task 5: Add chat endpoints for sessions, runs, retry, undo, and stop

**Objective:** Make chat control first-class in the GUI backend.

**Files:**
- Create: `gateway/web_console/api/chat.py`
- Modify: `gateway/web_console/routes.py`
- Test: `tests/web_console/test_chat_api.py`

**Step 1: Write failing tests for chat endpoints**

Endpoints to cover:
- `POST /api/gui/chat/send`
- `POST /api/gui/chat/stop`
- `POST /api/gui/chat/retry`
- `POST /api/gui/chat/undo`
- `GET /api/gui/chat/run/{run_id}`

**Step 2: Run test to verify failure**

Run: `python3 -m pytest tests/web_console/test_chat_api.py -q`
Expected: FAIL — routes missing.

**Step 3: Implement endpoints with thin service layer**

Response shape example:

```json
{
  "ok": true,
  "session_id": "sess_123",
  "run_id": "run_123",
  "status": "started"
}
```

**Step 4: Run tests**

Run: `python3 -m pytest tests/web_console/test_chat_api.py -q`
Expected: PASS.

**Step 5: Commit**

```bash
git add gateway/web_console/api/chat.py gateway/web_console/routes.py tests/web_console/test_chat_api.py
git commit -m "feat: add web console chat control api"
```

### Task 6: Add sessions API backed by existing Hermes storage

**Objective:** Let the GUI browse, search, preview, resume, title, export, and delete sessions.

**Files:**
- Create: `gateway/web_console/api/sessions.py`
- Create: `gateway/web_console/services/session_service.py`
- Test: `tests/web_console/test_sessions_api.py`

**Step 1: Write failing tests for session list/preview/resume actions**

Cover:
- `GET /api/gui/sessions`
- `GET /api/gui/sessions/{session_id}`
- `GET /api/gui/sessions/{session_id}/transcript`
- `POST /api/gui/sessions/{session_id}/title`
- `POST /api/gui/sessions/{session_id}/resume`
- `DELETE /api/gui/sessions/{session_id}`

**Step 2: Run test to verify failure**

Run: `python3 -m pytest tests/web_console/test_sessions_api.py -q`
Expected: FAIL.

**Step 3: Reuse existing session/state storage**

Do not create a new database. Wrap the existing session read/search/title/delete logic exposed by Hermes internals.

**Step 4: Include lineage and recap fields**

Session list items should expose:
- `session_id`
- `title`
- `last_active`
- `source`
- `workspace`
- `model`
- `token_summary`
- `parent_session_id`
- `has_tools`
- `has_attachments`

**Step 5: Run tests**

Run: `python3 -m pytest tests/web_console/test_sessions_api.py -q`
Expected: PASS.

**Step 6: Commit**

```bash
git add gateway/web_console/api/sessions.py gateway/web_console/services/session_service.py tests/web_console/test_sessions_api.py
git commit -m "feat: add sessions api for web console"
```

### Task 7: Add approvals and clarifications APIs

**Objective:** Convert human-in-the-loop runtime pauses into GUI-native actions.

**Files:**
- Create: `gateway/web_console/api/approvals.py`
- Create: `gateway/web_console/services/approval_service.py`
- Modify: `gateway/web_console/services/chat_service.py`
- Test: `tests/web_console/test_approvals_api.py`

**Step 1: Write failing tests for approval and clarification lifecycle**

Cover:
- approval request appears in pending list
- submit approve/deny decisions
- submit clarify responses
- timeout/expired request states

**Step 2: Run test to verify failure**

Run: `python3 -m pytest tests/web_console/test_approvals_api.py -q`
Expected: FAIL.

**Step 3: Wrap existing callbacks into resumable service state**

Expose endpoints:
- `GET /api/gui/human/pending`
- `POST /api/gui/human/approve`
- `POST /api/gui/human/deny`
- `POST /api/gui/human/clarify`

**Step 4: Add secure secret/passphrase submission path**

Never return stored values in API responses. Return only masked metadata.

**Step 5: Run tests**

Run: `python3 -m pytest tests/web_console/test_approvals_api.py -q`
Expected: PASS.

**Step 6: Commit**

```bash
git add gateway/web_console/api/approvals.py gateway/web_console/services/approval_service.py tests/web_console/test_approvals_api.py
git commit -m "feat: add human approval and clarification api"
```

### Task 8: Add workspace, diff, process, and rollback APIs

**Objective:** Expose Hermes coding/operator workflow in the GUI.

**Files:**
- Create: `gateway/web_console/api/workspace.py`
- Create: `gateway/web_console/services/workspace_service.py`
- Test: `tests/web_console/test_workspace_api.py`

**Step 1: Write failing tests for workspace actions**

Cover:
- list files / tree
- read file
- search files
- get diff/patch preview
- list checkpoints
- rollback workspace
- rollback file
- list processes
- process logs/kill

**Step 2: Run test to verify failure**

Run: `python3 -m pytest tests/web_console/test_workspace_api.py -q`
Expected: FAIL.

**Step 3: Wrap existing file/process/checkpoint tools safely**

Expose read-only defaults and explicit mutating endpoints.

Example endpoints:
- `GET /api/gui/workspace/tree`
- `GET /api/gui/workspace/file`
- `GET /api/gui/workspace/search`
- `GET /api/gui/workspace/diff`
- `GET /api/gui/workspace/checkpoints`
- `POST /api/gui/workspace/rollback`
- `GET /api/gui/processes`
- `GET /api/gui/processes/{id}/log`
- `POST /api/gui/processes/{id}/kill`

**Step 4: Run tests**

Run: `python3 -m pytest tests/web_console/test_workspace_api.py -q`
Expected: PASS.

**Step 5: Commit**

```bash
git add gateway/web_console/api/workspace.py gateway/web_console/services/workspace_service.py tests/web_console/test_workspace_api.py
git commit -m "feat: add workspace and rollback api"
```

### Task 9: Add memory and session-search APIs

**Objective:** Surface durable memory and past-conversation recall.

**Files:**
- Create: `gateway/web_console/api/memory.py`
- Create: `gateway/web_console/services/memory_service.py`
- Test: `tests/web_console/test_memory_api.py`

**Step 1: Write failing tests for memory CRUD and session search**

Cover:
- list memory entries
- add/replace/remove memory
- list user profile entries
- run session search

**Step 2: Run test to verify failure**

Run: `python3 -m pytest tests/web_console/test_memory_api.py -q`
Expected: FAIL.

**Step 3: Implement thin wrappers over existing memory/session_search logic**

Endpoints:
- `GET /api/gui/memory`
- `POST /api/gui/memory`
- `PATCH /api/gui/memory`
- `DELETE /api/gui/memory`
- `GET /api/gui/user-profile`
- `GET /api/gui/session-search`

**Step 4: Run tests**

Run: `python3 -m pytest tests/web_console/test_memory_api.py -q`
Expected: PASS.

**Step 5: Commit**

```bash
git add gateway/web_console/api/memory.py gateway/web_console/services/memory_service.py tests/web_console/test_memory_api.py
git commit -m "feat: add memory and session search api"
```

### Task 10: Add skills API and session skill-loading controls

**Objective:** Turn skills into a manageable GUI surface.

**Files:**
- Create: `gateway/web_console/api/skills.py`
- Create: `gateway/web_console/services/skill_service.py`
- Test: `tests/web_console/test_skills_api.py`

**Step 1: Write failing tests for skill list/view/manage endpoints**

Cover:
- list installed skills
- get skill details
- install/remove/update if supported in current environment
- mark skill as loaded for session draft state

**Step 2: Run test to verify failure**

Run: `python3 -m pytest tests/web_console/test_skills_api.py -q`
Expected: FAIL.

**Step 3: Implement wrappers over existing skills hub/service commands**

Keep writes capability-gated if skill management requires extra permissions.

**Step 4: Run tests**

Run: `python3 -m pytest tests/web_console/test_skills_api.py -q`
Expected: PASS.

**Step 5: Commit**

```bash
git add gateway/web_console/api/skills.py gateway/web_console/services/skill_service.py tests/web_console/test_skills_api.py
git commit -m "feat: add skills api for web console"
```

### Task 11: Add cron API and run history

**Objective:** Make scheduled automations manageable from the GUI.

**Files:**
- Create: `gateway/web_console/api/cron.py`
- Create: `gateway/web_console/services/cron_service.py`
- Test: `tests/web_console/test_cron_api.py`

**Step 1: Write failing tests for cron CRUD**

Cover:
- list jobs
- create job
- update job
- pause/resume/remove
- run-now
- fetch run history/output metadata

**Step 2: Run test to verify failure**

Run: `python3 -m pytest tests/web_console/test_cron_api.py -q`
Expected: FAIL.

**Step 3: Implement wrappers over existing cronjob logic**

**Step 4: Run tests**

Run: `python3 -m pytest tests/web_console/test_cron_api.py -q`
Expected: PASS.

**Step 5: Commit**

```bash
git add gateway/web_console/api/cron.py gateway/web_console/services/cron_service.py tests/web_console/test_cron_api.py
git commit -m "feat: add cron management api"
```

### Task 12: Add gateway/platform admin and pairing APIs

**Objective:** Expose Hermes as a multi-platform operations console.

**Files:**
- Create: `gateway/web_console/api/gateway_admin.py`
- Create: `gateway/web_console/services/gateway_service.py`
- Test: `tests/web_console/test_gateway_admin_api.py`

**Step 1: Write failing tests for gateway overview endpoints**

Cover:
- current gateway state
- platform list/status
- pairing list/approve/revoke
- service command endpoints if enabled
- delivery/home target read/update

**Step 2: Run test to verify failure**

Run: `python3 -m pytest tests/web_console/test_gateway_admin_api.py -q`
Expected: FAIL.

**Step 3: Implement admin wrappers with safe capability checks**

Do not silently allow service control on unsupported OS/runtime; return structured capability metadata.

**Step 4: Run tests**

Run: `python3 -m pytest tests/web_console/test_gateway_admin_api.py -q`
Expected: PASS.

**Step 5: Commit**

```bash
git add gateway/web_console/api/gateway_admin.py gateway/web_console/services/gateway_service.py tests/web_console/test_gateway_admin_api.py
git commit -m "feat: add gateway admin api"
```

### Task 13: Add settings, auth-status, logs, browser, and media APIs

**Objective:** Complete the backend surface needed for a full control plane.

**Files:**
- Create: `gateway/web_console/api/settings.py`
- Create: `gateway/web_console/api/logs.py`
- Create: `gateway/web_console/api/browser.py`
- Create: `gateway/web_console/api/media.py`
- Create: `gateway/web_console/services/settings_service.py`
- Create: `gateway/web_console/services/log_service.py`
- Create: `gateway/web_console/services/browser_service.py`
- Test: `tests/web_console/test_settings_api.py`
- Test: `tests/web_console/test_logs_api.py`
- Test: `tests/web_console/test_browser_api.py`

**Step 1: Write failing tests**

Cover:
- current model/provider/auth status
- config sections safe read/update
- toolsets by platform
- logs tail/filter
- browser status/connect/disconnect metadata
- media upload/transcription/TTS metadata stubs

**Step 2: Run tests to verify failure**

Run: `python3 -m pytest tests/web_console/test_settings_api.py tests/web_console/test_logs_api.py tests/web_console/test_browser_api.py -q`
Expected: FAIL.

**Step 3: Implement service wrappers**

Protect secret-bearing fields by masking or omitting values.

**Step 4: Run tests**

Run: `python3 -m pytest tests/web_console/test_settings_api.py tests/web_console/test_logs_api.py tests/web_console/test_browser_api.py -q`
Expected: PASS.

**Step 5: Commit**

```bash
git add gateway/web_console/api/settings.py gateway/web_console/api/logs.py gateway/web_console/api/browser.py gateway/web_console/api/media.py gateway/web_console/services/settings_service.py gateway/web_console/services/log_service.py gateway/web_console/services/browser_service.py tests/web_console/test_settings_api.py tests/web_console/test_logs_api.py tests/web_console/test_browser_api.py
git commit -m "feat: add settings logs browser and media api"
```

### Task 14: Create the frontend app scaffold

**Objective:** Add a standalone SPA source tree for Hermes Web Console.

**Files:**
- Create: `web_console/package.json`
- Create: `web_console/tsconfig.json`
- Create: `web_console/vite.config.ts`
- Create: `web_console/index.html`
- Create: `web_console/src/main.tsx`
- Create: `web_console/src/app/App.tsx`
- Create: `web_console/src/app/router.tsx`
- Create: `web_console/src/app/providers.tsx`
- Create: `web_console/src/app/theme.css`

**Step 1: Write failing frontend smoke test**

Add a Vitest render test that mounts `App` and expects navigation labels:
- Chat
- Sessions
- Workspace
- Automations
- Memory
- Skills
- Gateway
- Settings
- Logs

**Step 2: Run test to verify failure**

Run: `cd web_console && npm test -- --run`
Expected: FAIL — app missing.

**Step 3: Create the scaffold**

Use React + TypeScript + Vite, with one app shell and placeholder routes.

**Step 4: Run frontend tests**

Run: `cd web_console && npm test -- --run`
Expected: PASS.

**Step 5: Commit**

```bash
git add web_console
git commit -m "feat: scaffold hermes web console frontend"
```

### Task 15: Build the application shell, router, stores, and API client

**Objective:** Create the reusable frontend foundation.

**Files:**
- Create: `web_console/src/lib/api.ts`
- Create: `web_console/src/lib/events.ts`
- Create: `web_console/src/lib/types.ts`
- Create: `web_console/src/lib/utils.ts`
- Create: `web_console/src/store/uiStore.ts`
- Create: `web_console/src/store/sessionStore.ts`
- Create: `web_console/src/store/runStore.ts`
- Create: `web_console/src/store/workspaceStore.ts`
- Create: `web_console/src/components/layout/*.tsx`

**Step 1: Write failing tests for shell behavior**

Test:
- nav renders
- route changes work
- right inspector toggles
- bottom drawer opens

**Step 2: Run tests to verify failure**

Run: `cd web_console && npm test -- --run`
Expected: FAIL.

**Step 3: Implement app shell and stores**

Include:
- top bar with model/provider/run state
- sidebar nav
- inspector tabs
- bottom drawer for terminal/logs/processes

**Step 4: Run tests**

Run: `cd web_console && npm test -- --run`
Expected: PASS.

**Step 5: Commit**

```bash
git add web_console/src
git commit -m "feat: add web console shell stores and api client"
```

### Task 16: Build the Chat page with transcript, composer, tool timeline, approvals, and streaming

**Objective:** Deliver the browser experience that matches Hermes core value.

**Files:**
- Create: `web_console/src/pages/ChatPage.tsx`
- Create: `web_console/src/components/chat/*.tsx`
- Test: `web_console/src/components/chat/*.test.tsx`
- Test: `web_console/playwright/chat.spec.ts`

**Step 1: Write failing component tests**

Cover:
- transcript rendering for user/assistant/tool/system messages
- composer send
- tool timeline expand/collapse
- approval modal appears
- clarification form appears
- stop button dispatches action

**Step 2: Run tests to verify failure**

Run: `cd web_console && npm test -- --run`
Expected: FAIL.

**Step 3: Implement Chat page**

Must include:
- transcript stream
- image attach control
- multiline composer
- stop/retry/undo/new chat actions
- run status bar
- pending approval/clarify surfaces

**Step 4: Add Playwright smoke flow**

Flow:
- open app
- start chat
- verify streamed content placeholder / mocked event flow

**Step 5: Run tests**

Run:
- `cd web_console && npm test -- --run`
- `cd web_console && npm run test:e2e`
Expected: PASS.

**Step 6: Commit**

```bash
git add web_console/src/pages/ChatPage.tsx web_console/src/components/chat web_console/playwright/chat.spec.ts
git commit -m "feat: implement web console chat experience"
```

### Task 17: Build Sessions page and transcript preview

**Objective:** Expose Hermes durable history visually.

**Files:**
- Create: `web_console/src/pages/SessionsPage.tsx`
- Create: `web_console/src/components/sessions/*.tsx`
- Test: `web_console/src/components/sessions/*.test.tsx`

**Step 1: Write failing tests**

Cover:
- session list rendering
- filter/search input
- preview panel
- resume/export/delete actions

**Step 2: Run tests to verify failure**

Run: `cd web_console && npm test -- --run`
Expected: FAIL.

**Step 3: Implement Sessions page**

**Step 4: Run tests**

Run: `cd web_console && npm test -- --run`
Expected: PASS.

**Step 5: Commit**

```bash
git add web_console/src/pages/SessionsPage.tsx web_console/src/components/sessions
git commit -m "feat: add sessions browser to web console"
```

### Task 18: Build Workspace page with file tree, viewer, diff, checkpoints, terminal, and processes

**Objective:** Make the GUI viable for development and ops workflows.

**Files:**
- Create: `web_console/src/pages/WorkspacePage.tsx`
- Create: `web_console/src/components/workspace/*.tsx`
- Test: `web_console/src/components/workspace/*.test.tsx`
- Test: `web_console/playwright/workspace.spec.ts`

**Step 1: Write failing tests**

Cover:
- file tree render/select
- file contents view
- diff view
- checkpoint list
- terminal panel
- process panel

**Step 2: Run tests to verify failure**

Run: `cd web_console && npm test -- --run`
Expected: FAIL.

**Step 3: Implement Workspace page**

**Step 4: Run tests**

Run:
- `cd web_console && npm test -- --run`
- `cd web_console && npm run test:e2e`
Expected: PASS.

**Step 5: Commit**

```bash
git add web_console/src/pages/WorkspacePage.tsx web_console/src/components/workspace web_console/playwright/workspace.spec.ts
git commit -m "feat: add workspace tools to web console"
```

### Task 19: Build Memory, Skills, and Automations pages

**Objective:** Surface Hermes-native long-term systems.

**Files:**
- Create: `web_console/src/pages/MemoryPage.tsx`
- Create: `web_console/src/pages/SkillsPage.tsx`
- Create: `web_console/src/pages/AutomationsPage.tsx`
- Create: `web_console/src/components/memory/*.tsx`
- Create: `web_console/src/components/skills/*.tsx`
- Create: `web_console/src/components/cron/*.tsx`
- Test: `web_console/src/components/**/*.test.tsx`
- Test: `web_console/playwright/cron.spec.ts`

**Step 1: Write failing tests**

Cover:
- memory list + edit
- session search results
- skills list + inspect
- cron list + create dialog

**Step 2: Run tests to verify failure**

Run: `cd web_console && npm test -- --run`
Expected: FAIL.

**Step 3: Implement the three pages**

**Step 4: Run tests**

Run:
- `cd web_console && npm test -- --run`
- `cd web_console && npm run test:e2e`
Expected: PASS.

**Step 5: Commit**

```bash
git add web_console/src/pages/MemoryPage.tsx web_console/src/pages/SkillsPage.tsx web_console/src/pages/AutomationsPage.tsx web_console/src/components/memory web_console/src/components/skills web_console/src/components/cron web_console/playwright/cron.spec.ts
git commit -m "feat: add memory skills and automations pages"
```

### Task 20: Build Gateway, Settings, and Logs pages

**Objective:** Turn the GUI into a true Hermes control plane.

**Files:**
- Create: `web_console/src/pages/GatewayPage.tsx`
- Create: `web_console/src/pages/SettingsPage.tsx`
- Create: `web_console/src/pages/LogsPage.tsx`
- Create: `web_console/src/components/gateway/*.tsx`
- Create: `web_console/src/components/settings/*.tsx`
- Create: `web_console/src/components/logs/*.tsx`
- Test: `web_console/src/components/**/*.test.tsx`

**Step 1: Write failing tests**

Cover:
- platform cards render statuses
- pairing actions render
- settings form renders sections
- logs viewer supports tabs/filtering

**Step 2: Run tests to verify failure**

Run: `cd web_console && npm test -- --run`
Expected: FAIL.

**Step 3: Implement the three pages**

**Step 4: Run tests**

Run: `cd web_console && npm test -- --run`
Expected: PASS.

**Step 5: Commit**

```bash
git add web_console/src/pages/GatewayPage.tsx web_console/src/pages/SettingsPage.tsx web_console/src/pages/LogsPage.tsx web_console/src/components/gateway web_console/src/components/settings web_console/src/components/logs
git commit -m "feat: add gateway settings and logs pages"
```

### Task 21: Serve built frontend assets from aiohttp and support local dev mode

**Objective:** Integrate the SPA into the existing Hermes API server cleanly.

**Files:**
- Modify: `gateway/web_console/static.py`
- Modify: `gateway/web_console/app.py`
- Modify: `gateway/platforms/api_server.py`
- Create: `tests/web_console/test_static_assets.py`

**Step 1: Write failing tests for static asset serving**

Cover:
- serves `index.html` from `/app/`
- serves hashed JS/CSS assets
- SPA fallback works for nested routes like `/app/sessions`

**Step 2: Run tests to verify failure**

Run: `python3 -m pytest tests/web_console/test_static_assets.py -q`
Expected: FAIL.

**Step 3: Implement static serving**

Support two modes:
- production build from `gateway/web_console/static_dist/`
- development proxy to `config.gui.dev_server_url`

**Step 4: Run tests**

Run: `python3 -m pytest tests/web_console/test_static_assets.py -q`
Expected: PASS.

**Step 5: Commit**

```bash
git add gateway/web_console/static.py gateway/web_console/app.py gateway/platforms/api_server.py tests/web_console/test_static_assets.py
git commit -m "feat: serve hermes web console assets"
```

### Task 22: Add docs, operator guidance, and packaging scripts

**Objective:** Make the GUI discoverable and maintainable.

**Files:**
- Modify: `README.md`
- Create: `website/docs/user-guide/features/web-console.md`
- Create: `website/docs/developer-guide/web-console-architecture.md`
- Modify: `package.json`
- Modify: `web_console/package.json`

**Step 1: Write failing docs/build checklist**

Create a checklist in the PR description or local notes for:
- dev mode
- prod build
- start server and open GUI
- auth/approval flow

**Step 2: Add scripts**

Examples:

```json
{
  "scripts": {
    "gui:install": "cd web_console && npm install",
    "gui:dev": "cd web_console && npm run dev",
    "gui:build": "cd web_console && npm run build",
    "gui:test": "cd web_console && npm test -- --run"
  }
}
```

**Step 3: Write docs**

Document:
- architecture
- local development
- how `/v1` and `/api/gui` differ
- security model
- release/build flow

**Step 4: Verify docs and scripts**

Run:
- `npm run gui:build`
- `python3 -m pytest tests/web_console -q`

Expected: PASS.

**Step 5: Commit**

```bash
git add README.md website/docs/user-guide/features/web-console.md website/docs/developer-guide/web-console-architecture.md package.json web_console/package.json
git commit -m "docs: add hermes web console documentation"
```

### Task 23: End-to-end stabilization and release gate

**Objective:** Ensure the GUI is production-ready enough for an initial release.

**Files:**
- Modify: any files touched in prior tasks
- Test: all GUI/backend/frontend tests

**Step 1: Run backend tests**

Run: `python3 -m pytest tests/gateway/test_api_server_gui_mount.py tests/web_console -q`
Expected: PASS.

**Step 2: Run frontend unit tests**

Run: `cd web_console && npm test -- --run`
Expected: PASS.

**Step 3: Run Playwright tests**

Run: `cd web_console && npm run test:e2e`
Expected: PASS.

**Step 4: Run full smoke test manually**

Manual checklist:
- open `/app/`
- start a chat
- watch streamed tool event(s)
- approve/deny a request
- open a session
- inspect a file/diff
- list cron jobs
- view gateway state
- browse logs

**Step 5: Commit**

```bash
git add -A
git commit -m "feat: ship hermes web console v1"
```

---

## 4. API contracts that must exist by the end

### Core endpoints

- `GET /api/gui/health`
- `GET /api/gui/meta`
- `GET /api/gui/stream/session/{session_id}`
- `POST /api/gui/chat/send`
- `POST /api/gui/chat/stop`
- `POST /api/gui/chat/retry`
- `POST /api/gui/chat/undo`
- `GET /api/gui/sessions`
- `GET /api/gui/sessions/{session_id}`
- `GET /api/gui/sessions/{session_id}/transcript`
- `POST /api/gui/sessions/{session_id}/title`
- `DELETE /api/gui/sessions/{session_id}`
- `GET /api/gui/workspace/tree`
- `GET /api/gui/workspace/file`
- `GET /api/gui/workspace/search`
- `GET /api/gui/workspace/diff`
- `GET /api/gui/workspace/checkpoints`
- `POST /api/gui/workspace/rollback`
- `GET /api/gui/processes`
- `GET /api/gui/processes/{process_id}/log`
- `POST /api/gui/processes/{process_id}/kill`
- `GET /api/gui/human/pending`
- `POST /api/gui/human/approve`
- `POST /api/gui/human/deny`
- `POST /api/gui/human/clarify`
- `GET /api/gui/memory`
- `GET /api/gui/user-profile`
- `GET /api/gui/session-search`
- `GET /api/gui/skills`
- `GET /api/gui/skills/{name}`
- `GET /api/gui/cron/jobs`
- `POST /api/gui/cron/jobs`
- `PATCH /api/gui/cron/jobs/{job_id}`
- `POST /api/gui/cron/jobs/{job_id}/run`
- `GET /api/gui/gateway/overview`
- `GET /api/gui/gateway/platforms`
- `GET /api/gui/gateway/pairing`
- `POST /api/gui/gateway/pairing/approve`
- `POST /api/gui/gateway/pairing/revoke`
- `GET /api/gui/settings`
- `PATCH /api/gui/settings`
- `GET /api/gui/logs`
- `GET /api/gui/browser/status`
- `POST /api/gui/browser/connect`
- `POST /api/gui/browser/disconnect`
- `POST /api/gui/media/upload`
- `POST /api/gui/media/transcribe`
- `POST /api/gui/media/tts`

---

## 5. Event types that must exist by the end

- `run.started`
- `run.completed`
- `run.failed`
- `message.user`
- `message.assistant.delta`
- `message.assistant.completed`
- `tool.started`
- `tool.stdout`
- `tool.completed`
- `tool.failed`
- `approval.requested`
- `approval.resolved`
- `clarify.requested`
- `clarify.resolved`
- `session.updated`
- `todo.updated`
- `subagent.started`
- `subagent.updated`
- `subagent.completed`
- `background_task.started`
- `background_task.completed`
- `process.updated`
- `checkpoint.created`
- `rollback.completed`

---

## 6. Acceptance criteria for “fully featured Hermes GUI”

The build is complete when a user can:

1. Open a browser to Hermes and land in a real app shell, not a single chat card.
2. Start, stop, retry, undo, and resume sessions.
3. See live tool activity during a run.
4. Approve risky actions and answer clarifications inline.
5. Browse sessions, inspect transcripts, and export/delete them.
6. Inspect files, diffs, checkpoints, processes, and terminal output.
7. View and edit memory, search past sessions, and inspect skills.
8. Create and manage cron jobs.
9. Monitor gateway/platform health, pairing requests, and service state.
10. Adjust settings for models/providers/toolsets/voice/browser/theme.
11. Read logs and recover from failures without dropping to CLI for routine workflows.

---

## 7. Recommended implementation order if you want maximum value early

1. Tasks 1-5
2. Task 14-16
3. Task 6-7
4. Task 21
5. Task 8
6. Task 9-11
7. Task 12-13
8. Task 17-20
9. Task 22-23

This gets a working, inspectable chat console into users’ hands quickly while preserving a path to the full control plane.

---

## 8. Final handoff note

Plan complete and saved. Ready to execute using subagent-driven-development — dispatch a fresh subagent per task, review spec compliance after each task, then perform code-quality review before continuing.
