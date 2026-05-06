---
name: computer-use
description: When and how to use the native desktop computer_use_* tool — screenshot first, click by absolute pixel, never reach for it when browser_tool or terminal will do.
metadata:
  hermes:
    tags: [desktop, mouse, keyboard, click, screenshot, gui, automation, computer-use]
---

# Native desktop control (`computer_use_*`)

You have a desktop-control tool that takes screenshots, clicks, types, and sends key combinations on the host machine. The exact tool name depends on the OS the agent is running on:

* `computer_use_macos` — when the host is macOS
* `computer_use_linux` — when the host is Linux (X11 or Wayland)
* `computer_use_windows` — when the host is Windows

Only one of these is registered per session — whichever matches the host. Don't try to call a different one. The tool surface (parameters, action names, return shape) is identical across the three; only the OS-specific shortcuts and idioms differ — see the per-OS skill for that.

## When to use it

Reach for `computer_use_*` only when **simpler tools genuinely can't do the job**:

- `browser_tool` / `browser_camofox` — already covers any web workflow. Don't drive a browser through screenshots when CDP gives you the DOM.
- `terminal` — covers anything a CLI can do. Don't click through a GUI installer when a `brew install` / `apt install` / `winget install` line exists.
- `file_*` tools — for reading/writing files on disk.

`computer_use_*` is the right tool when:

- The target is a **native desktop app** with no usable CLI or web surface (Adobe apps, Office desktop, native installers, system settings panels).
- You need to **interact with a popup, modal, or system dialog** that lives outside any controllable surface.
- The user explicitly asks you to "click", "open this app", "drag", "use the GUI".
- A vision-on-dense-UI workflow benefits from screenshot grounding (proofreading a slide layout, validating a render in a 3D app).

## When NOT to use it

- Web tasks → use `browser_tool` instead. Faster, more reliable, no screenshot ambiguity.
- Anything scriptable via shell → use `terminal`.
- File reads / edits → use `file_*`.
- Anything touching credentials, password fields, MFA codes, or banking UIs unless the user has explicitly asked. Even then: prefer not to. Logs of synthetic clicks near sensitive UI elements are a footgun.

## The screenshot-first discipline

Every desktop-control session starts the same way:

1. **Screenshot first.** Always. You don't know what's on the screen until you look. A click at coordinates you guessed from "well the button is usually in the top-right" lands somewhere wrong about half the time.
2. **Identify the target visually** in the screenshot. Note its approximate pixel position.
3. **Take the action** at those absolute coordinates.
4. **Screenshot again** after any non-trivial action (window opened, dialog appeared, focus changed) to confirm the world looks like you expected.
5. **Adjust or recover** if it doesn't.

This is slow. That's the price. Skipping it is how computer-use agents go off the rails.

## Coordinates

Coordinates are **absolute screen pixels**, origin at top-left. On HiDPI / Retina displays, the tool already runs in DPI-aware mode; the numbers you see in screenshots are the numbers you click. No scaling math.

## Cost discipline

- A screenshot costs an action and adds a base64 PNG (often >100KB) to the next turn's context. Don't take six in a row.
- A `wait` between actions is sometimes necessary (window opens, network roundtrip, animation completes) but each `wait` costs latency too. Use `ms: 200` or `500`, not `5000`.
- If a target app needs many clicks to do something a CLI command would do in one line, **switch to terminal** mid-task. There's no shame.

## Safety

The tool is **opt-in by env var**: `HERMES_COMPUTER_USE_ENABLED=true` must be set on the host. If it isn't, every call returns a refusal — that's by design, not a bug. Tell the user how to enable it; don't try to enable it yourself.

A `redact_regions` parameter on `screenshot` lets you blank rectangles (e.g. password manager popup, MFA code) before the image reaches the model. Use it when you can identify a sensitive zone in advance.

Every action attempt is logged to `$HERMES_HOME/logs/computer_use.jsonl`. If you do something the user doesn't expect, the log is the audit trail.

## Reading the result

Every action returns a JSON dict. Useful fields:

- `success` — bool. Always check this; non-zero `error` means the action didn't happen.
- `screenshot_b64` — base64 PNG (only on `screenshot`).
- `cursor` — `{x, y}` after the action.
- `screen` — `{width, height}` of the primary display.
- `active_window` — `{app, title}` (best-effort; some Wayland compositors return empty).
- `error` — a string if the action failed; surface it to the user, don't silently retry.

## Per-OS skills

For platform-specific shortcuts, screenshot tooling, and gotchas (Cmd-vs-Ctrl, X11-vs-Wayland, UAC dialogs, accessibility permissions), load the skill that matches the host:

- `computer-use-macos`
- `computer-use-linux`
- `computer-use-windows`

You typically load the per-OS skill once at the start of a desktop-control task and follow this common skill for the discipline.
