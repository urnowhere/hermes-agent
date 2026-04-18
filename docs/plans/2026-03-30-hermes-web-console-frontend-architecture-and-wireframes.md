# Hermes Web Console Frontend Architecture and Wireframes

Date: 2026-03-30

## Purpose

This document defines the frontend information architecture, route map, component hierarchy, state model, and wireframe expectations for a fully featured Hermes GUI.

## Product structure

### Primary navigation

- Chat
- Sessions
- Workspace
- Automations
- Memory
- Skills
- Gateway
- Settings
- Logs

### Global layout

- Top bar
- Left sidebar navigation
- Main content region
- Right inspector
- Bottom drawer

### Persistent shell responsibilities

The shell should always show:
- current workspace
- current model/provider
- current run status
- quick stop button
- quick new chat button
- current session title
- connection/health status

## Route map

- `/app/` -> Chat
- `/app/chat/:sessionId?`
- `/app/sessions`
- `/app/workspace`
- `/app/automations`
- `/app/memory`
- `/app/skills`
- `/app/gateway`
- `/app/settings`
- `/app/logs`

Optional nested routes:
- `/app/sessions/:sessionId`
- `/app/workspace/file/*`
- `/app/gateway/platform/:platform`
- `/app/settings/:section`

## Component tree

### App shell

- `AppShell`
  - `TopBar`
  - `Sidebar`
  - `MainRouterOutlet`
  - `Inspector`
  - `BottomDrawer`

### TopBar

Responsibilities:
- workspace switcher
- session title display/edit
- model/provider pills
- run-state chip
- stop button
- health chip
- quick actions menu

### Sidebar

Responsibilities:
- primary nav
- recent sessions list
- pinned sessions
- new chat button
- global search trigger

### Inspector

Tabbed side panel. Tabs:
- Run
- Tools
- TODO
- Session
- Human

#### Run tab
- active run metadata
- current step
- elapsed time
- stop/retry/undo buttons

#### Tools tab
- chronological tool timeline
- filter by status/tool type
- expand raw args/results

#### TODO tab
- Hermes todo list
- pending/in-progress/completed/cancelled groups

#### Session tab
- session metadata
- export/delete/title edit
- lineage graph summary

#### Human tab
- pending approvals
- pending clarifications
- secret/password prompts

### BottomDrawer

Tabs:
- Terminal
- Processes
- Logs
- Browser

## Page specs

### 1. Chat page

#### Responsibilities
- transcript rendering
- live SSE subscription
- composer and attachments
- approvals/clarify UI
- visible tool stream

#### Layout
- main transcript center
- right inspector open by default
- optional left recent-session rail

#### Required components
- `Transcript`
- `MessageCard`
- `Composer`
- `RunStatusBar`
- `ToolTimeline`
- `ApprovalPrompt`
- `ClarifyPrompt`

#### Transcript message types
- user
- assistant
- tool_call
- tool_result
- system
- approval_request
- clarification_request
- background_result
- subagent_summary
- attachment

#### Chat states
- idle
- sending
- streaming
- waiting_for_human
- interrupted
- failed
- completed

#### Composer states
- default
- drag-over
- uploading
- recording-audio
- disabled-busy

### 2. Sessions page

#### Responsibilities
- search/filter sessions
- preview and resume
- inspect lineage
- export/delete

#### Required components
- `SessionFilterBar`
- `SessionList`
- `SessionPreview`
- `LineageMiniGraph`

#### Layout
- filters at top/left
- results list center
- preview detail panel right

### 3. Workspace page

#### Responsibilities
- browse files
- inspect contents
- inspect diffs/patches
- inspect checkpoints
- terminal/process control

#### Required components
- `FileTree`
- `FileViewer`
- `SearchPanel`
- `DiffViewer`
- `CheckpointList`
- `TerminalPanel`
- `ProcessPanel`

#### Layout
- left: file tree
- center: file/diff tabs
- right: patch/checkpoint inspector
- bottom: terminal/process drawer

