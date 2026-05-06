---
name: computer-use-macos
description: macOS-specific shortcuts, screenshot tool, and accessibility/screen-recording permission setup for computer_use_macos.
metadata:
  hermes:
    tags: [macos, desktop, mouse, keyboard, accessibility, screen-recording, computer-use]
---

# macOS desktop control — what's different

You have access to `computer_use_macos`. The action set and parameter shape are documented in the parent `computer-use` skill — load that first if you haven't. This skill covers macOS-only things you must know.

## One-time host setup (the user does this, not you)

Before `computer_use_macos` works at all, the operator needs to grant **two** permissions in *System Settings → Privacy & Security*:

1. **Accessibility** — required for synthetic mouse and keyboard events to reach other apps. Without this, `CGEventPost` returns success but nothing actually happens — the events are silently dropped at the WindowServer.
2. **Screen Recording** — required for `screencapture` to include other apps' windows. Without this, screenshots show the desktop background and your own app's windows only — every other window is rendered as wallpaper, which is misleading rather than blank.

Both prompts appear automatically the first time the tool runs. Tell the user this once at the start of a session if it looks like the permissions aren't granted (you can detect this by an action that "succeeds" but the screenshot doesn't reflect the click).

The Hermes process needs both permissions — toggling them on is a per-binary grant, so if the user runs Hermes from a virtualenv vs system Python they'll need to grant it for whichever they're using.

## Modifier keys

macOS uses **Cmd** where Linux/Windows use **Ctrl** for almost every shortcut. The grammar parser accepts both `Cmd+...` (canonical for macOS) and `cmd+...`. Common combinations:

| Action | Combo |
|---|---|
| New / open / save / close window | `Cmd+N` / `Cmd+O` / `Cmd+S` / `Cmd+W` |
| Cut / copy / paste / undo | `Cmd+X` / `Cmd+C` / `Cmd+V` / `Cmd+Z` |
| Find / find-next | `Cmd+F` / `Cmd+G` |
| Quit app | `Cmd+Q` |
| Switch app | `Cmd+Tab` |
| Switch window within app | `Cmd+~` |
| Spotlight | `Cmd+Space` |
| Mission Control | `Ctrl+Up` |
| Force quit | `Cmd+Option+Esc` |
| Full screenshot to clipboard | `Cmd+Shift+Ctrl+3` (rarely needed; use the tool's `screenshot` action) |

Don't use `Ctrl+...` for app shortcuts on macOS unless the app is a Linux/Windows port that documented the Ctrl form (some IDEs do this, e.g. Cursor).

## Spotlight is your friend

To open any app reliably:

1. `key Cmd+Space` — opens Spotlight.
2. `wait 200` — let it focus.
3. `type <app name>` — narrow the result.
4. `wait 100` — let Spotlight resolve.
5. `key Return` — launch the top hit.

This works whether or not the app is in the Dock and is much more reliable than clicking the Dock or hunting in Finder.

## Active-window queries

`get_active_window` returns `{app, title}` on macOS — the frontmost on-screen application name and the window title (when available). Some apps (especially Electron) don't expose a window title; expect empty strings sometimes.

## Screenshot quirks

- `screencapture` captures the full primary display. On a multi-monitor Mac you'll see only the primary; we don't currently expose multi-display capture.
- HiDPI / Retina screens return native-pixel screenshots (e.g. 3024×1964 on a 14" MacBook Pro). The pixel coordinates you click are in this same native space — no scaling.
- The first call shows a system permission prompt; subsequent calls are silent.

## Don't try to do these

- **Mission Control swipes** with `mouse_drag` — three-finger swipe is a trackpad-gesture-only interaction, not synthesisable through CGEvent.
- **Touch ID / Apple Watch unlock** — system-modal prompts that synthetic clicks can't pass through.
- **Quartz screen rotation / display arrangement** — those panes in System Settings have a UIPI-like restriction; ask the user instead.
- **Anything that requires admin privilege escalation** — the standard sudo/Authorization Services prompt won't accept synthetic password input.

## Example — open Safari, navigate to a URL, screenshot

```
{"action": "key", "keys": "Cmd+Space"}
{"action": "wait", "ms": 200}
{"action": "type", "text": "Safari"}
{"action": "wait", "ms": 150}
{"action": "key", "keys": "Return"}
{"action": "wait", "ms": 800}
{"action": "key", "keys": "Cmd+L"}
{"action": "type", "text": "https://example.com"}
{"action": "key", "keys": "Return"}
{"action": "wait", "ms": 1500}
{"action": "screenshot"}
```

(For web tasks `browser_tool` is faster and more reliable — this is just an illustration of the macOS idiom.)
