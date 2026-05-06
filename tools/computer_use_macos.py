"""Native macOS backend for computer_use.

Mouse and keyboard go through Quartz ``CGEvent`` calls (via
``pyobjc-framework-Quartz``); screenshots use Apple's ``screencapture``
CLI which is always present on macOS and produces clean PNGs without
any extra dependencies.

Two macOS-specific gotchas the user has to handle once, outside of code:

* **Accessibility permission** — required for synthetic mouse/keyboard
  events to reach other applications. macOS prompts the first time a
  CGEvent is posted; the operator grants under
  *System Settings → Privacy & Security → Accessibility*.
* **Screen Recording permission** — required for screenshots that
  include other application windows. ``screencapture`` shows an
  unrecognised permission dialog the first time it runs.

These cannot be granted programmatically; the per-OS skill prompts the
operator through the setup once.

The tool registers unconditionally so the AST tool scanner picks it up,
but ``check_fn`` returns False on non-Darwin hosts, when pyobjc isn't
importable, or when ``HERMES_COMPUTER_USE_ENABLED`` isn't truthy. So
agents on Linux/Windows never see this tool offered.
"""

from __future__ import annotations

import base64
import logging
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from tools.computer_use_common import (
    ActionRequest,
    ActionResult,
    ValidationError,
    build_schema,
    parse_request,
    validate_coords_within,
)
from tools.computer_use_grammar import parse_combo, to_macos
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
# Lazy pyobjc loader. Called from check_fn and from each handler that needs
# Quartz. Cached on the module to avoid re-importing on every action.
# ---------------------------------------------------------------------------

_quartz: Optional[Any] = None


def _load_quartz() -> Optional[Any]:
    """Import Quartz lazily; return module or None if unavailable."""
    global _quartz
    if _quartz is not None:
        return _quartz
    try:
        import Quartz  # type: ignore
    except ImportError:
        return None
    _quartz = Quartz
    return _quartz


def _check_macos() -> bool:
    """check_fn for the registry — gates visibility of the tool."""
    if sys.platform != "darwin":
        return False
    if not is_enabled():
        return False
    if _load_quartz() is None:
        return False
    if not shutil.which("screencapture"):
        return False
    return True


# ---------------------------------------------------------------------------
# Screenshots — call /usr/sbin/screencapture into a tempfile, read bytes.
# screencapture takes ~80-150ms; cheap enough for an action loop.
# ---------------------------------------------------------------------------

def _screenshot_full() -> bytes:
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
        path = tmp.name
    try:
        subprocess.run(
            ["screencapture", "-x", "-t", "png", path],
            check=True,
            capture_output=True,
            timeout=10,
        )
        return Path(path).read_bytes()
    finally:
        try:
            Path(path).unlink()
        except OSError:
            pass


def _screen_size() -> Tuple[int, int]:
    """Return (width, height) of the main display in pixels."""
    Q = _load_quartz()
    if Q is None:
        return (0, 0)
    main = Q.CGMainDisplayID()
    return int(Q.CGDisplayPixelsWide(main)), int(Q.CGDisplayPixelsHigh(main))


# ---------------------------------------------------------------------------
# Mouse + keyboard — Quartz CGEvent. All synthetic events use HID source.
# ---------------------------------------------------------------------------

def _post_mouse(event_type: int, x: int, y: int, button: int = 0) -> None:
    Q = _load_quartz()
    assert Q is not None, "pyobjc Quartz must be loaded before _post_mouse"
    event = Q.CGEventCreateMouseEvent(
        None, event_type, (x, y), button,
    )
    Q.CGEventPost(Q.kCGHIDEventTap, event)


