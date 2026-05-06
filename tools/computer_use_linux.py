"""Native Linux backend for computer_use.

Linux is the awkward one — it's two display servers in a trench coat. The
backend detects ``$WAYLAND_DISPLAY`` / ``$XDG_SESSION_TYPE`` once at
import time and routes every action to the matching toolchain:

* **X11 path** uses the venerable ``xdotool`` (``xdotool click``,
  ``xdotool key``, ``xdotool mousemove``) plus ``scrot`` for screenshots.
  Both packages are in every distro and have stable CLI surfaces.
* **Wayland path** uses ``ydotool`` (needs the ydotoold daemon running
  and ``/dev/uinput`` accessible — that's a one-time host setup) plus
  one of ``grim`` (wlroots compositors: Sway, Hyprland, labwc),
  ``gnome-screenshot --file=`` (GNOME), or ``spectacle -bno`` (KDE).
  Active-window / cursor-position queries are best-effort: Wayland's
  application isolation makes some of these structurally impossible
  on certain compositors. Tool returns empty dicts in that case
  rather than failing.

This backend is unit-test-only at the moment — author has no Linux
host immediately available for end-to-end validation. Mocked subprocess
tests cover every code path; real-world testing is deferred.
"""

from __future__ import annotations

import base64
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from tools.computer_use_common import (
    ActionRequest,
    ActionResult,
    ValidationError,
    build_schema,
    parse_request,
    validate_coords_within,
)
from tools.computer_use_grammar import parse_combo, to_xdotool, to_ydotool
from tools.computer_use_safety import (
    SafetyRefusal,
    gate,
    is_enabled,
    log_action,
    redact_image,
)
from tools.registry import registry

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Display server detection. We re-check on every action rather than caching
# at import — operators who run X11 sessions on a Wayland-default distro
# may have either set, and the env can flip across nested sessions.
# ---------------------------------------------------------------------------

def _is_wayland() -> bool:
    if os.environ.get("WAYLAND_DISPLAY"):
        return True
    if os.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland":
        return True
    return False


def _x11_available() -> bool:
    return bool(shutil.which("xdotool")) and bool(shutil.which("scrot") or shutil.which("import"))


def _wayland_available() -> bool:
    has_input = bool(shutil.which("ydotool"))
    has_capture = any(shutil.which(t) for t in ("grim", "gnome-screenshot", "spectacle"))
    return has_input and has_capture


def _check_linux() -> bool:
    if sys.platform != "linux":
        return False
    if not is_enabled():
        return False
    return _x11_available() or _wayland_available()


# ---------------------------------------------------------------------------
# X11 implementations.
# ---------------------------------------------------------------------------

def _run(cmd: List[str], *, timeout: float = 10.0, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, check=check, capture_output=True, text=True, timeout=timeout)


def _x11_screenshot() -> bytes:
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
        path = tmp.name
    try:
        if shutil.which("scrot"):
            _run(["scrot", "--silent", "--overwrite", path])
        else:
            _run(["import", "-window", "root", path])
        return Path(path).read_bytes()
    finally:
        try:
            Path(path).unlink()
        except OSError:
            pass


def _x11_screen_size() -> Tuple[int, int]:
    if shutil.which("xdpyinfo"):
        try:
            out = _run(["xdpyinfo"]).stdout
            for line in out.splitlines():
                line = line.strip()
                if line.startswith("dimensions:"):
                    # "dimensions:    1920x1080 pixels (508x285 millimeters)"
                    parts = line.split()
                    if len(parts) >= 2 and "x" in parts[1]:
                        w, h = parts[1].split("x")
                        return int(w), int(h)
        except (subprocess.SubprocessError, ValueError):
            pass
    if shutil.which("xrandr"):
        try:
            out = _run(["xrandr"]).stdout
            for line in out.splitlines():
                if " connected primary " in line or " connected " in line:
                    # "HDMI-1 connected primary 1920x1080+0+0 ..."
                    for tok in line.split():
                        if "x" in tok and "+" in tok:
                            res = tok.split("+", 1)[0]
                            if "x" in res:
                                w, h = res.split("x")
                                return int(w), int(h)
        except (subprocess.SubprocessError, ValueError):
            pass
    return (0, 0)


def _x11_click(x: int, y: int, button: str = "left") -> None:
    btn_map = {"left": "1", "middle": "2", "right": "3"}
    _run(["xdotool", "mousemove", str(x), str(y), "click", btn_map[button]])


def _x11_double_click(x: int, y: int) -> None:
    _run(["xdotool", "mousemove", str(x), str(y), "click", "--repeat", "2", "--delay", "50", "1"])


