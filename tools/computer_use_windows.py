"""Native Windows backend for computer_use.

Mouse and keyboard go through ``user32.SendInput`` via ``ctypes`` —
``SendInput`` is the modern Win32 input API and the only one that works
reliably with DirectInput games and UAC-aware applications. The legacy
``keybd_event`` / ``mouse_event`` calls are avoided.

Screenshots use ``mss`` when available (BitBlt-based, MIT-licensed,
fast and cross-Windows-version). When ``mss`` isn't installed we fall
back to a minimal ctypes BitBlt path so the tool still functions on a
default Python install.

Two Windows-specific gotchas:

* **UAC / UIPI** — synthetic input from a non-elevated process can't
  reach an elevated window (User Interface Privilege Isolation). If
  the agent's host process is non-elevated and the target window is,
  clicks land in dead air. Run the gateway elevated for full coverage.
* **DPI awareness** — the tool runs the process as per-monitor DPI
  aware so click coordinates match what the user sees on HiDPI
  displays. Without this, a click at (1000, 1000) on a 200 % screen
  lands at (500, 500).

This backend is unit-test-only — author has no Windows host for
integration validation. Mocks cover every code path.
"""

from __future__ import annotations

import base64
import ctypes
import ctypes.wintypes as wt
import logging
import os
import sys
import time
from typing import Any, Dict, List, Optional, Tuple

from tools.computer_use_common import (
    ActionRequest,
    ActionResult,
    ValidationError,
    build_schema,
    parse_request,
    validate_coords_within,
)
from tools.computer_use_grammar import parse_combo, to_windows
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
# Lazy load user32 + supporting structs. Importing this module on non-Windows
# must not raise, hence the guarded loader.
# ---------------------------------------------------------------------------

_user32 = None
_gdi32 = None


def _load_user32():
    global _user32, _gdi32
    if _user32 is not None:
        return _user32
    if sys.platform != "win32":
        return None
    try:
        _user32 = ctypes.WinDLL("user32", use_last_error=True)
        _gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
        try:
            # Per-monitor v2 DPI awareness so coordinates aren't scaled.
            _user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
        except (AttributeError, OSError):
            pass
        return _user32
    except OSError:
        return None


def _check_windows() -> bool:
    if sys.platform != "win32":
        return False
    if not is_enabled():
        return False
    return _load_user32() is not None


# ---------------------------------------------------------------------------
# SendInput structures.
# ---------------------------------------------------------------------------

ULONG_PTR = ctypes.c_size_t


