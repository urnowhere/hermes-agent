# Multi-Workspace Slack Socket Mode

Hermes supports two Slack credential modes:

- `SLACK_BOT_TOKEN` plus `SLACK_APP_TOKEN`: backward-compatible single
  Socket Mode connection. Comma-separated bot tokens and `slack_tokens.json`
  remain send-capable legacy paths.
- `~/.hermes/slack_accounts.json`: true multi-workspace receive support.
  Each account entry has its own bot token and app token, so Hermes opens one
  independent Socket Mode connection per Slack workspace.

Example:

```json
[
  {
    "name": "engineering",
    "bot_token": "xoxb-...",
    "app_token": "xapp-..."
  },
  {
    "name": "partner",
    "bot_token": "xoxb-...",
    "app_token": "xapp-..."
  }
]
```

Each app token is protected by its own gateway lock. This matters because Slack
Socket Mode distributes events across competing connections for the same app
token, so two gateway processes using one token can both miss events.

Inbound events record the owning `team_id` for each channel. Hermes persists
that channel-to-team map in `~/.hermes/slack_channel_teams.json`, allowing
outbound messages after restart to use the correct workspace client.
