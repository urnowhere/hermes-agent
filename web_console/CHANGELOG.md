# Changelog

All notable changes to the Hermes Web Console will be documented in this file.

## [Unreleased]

### Added
- **Stronger Snapshot Parity**: Upgraded `/snapshot` and `/snap` from a backup-export shortcut into a richer web-console flow with list, create, restore, and prune subcommands backed by dedicated snapshot APIs.
- **Chat Command Parity Cleanup**: Added web-chat parity for `/title`, `/rollback`, `/fork`, and `/reload_mcp`, closing the remaining slash-command gaps for session renaming, checkpoint restore, branch aliasing, and MCP reloads.
- **Browser-Native Session Controls**: Added web-native `/statusbar` and `/sb` handling to toggle the in-chat run/usage status bar, plus explicit `/quit` and `/exit` guidance and smart `/q` alias behavior in chat.
- **Snapshot API Surface**: Added `/api/gui/system/snapshots`, `/api/gui/system/snapshots/restore`, and `/api/gui/system/snapshots/prune` so the browser can manage Hermes quick state snapshots directly.
- **Snapshot / Reload / Debug Parity**: Added web-console support for `/snapshot`, `/reload`, and `/debug`, including an in-process `.env` reload route and debug-report upload/local-output route.
- **System API Coverage**: Added dedicated backend tests for the new snapshot endpoints plus `/api/gui/system/reload` and `/api/gui/system/debug`, along with frontend chat-command coverage in `App.test.tsx`.
- **Missions Kanban Board**: New `/missions` overarching route providing an intuitive HTML5 drag-and-drop interface for managing agent tasks with Backlog, In Progress, Review, and Done columns.
- **Dashboard Command Center**: Live-polling overarching global interface tracking CPU limits, host memory footprint, active Cron Jobs, and background operations in real-time.
- **CLI Session Bridge**: Sessions viewer now imports and segregates interactions made natively in the CLI vs the Web UI via SQLite reads.
- **Rich Vision Input**: Added glow-visualized drag-and-drop dropzones over the main chat composer to securely facilitate image context streaming.
- **Workspace File @Mentions**: Introduced an elegant native popup autocomplete inside the chat composer. Type `@` to select local workspace files to be injected efficiently into context.
- **Portable Mode (Backend Agnostic)**: The UI now degrades gracefully when the Hermes core backend is down, exposing a red health banner instead of crashing the interface with 500s.
- **PWA Installation**: Fully initialized `manifest.json`, local `<link>` tags, generated icon sets, and an offline-ready `sw.js` Service Worker to run Hermes natively on any Desktop or device.
- **Universal CLI Command Parity**: Added full backend support and chat component slash dispatching for `/fast`, `/yolo`, `/reasoning`, and `/verbose` tracking core CLI parameters seamlessly.
- **Intelligent Autocomplete Registry**: Exposed the global static command registry to the Web UI via `/api/gui/commands` for unified composer auto-completion.
- **Dedicated Command Browser**: Added a new Commands route backed by the shared CLI registry, including usage hints, aliases, and parity badges (`Full`, `Partial`, `CLI only`).
- **Expanded Slash Coverage**: Added practical web-console dispatch for `/queue`, `/branch`, `/resume`, `/save`, `/approve`, `/deny`, `/history`, `/config`, `/platforms`, `/image`, `/paste`, `/restart`, `/update`, and `/sethome`.
- **Weixin (WeChat) Support**: Config Modal handles `token` and `account_id` structures mirroring the new native Gateway integrations.
- **Docker Strategy**: Created standalone `Dockerfile.frontend` and `Dockerfile.backend` setups composed via `docker-compose.yml` to instantly spin up the proxy architectures seamlessly.

### Fixed
- **Full-Suite CI Determinism**: Made the upstream GitHub Actions-equivalent test suite hermetic against local Codex CLI credentials and aligned Honcho/opencode model-list assertions with current behavior, clearing the last full-suite failures after the upstream sync.
- **CI Stabilization & Test Hygiene**: Resolved the remaining broad gateway/hermes_cli/tools/web_console pytest failures and cleaned up straightforward warning sources like the missing `ssh` pytest mark registration and aiohttp AppKey usage in API-server job tests.
- **Upstream CI Stabilization**: Fixed additional fork-vs-upstream regressions in gateway pairing storage, session-context cleanup, runtime-provider custom endpoint resolution, auth command removal behavior, and browser local-mode detection so the fork stays merge-ready.
- Re-architected Vitest `App.test.tsx` mock server payloads to cleanly yield `commands: []` bypassing fatal `flatMap` undefined array mapping crashes.
- Refined command-browser parity labeling so browser-native approximations like `/config`, `/history`, `/platforms`, `/voice`, `/update`, and `/restart` are marked **Partial** instead of overstating full CLI equivalence.
- Replaced deprecated `apple-mobile-web-app-capable` meta tags natively inside `index.html`.
- Implemented robust `ConnectionProvider` polling states preventing error-log flooding in DevTools when backend connectivity is severed.
- Adjusted CSS spacing within `MissionsPage.tsx` using `overflowX: 'auto'` to ensure the fourth boundary column isn't obscured out of frame relative to the Inspector pane.
