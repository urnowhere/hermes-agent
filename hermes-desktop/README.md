# Hermes Desktop Computer Use

This directory provides an optional persistent desktop VM for Hermes Agent. It gives Hermes a full graphical "computer body" with:

- Xfce desktop
- Google Chrome with a persistent profile
- VNC and noVNC for live user takeover
- SSH access for Hermes terminal/computer-use commands
- `xdotool` for mouse and keyboard control
- `scrot` and ImageMagick for screenshots

The feature is disabled by default. It only appears when `COMPUTER_USE_ENABLED=true`.

## Why Use This

Hermes already has browser and terminal tools. The desktop VM adds full screen-level workflows for tasks that need a real GUI:

- websites that require a logged-in browser session
- visual workflows that are difficult to express through DOM-only browser automation
- desktop applications and file managers
- live user takeover for login, MFA, CAPTCHA, or sensitive actions
- persistent browser cookies and application state across restarts

The user can watch the desktop at any time through noVNC and take over manually.

## Quick Start

```bash
cd hermes-desktop
./setup.sh
```

The setup script is idempotent. It checks Docker, creates `.env` with generated passwords if needed, builds the image, starts the desktop, and runs health checks.

Open the live desktop:

```text
http://localhost:6080/vnc.html
```

SSH into the desktop:

```bash
ssh hermes@localhost -p 2222
```

Run health checks any time:

```bash
./verify.sh
```

## Enable The Tool

Set these variables for the Hermes process that should use the desktop, for example in `~/.hermes/.env`:

```bash
COMPUTER_USE_ENABLED=true
COMPUTER_USE_BACKEND=ssh
COMPUTER_USE_DISPLAY=:1
COMPUTER_USE_VNC_URL=http://localhost:6080/vnc.html
TERMINAL_SSH_HOST=localhost
TERMINAL_SSH_PORT=2222
TERMINAL_SSH_USER=hermes
```

Then restart Hermes so tool discovery picks up `computer_use`.

When `COMPUTER_USE_ENABLED` is not set to `true`, `1`, or `yes`, the tool check fails closed and default Hermes behavior is unchanged.

## Basic Workflow

1. Take a screenshot with `computer_use(action="screenshot")`.
2. Use the returned visual analysis to identify the next action.
3. Click, type, press keys, scroll, move, or drag with `computer_use`.
4. Take another screenshot to verify the result.
5. Ask the user to open the noVNC URL when login, MFA, CAPTCHA, payment, or other sensitive manual takeover is required.

## Manual Login Via noVNC

Some sites require human login, MFA, CAPTCHA, or device verification. Use noVNC for those steps:

1. Open `http://localhost:6080/vnc.html`.
2. Enter the `VNC_PASSWORD` from `.env`.
3. Open Chrome in the desktop.
4. Log into the site manually.
5. Close only the tab or leave Chrome open. The browser profile is stored in the persistent Docker volume, so cookies and sessions survive container restarts.

## Security Considerations

- Change `VNC_PASSWORD` and `HERMES_PASSWORD` before exposing ports.
- Prefer SSH key auth through `AUTHORIZED_KEYS` instead of password auth for remote deployments.
- Do not expose VNC/noVNC/SSH directly to the public internet without firewalling, VPN, or a trusted reverse proxy.
- The desktop browser profile is persistent. Treat it like a real user browser profile because it may contain cookies and logged-in sessions.
- The tool can click, type, and operate the OS-level desktop. Keep it disabled unless you intentionally want this capability.
- Public or irreversible actions should still require user confirmation at the agent/policy layer.

## Troubleshooting

### Docker is not installed or not running

`./setup.sh` checks both Docker and Docker Compose. If it fails here:

```bash
docker --version
docker compose version
docker info
```

Install Docker from the official Docker docs or start the Docker service/daemon, then rerun `./setup.sh`.

### Port conflicts

Defaults are SSH `2222`, VNC `5901`, and noVNC `6080`. If Docker cannot bind a port, edit `.env`:

```bash
TERMINAL_SSH_PORT=2223
VNC_PORT=5902
NOVNC_PORT=6081
```

Then restart:

```bash
docker compose up -d
./verify.sh
```

### Permission errors

If the container starts but Chrome or the profile fails to write, check the persistent volume:

```bash
docker logs hermes-desktop --tail 100
docker exec hermes-desktop bash -lc 'id hermes && ls -ld /home/hermes /home/hermes/.config'
```

If permissions are corrupted, recreate the desktop volume only if you are willing to lose browser state.

### Browser not starting

Use the helper inside the desktop container:

```bash
ssh -p 2222 hermes@localhost '/home/hermes/bin/open-chrome https://example.com'
```

Then open noVNC and verify Chrome appears. If it does not, inspect logs:

```bash
docker logs hermes-desktop --tail 100
```

### Tool not registered

The tool is intentionally hidden unless enabled. Confirm the Hermes process sees:

```bash
COMPUTER_USE_ENABLED=true
```

Then restart Hermes and check:

```bash
hermes tools | grep computer_use
```

If it still does not appear, verify `tools/computer_use_tool.py` exists in the Hermes install and run `./verify.sh`.

### Profile not persisting

The browser profile lives under `/home/hermes` in the `desktop_home` Docker volume. Confirm the volume exists:

```bash
docker volume ls | grep desktop_home
```

Do not run `docker compose down -v` unless you intentionally want to delete browser state.

### noVNC opens but login sites fail

This is expected for MFA, CAPTCHA, device checks, or bot detection. Use noVNC to log in manually once. After login, the persistent browser profile should keep cookies/session state for future Hermes runs.

## Limitations

- Coordinate accuracy depends on screenshot quality and the selected vision model.
- Some websites block automation or require human login/MFA.
- GUI state can drift. Agents should verify after each action and stop after repeated failures instead of looping.
- Large screenshots can be expensive to analyze. The tool sends images to the vision model internally and does not return raw base64 image data by default.