### 4. Automations page

#### Responsibilities
- view/edit cron jobs
- run-now / pause / resume / remove
- inspect history/output

#### Required components
- `CronToolbar`
- `CronList`
- `CronEditor`
- `CronRunHistory`

### 5. Memory page

#### Responsibilities
- view/edit memory
- view/edit user profile memory
- search past sessions
- inspect memory provenance

#### Required components
- `MemoryTabs`
- `MemoryList`
- `MemoryEditor`
- `SessionSearchBox`
- `SessionSearchResults`

### 6. Skills page

#### Responsibilities
- browse installed skills
- inspect skill content
- install/update/remove
- load skill for session

#### Required components
- `SkillTabs`
- `SkillList`
- `SkillDetail`
- `SkillActionBar`

### 7. Gateway page

#### Responsibilities
- platform overview
- pairing management
- service state/logs
- home-channel/delivery summary

#### Required components
- `GatewayOverviewCards`
- `PlatformCards`
- `PairingQueue`
- `ApprovedUsersTable`
- `GatewayServicePanel`

### 8. Settings page

#### Responsibilities
- provider/model config
- auth status
- toolsets
- terminal/browser/voice settings
- themes/plugins
- advanced config

#### Required components
- `SettingsSidebar`
- `SettingsForm`
- `AuthStatusCards`
- `ToolsetMatrix`
- `PluginList`
- `ThemePicker`
- `AdvancedConfigEditor`

### 9. Logs page

#### Responsibilities
- tail/filter logs
- switch log source
- correlate logs with sessions/runs

#### Required components
- `LogSourceTabs`
- `LogViewer`
- `LogFilterBar`
- `LogDetailPanel`

## Frontend state model

### Server state (TanStack Query)
- sessions list
- session details/transcript
- workspace tree/file/search
- checkpoints
- processes/logs
- memory
- skills
- cron jobs
- gateway overview/platforms/pairing
- settings sections
- browser status

### Live event state (Zustand)
- current run id
- run status
- streaming assistant text
- pending tool timeline
- pending approvals/clarifications
- todo list
- subagent summaries
- transient banners/toasts

### Local UI state (Zustand)
- inspector open/tab
- bottom drawer open/tab
- selected workspace file
- selected diff/checkpoint
- chat composer draft
- session filters
- theme preference

## Event handling model

### SSE subscription behavior

When Chat page mounts:
1. open SSE for current session
2. buffer incoming events in `runStore`
3. merge completed events into session/transcript caches
4. show reconnection banner if stream drops

### Optimistic UI actions

Allowed:
- session title edit
- memory entry edit
- cron pause/resume
- inspector tab changes

Not optimistic:
- risky service control
- rollback
- delete session
- plugin install/remove

## Wireframes

### Global shell

```text
┌──────────────────────────────────────────────────────────────────────────────┐
│ Hermes | Workspace ▼ | Model ▼ | Provider ▼ | Run: Active | Stop | Health │
├───────────────┬─────────────────────────────────────┬───────────────────────┤
│ Sidebar       │ Main content                         │ Inspector             │
│               │                                      │ Run / Tools / TODO    │
│ Chat          │                                      │ Session / Human       │
│ Sessions      │                                      │                       │
│ Workspace     │                                      │                       │
│ Automations   │                                      │                       │
│ Memory        │                                      │                       │
│ Skills        │                                      │                       │
│ Gateway       │                                      │                       │
│ Settings      │                                      │                       │
│ Logs          │                                      │                       │
├───────────────┴─────────────────────────────────────┴───────────────────────┤
│ Bottom drawer: Terminal | Processes | Logs | Browser                        │
└──────────────────────────────────────────────────────────────────────────────┘
```

### Chat page

