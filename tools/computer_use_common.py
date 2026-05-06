"""Shared types, schema, and validation primitives for the computer_use_* tools.

The per-OS backends (computer_use_macos.py, computer_use_linux.py,
computer_use_windows.py) each register a tool against the same JSON schema
defined here, so a model trained on one platform's tool surface generalises
to the others. Per-OS skills handle the platform-specific shortcut idioms
(Cmd vs Ctrl, X11 vs Wayland, etc.).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Action set — mirrors Anthropic's computer_20251124 schema with a few
# practical additions (get_active_window, screen_size, find_text).
# ---------------------------------------------------------------------------

ACTIONS: Tuple[str, ...] = (
    "screenshot",
    "left_click",
    "double_click",
    "right_click",
    "middle_click",
    "left_button_press",
    "left_button_release",
    "mouse_move",
    "mouse_drag",
    "type",
    "key",
    "scroll",
    "wait",
    "cursor_position",
    "screen_size",
    "get_active_window",
)

# Hard caps to prevent the model from clobbering the host with one bad call.
MAX_TYPE_CHARS = 10_000
MAX_KEY_CHARS = 256
MAX_WAIT_MS = 30_000
MAX_SCROLL_AMOUNT = 50


@dataclass
class ActionRequest:
    """Validated request for a single computer-use action."""

    action: str
    x: Optional[int] = None
    y: Optional[int] = None
    x2: Optional[int] = None
    y2: Optional[int] = None
    text: Optional[str] = None
    keys: Optional[str] = None
    direction: Optional[str] = None
    amount: Optional[int] = None
    ms: Optional[int] = None
    region: Optional[List[int]] = None
    redact_regions: Optional[List[List[int]]] = None


@dataclass
class ActionResult:
    """Result envelope returned to the model."""

    success: bool
    action: str
    message: str = ""
    screenshot_b64: Optional[str] = None
    screenshot_format: str = "png"
    cursor_x: Optional[int] = None
    cursor_y: Optional[int] = None
    screen_width: Optional[int] = None
    screen_height: Optional[int] = None
    active_window: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "success": self.success,
            "action": self.action,
        }
        if self.message:
            out["message"] = self.message
        if self.screenshot_b64 is not None:
            out["screenshot_b64"] = self.screenshot_b64
            out["screenshot_format"] = self.screenshot_format
        if self.cursor_x is not None and self.cursor_y is not None:
            out["cursor"] = {"x": self.cursor_x, "y": self.cursor_y}
        if self.screen_width is not None and self.screen_height is not None:
            out["screen"] = {"width": self.screen_width, "height": self.screen_height}
        if self.active_window is not None:
            out["active_window"] = self.active_window
        if self.error:
            out["error"] = self.error
        if self.metadata:
            out["metadata"] = self.metadata
        return out


class ValidationError(Exception):
    """Raised when an ActionRequest fails parameter validation."""


def parse_request(args: Dict[str, Any]) -> ActionRequest:
    """Parse and validate a tool-call arg dict into an ActionRequest.

    Raises ValidationError with a model-readable explanation when the input
    is malformed. The handler should catch and turn it into an ActionResult
    with success=False so the model can self-correct.
    """
    if not isinstance(args, dict):
        raise ValidationError("arguments must be a JSON object")

    action = args.get("action")
    if action not in ACTIONS:
        raise ValidationError(
            f"action must be one of {ACTIONS}, got {action!r}"
        )

    req = ActionRequest(action=action)

    # Coordinate-bearing actions
    coord_actions = {
        "left_click", "double_click", "right_click", "middle_click",
        "left_button_press", "left_button_release",
        "mouse_move", "scroll",
    }
    if action in coord_actions:
        req.x = _coerce_int(args, "x", required=True)
        req.y = _coerce_int(args, "y", required=True)

    if action == "mouse_drag":
        req.x = _coerce_int(args, "x", required=True)
        req.y = _coerce_int(args, "y", required=True)
        req.x2 = _coerce_int(args, "x2", required=True)
        req.y2 = _coerce_int(args, "y2", required=True)

    if action == "type":
        text = args.get("text")
        if not isinstance(text, str) or not text:
            raise ValidationError("'type' requires non-empty 'text' string")
        if len(text) > MAX_TYPE_CHARS:
            raise ValidationError(f"'text' exceeds {MAX_TYPE_CHARS}-char cap")
        req.text = text

    if action == "key":
        keys = args.get("keys")
        if not isinstance(keys, str) or not keys:
            raise ValidationError("'key' requires non-empty 'keys' string")
        if len(keys) > MAX_KEY_CHARS:
            raise ValidationError(f"'keys' exceeds {MAX_KEY_CHARS}-char cap")
        req.keys = keys

    if action == "scroll":
        direction = args.get("direction")
        if direction not in {"up", "down", "left", "right"}:
            raise ValidationError("'scroll' requires direction in {up,down,left,right}")
        req.direction = direction
        req.amount = _coerce_int(args, "amount", required=False, default=3)
        if req.amount is not None and (req.amount < 1 or req.amount > MAX_SCROLL_AMOUNT):
            raise ValidationError(f"'amount' must be in [1, {MAX_SCROLL_AMOUNT}]")

    if action == "wait":
        req.ms = _coerce_int(args, "ms", required=True)
        if req.ms is None or req.ms < 0 or req.ms > MAX_WAIT_MS:
            raise ValidationError(f"'ms' must be in [0, {MAX_WAIT_MS}]")

    if action == "screenshot":
        region = args.get("region")
        if region is not None:
            if (
                not isinstance(region, list)
                or len(region) != 4
                or not all(isinstance(v, (int, float)) for v in region)
            ):
                raise ValidationError("'region' must be [x1,y1,x2,y2] integers")
            req.region = [int(v) for v in region]
        redact = args.get("redact_regions")
        if redact is not None:
            if not isinstance(redact, list):
                raise ValidationError("'redact_regions' must be a list of [x1,y1,x2,y2]")
            for r in redact:
                if not isinstance(r, list) or len(r) != 4:
                    raise ValidationError("each redact region must be [x1,y1,x2,y2]")
            req.redact_regions = [[int(v) for v in r] for r in redact]

    return req


def _coerce_int(args: Dict[str, Any], key: str, *, required: bool, default: Optional[int] = None) -> Optional[int]:
    val = args.get(key)
    if val is None:
        if required:
            raise ValidationError(f"missing required integer field {key!r}")
        return default
    if isinstance(val, bool) or not isinstance(val, (int, float)):
        raise ValidationError(f"{key!r} must be a number")
    return int(val)


def validate_coords_within(req: ActionRequest, screen_w: int, screen_h: int) -> None:
    """Reject obviously-invalid coordinates before they reach the OS layer.

    Off-screen clicks waste an action and can cause unexpected focus changes
    on multi-monitor setups. Better to fail early with a clear message.
    """
    pairs: List[Tuple[Optional[int], Optional[int], str]] = [
        (req.x, req.y, "(x,y)"),
        (req.x2, req.y2, "(x2,y2)"),
    ]
    for x, y, label in pairs:
        if x is None or y is None:
            continue
        if x < 0 or y < 0 or x > screen_w or y > screen_h:
            raise ValidationError(
                f"{label}=({x},{y}) outside screen bounds {screen_w}x{screen_h}"
            )


# ---------------------------------------------------------------------------
# Shared JSON schema. Each per-OS tool registers under a distinct ``name``
# (computer_use_macos / _linux / _windows) so the model can route by check_fn
# but presents an identical parameter surface.
# ---------------------------------------------------------------------------

def build_schema(tool_name: str, platform_label: str) -> Dict[str, Any]:
    """Build the JSON schema for an OS-specific computer_use tool variant."""
    return {
        "name": tool_name,
        "description": (
            f"Native desktop control on {platform_label}. Take screenshots, click, "
            f"type, send key combinations, scroll, drag. Always start a task with "
            f"a screenshot to ground subsequent actions in pixel coordinates. "
            f"Coordinates are absolute screen pixels (origin top-left). "
            f"For key combos use the per-OS skill grammar (Cmd+Tab on macOS, "
            f"ctrl+alt+t on Linux, win+s on Windows)."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": list(ACTIONS),
                    "description": "Which desktop action to perform.",
                },
                "x": {"type": "integer", "description": "Absolute x pixel."},
                "y": {"type": "integer", "description": "Absolute y pixel."},
                "x2": {"type": "integer", "description": "Drag end x (mouse_drag only)."},
                "y2": {"type": "integer", "description": "Drag end y (mouse_drag only)."},
                "text": {
                    "type": "string",
                    "description": f"Text to type (max {MAX_TYPE_CHARS} chars).",
                },
                "keys": {
                    "type": "string",
                    "description": "Key combo string e.g. 'Cmd+Tab', 'ctrl+shift+T'.",
                },
                "direction": {
                    "type": "string",
                    "enum": ["up", "down", "left", "right"],
                    "description": "Scroll direction.",
                },
                "amount": {
                    "type": "integer",
                    "description": f"Scroll wheel ticks (1-{MAX_SCROLL_AMOUNT}, default 3).",
                },
                "ms": {
                    "type": "integer",
                    "description": f"Wait duration in ms (0-{MAX_WAIT_MS}).",
                },
                "region": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "description": "Screenshot crop [x1,y1,x2,y2]. Omit for full screen.",
                },
                "redact_regions": {
                    "type": "array",
                    "items": {"type": "array", "items": {"type": "integer"}},
                    "description": "Rectangles to blank in returned screenshot — for hiding password fields, MFA codes, etc. before the image reaches the model.",
                },
            },
            "required": ["action"],
        },
    }
