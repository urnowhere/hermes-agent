---
sidebar_position: 12
title: "Cross-Platform Paths"
description: "HERMES_HOME, profile awareness, and the path conventions plugin and skill authors must follow on Windows (WSL2), macOS, and Linux."
---

# Cross-Platform Paths

This page is for **plugin, skill, and tool authors** who need to read or write files inside the Hermes data root, spawn subprocesses, or branch on platform. It documents the conventions enforced inside `agent/` and `hermes_cli/` so that contributed code works identically on Linux, macOS, and WSL2 — and so it doesn't silently corrupt user data when profiles or Docker are in play.

The single source of truth for everything below is `hermes_constants.py`. Import from there. Do not re-derive paths or platform flags by hand.

## Use `get_hermes_home()` — never `expanduser("~/.hermes")`

`hermes_constants.get_hermes_home()` is the canonical helper for the Hermes data root. It reads the `HERMES_HOME` env var first and falls back to `~/.hermes` only when the var is unset or blank. Hardcoding `~/.hermes` (or `Path.home() / ".hermes"`) bypasses Docker deployments, profile mode, and any custom relocation a user has configured.

```python
# Wrong — ignores HERMES_HOME, breaks Docker (HERMES_HOME=/opt/data)
# and profile mode (HERMES_HOME=~/.hermes/profiles/<name>).
import os
state_path = os.path.join(os.path.expanduser("~"), ".hermes", "my_plugin", "state.json")
```

```python
# Right — single source of truth.
from hermes_constants import get_hermes_home
state_path = get_hermes_home() / "my_plugin" / "state.json"
```

`get_hermes_home()` returns a `pathlib.Path`. Use `pathlib` operations downstream — they're cross-platform out of the box (forward slashes, drive letters, UNC paths). Mixed `os.path.join` and `pathlib.Path` works but reads worse and is easy to break on Windows.

:::warning Same bug, two issues
The historical bug class is documented in [#18594](https://github.com/NousResearch/hermes-agent/issues/18594) (cross-profile data corruption) and surfaced again recently in `agent/nous_rate_guard.py::_state_path`, where the `ImportError` fallback hardcoded `~/.hermes` and silently misrouted rate-limit state under Docker. Auditing every new path-construction site against `get_hermes_home()` is the cheapest way to keep this from coming back.
:::

## Profile-aware code paths

When a non-default profile is active, `HERMES_HOME` is set to `<root>/profiles/<name>`. Any code that reaches for `Path.home() / ".hermes"` will write into the **default** profile while the rest of the process reads from the **active** profile. The user sees half-applied state and no error.

`get_hermes_home()` includes a one-shot stderr warning when it detects this divergence (an `active_profile` file naming a non-default profile while `HERMES_HOME` is unset). The warning is intentionally loud — if you see it during plugin development, the spawning code is the bug, not the helper.

For a primer on how profiles are laid out, see [Profiles: Running Multiple Agents](/user-guide/profiles).

## Subprocess spawning: propagate `HERMES_HOME`

`get_hermes_home()` is import-safe and reads `os.environ` on every call. That means a child process inherits the correct root **only if the parent passes `HERMES_HOME` through**. Spawners that build their own `env=` dict from scratch must include it explicitly.

Two canonical spawners in the codebase already do this — copy their pattern:

- `hermes_cli/gateway.py` — pulls the resolved root via `get_hermes_home().resolve()` and writes it into the systemd unit / launcher template before exec.
- `hermes_cli/kanban_db.py` — same treatment for the kanban dispatcher; the docstring there explains why per-profile forking would silently fragment the board across profiles.

If your skill or tool spawns a long-lived child (gateway, daemon, watcher), do the same. If it shells out for a short command, prefer inheriting the parent environment unmodified rather than building `env=` from scratch.

## Platform detection helpers

Three import-safe helpers live in `hermes_constants.py`. Use them; do not re-derive.

| Helper | Returns `True` when | Notes |
|---|---|---|
| `is_wsl()` | `/proc/version` contains `microsoft` (WSL1 or WSL2) | Cached for process lifetime. |
| `is_termux()` | `TERMUX_VERSION` is set, or `PREFIX` contains `com.termux/files/usr` | Android via Termux. |
| `is_container()` | `/.dockerenv`, `/run/.containerenv`, or container markers in `/proc/1/cgroup` | Docker, Podman, LXC. Cached. |

Real call sites to learn from:

- `agent/prompt_builder.py` — branches the system prompt on `is_wsl()` so the agent's own self-description matches its runtime.
- `hermes_cli/clipboard.py` — imports `from hermes_constants import is_wsl as _is_wsl` and routes clipboard reads/writes through `clip.exe` / `powershell.exe` when running under WSL.
- `hermes_cli/doctor.py`, `setup.py`, `status.py`, `uninstall.py` — adapt diagnostic and lifecycle output for WSL.

```python
from hermes_constants import is_wsl, is_container

def best_clipboard_backend() -> str:
    if is_wsl():
        return "clip.exe"        # Windows host's clipboard, reachable from WSL.
    if is_container():
        return "noop"             # No graphical clipboard available.
    return "xclip"
```

## Windows native (non-WSL2) caveats

Hermes Agent's supported Windows path is **WSL2** ([Windows (WSL2) Quick Start](/user-guide/windows-wsl-quickstart)). A subset of the codebase still has to behave on native Windows shells — anything spawned from a sidecar process, parsed by an external service, or invoked from a Windows-native installer.

The two anchors for native-Windows work are:

- `hermes_cli/config.py` defines `_IS_WINDOWS = platform.system() == "Windows"` once and branches `load_env()` and the `.env` writers on it. They open the file with `encoding="utf-8"` (and `errors="replace"` on read) on Windows so a stray non-UTF-8 byte in a hand-edited `.env` won't crash `cp1252` decoding. Mirror this pattern when your code reads or writes a text file the user is allowed to hand-edit.
- `hermes_cli/clipboard.py` shells out to `clip.exe` and `powershell.exe` under WSL but the same routing applies on native Windows; consult the helpers there before reinventing clipboard or `pbcopy`-style fallbacks.

A few rules that keep cross-platform code honest:

- Use `pathlib.Path`. `os.path.join` works but mixes badly with hardcoded forward slashes.
- Quote shell arguments with `shlex.quote()` (POSIX) — never string-concatenate user input into a `subprocess` shell command. On Windows `cmd.exe`, prefer `subprocess.run([...])` with a list and skip the shell entirely.
- Don't assume `/tmp`. Use `tempfile.mkdtemp()` / `tempfile.NamedTemporaryFile()`; on Windows they resolve to `%TEMP%`.

## Don't bypass `agent/file_safety.py::is_write_denied()`

If your tool or skill writes to a path the user supplies, route it through `agent.file_safety.is_write_denied(path)` before opening the file. The deny list in `build_write_denied_paths()` and `build_write_denied_prefixes()` blocks SSH keys, shell config, GnuPG, AWS credentials, and similar high-blast-radius targets. `is_write_denied` resolves symlinks via `os.path.realpath` before checking, so attacks via symlink redirection are caught.

The deny list is **platform-mixed by design**: it includes Linux paths like `/etc/sudoers` and `/etc/passwd` even though they're meaningless on Windows. That's intentional — the cost of a no-op check on Windows is zero, and listing them keeps a single source of truth that survives moves between platforms (notably WSL2 → Linux container).

If you find yourself wanting to skip the check, file an issue first. The check is one of the few things between an agent error and unrecoverable user data loss.