```text
┌────────────────────────────── Chat ──────────────────────────────────────────┐
│ Transcript                                                                │
│ ┌─────────────────────────────────────────────────────────────────────────┐ │
│ │ User: research and scan and tell me all the functions...               │ │
│ │ Assistant: Hermes needs a full control plane...                        │ │
│ │ Tool: search_files(...)                                                │ │
│ │ Tool: read_file(...)                                                   │ │
│ │ Approval needed: Allow terminal command? [Approve once] [Deny]         │ │
│ └─────────────────────────────────────────────────────────────────────────┘ │
│                                                                             │
│ [Attach] [Mic] [multiline prompt.......................................]   │
│ [Send]                                                                      │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Sessions page

```text
┌──────────────────────────── Sessions ────────────────────────────────────────┐
│ Filters: [query] [source] [workspace] [model] [has tools]                  │
├──────────────────────────────┬───────────────────────────────────────────────┤
│ Session list                  │ Preview                                      │
│ - Hermes GUI planning         │ Title: Hermes GUI planning                   │
│ - Pricing architecture        │ Last active: ...                             │
│ - Telegram pairing            │ Lineage: root -> compressed -> current       │
│                               │ Actions: Resume Export Delete                │
└──────────────────────────────┴───────────────────────────────────────────────┘
```

### Workspace page

```text
┌──────────────────────────── Workspace ───────────────────────────────────────┐
│ File tree             │ Editor / Diff / Search           │ Checkpoints      │
│ src/                  │ -------------------------------- │ - before patch   │
│ tests/                │ current file contents            │ - before rollback│
│ docs/                 │ or unified diff                  │                  │
├──────────────────────────────────────────────────────────────────────────────┤
│ Terminal | Processes | Logs                                                 │
└──────────────────────────────────────────────────────────────────────────────┘
```

### Gateway page

```text
┌──────────────────────────── Gateway ─────────────────────────────────────────┐
│ Overview cards: running / platforms healthy / pending pairing / home target │
├──────────────────────────────┬───────────────────────────────────────────────┤
│ Platform cards                │ Pairing / service panel                      │
│ Telegram: connected           │ Pending code AB12CD34 [Approve] [Reject]    │
│ Discord: configured           │ Service: running [Restart] [Stop]            │
│ Slack: error                  │ Logs tail...                                 │
└──────────────────────────────┴───────────────────────────────────────────────┘
```

## UX rules

1. Tool details default collapsed but one click away.
2. Human-required actions must visually block the active run state.
3. Destructive actions need confirmation copy naming what will be affected.
4. Raw JSON or raw log views must be available from every rich card/detail view.
5. Every long-running action should show status, elapsed time, and cancelability if supported.

## Accessibility requirements

- all primary actions keyboard reachable
- focus trap in approval/clarify modals
- aria-live region for streamed response text and status updates
- high-contrast theme support
- avoid color-only status signaling

## Frontend testing strategy

### Unit/component tests
- render shell
- route transitions
- transcript item rendering
- approval and clarify forms
- file viewer/diff viewer states
- cron editor forms
- settings save/reset behaviors

### Playwright flows
- start and stream a chat
- approve a pending action
- open a session preview and resume it
- inspect a diff and checkpoint list
- create/pause/run a cron job
- open gateway page and inspect platform cards

## Build/deploy model

### Development
- `web_console` runs via Vite dev server
- aiohttp proxies `/app/*` to `config.gui.dev_server_url`

### Production
- frontend builds static assets into a dist directory
- build artifacts copied into `gateway/web_console/static_dist/`
- aiohttp serves `index.html` + hashed assets with SPA fallback

## Open implementation questions

1. Whether to add drag-and-drop diff acceptance in v1 or v1.1
2. Whether the Browser tab in bottom drawer should include screenshots in v1
3. Whether remote GUI auth should be separate from API server bearer auth
4. Whether logs page should stream over SSE or poll initially

## Definition of done for frontend

Frontend is done when:
- all primary nav pages render
- chat streaming works against real backend events
- session resume works
- workspace diff/checkpoint/process views work
- memory/skills/cron/gateway/settings/logs pages all consume real backend data
- Playwright smoke suite passes