def _click(x: int, y: int, button: str = "left") -> None:
    Q = _load_quartz()
    assert Q is not None
    btn_map = {
        "left": (Q.kCGEventLeftMouseDown, Q.kCGEventLeftMouseUp, Q.kCGMouseButtonLeft),
        "right": (Q.kCGEventRightMouseDown, Q.kCGEventRightMouseUp, Q.kCGMouseButtonRight),
        "middle": (Q.kCGEventOtherMouseDown, Q.kCGEventOtherMouseUp, Q.kCGMouseButtonCenter),
    }
    down, up, btn = btn_map[button]
    _post_mouse(down, x, y, btn)
    time.sleep(0.02)
    _post_mouse(up, x, y, btn)


def _double_click(x: int, y: int) -> None:
    Q = _load_quartz()
    assert Q is not None
    # Quartz needs the same event with click count = 2 to register a double-click
    # in apps like Finder. Easiest: post two clicks within the system threshold.
    _click(x, y, "left")
    time.sleep(0.05)
    _click(x, y, "left")


def _move(x: int, y: int) -> None:
    Q = _load_quartz()
    assert Q is not None
    _post_mouse(Q.kCGEventMouseMoved, x, y, 0)


def _drag(x1: int, y1: int, x2: int, y2: int) -> None:
    Q = _load_quartz()
    assert Q is not None
    _post_mouse(Q.kCGEventLeftMouseDown, x1, y1, Q.kCGMouseButtonLeft)
    time.sleep(0.05)
    # Smooth interpolation so apps that need motion events (e.g. drag-drop
    # validators) actually see the drag rather than a teleport.
    steps = max(10, int(((x2 - x1) ** 2 + (y2 - y1) ** 2) ** 0.5 / 20))
    for i in range(1, steps + 1):
        ix = x1 + (x2 - x1) * i // steps
        iy = y1 + (y2 - y1) * i // steps
        _post_mouse(Q.kCGEventLeftMouseDragged, ix, iy, Q.kCGMouseButtonLeft)
        time.sleep(0.005)
    _post_mouse(Q.kCGEventLeftMouseUp, x2, y2, Q.kCGMouseButtonLeft)


def _scroll(direction: str, amount: int) -> None:
    Q = _load_quartz()
    assert Q is not None
    dy = -amount if direction == "down" else amount if direction == "up" else 0
    dx = -amount if direction == "right" else amount if direction == "left" else 0
    event = Q.CGEventCreateScrollWheelEvent(
        None, Q.kCGScrollEventUnitLine, 2, dy, dx,
    )
    Q.CGEventPost(Q.kCGHIDEventTap, event)


def _type_text(text: str) -> None:
    Q = _load_quartz()
    assert Q is not None
    # Use CGEventKeyboardSetUnicodeString for full Unicode coverage —
    # avoids per-character keycode lookups and works for non-ASCII.
    for ch in text:
        for kind in (True, False):
            event = Q.CGEventCreateKeyboardEvent(None, 0, kind)
            Q.CGEventKeyboardSetUnicodeString(event, 1, ch)
            Q.CGEventPost(Q.kCGHIDEventTap, event)
        time.sleep(0.005)


def _key_combo(combo: str) -> None:
    Q = _load_quartz()
    assert Q is not None
    parsed = parse_combo(combo)
    flags, keycode = to_macos(parsed)

    down = Q.CGEventCreateKeyboardEvent(None, keycode, True)
    if flags:
        Q.CGEventSetFlags(down, flags)
    Q.CGEventPost(Q.kCGHIDEventTap, down)

    up = Q.CGEventCreateKeyboardEvent(None, keycode, False)
    if flags:
        Q.CGEventSetFlags(up, flags)
    Q.CGEventPost(Q.kCGHIDEventTap, up)


def _cursor_position() -> Tuple[int, int]:
    Q = _load_quartz()
    assert Q is not None
    event = Q.CGEventCreate(None)
    pt = Q.CGEventGetLocation(event)
    return int(pt.x), int(pt.y)


