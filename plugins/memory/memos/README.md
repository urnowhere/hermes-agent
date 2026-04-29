# MemOS Memory Provider

Server-side memory extraction and semantic search via MemOS Platform.

## Requirements

- `pip install MemoryOS`
- MemOS API key from [MemOS Dashboard](https://memos-dashboard.openmem.net)

## Setup

```bash
hermes memory setup    # select "memos"
```

Or manually:
```bash
hermes config set memory.provider memos
echo "MEMOS_API_KEY=your-key" >> ~/.hermes/.env
```

## Config

Config file: `$HERMES_HOME/memos.json`

| Key | Default | Description |
|-----|---------|-------------|
| `api_key` | `""` | MemOS API key |
| `user_id` | `hermes_user` | User identifier on MemOS |
| `knowledgebase` | `None` | (Optional) Knowledgebase ID or list of IDs for searching |
| `allowedAgents` | `None` | (Optional) List of agent IDs allowed to use memory |
| `multiAgentMode` | `False` | (Optional) Enable multi-agent memory isolation |

## Tools
| Tool | Description |
|------|-------------|
| `memos_search` | Search user's memories using MemOS Platform |
| `memos_add_message` | Explicitly store a fact or message into MemOS memory |
