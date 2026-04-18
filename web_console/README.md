<p align="center">
  <img src="../assets/banner.png" alt="Hermes Agent" width="100%">
</p>

# Hermes Web Console 🖥️✨

A highly polished, modern web dashboard for **Hermes Agent**. The Web Console brings the raw power of the Hermes terminal and its core configurability directly to the browser, offering broad practical parity with the CLI while introducing intuitive drag-and-drop workflows for advanced agentic operations.

## ✨ Features

- **Live Streaming Parity**: Connects directly to the core Hermes API Event Stream (`message.assistant.delta`) to offer responsive typewriter streaming without delays.
- **Agentic IDE Sandbox**: Inspect live runtime logs and tools, and spawn native `xterm.js` terminal environments inside the drawer.
- **Dashboard Command Center**: Real-time observability dashboard streaming CPU, memory, Process, and Cron active metrics directly from the host.
- **CLI Session Bridge**: Seamlessly view and interact with CLI terminal sessions and memory straight from the web console.
- **Offline Portable Mode**: Fallback to local offline mode with graceful degradation when the backend is unreachable. 
- **Missions Kanban**: Create, drag-and-drop, and monitor agentic missions on a comprehensive visual board.
- **Workspace Integration**: Mentioning files with `@` directly links to your file explorer context. Rich dropzones power native vision multi-modal interactions.
- **PWA Support**: Full manifest and service worker deployment for native standalone app-like installations across Desktop and Mobile.
- **Visual Configurations**: Avoid editing `config.yaml` manually. Setup complex hierarchies like drag-and-drop ordered **Fallback Providers**, multi-key **Credential Pools**, and isolated Messaging Gateways all from an organized UI.
- **Theme Persistence**: Dark, light, or completely custom skins. Changes are natively synchronized with your overarching Hermes profile.
- **Syntax Highlighting & Inline Diffs**: Unified, collapsible Git-style file diffs directly inside the chat interface letting you confidently review the agent's file modifications.
- **Shared Command Registry**: The composer autocomplete is powered by the same slash-command registry as the Hermes CLI via `/api/gui/commands`.
- **Command Browser**: A dedicated Commands page lets you browse canonical commands, aliases, usage hints, and parity badges (`Full`, `Partial`, `CLI only`).
- **Browser-Native Slash Flows**: The chat interface now supports practical web versions of many CLI commands including `/queue`, `/branch`, `/resume`, `/save`, `/snapshot` (list/create/restore/prune), `/platforms`, `/image`, `/paste`, `/fast`, `/yolo`, `/reasoning`, `/verbose`, `/reload`, and `/debug`.

## 🚀 Quick Start

The console acts as a client connected to the Hermes Local API Server.

Ensure you have your backend running:
```bash
hermes api start
```

### Running the UI (Development)

Navigate to the `web_console` directory:

```bash
cd web_console
npm install
npm run dev
```
Navigate to `http://localhost:5173` locally. Set your backend URL mapping inside the UI Settings if the API server resides on a custom port or remote network.

### Building for Production

Compile the production bundle cleanly:

```bash
npm run build
```

The optimized static assets will populate the `/dist` directory automatically compatible with most static web-farm configurations or native integrations back onto the python API router.

## ⌘ Slash Commands in the Web Console

The web console supports a large subset of Hermes slash commands directly inside chat. Highlights include:

- **Session**: `/new`, `/retry`, `/undo`, `/branch`, `/resume`, `/queue`, `/save`, `/snapshot` (list/create/restore/prune)
- **Config**: `/model`, `/provider`, `/reasoning`, `/verbose`, `/fast`, `/yolo`, `/reload`
- **Gateway/Admin**: `/platforms`, `/sethome`, `/restart`, `/update`, `/debug`
- **Attachments**: `/image`, `/paste`

Some commands are intentionally marked **Partial** because browser behavior differs from terminal behavior. For the current source of truth, use the **Commands** page inside the app.

## 🛠️ Tech Stack
- **React.js 18** (Vite Compiler)
- **TypeScript** natively integrated for safe schema bindings.
- **xterm.js** offering completely native ANSI terminal playback.
- **react-markdown** & **PrismJS** for syntax-focused presentation.

---

*Part of the NousResearch / Hermes Agent Ecosystem.*