class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wt.LONG),
        ("dy", wt.LONG),
        ("mouseData", wt.DWORD),
        ("dwFlags", wt.DWORD),
        ("time", wt.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wt.WORD),
        ("wScan", wt.WORD),
        ("dwFlags", wt.DWORD),
        ("time", wt.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class _HARDWAREINPUT(ctypes.Structure):
    _fields_ = [("uMsg", wt.DWORD), ("wParamL", wt.WORD), ("wParamH", wt.WORD)]


class _INPUTunion(ctypes.Union):
    _fields_ = [("mi", _MOUSEINPUT), ("ki", _KEYBDINPUT), ("hi", _HARDWAREINPUT)]


class _INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [("type", wt.DWORD), ("u", _INPUTunion)]


INPUT_MOUSE = 0
INPUT_KEYBOARD = 1

# Mouse flags
MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x0010
MOUSEEVENTF_MIDDLEDOWN = 0x0020
MOUSEEVENTF_MIDDLEUP = 0x0040
MOUSEEVENTF_WHEEL = 0x0800
MOUSEEVENTF_HWHEEL = 0x01000
MOUSEEVENTF_ABSOLUTE = 0x8000

# Keyboard flags
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004


def _send_inputs(inputs: List[_INPUT]) -> None:
    user32 = _load_user32()
    assert user32 is not None
    n = len(inputs)
    arr_t = _INPUT * n
    arr = arr_t(*inputs)
    sent = user32.SendInput(n, ctypes.byref(arr), ctypes.sizeof(_INPUT))
    if sent != n:
        err = ctypes.get_last_error()
        raise RuntimeError(f"SendInput sent {sent}/{n} (last error {err})")


def _mouse_input(flags: int, dx: int = 0, dy: int = 0, data: int = 0) -> _INPUT:
    inp = _INPUT()
    inp.type = INPUT_MOUSE
    inp.mi = _MOUSEINPUT(dx=dx, dy=dy, mouseData=data, dwFlags=flags, time=0, dwExtraInfo=0)
    return inp


def _key_input(vk: int, key_up: bool = False, scan: int = 0, unicode_char: bool = False) -> _INPUT:
    inp = _INPUT()
    inp.type = INPUT_KEYBOARD
    flags = 0
    if key_up:
        flags |= KEYEVENTF_KEYUP
    if unicode_char:
        flags |= KEYEVENTF_UNICODE
    inp.ki = _KEYBDINPUT(wVk=vk, wScan=scan, dwFlags=flags, time=0, dwExtraInfo=0)
    return inp


# ---------------------------------------------------------------------------
# Mouse helpers — convert absolute pixel to virtual desktop normalized coords.
# ---------------------------------------------------------------------------

SM_CXSCREEN = 0
SM_CYSCREEN = 1


def _screen_size() -> Tuple[int, int]:
    user32 = _load_user32()
    if user32 is None:
        return (0, 0)
    return int(user32.GetSystemMetrics(SM_CXSCREEN)), int(user32.GetSystemMetrics(SM_CYSCREEN))


def _to_normalized(x: int, y: int) -> Tuple[int, int]:
    sw, sh = _screen_size()
    sw = max(sw, 1)
    sh = max(sh, 1)
    return int(x * 65535 / sw), int(y * 65535 / sh)


def _move(x: int, y: int) -> None:
    nx, ny = _to_normalized(x, y)
    _send_inputs([_mouse_input(MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE, nx, ny)])


def _click(x: int, y: int, button: str = "left") -> None:
    _move(x, y)
    if button == "left":
        down, up = MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP
    elif button == "right":
        down, up = MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP
    else:
        down, up = MOUSEEVENTF_MIDDLEDOWN, MOUSEEVENTF_MIDDLEUP
    _send_inputs([_mouse_input(down), _mouse_input(up)])


def _double_click(x: int, y: int) -> None:
    _click(x, y, "left")
    time.sleep(0.05)
    _click(x, y, "left")


def _button_event(x: int, y: int, kind: str) -> None:
    _move(x, y)
    flag = MOUSEEVENTF_LEFTDOWN if kind == "down" else MOUSEEVENTF_LEFTUP
    _send_inputs([_mouse_input(flag)])


def _drag(x1: int, y1: int, x2: int, y2: int) -> None:
    _button_event(x1, y1, "down")
    steps = max(10, int(((x2 - x1) ** 2 + (y2 - y1) ** 2) ** 0.5 / 20))
    for i in range(1, steps + 1):
        ix = x1 + (x2 - x1) * i // steps
        iy = y1 + (y2 - y1) * i // steps
        _move(ix, iy)
        time.sleep(0.005)
    _button_event(x2, y2, "up")


def _scroll(direction: str, amount: int) -> None:
    # WHEEL_DELTA = 120 per notch
    delta = 120 * amount
    if direction == "down":
        delta = -delta
    if direction in ("up", "down"):
        _send_inputs([_mouse_input(MOUSEEVENTF_WHEEL, data=ctypes.c_int32(delta).value)])
    else:
        if direction == "left":
            delta = -delta
        _send_inputs([_mouse_input(MOUSEEVENTF_HWHEEL, data=ctypes.c_int32(delta).value)])


def _type_text(text: str) -> None:
    inputs: List[_INPUT] = []
    for ch in text:
        code = ord(ch)
        # KEYEVENTF_UNICODE drives wScan with the codepoint, vk=0.
        inputs.append(_key_input(0, key_up=False, scan=code, unicode_char=True))
        inputs.append(_key_input(0, key_up=True, scan=code, unicode_char=True))
        if len(inputs) >= 100:
            _send_inputs(inputs)
            inputs = []
    if inputs:
        _send_inputs(inputs)


def _key_combo(combo: str) -> None:
    parsed = parse_combo(combo)
    vks = to_windows(parsed)
    inputs: List[_INPUT] = [_key_input(vk, key_up=False) for vk in vks]
    inputs += [_key_input(vk, key_up=True) for vk in reversed(vks)]
    _send_inputs(inputs)


def _cursor_position() -> Tuple[int, int]:
    user32 = _load_user32()
    if user32 is None:
        return (0, 0)
    pt = wt.POINT()
    user32.GetCursorPos(ctypes.byref(pt))
    return int(pt.x), int(pt.y)


def _active_window() -> Optional[Dict[str, Any]]:
    user32 = _load_user32()
    if user32 is None:
        return None
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return None
    length = user32.GetWindowTextLengthW(hwnd) + 1
    buf = ctypes.create_unicode_buffer(length)
    user32.GetWindowTextW(hwnd, buf, length)
    return {"id": str(int(hwnd)), "title": buf.value}


# ---------------------------------------------------------------------------
# Screenshot — mss preferred, ctypes BitBlt fallback.
# ---------------------------------------------------------------------------

def _screenshot_mss() -> Optional[bytes]:
    try:
        import mss  # type: ignore
        import mss.tools  # type: ignore
    except ImportError:
        return None
    with mss.mss() as sct:
        monitor = sct.monitors[1]  # primary
        sct_img = sct.grab(monitor)
        return mss.tools.to_png(sct_img.rgb, sct_img.size)


def _screenshot_bitblt() -> bytes:
    """Minimal ctypes BitBlt screenshot. Slower but no extra deps."""
    user32 = _load_user32()
    gdi32 = _gdi32
    assert user32 is not None and gdi32 is not None
    sw, sh = _screen_size()
    # GetDC, CreateCompatibleDC, CreateCompatibleBitmap, BitBlt → DIB
    hdc_screen = user32.GetDC(0)
    hdc_mem = gdi32.CreateCompatibleDC(hdc_screen)
    hbm = gdi32.CreateCompatibleBitmap(hdc_screen, sw, sh)
    gdi32.SelectObject(hdc_mem, hbm)
    SRCCOPY = 0x00CC0020
    gdi32.BitBlt(hdc_mem, 0, 0, sw, sh, hdc_screen, 0, 0, SRCCOPY)

    # Read pixels as 32bpp RGBA
    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [
            ("biSize", wt.DWORD), ("biWidth", wt.LONG), ("biHeight", wt.LONG),
            ("biPlanes", wt.WORD), ("biBitCount", wt.WORD),
            ("biCompression", wt.DWORD), ("biSizeImage", wt.DWORD),
            ("biXPelsPerMeter", wt.LONG), ("biYPelsPerMeter", wt.LONG),
            ("biClrUsed", wt.DWORD), ("biClrImportant", wt.DWORD),
        ]

    class BITMAPINFO(ctypes.Structure):
        _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wt.DWORD * 3)]

    bmi = BITMAPINFO()
    bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    bmi.bmiHeader.biWidth = sw
    bmi.bmiHeader.biHeight = -sh  # top-down
    bmi.bmiHeader.biPlanes = 1
    bmi.bmiHeader.biBitCount = 32
    bmi.bmiHeader.biCompression = 0  # BI_RGB

    buf = (ctypes.c_ubyte * (sw * sh * 4))()
    gdi32.GetDIBits(hdc_mem, hbm, 0, sh, buf, ctypes.byref(bmi), 0)

    gdi32.DeleteObject(hbm)
    gdi32.DeleteDC(hdc_mem)
    user32.ReleaseDC(0, hdc_screen)

    try:
        from PIL import Image  # type: ignore
        # BGRA → RGB
        img = Image.frombuffer("RGBA", (sw, sh), bytes(buf), "raw", "BGRA", 0, 1).convert("RGB")
        import io as _io
        out = _io.BytesIO()
        img.save(out, format="PNG", optimize=True)
        return out.getvalue()
    except ImportError:
        # Without PIL we can't encode PNG; return raw BGRA. Honest failure.
        raise RuntimeError("PIL not installed and mss not available; cannot encode screenshot")