def _x11_button_event(x: int, y: int, kind: str) -> None:
    cmd = "mousedown" if kind == "down" else "mouseup"
    _run(["xdotool", "mousemove", str(x), str(y), cmd, "1"])


def _x11_move(x: int, y: int) -> None:
    _run(["xdotool", "mousemove", str(x), str(y)])


def _x11_drag(x1: int, y1: int, x2: int, y2: int) -> None:
    _run([
        "xdotool", "mousemove", str(x1), str(y1),
        "mousedown", "1",
        "mousemove", str(x2), str(y2),
        "mouseup", "1",
    ])


def _x11_scroll(direction: str, amount: int) -> None:
    btn = {"up": "4", "down": "5", "left": "6", "right": "7"}[direction]
    _run(["xdotool", "click", "--repeat", str(amount), btn])


def _x11_type(text: str) -> None:
    _run(["xdotool", "type", "--delay", "5", "--", text])


def _x11_key(combo: str) -> None:
    parsed = parse_combo(combo)
    arg = to_xdotool(parsed)
    _run(["xdotool", "key", "--", arg])


def _x11_cursor() -> Tuple[int, int]:
    out = _run(["xdotool", "getmouselocation"]).stdout
    # "x:123 y:456 screen:0 window:abc"
    parts = dict(p.split(":") for p in out.split() if ":" in p)
    return int(parts.get("x", 0)), int(parts.get("y", 0))