def _active_window() -> Optional[Dict[str, Any]]:
    """Return basic info about the frontmost window."""
    Q = _load_quartz()
    if Q is None:
        return None
    info_list = Q.CGWindowListCopyWindowInfo(
        Q.kCGWindowListOptionOnScreenOnly | Q.kCGWindowListExcludeDesktopElements,
        Q.kCGNullWindowID,
    )
    if not info_list:
        return None
    # Frontmost is highest layer; pick the first on-screen window with a name.
    for w in info_list:
        name = w.get("kCGWindowName") or ""
        owner = w.get("kCGWindowOwnerName") or ""
        if owner:
            return {"app": str(owner), "title": str(name)}
    return None


# ---------------------------------------------------------------------------
# Handler entry point.
# ---------------------------------------------------------------------------

def _handle(req: ActionRequest) -> ActionResult:
    a = req.action

    if a == "screen_size":
        w, h = _screen_size()
        return ActionResult(success=True, action=a, screen_width=w, screen_height=h)

    if a == "cursor_position":
        x, y = _cursor_position()
        return ActionResult(success=True, action=a, cursor_x=x, cursor_y=y)

    if a == "get_active_window":
        win = _active_window()
        return ActionResult(success=True, action=a, active_window=win or {})

    if a == "screenshot":
        png = _screenshot_full()
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

    # Coordinate-bearing actions: validate against screen bounds.
    sw, sh = _screen_size()
    validate_coords_within(req, sw, sh)

    if a == "left_click":
        _click(req.x, req.y, "left")
    elif a == "double_click":
        _double_click(req.x, req.y)
    elif a == "right_click":
        _click(req.x, req.y, "right")
    elif a == "middle_click":
        _click(req.x, req.y, "middle")
    elif a == "left_button_press":
        Q = _load_quartz()
        _post_mouse(Q.kCGEventLeftMouseDown, req.x, req.y, Q.kCGMouseButtonLeft)
    elif a == "left_button_release":
        Q = _load_quartz()
        _post_mouse(Q.kCGEventLeftMouseUp, req.x, req.y, Q.kCGMouseButtonLeft)
    elif a == "mouse_move":
        _move(req.x, req.y)
    elif a == "mouse_drag":
        _drag(req.x, req.y, req.x2, req.y2)
    elif a == "scroll":
        _move(req.x, req.y)
        _scroll(req.direction, req.amount or 3)
    elif a == "type":
        _type_text(req.text)
    elif a == "key":
        _key_combo(req.keys)
    else:
        return ActionResult(success=False, action=a, error=f"unhandled action {a!r}")

    return ActionResult(success=True, action=a, screen_width=sw, screen_height=sh)


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


# ---------------------------------------------------------------------------
# Tool entry point — called by the registry dispatcher.
# ---------------------------------------------------------------------------

def computer_use_macos_tool(args: Dict[str, Any]) -> Dict[str, Any]:
    try:
        gate(args.get("action", "<unknown>"))
        req = parse_request(args)
        result = _handle(req)
    except SafetyRefusal as e:
        result = ActionResult(success=False, action=str(args.get("action", "")), error=f"refused: {e}")
    except ValidationError as e:
        result = ActionResult(success=False, action=str(args.get("action", "")), error=f"validation: {e}")
    except Exception as e:
        logger.exception("computer_use_macos handler failed")
        result = ActionResult(success=False, action=str(args.get("action", "")), error=f"runtime: {e}")

    log_action(result.action, args, result.success, result.error)
    return result.to_dict()


# ---------------------------------------------------------------------------
# Registration. AST scanner expects a top-level call; check_fn gates
# availability per platform/permission.
# ---------------------------------------------------------------------------

registry.register(
    name="computer_use_macos",
    toolset="computer_use",
    schema=build_schema("computer_use_macos", "macOS"),
    handler=lambda args, **kw: computer_use_macos_tool(args),
    check_fn=_check_macos,
    requires_env=["HERMES_COMPUTER_USE_ENABLED"],
    is_async=False,
    description="Native macOS desktop control (screenshot, click, type, key combos).",
    emoji="🖱",
    max_result_size_chars=8_000_000,  # base64 PNG can run large
)
