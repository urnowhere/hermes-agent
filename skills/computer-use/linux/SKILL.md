---
name: computer-use-linux
description: Linux X11 vs Wayland gotchas, xdotool/ydotool/grim setup, and DE-specific shortcuts for computer_use_linux.
metadata:
  hermes:
    tags: [linux, x11, wayland, xdotool, ydotool, grim, gnome, kde, computer-use]
---

# Linux desktop control — what's different

You have access to `computer_use_linux`. Linux is the awkward one because it's two display servers in a trench coat. Same agent-facing API, two completely different toolchains under the hood.

## Detecting the active session

The tool detects the display server automatically each call from `WAYLAND_DISPLAY` and `XDG_SESSION_TYPE`. You don't choose. You can confirm what the host is using with a short terminal call:

```
echo "$XDG_SESSION_TYPE"  # x11 or wayland
```

If the user can choose, **X11 is more capable** for scripted automation right now. Wayland's app-isolation model deliberately makes synthetic input and full-screen capture harder. If you have the choice and the workflow is automation-heavy, suggest the user log in to an X11 session.

## One-time host setup

### X11 path (preferred when available)

The host needs `xdotool` and a screenshot tool — usually `scrot`, sometimes `imagemagick` (which provides `import`). On Debian/Ubuntu/Mint:

```
sudo apt install xdotool scrot xdpyinfo xprop
```

On Arch/Manjaro:

```
sudo pacman -S xdotool scrot xorg-xdpyinfo xorg-xprop
```

After install everything works without further setup; xdotool synthesises events through XTEST.

### Wayland path (when X11 isn't available)

Wayland needs `ydotool` (input via `/dev/uinput`) and one of `grim` (wlroots compositors: Sway, Hyprland, labwc, river), `gnome-screenshot` (GNOME), or `spectacle` (KDE).

```
# Sway / Hyprland / labwc
sudo apt install ydotool grim
sudo usermod -aG input "$USER"  # for /dev/uinput access
sudo systemctl enable --now ydotoold

# GNOME on Wayland
sudo apt install ydotool gnome-screenshot
sudo systemctl enable --now ydotoold

# KDE on Wayland
sudo apt install ydotool kde-spectacle
sudo systemctl enable --now ydotoold
```

The `usermod` change requires re-login to take effect. If the operator hasn't done this, every input action will fail with a permission error on `/dev/uinput`.

## Modifier keys

Linux uses **Ctrl** for the same shortcuts macOS uses **Cmd** for. The grammar parser accepts `Ctrl+T`, `ctrl+t`, `control+t` — all equivalent. The macOS `Cmd` token is reinterpreted as the **Super** (Windows) key on Linux, which is what the user wants in practice when porting muscle-memory.

| Action | Combo |
|---|---|
| New tab / new window / save / close | `Ctrl+T` / `Ctrl+N` / `Ctrl+S` / `Ctrl+W` |
| Cut / copy / paste / undo | `Ctrl+X` / `Ctrl+C` / `Ctrl+V` / `Ctrl+Z` |
| Find | `Ctrl+F` |
| Switch window (most DEs) | `Alt+Tab` |
| Open terminal in many DEs | `Ctrl+Alt+T` |
| Lock screen (GNOME / KDE) | `Super+L` / `Ctrl+Alt+L` |
| Activities / overview (GNOME) | `Super` |
| App launcher (KDE) | `Alt+F1` |

In a terminal **Ctrl+C is interrupt**, not copy. Use `Ctrl+Shift+C` / `Ctrl+Shift+V` for clipboard inside terminal emulators.

## Active-window queries

- **X11**: `get_active_window` returns `{id, title, app}` derived from `xdotool getactivewindow` and `xprop WM_CLASS`. Reliable.
- **Wayland (Sway)**: returns the `app_id` and window name from the i3 IPC tree. Reliable.
- **Wayland (Hyprland)**: returns from `hyprctl -j activewindow`. Reliable.
- **Wayland (GNOME)**: there is no public IPC for this. Returns empty `{}`. Don't depend on it.
- **Wayland (KDE)**: best-effort via KWin scripting; often empty.

## Screenshot quirks

- **X11**: `scrot` returns the full root window — works on multi-monitor setups, captures everything.
- **Wayland (wlroots / `grim`)**: full virtual desktop including all outputs.
- **Wayland (GNOME / `gnome-screenshot`)**: full screen of the focused monitor; multi-monitor capture is a known gap.
- **Wayland (KDE / `spectacle`)**: full screen of all outputs.

`gnome-screenshot` produces a flash + shutter sound by default. There's no reliable way to suppress it from the API; warn the user once if it bothers them.

## DE-specific things to know

| DE | Distinctive feature | Watch out for |
|---|---|---|
| GNOME (Wayland) | Activities overview opens with `Super` | No window-position queries; `move`/`resize` programmatic control is limited |
| KDE Plasma | Most flexible; rich KWin scripting | Spectacle screenshot is async — add `wait 300` after triggering |
| Sway / Hyprland / labwc (wlroots) | Best Wayland tooling | Tiling — clicks at fixed coordinates may target wrong window if user resizes |
| Xfce / MATE / Cinnamon (X11) | Just works with xdotool | None significant |
| Unity / Pantheon | X11 — works but DE-specific shortcuts vary | Some shortcuts are DE-overridden |

## Don't try to do these

- **Sudo password prompts** in graphical password dialogs (polkit / pkexec) — the focus locks out synthetic input as a security measure. Use `sudo` over terminal instead, or ask the user.
- **Wayland security keyrings** (gnome-keyring unlock prompt) — same restriction.
- **VirtualKeyboard / OSK input** — these run in compositor-privileged space.

## Example — open a terminal, run a command, screenshot

```
{"action": "key", "keys": "Ctrl+Alt+T"}
{"action": "wait", "ms": 500}
{"action": "type", "text": "uname -a"}
{"action": "key", "keys": "Return"}
{"action": "wait", "ms": 200}
{"action": "screenshot"}
```

For most CLI workflows just use the `terminal` tool — it's faster than driving a GUI terminal through screenshots.
