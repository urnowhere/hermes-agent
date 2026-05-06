---
sidebar_position: 3
title: "Run Hermes on boxd"
description: "Two ways to combine Hermes with boxd: run the whole agent inside a boxd VM, or run Hermes locally and use boxd as a sandboxed terminal backend"
---

# Run Hermes on boxd

There are two ways to combine Hermes Agent with [boxd](https://boxd.sh) cloud microVMs. They solve different problems and you can use either, both, or neither.

| Mode | What runs where | Use it when |
|------|-----------------|-------------|
| **A. Hermes inside a boxd VM** | The whole agent — CLI, gateway, cron — lives inside a boxd VM. Your laptop is just an SSH client. | You want a persistent always-on assistant reachable from Telegram / Discord / Slack, without leaving your laptop running. |
| **B. boxd as a terminal backend** | Hermes runs locally (or anywhere). Every shell command the agent issues executes inside a per-task boxd VM. | You want a real Linux sandbox for tool execution, with sub-ms suspend/resume between turns and full isolation from your host. |

Most people start with **A** for the always-on use case, then add **B** when they want safer execution. The two modes compose — you can run Hermes inside one boxd VM and have *that* Hermes use a separate boxd VM as its terminal sandbox.

---

## Why boxd

Whichever mode you pick, the boxd properties that matter for Hermes are the same:

- **Per-VM public IPv4** with a real subdomain on `*.boxd.sh`. Webhook-driven platforms (Discord, Slack, WhatsApp, Mattermost) just work — no ngrok, no certbot.
- **Sub-millisecond suspend/resume.** boxd warm-suspends VMs on idle and resumes them on the next request. You pay near-zero when nobody's talking to the agent.
- **SSH-key auth.** No passwords. boxd reads your GitHub keys at signup.
- **Persistent disk.** Unlike pure serverless backends, the VM keeps `~/.hermes/` (or `/root/.hermes/` in backend mode) between sessions — sessions, memory, skills, and config survive across restarts.
- **Fixed shape (2 vCPU, 8 GiB RAM, 100 GB disk).** Comfortably above what Hermes needs; LLM calls happen remotely anyway.

---

## Prerequisites (both modes)

- **A boxd account.** Sign up at [boxd.sh](https://boxd.sh) — uses your existing GitHub SSH key.
- **An LLM provider key** — at minimum OpenRouter, OpenAI, Anthropic, or Nous Portal. The examples below use OpenRouter.
- **Local SSH key** registered with GitHub (boxd reads your GitHub keys at signup).
- **The boxd CLI** on your laptop:

  ```bash
  curl -fsSL https://boxd.sh/downloads/install.sh | sh
  boxd login
  boxd whoami       # verify
  ```

---

## Mode A — Run Hermes inside a boxd VM

The whole agent lives in the cloud. Your laptop is just a shell into it.

### A1. Create a VM

```bash
boxd new --name hermes
```

This creates a fresh Linux VM, prints the SSH command, and gives it a public IP plus a default `<vm-name>.<user>.boxd.sh` HTTPS subdomain.

By default the VM auto-suspends after 5 minutes of network idle. If you're going to run the messaging gateway and need to receive incoming pushes 24/7, disable auto-suspend up front:

```bash
boxd auto-suspend hermes 0
```

You can re-enable it later with `boxd auto-suspend hermes 300` (5 minutes), or any value `>= 5` seconds.

:::tip
Keep auto-suspend on for CLI / SSH-only use — every connection wakes the VM in under a millisecond. Only disable it when something needs to push to the VM unsolicited.
:::

### A2. Install Hermes inside the VM

```bash
boxd connect hermes
```

Inside the VM:

```bash
curl -fsSL https://raw.githubusercontent.com/NousResearch/hermes-agent/main/scripts/install.sh | bash
source ~/.bashrc
```

The installer handles Python, uv, Node, ripgrep, ffmpeg, and the venv. See the [installation guide](../getting-started/installation.md) for what it does under the hood.

### A3. Configure your provider key

Still inside the VM:

```bash
hermes config set OPENROUTER_API_KEY sk-or-v1-...
hermes model
```

Then verify:

```bash
hermes doctor
hermes -q "Hello from boxd"
```

That's the minimum viable install. You can stop here if you only want to SSH in and chat.

### A4. (Optional) Add a messaging gateway

This is where boxd's HTTPS subdomain pays off. Pick a platform and follow its setup, then start the gateway:

```bash
hermes gateway setup     # interactive — Telegram, Discord, Slack, WhatsApp, ...
hermes gateway start
```

For [Telegram](../user-guide/messaging/telegram.md) you don't need anything else — it long-polls. For platforms that push webhooks, expose a port through boxd's proxy from your laptop:

```bash
boxd proxy new hermes-webhook --vm hermes --port 8080
```

You'll get back something like `https://hermes-webhook.<you>.boxd.sh` — give that URL to Discord / Slack / Mattermost as your webhook target. TLS is terminated for you; no certbot, no reverse proxy.

For the full team-bot walkthrough (per-user authorization, scheduled messages, etc.), see the [Team Telegram Assistant tutorial](./team-telegram-assistant.md).

### A5. (Optional) Run scheduled tasks

Hermes' [cron scheduler](../user-guide/features/cron.md) is the killer combo with boxd's auto-suspend: the VM wakes when cron fires, runs the job, delivers the result to your messaging platform, and goes back to sleep.

Inside the VM:

```bash
hermes cron create "every 1d at 08:00" \
  "Summarize today's calendar and unread emails, send to Telegram" \
  --name "morning-briefing"
```

If you set auto-suspend to a small value, the in-VM cron tick keeps the VM awake long enough to run scheduled jobs.

---

## Mode B — Use boxd as a terminal backend

Hermes runs locally (or in another VM). Every shell command the agent issues — `terminal_tool`, `execute_code`, file operations through the sandbox — runs inside a boxd VM, automatically created and managed by Hermes.

This is the same shape as the [Modal](../user-guide/configuration.md#modal-backend) and [Daytona](../user-guide/configuration.md#daytona-backend) terminal backends, but with boxd's persistence model: the VM warm-suspends on cleanup and resumes in sub-ms on the next session, preserving filesystem **and** running processes.

### B1. Install the boxd Python SDK

The terminal backend lives in the optional `[boxd]` extra:

```bash
pip install 'hermes-agent[boxd]'
```

Or just the SDK if you already have Hermes installed:

```bash
pip install boxd
```

### B2. Create an API key

The SDK authenticates with `BOXD_API_KEY` (long-lived) instead of a browser login. From your laptop:

```bash
boxd keys create hermes-backend
# Save the printed bxk_... value — it's only shown once.
```

Then drop it in `~/.hermes/.env`:

```bash
BOXD_API_KEY=bxk_...
```

### B3. Switch the terminal backend

Either via `hermes setup` (pick **boxd** in the terminal-backend menu — Hermes will offer to install the SDK and prompt for the key) or directly:

```bash
hermes config set terminal.backend boxd
```

That's it. The next command Hermes runs creates a VM named `hermes-default` (or `hermes-<task_id>` for delegated subagents), runs your command inside it, and on session cleanup either suspends or destroys it depending on `terminal.container_persistent`.

Verify:

```bash
hermes -q "run uname -a in your terminal and tell me the kernel"
```

The agent's response should reference an Ubuntu kernel running inside the boxd VM, not your local OS.

### B4. (Optional) Knobs

```yaml
# ~/.hermes/config.yaml
terminal:
  backend: boxd
  boxd_image: ""             # "" = server default (ubuntu:latest)
  container_cpu: 2
  container_memory: 8192     # MB → "8G"
  container_disk: 102400     # MB → "100G"
  container_persistent: true # Suspend on cleanup vs destroy
```

Full reference: [Terminal Backend Configuration → boxd Backend](../user-guide/configuration.md#boxd-backend).

---

## Combining A + B

Nothing stops you from running Mode A and Mode B together: install Hermes inside one boxd VM (the always-on agent VM), then configure *that* Hermes to use boxd as its terminal backend. The agent VM gets a separate, ephemeral backend VM per task, which means dangerous commands can't trash your skills / memory / sessions on the agent VM.

```bash
# Inside the agent VM (Mode A)
pip install 'hermes-agent[boxd]'
hermes config set terminal.backend boxd
echo "BOXD_API_KEY=bxk_..." >> ~/.hermes/.env
```

---

## Updating Hermes

```bash
boxd exec hermes -- hermes update      # Mode A: in the agent VM
pip install --upgrade boxd             # Mode B: backend SDK on the host
```

---

## Stopping and resuming

```bash
boxd pause hermes        # warm suspend, sub-ms resume
boxd resume hermes       # wake it back up
boxd destroy hermes      # gone forever (name reserved for re-creation)
```

In Mode B, Hermes manages the backend VMs for you — no manual lifecycle calls needed. Set `container_persistent: false` if you want every session to start with a fresh VM.

---

## Troubleshooting

| Problem | Fix |
|---------|-----|
| `boxd connect` hangs | If the VM is paused, any TCP connect wakes it but takes a moment. Try again. |
| Telegram bot stops responding overnight (Mode A) | Auto-suspend paused the outbound long-poll. Run `boxd auto-suspend hermes 0`. |
| Webhook returns 502 (Mode A) | Confirm the gateway is listening on the forwarded port: `boxd exec hermes -- ss -ltnp \| grep 8080`. |
| `hermes: command not found` after install (Mode A) | Reload the shell (`source ~/.bashrc`) or open a fresh `boxd connect`. |
| Mode B: `ModuleNotFoundError: No module named 'boxd'` | `pip install 'hermes-agent[boxd]'` (or `pip install boxd`). |
| Mode B: `BOXD_API_KEY not set` | `hermes config set BOXD_API_KEY bxk_...` or add it to `~/.hermes/.env`. |
| Mode B: first command is slow | Cold-start of a new VM. Subsequent commands hit the suspended VM and resume sub-ms. Set `container_persistent: true`. |

For deeper diagnostics, `hermes doctor` and `boxd info <vm-name>`.

---

## Where to go next

- **[Configuration reference → boxd Backend](../user-guide/configuration.md#boxd-backend)** — every knob for Mode B
- **[Messaging Gateway overview](../user-guide/messaging/index.md)** — wire up platforms in Mode A
- **[Cron scheduling](../user-guide/features/cron.md)** — daily reports, nightly backups, weekly audits
- **[Skills](../user-guide/features/skills.md)** — extend Hermes with procedural memory
