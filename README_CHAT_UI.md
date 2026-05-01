# Hermes Agent Chat UI

Web-based chat interface for Hermes Agent.

## Features

- Modern chat interface with real-time message streaming
- Session persistence and recovery
- Workspace integration (file operations, path autocomplete)
- Multi-provider support (Anthropic, OpenAI, Google, OpenRouter, MiniMax, etc.)
- Tool execution progress display

## Quick Start

### 1. Configure API Key

Edit your `.env` file (or `~/.hermes/.env`):

```bash
# Choose one provider:
OPENROUTER_API_KEY=sk-or-...
# or
GOOGLE_API_KEY=AIza...
# or
ANTHROPIC_API_KEY=sk-ant-...
```

### 2. Start the Server

```bash
.venv/bin/hermes web
```

### 3. Open Browser

Visit **http://localhost:8000** (or the configured port).

## Architecture

### Key Components

| File | Purpose |
|------|---------|
| `hermes_cli/web_server.py` | FastAPI server, session management, API endpoints |
| `hermes_cli/web_chat_api/chat_stream.py` | Chat stream logic, model/provider resolution |
| `hermes_cli/web_chat_api/config.py` | Model and provider configuration |
| `hermes_cli/web_chat_dist/messages.js` | Frontend SSE handling, UI logic |

### Session Flow

```
/api/chat/start
  -> Create in-memory session
  -> Pre-create database session (source='webui')
  -> Start background chat stream
  -> Return stream_id to frontend
  -> Frontend polls /api/session -> 200 OK (session exists)
```

### Recent Fixes

- **Race condition fix**: Session is now pre-created in the database before the frontend polls for it, eliminating 404 errors on `/api/session`
- **API response format**: `/api/session` now returns `{session: {...}}` matching frontend expectations
- **Model resolution**: Web chat now uses the same runtime provider resolution as CLI mode
- **SSE error handling**: Enhanced frontend logging and reconnection logic

## Debugging

### Check Server Logs

```bash
tail -f ~/.hermes/logs/agent.log
```

### Check Database

```bash
sqlite3 ~/.hermes/state.db "SELECT id, source, model FROM sessions WHERE source='webui' ORDER BY started_at DESC LIMIT 5;"
```

### Browser Console

Open F12 developer tools and check the Console tab for `[SSE]` and `[DEBUG]` prefixed logs.

## Troubleshooting

| Problem | Solution |
|---------|----------|
| "Connection lost" error | Check server logs, ensure model config is correct |
| 404 on `/api/session` | Server should auto-create session; check `agent.log` for errors |
| Model not available | Run `hermes model` to see available models |
| Reset configuration | Delete `~/.hermes/config.yaml` and run `hermes setup` |
