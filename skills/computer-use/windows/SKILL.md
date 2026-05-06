---
name: computer-use-windows
description: Windows shortcuts, UAC handling, mss screenshot setup, and DPI-awareness gotchas for computer_use_windows.
metadata:
  hermes:
    tags: [windows, win32, sendinput, mss, uac, dpi, computer-use]
---

# Windows desktop control — what's different

You have access to `computer_use_windows`. The action set and parameter shape are documented in the parent `computer-use` skill — load that first if you haven't. This skill covers Windows-only things you must know.

## One-time host setup

`computer_use_windows` works out of the box on a default Python install — `ctypes` is in the stdlib, and the screenshot path falls back to a built-in BitBlt routine when `mss` isn't installed.

For best performance install `mss`:

```
pip install mss
```

`mss` is MIT-licensed, pure-Python over ctypes, and ~5-10× faster than the BitBlt fallback. Worth having if the agent will be running screenshots in a loop.

## DPI awareness

Modern Windows displays are usually scaled (125 %, 150 %, 200 %). The tool sets per-monitor v2 DPI awareness on import, so:

- Click coordinates are in **physical screen pixels**, not scaled coordinates.
- Screenshots return the same physical pixel resolution.
- The numbers the screenshot shows are the numbers you click. No math.

If you see clicks landing in the wrong place — typically off by exactly the DPI scale factor — the import-time DPI call failed (older Windows 7/8 hosts). On those hosts the fallback is to ask the user to reduce display scaling to 100 % for the session.

## Modifier keys

Windows uses **Ctrl** for the same shortcuts macOS uses **Cmd** for. The grammar parser accepts `Ctrl+T`, `ctrl+t`, `control+t` — all equivalent. The macOS `Cmd` token is reinterpreted as the **Win** (LWin) key on Windows, which is what the user wants in practice when porting muscle-memory.

| Action | Combo |
|---|---|
| New tab / new window / save / close | `Ctrl+T` / `Ctrl+N` / `Ctrl+S` / `Ctrl+W` |
| Cut / copy / paste / undo | `Ctrl+X` / `Ctrl+C` / `Ctrl+V` / `Ctrl+Z` |
| Find | `Ctrl+F` |
| Switch window | `Alt+Tab` |
| Switch app (Win10/11) | `Win+Tab` |
| Start menu / search | `Win+S` (search), or just `Win` then type |
| Run dialog | `Win+R` |
| Lock workstation | `Win+L` |
| File Explorer | `Win+E` |
| Show desktop | `Win+D` |
| Window snap | `Win+Left` / `Win+Right` / `Win+Up` |
| Force-close window | `Alt+F4` |

`Win+S` followed by typing is the analogue of macOS's `Cmd+Space` Spotlight pattern.

## UAC (User Account Control) — the big one

Windows enforces **UIPI** (User Interface Privilege Isolation): synthetic input from a **non-elevated** process cannot reach an **elevated** window. Specifically:

- If a UAC prompt appears, your `key` and `click` actions hit dead air — the prompt window runs at higher integrity than the agent.
- If the user runs an admin-only program (Task Manager, Registry Editor, services.msc), same problem: the agent's clicks are silently dropped.

Two ways to handle this:

1. **Run Hermes elevated.** Right-click the Hermes launcher / terminal → "Run as administrator". The whole session then has integrity high, and all clicks land. This is the simplest fix when you know you'll need elevated control.
2. **Avoid elevated UI.** For most tasks (typing in apps, clicking buttons in regular programs, browser work) integrity medium is enough. UAC prompts that pop up unexpectedly should be deferred to the user.

You can detect the UIPI failure case: a click "succeeds" (no error) but a follow-up screenshot shows nothing happened. If you see this pattern on a UAC dialog, surface to the user — don't keep retrying.

## Active-window queries

`get_active_window` returns `{id, title}` from `GetForegroundWindow` + `GetWindowTextW`. Reliable. The `id` is the HWND (window handle) as a decimal string, which can be passed to other Win32 calls if needed.

## Screenshot quirks

- Captures the **primary monitor only** by default. Multi-monitor capture is a known gap; we'd need to extend the tool to pass `monitor=N`.
- Includes the cursor by default (BitBlt path) — there's no easy way to hide it.
- Minimised windows are **not** captured (they have no client area to BitBlt). To screenshot a minimised window, restore it first with a click on the taskbar.

## Don't try to do these

- **UAC consent dialogs** — see UIPI above.
- **Lock screen / login screen** — different desktop session, no synthetic input access.
- **Game DirectInput** — the SendInput path injects to the standard message queue; many games (DirectX exclusive mode) read DirectInput directly and don't see synthetic events. `pydirectinput` is a Windows-game-specific library that handles this; we don't currently bundle it.
- **Driver / kernel-level UI** — same UIPI restriction as UAC.

## Example — Win+R, run notepad, type, screenshot

```
{"action": "key", "keys": "Win+R"}
{"action": "wait", "ms": 300}
{"action": "type", "text": "notepad"}
{"action": "key", "keys": "Return"}
{"action": "wait", "ms": 800}
{"action": "type", "text": "hello from hermes"}
{"action": "screenshot"}
```

For most workflows just use `terminal` (PowerShell / cmd.exe) — it's faster than driving Notepad through the GUI.