def _screenshot() -> bytes:
    png = _screenshot_mss()
    if png is not None:
        return png
    return _screenshot_bitblt()


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
# Handler.
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
        return ActionResult(success=True, action=a, active_window=_active_window() or {})

    if a == "screenshot":
        png = _screenshot()
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
        _button_event(req.x, req.y, "down")
    elif a == "left_button_release":
        _button_event(req.x, req.y, "up")
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


def computer_use_windows_tool(args: Dict[str, Any]) -> Dict[str, Any]:
    try:
        gate(args.get("action", "<unknown>"))
        req = parse_request(args)
        result = _handle(req)
    except SafetyRefusal as e:
        result = ActionResult(success=False, action=str(args.get("action", "")), error=f"refused: {e}")
    except ValidationError as e:
        result = ActionResult(success=False, action=str(args.get("action", "")), error=f"validation: {e}")
    except Exception as e:
        logger.exception("computer_use_windows handler failed")
        result = ActionResult(success=False, action=str(args.get("action", "")), error=f"runtime: {e}")

    log_action(result.action, args, result.success, result.error)
    return result.to_dict()


registry.register(
    name="computer_use_windows",
    toolset="computer_use",
    schema=build_schema("computer_use_windows", "Windows"),
    handler=lambda args, **kw: computer_use_windows_tool(args),
    check_fn=_check_windows,
    requires_env=["HERMES_COMPUTER_USE_ENABLED"],
    is_async=False,
    description="Native Windows desktop control (SendInput + mss/BitBlt).",
    emoji="🪟",
    max_result_size_chars=8_000_000,
)