def _x11_active_window() -> Optional[Dict[str, Any]]:
    try:
        wid = _run(["xdotool", "getactivewindow"]).stdout.strip()
        if not wid:
            return None
        name = _run(["xdotool", "getwindowname", wid]).stdout.strip()
        cls = ""
        if shutil.which("xprop"):
            xp = _run(["xprop", "-id", wid, "WM_CLASS"], check=False).stdout.strip()
            if "=" in xp:
                cls = xp.split("=", 1)[1].strip().strip('"').split('", "')[-1].strip('"')
        return {"id": wid, "title": name, "app": cls}
    except (subprocess.SubprocessError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Wayland implementations.
# ---------------------------------------------------------------------------

def _wayland_screenshot() -> bytes:
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
        path = tmp.name
    try:
        if shutil.which("grim"):
            _run(["grim", path])
        elif shutil.which("gnome-screenshot"):
            _run(["gnome-screenshot", "-f", path])
        elif shutil.which("spectacle"):
            _run(["spectacle", "-b", "-n", "-o", path])
        else:
            raise RuntimeError("no Wayland screenshot tool available")
        return Path(path).read_bytes()
    finally:
        try:
            Path(path).unlink()
        except OSError:
            pass


def _wayland_screen_size() -> Tuple[int, int]:
    # wlr-randr is the closest analog to xrandr on wlroots compositors.
    if shutil.which("wlr-randr"):
        try:
            out = _run(["wlr-randr"]).stdout
            for line in out.splitlines():
                line = line.strip()
                if "current" in line.lower() and "x" in line:
                    parts = line.split()
                    for tok in parts:
                        if "x" in tok and tok[0].isdigit():
                            try:
                                w, h = tok.split("x")
                                return int(w), int(h.split(",")[0].rstrip("p"))
                            except ValueError:
                                continue
        except subprocess.SubprocessError:
            pass
    # swaymsg / hyprctl fallbacks
    if shutil.which("swaymsg"):
        try:
            import json as _json
            out = _run(["swaymsg", "-t", "get_outputs"]).stdout
            for o in _json.loads(out):
                if o.get("active"):
                    mode = o.get("current_mode") or {}
                    return int(mode.get("width", 0)), int(mode.get("height", 0))
        except (subprocess.SubprocessError, ValueError, KeyError):
            pass
    if shutil.which("hyprctl"):
        try:
            import json as _json
            out = _run(["hyprctl", "-j", "monitors"]).stdout
            for m in _json.loads(out):
                if m.get("focused"):
                    return int(m.get("width", 0)), int(m.get("height", 0))
        except (subprocess.SubprocessError, ValueError, KeyError):
            pass
    return (0, 0)


def _ydotool_send_keys(codes: List[int]) -> None:
    """Press codes in order, release in reverse — emulates a chord."""
    args = ["ydotool", "key"]
    for code in codes:
        args.append(f"{code}:1")
    for code in reversed(codes):
        args.append(f"{code}:0")
    _run(args)


def _wayland_click(x: int, y: int, button: str = "left") -> None:
    # ydotool button codes: BTN_LEFT=0xC0, BTN_RIGHT=0xC1, BTN_MIDDLE=0xC2
    code = {"left": 0xC0, "right": 0xC1, "middle": 0xC2}[button]
    _run(["ydotool", "mousemove", "--absolute", "-x", str(x), "-y", str(y)])
    _run(["ydotool", "click", f"0x{code:X}"])


def _wayland_double_click(x: int, y: int) -> None:
    _wayland_click(x, y, "left")
    time.sleep(0.05)
    _wayland_click(x, y, "left")


def _wayland_button_event(x: int, y: int, kind: str) -> None:
    code = 0xC0
    suffix = "1" if kind == "down" else "0"
    _run(["ydotool", "mousemove", "--absolute", "-x", str(x), "-y", str(y)])
    _run(["ydotool", "click", f"{code:#x}:{suffix}"])


def _wayland_move(x: int, y: int) -> None:
    _run(["ydotool", "mousemove", "--absolute", "-x", str(x), "-y", str(y)])


def _wayland_drag(x1: int, y1: int, x2: int, y2: int) -> None:
    _wayland_button_event(x1, y1, "down")
    _run(["ydotool", "mousemove", "--absolute", "-x", str(x2), "-y", str(y2)])
    _wayland_button_event(x2, y2, "up")


def _wayland_scroll(direction: str, amount: int) -> None:
    # ydotool wheel positive = up, negative = down on most compositors.
    delta = amount if direction == "up" else -amount if direction == "down" else 0
    if delta:
        _run(["ydotool", "mousemove", "--wheel", "-y", str(delta)])


def _wayland_type(text: str) -> None:
    _run(["ydotool", "type", "--", text])


def _wayland_key(combo: str) -> None:
    parsed = parse_combo(combo)
    codes = to_ydotool(parsed)
    _ydotool_send_keys(codes)


def _wayland_cursor() -> Tuple[int, int]:
    # No portable Wayland cursor query. Best-effort via Sway IPC if present.
    if shutil.which("swaymsg"):
        try:
            import json as _json
            out = _run(["swaymsg", "-t", "get_seats"]).stdout
            for s in _json.loads(out):
                pos = s.get("pointer", {})
                if "x" in pos and "y" in pos:
                    return int(pos["x"]), int(pos["y"])
        except (subprocess.SubprocessError, ValueError, KeyError):
            pass
    return (0, 0)


def _wayland_active_window() -> Optional[Dict[str, Any]]:
    if shutil.which("swaymsg"):
        try:
            import json as _json
            out = _run(["swaymsg", "-t", "get_tree"]).stdout
            tree = _json.loads(out)

            def walk(node):
                if node.get("focused"):
                    return node
                for child in (node.get("nodes") or []) + (node.get("floating_nodes") or []):
                    found = walk(child)
                    if found:
                        return found
                return None

            focused = walk(tree)
            if focused:
                return {
                    "id": str(focused.get("id", "")),
                    "title": focused.get("name") or "",
                    "app": (focused.get("app_id") or focused.get("window_properties", {}).get("class") or ""),
                }
        except (subprocess.SubprocessError, ValueError, KeyError):
            pass
    if shutil.which("hyprctl"):
        try:
            import json as _json
            out = _run(["hyprctl", "-j", "activewindow"]).stdout
            w = _json.loads(out)
            return {"id": str(w.get("address", "")), "title": w.get("title", ""), "app": w.get("class", "")}
        except (subprocess.SubprocessError, ValueError, KeyError):
            pass
    return None


# ---------------------------------------------------------------------------
# Routing layer.
# ---------------------------------------------------------------------------

class Backend:
    screenshot = staticmethod(lambda: b"")
    screen_size = staticmethod(lambda: (0, 0))
    click = staticmethod(lambda x, y, b="left": None)
    double_click = staticmethod(lambda x, y: None)
    button_event = staticmethod(lambda x, y, kind: None)
    move = staticmethod(lambda x, y: None)
    drag = staticmethod(lambda x1, y1, x2, y2: None)
    scroll = staticmethod(lambda d, a: None)
    type_text = staticmethod(lambda t: None)
    key = staticmethod(lambda c: None)
    cursor = staticmethod(lambda: (0, 0))
    active_window = staticmethod(lambda: None)


def _select_backend() -> Backend:
    """Choose X11 or Wayland routing once per call."""
    use_wayland = _is_wayland() and _wayland_available()
    if use_wayland:
        b = Backend()
        b.screenshot = _wayland_screenshot
        b.screen_size = _wayland_screen_size
        b.click = _wayland_click
        b.double_click = _wayland_double_click
        b.button_event = _wayland_button_event
        b.move = _wayland_move
        b.drag = _wayland_drag
        b.scroll = _wayland_scroll
        b.type_text = _wayland_type
        b.key = _wayland_key
        b.cursor = _wayland_cursor
        b.active_window = _wayland_active_window
        return b

    if _x11_available():
        b = Backend()
        b.screenshot = _x11_screenshot
        b.screen_size = _x11_screen_size
        b.click = _x11_click
        b.double_click = _x11_double_click
        b.button_event = _x11_button_event
        b.move = _x11_move
        b.drag = _x11_drag
        b.scroll = _x11_scroll
        b.type_text = _x11_type
        b.key = _x11_key
        b.cursor = _x11_cursor
        b.active_window = _x11_active_window
        return b

    raise RuntimeError(
        "no Linux desktop automation backend available; install xdotool+scrot "
        "for X11 or ydotool+grim for Wayland"
    )


def _crop_region(png_bytes: bytes, region) -> bytes:
    try:
        from PIL import Image  # type: ignore
    except ImportError:
        return png_bytes
    import io as _io
    x1, y1, x2, y2 = (int(v) for v in region)
    img = Image.open(_io.BytesIO(png_bytes)).convert("RGB")
    cropped = img.crop((x1, y1, x2, y2))
    buf = _io.BytesIO()
    cropped.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def _handle(req: ActionRequest, backend: Backend) -> ActionResult:
    a = req.action

    if a == "screen_size":
        w, h = backend.screen_size()
        return ActionResult(success=True, action=a, screen_width=w, screen_height=h)

    if a == "cursor_position":
        x, y = backend.cursor()
        return ActionResult(success=True, action=a, cursor_x=x, cursor_y=y)

    if a == "get_active_window":
        return ActionResult(success=True, action=a, active_window=backend.active_window() or {})

    if a == "screenshot":
        png = backend.screenshot()
        if req.region:
            png = _crop_region(png, req.region)
        if req.redact_regions:
            png = redact_image(png, req.redact_regions)
        return ActionResult(
            success=True,
            action=a,
            screenshot_b64=base64.b64encode(png).decode("ascii"),
        )

    if a == "wait":
        time.sleep((req.ms or 0) / 1000.0)
        return ActionResult(success=True, action=a, message=f"waited {req.ms}ms")

    sw, sh = backend.screen_size()
    if sw and sh:
        validate_coords_within(req, sw, sh)

    if a == "left_click":
        backend.click(req.x, req.y, "left")
    elif a == "double_click":
        backend.double_click(req.x, req.y)
    elif a == "right_click":
        backend.click(req.x, req.y, "right")
    elif a == "middle_click":
        backend.click(req.x, req.y, "middle")
    elif a == "left_button_press":
        backend.button_event(req.x, req.y, "down")
    elif a == "left_button_release":
        backend.button_event(req.x, req.y, "up")
    elif a == "mouse_move":
        backend.move(req.x, req.y)
    elif a == "mouse_drag":
        backend.drag(req.x, req.y, req.x2, req.y2)
    elif a == "scroll":
        backend.move(req.x, req.y)
        backend.scroll(req.direction, req.amount or 3)
    elif a == "type":
        backend.type_text(req.text)
    elif a == "key":
        backend.key(req.keys)
    else:
        return ActionResult(success=False, action=a, error=f"unhandled action {a!r}")

    return ActionResult(success=True, action=a, screen_width=sw, screen_height=sh)


def computer_use_linux_tool(args: Dict[str, Any]) -> Dict[str, Any]:
    try:
        gate(args.get("action", "<unknown>"))
        req = parse_request(args)
        backend = _select_backend()
        result = _handle(req, backend)
    except SafetyRefusal as e:
        result = ActionResult(success=False, action=str(args.get("action", "")), error=f"refused: {e}")
    except ValidationError as e:
        result = ActionResult(success=False, action=str(args.get("action", "")), error=f"validation: {e}")
    except subprocess.CalledProcessError as e:
        logger.error("backend subprocess failed: %s\nstderr: %s", e, e.stderr)
        result = ActionResult(success=False, action=str(args.get("action", "")), error=f"backend: {e.stderr or e}")
    except Exception as e:
        logger.exception("computer_use_linux handler failed")
        result = ActionResult(success=False, action=str(args.get("action", "")), error=f"runtime: {e}")

    log_action(result.action, args, result.success, result.error)
    return result.to_dict()


registry.register(
    name="computer_use_linux",
    toolset="computer_use",
    schema=build_schema("computer_use_linux", "Linux"),
    handler=lambda args, **kw: computer_use_linux_tool(args),
    check_fn=_check_linux,
    requires_env=["HERMES_COMPUTER_USE_ENABLED"],
    is_async=False,
    description="Native Linux desktop control (X11 + Wayland; xdotool/scrot or ydotool/grim).",
    emoji="🐧",
    max_result_size_chars=8_000_000,
)
