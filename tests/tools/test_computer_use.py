"""Unit tests for the computer_use_* tool family.

Mocked end-to-end. The macOS backend can additionally be integration
tested on a Mac host with HERMES_COMPUTER_USE_ENABLED=true and
pyobjc-framework-Quartz installed; that lives in
test_computer_use_macos_integration.py and is opt-in via env var.
"""

from __future__ import annotations

import base64
import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

# Ensure tests/ runs from repo root regardless of cwd.
ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.computer_use_common import (
    ACTIONS,
    ActionRequest,
    ActionResult,
    MAX_TYPE_CHARS,
    MAX_KEY_CHARS,
    MAX_WAIT_MS,
    MAX_SCROLL_AMOUNT,
    ValidationError,
    build_schema,
    parse_request,
    validate_coords_within,
)
from tools.computer_use_grammar import (
    KeyParseError,
    ParsedKey,
    parse_combo,
    to_macos,
    to_windows,
    to_xdotool,
    to_ydotool,
)
from tools.computer_use_safety import (
    SafetyRefusal,
    clear_kill_switch,
    gate,
    is_enabled,
    is_killed,
    log_action,
    redact_image,
    set_kill_switch,
)

# Import all three OS backends at module load so registry.register() runs
# regardless of test class ordering.
import tools.computer_use_macos  # noqa: E402,F401
import tools.computer_use_linux  # noqa: E402,F401
import tools.computer_use_windows  # noqa: E402,F401


# ---------------------------------------------------------------------------
# common
# ---------------------------------------------------------------------------

class CommonValidationTests(unittest.TestCase):
    def test_missing_action_rejected(self):
        with self.assertRaises(ValidationError):
            parse_request({})

    def test_unknown_action_rejected(self):
        with self.assertRaises(ValidationError):
            parse_request({"action": "smash_keyboard"})

    def test_screenshot_no_args(self):
        req = parse_request({"action": "screenshot"})
        self.assertEqual(req.action, "screenshot")
        self.assertIsNone(req.region)

    def test_screenshot_region_validated(self):
        req = parse_request({"action": "screenshot", "region": [0, 0, 100, 100]})
        self.assertEqual(req.region, [0, 0, 100, 100])
        with self.assertRaises(ValidationError):
            parse_request({"action": "screenshot", "region": [0, 0, 100]})

    def test_screenshot_redact_validated(self):
        req = parse_request({
            "action": "screenshot",
            "redact_regions": [[0, 0, 50, 50], [100, 100, 200, 200]],
        })
        self.assertEqual(len(req.redact_regions), 2)
        with self.assertRaises(ValidationError):
            parse_request({
                "action": "screenshot",
                "redact_regions": [[0, 0, 50]],
            })

    def test_left_click_requires_xy(self):
        req = parse_request({"action": "left_click", "x": 10, "y": 20})
        self.assertEqual((req.x, req.y), (10, 20))
        with self.assertRaises(ValidationError):
            parse_request({"action": "left_click", "x": 10})

    def test_drag_requires_four_coords(self):
        req = parse_request({
            "action": "mouse_drag",
            "x": 1, "y": 2, "x2": 3, "y2": 4,
        })
        self.assertEqual((req.x, req.y, req.x2, req.y2), (1, 2, 3, 4))
        with self.assertRaises(ValidationError):
            parse_request({"action": "mouse_drag", "x": 1, "y": 2, "x2": 3})

    def test_type_text_required_and_capped(self):
        req = parse_request({"action": "type", "text": "hello"})
        self.assertEqual(req.text, "hello")
        with self.assertRaises(ValidationError):
            parse_request({"action": "type"})
        with self.assertRaises(ValidationError):
            parse_request({"action": "type", "text": "x" * (MAX_TYPE_CHARS + 1)})

    def test_key_keys_required_and_capped(self):
        req = parse_request({"action": "key", "keys": "Cmd+Tab"})
        self.assertEqual(req.keys, "Cmd+Tab")
        with self.assertRaises(ValidationError):
            parse_request({"action": "key", "keys": ""})
        with self.assertRaises(ValidationError):
            parse_request({"action": "key", "keys": "x" * (MAX_KEY_CHARS + 1)})

    def test_scroll_direction_and_amount(self):
        req = parse_request({"action": "scroll", "x": 0, "y": 0, "direction": "down"})
        self.assertEqual(req.direction, "down")
        self.assertEqual(req.amount, 3)
        with self.assertRaises(ValidationError):
            parse_request({"action": "scroll", "x": 0, "y": 0, "direction": "diagonal"})
        with self.assertRaises(ValidationError):
            parse_request({
                "action": "scroll", "x": 0, "y": 0,
                "direction": "down", "amount": MAX_SCROLL_AMOUNT + 10,
            })

    def test_wait_bounds(self):
        req = parse_request({"action": "wait", "ms": 100})
        self.assertEqual(req.ms, 100)
        with self.assertRaises(ValidationError):
            parse_request({"action": "wait", "ms": -1})
        with self.assertRaises(ValidationError):
            parse_request({"action": "wait", "ms": MAX_WAIT_MS + 1})

    def test_validate_coords_within(self):
        req = ActionRequest(action="left_click", x=100, y=100)
        validate_coords_within(req, 1920, 1080)
        with self.assertRaises(ValidationError):
            validate_coords_within(ActionRequest(action="left_click", x=-1, y=0), 1920, 1080)
        with self.assertRaises(ValidationError):
            validate_coords_within(ActionRequest(action="left_click", x=2000, y=0), 1920, 1080)

    def test_action_result_to_dict_omits_empty(self):
        r = ActionResult(success=True, action="left_click")
        d = r.to_dict()
        self.assertEqual(d, {"success": True, "action": "left_click"})

    def test_action_result_to_dict_includes_screenshot(self):
        r = ActionResult(success=True, action="screenshot", screenshot_b64="abc")
        d = r.to_dict()
        self.assertIn("screenshot_b64", d)
        self.assertEqual(d["screenshot_format"], "png")

    def test_build_schema_lists_all_actions(self):
        schema = build_schema("computer_use_test", "Test OS")
        enums = schema["parameters"]["properties"]["action"]["enum"]
        self.assertEqual(set(enums), set(ACTIONS))


# ---------------------------------------------------------------------------
# grammar
# ---------------------------------------------------------------------------

class GrammarTests(unittest.TestCase):
    def test_simple_letter(self):
        p = parse_combo("a")
        self.assertEqual(p.modifiers, set())
        self.assertEqual(p.key, "a")

    def test_modifier_aliases(self):
        for combo in ("Cmd+T", "command+t", "meta+T", "Win+t"):
            p = parse_combo(combo)
            self.assertEqual(p.modifiers, {"cmd"})
            self.assertEqual(p.key, "t")

    def test_separator_dash(self):
        p = parse_combo("ctrl-shift-A")
        self.assertEqual(p.modifiers, {"ctrl", "shift"})
        self.assertEqual(p.key, "a")

    def test_function_keys(self):
        p = parse_combo("F12")
        self.assertEqual(p.key, "f12")

    def test_key_aliases(self):
        for raw, expected in [("Esc", "escape"), ("Enter", "return"), ("Space", "space")]:
            self.assertEqual(parse_combo(raw).key, expected)

    def test_unknown_modifier_rejected(self):
        with self.assertRaises(KeyParseError):
            parse_combo("hyper+t")

    def test_unknown_multichar_key_rejected(self):
        with self.assertRaises(KeyParseError):
            parse_combo("ctrl+notakey")

    def test_empty_rejected(self):
        with self.assertRaises(KeyParseError):
            parse_combo("")
        with self.assertRaises(KeyParseError):
            parse_combo("   ")

    def test_to_macos_cmd_t(self):
        flags, code = to_macos(parse_combo("Cmd+T"))
        self.assertEqual(flags, 0x00100000)
        self.assertEqual(code, 0x11)

    def test_to_xdotool_cmd_to_super(self):
        # macOS Cmd → Linux Super
        self.assertEqual(to_xdotool(parse_combo("Cmd+Tab")), "super+Tab")

    def test_to_ydotool_codes(self):
        codes = to_ydotool(parse_combo("ctrl+alt+t"))
        self.assertIn(29, codes)  # KEY_LEFTCTRL
        self.assertIn(56, codes)  # KEY_LEFTALT
        self.assertIn(20, codes)  # KEY_T

    def test_to_ydotool_f12(self):
        codes = to_ydotool(parse_combo("F12"))
        self.assertEqual(codes, [88])

    def test_to_windows_letters(self):
        codes = to_windows(parse_combo("ctrl+a"))
        self.assertIn(0x11, codes)  # VK_CONTROL
        self.assertIn(0x41, codes)  # VK_A

    def test_to_windows_f1_f24(self):
        for i in range(1, 25):
            codes = to_windows(parse_combo(f"F{i}"))
            self.assertEqual(codes, [0x6F + i])


# ---------------------------------------------------------------------------
# safety
# ---------------------------------------------------------------------------

class SafetyTests(unittest.TestCase):
    def setUp(self):
        clear_kill_switch()

    def tearDown(self):
        clear_kill_switch()
        os.environ.pop("HERMES_COMPUTER_USE_ENABLED", None)

    def test_env_gate_default_off(self):
        os.environ.pop("HERMES_COMPUTER_USE_ENABLED", None)
        self.assertFalse(is_enabled())
        with self.assertRaises(SafetyRefusal):
            gate("screenshot")

    def test_env_gate_truthy_values(self):
        for v in ("true", "1", "yes", "on", "TRUE", "Yes"):
            os.environ["HERMES_COMPUTER_USE_ENABLED"] = v
            self.assertTrue(is_enabled(), f"value {v!r} should enable")

    def test_env_gate_falsy_values(self):
        for v in ("false", "0", "no", "off", "", "junk"):
            os.environ["HERMES_COMPUTER_USE_ENABLED"] = v
            self.assertFalse(is_enabled(), f"value {v!r} should disable")

    def test_kill_switch(self):
        os.environ["HERMES_COMPUTER_USE_ENABLED"] = "true"
        self.assertFalse(is_killed())
        gate("left_click")  # works
        set_kill_switch()
        self.assertTrue(is_killed())
        with self.assertRaises(SafetyRefusal):
            gate("left_click")

    def test_redact_image_blanks_region(self):
        try:
            from PIL import Image
            import io
        except ImportError:
            self.skipTest("PIL not installed")
        img = Image.new("RGB", (100, 100), (255, 0, 0))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        out = redact_image(buf.getvalue(), [[10, 10, 50, 50]])
        out_img = Image.open(io.BytesIO(out)).convert("RGB")
        # Pixel inside the redacted region should be black.
        self.assertEqual(out_img.getpixel((30, 30)), (0, 0, 0))
        # Pixel outside should still be red.
        self.assertEqual(out_img.getpixel((80, 80)), (255, 0, 0))

    def test_redact_image_no_regions_passthrough(self):
        try:
            from PIL import Image
            import io
        except ImportError:
            self.skipTest("PIL not installed")
        img = Image.new("RGB", (10, 10), (0, 255, 0))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        original = buf.getvalue()
        self.assertEqual(redact_image(original, []), original)

    def test_log_action_writes_jsonl(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            os.environ["HERMES_HOME"] = tmpdir
            log_action("left_click", {"x": 10, "y": 20}, True)
            log_action("type", {"text": "hi"}, False, error="oops")
            log_path = Path(tmpdir) / "logs" / "computer_use.jsonl"
            self.assertTrue(log_path.exists())
            lines = log_path.read_text().splitlines()
            self.assertEqual(len(lines), 2)
            r1 = json.loads(lines[0])
            self.assertEqual(r1["action"], "left_click")
            self.assertTrue(r1["success"])
            r2 = json.loads(lines[1])
            self.assertEqual(r2["error"], "oops")


# ---------------------------------------------------------------------------
# macOS backend (mocked Quartz)
# ---------------------------------------------------------------------------

class MacOSBackendTests(unittest.TestCase):
    def setUp(self):
        os.environ["HERMES_COMPUTER_USE_ENABLED"] = "true"
        clear_kill_switch()

    def tearDown(self):
        os.environ.pop("HERMES_COMPUTER_USE_ENABLED", None)

    def _stub_quartz(self):
        """Build a MagicMock that quacks like enough of pyobjc Quartz."""
        Q = MagicMock()
        # Mouse event flag constants
        for name in (
            "kCGEventLeftMouseDown", "kCGEventLeftMouseUp", "kCGEventLeftMouseDragged",
            "kCGEventRightMouseDown", "kCGEventRightMouseUp",
            "kCGEventOtherMouseDown", "kCGEventOtherMouseUp",
            "kCGEventMouseMoved", "kCGHIDEventTap", "kCGScrollEventUnitLine",
            "kCGMouseButtonLeft", "kCGMouseButtonRight", "kCGMouseButtonCenter",
            "kCGWindowListOptionOnScreenOnly", "kCGWindowListExcludeDesktopElements",
            "kCGNullWindowID",
        ):
            setattr(Q, name, 0)
        Q.CGMainDisplayID.return_value = 1
        Q.CGDisplayPixelsWide.return_value = 1920
        Q.CGDisplayPixelsHigh.return_value = 1080
        # Cursor location
        loc = MagicMock(); loc.x = 100; loc.y = 200
        Q.CGEventGetLocation.return_value = loc
        Q.CGWindowListCopyWindowInfo.return_value = [
            {"kCGWindowOwnerName": "Safari", "kCGWindowName": "Apple"},
        ]
        return Q

    @patch("tools.computer_use_macos._screenshot_full")
    @patch("tools.computer_use_macos._load_quartz")
    def test_screen_size(self, mock_load, mock_shot):
        mock_load.return_value = self._stub_quartz()
        from tools.computer_use_macos import computer_use_macos_tool
        out = computer_use_macos_tool({"action": "screen_size"})
        self.assertTrue(out["success"])
        self.assertEqual(out["screen"], {"width": 1920, "height": 1080})

    @patch("tools.computer_use_macos._load_quartz")
    def test_cursor_position(self, mock_load):
        mock_load.return_value = self._stub_quartz()
        from tools.computer_use_macos import computer_use_macos_tool
        out = computer_use_macos_tool({"action": "cursor_position"})
        self.assertEqual(out["cursor"], {"x": 100, "y": 200})

    @patch("tools.computer_use_macos._load_quartz")
    def test_active_window(self, mock_load):
        mock_load.return_value = self._stub_quartz()
        from tools.computer_use_macos import computer_use_macos_tool
        out = computer_use_macos_tool({"action": "get_active_window"})
        self.assertEqual(out["active_window"]["app"], "Safari")

    @patch("tools.computer_use_macos._screenshot_full")
    @patch("tools.computer_use_macos._load_quartz")
    def test_screenshot_returns_b64(self, mock_load, mock_shot):
        mock_load.return_value = self._stub_quartz()
        mock_shot.return_value = b"\x89PNG\r\n\x1a\n" + b"x" * 50
        from tools.computer_use_macos import computer_use_macos_tool
        out = computer_use_macos_tool({"action": "screenshot"})
        self.assertTrue(out["success"])
        self.assertEqual(base64.b64decode(out["screenshot_b64"])[:8], b"\x89PNG\r\n\x1a\n")

    @patch("tools.computer_use_macos._load_quartz")
    def test_left_click_posts_two_events(self, mock_load):
        Q = self._stub_quartz()
        mock_load.return_value = Q
        from tools.computer_use_macos import computer_use_macos_tool
        out = computer_use_macos_tool({"action": "left_click", "x": 100, "y": 100})
        self.assertTrue(out["success"], out)
        # CGEventPost called for both mouse-down and mouse-up
        self.assertGreaterEqual(Q.CGEventPost.call_count, 2)

    @patch("tools.computer_use_macos._load_quartz")
    def test_key_combo_uses_cgflags(self, mock_load):
        Q = self._stub_quartz()
        mock_load.return_value = Q
        from tools.computer_use_macos import computer_use_macos_tool
        out = computer_use_macos_tool({"action": "key", "keys": "Cmd+T"})
        self.assertTrue(out["success"], out)
        # CGEventCreateKeyboardEvent called twice (down + up)
        self.assertEqual(Q.CGEventCreateKeyboardEvent.call_count, 2)
        Q.CGEventSetFlags.assert_called()

    @patch("tools.computer_use_macos._load_quartz")
    def test_disabled_env_refuses(self, mock_load):
        os.environ.pop("HERMES_COMPUTER_USE_ENABLED", None)
        mock_load.return_value = self._stub_quartz()
        from tools.computer_use_macos import computer_use_macos_tool
        out = computer_use_macos_tool({"action": "left_click", "x": 100, "y": 100})
        self.assertFalse(out["success"])
        self.assertIn("refused", out["error"])

    @patch("tools.computer_use_macos._load_quartz")
    def test_off_screen_click_rejected(self, mock_load):
        mock_load.return_value = self._stub_quartz()
        from tools.computer_use_macos import computer_use_macos_tool
        out = computer_use_macos_tool({"action": "left_click", "x": 5000, "y": 5000})
        self.assertFalse(out["success"])
        self.assertIn("validation", out["error"])


# ---------------------------------------------------------------------------
# Linux backend (mocked subprocess)
# ---------------------------------------------------------------------------

class LinuxBackendTests(unittest.TestCase):
    def setUp(self):
        os.environ["HERMES_COMPUTER_USE_ENABLED"] = "true"
        clear_kill_switch()

    def tearDown(self):
        os.environ.pop("HERMES_COMPUTER_USE_ENABLED", None)

    def _x11_env(self):
        return {"WAYLAND_DISPLAY": "", "XDG_SESSION_TYPE": "x11"}

    def _wayland_env(self):
        return {"WAYLAND_DISPLAY": "wayland-0", "XDG_SESSION_TYPE": "wayland"}

    @patch.dict(os.environ, {"WAYLAND_DISPLAY": "", "XDG_SESSION_TYPE": "x11"})
    @patch("tools.computer_use_linux.shutil.which")
    @patch("tools.computer_use_linux._run")
    def test_x11_screen_size_from_xdpyinfo(self, mock_run, mock_which):
        mock_which.side_effect = lambda c: f"/usr/bin/{c}" if c in ("xdotool", "scrot", "xdpyinfo") else None
        mock_run.return_value = MagicMock(stdout="dimensions:    1920x1080 pixels\n")
        from tools.computer_use_linux import computer_use_linux_tool
        out = computer_use_linux_tool({"action": "screen_size"})
        self.assertTrue(out["success"], out)
        self.assertEqual(out["screen"], {"width": 1920, "height": 1080})

    @patch.dict(os.environ, {"WAYLAND_DISPLAY": "", "XDG_SESSION_TYPE": "x11"})
    @patch("tools.computer_use_linux.shutil.which")
    @patch("tools.computer_use_linux._run")
    def test_x11_left_click_invokes_xdotool(self, mock_run, mock_which):
        mock_which.side_effect = lambda c: f"/usr/bin/{c}" if c in ("xdotool", "scrot", "xdpyinfo") else None
        mock_run.return_value = MagicMock(stdout="dimensions:    1920x1080 pixels\n")
        from tools.computer_use_linux import computer_use_linux_tool
        out = computer_use_linux_tool({"action": "left_click", "x": 100, "y": 200})
        self.assertTrue(out["success"], out)
        # _run called for screen_size probe + click invocation
        click_calls = [c for c in mock_run.call_args_list if c.args and "xdotool" in c.args[0][0]]
        self.assertTrue(any("mousemove" in str(c) and "click" in str(c) for c in click_calls))

    @patch.dict(os.environ, {"WAYLAND_DISPLAY": "", "XDG_SESSION_TYPE": "x11"})
    @patch("tools.computer_use_linux.shutil.which")
    @patch("tools.computer_use_linux._run")
    def test_x11_screenshot_reads_scrot_output(self, mock_run, mock_which):
        mock_which.side_effect = lambda c: f"/usr/bin/{c}" if c in ("xdotool", "scrot", "xdpyinfo") else None
        mock_run.return_value = MagicMock(stdout="")
        with patch("tools.computer_use_linux.Path") as mock_path:
            mock_path.return_value.read_bytes.return_value = b"\x89PNG\r\n\x1a\nfake"
            mock_path.return_value.unlink = lambda: None
            from tools.computer_use_linux import computer_use_linux_tool
            out = computer_use_linux_tool({"action": "screenshot"})
            self.assertTrue(out["success"], out)
            self.assertEqual(base64.b64decode(out["screenshot_b64"])[:8], b"\x89PNG\r\n\x1a\n")

    @patch.dict(os.environ, {"WAYLAND_DISPLAY": "", "XDG_SESSION_TYPE": "x11"})
    @patch("tools.computer_use_linux.shutil.which")
    @patch("tools.computer_use_linux._run")
    def test_x11_key_combo_lowercases_modifier_string(self, mock_run, mock_which):
        mock_which.side_effect = lambda c: f"/usr/bin/{c}" if c in ("xdotool", "scrot", "xdpyinfo") else None
        mock_run.return_value = MagicMock(stdout="dimensions:    1920x1080 pixels\n")
        from tools.computer_use_linux import computer_use_linux_tool
        out = computer_use_linux_tool({"action": "key", "keys": "Ctrl+Alt+T"})
        self.assertTrue(out["success"], out)
        # the xdotool key argument should be 'ctrl+alt+t'
        key_calls = [c for c in mock_run.call_args_list if c.args and "key" in c.args[0]]
        self.assertTrue(any("ctrl+alt+t" in str(c) for c in key_calls), key_calls)

    @patch.dict(os.environ, {"WAYLAND_DISPLAY": "wayland-0", "XDG_SESSION_TYPE": "wayland"})
    @patch("tools.computer_use_linux.shutil.which")
    @patch("tools.computer_use_linux._run")
    def test_wayland_uses_grim_and_ydotool(self, mock_run, mock_which):
        mock_which.side_effect = lambda c: f"/usr/bin/{c}" if c in ("ydotool", "grim", "wlr-randr") else None
        mock_run.return_value = MagicMock(stdout="HDMI-A-1 \"Mock\"\n  current 1920x1080@60Hz\n")
        from tools.computer_use_linux import computer_use_linux_tool
        out = computer_use_linux_tool({"action": "screen_size"})
        self.assertTrue(out["success"], out)
        # Wayland branch was selected; grim should also be runnable for screenshot
        with patch("tools.computer_use_linux.Path") as mock_path:
            mock_path.return_value.read_bytes.return_value = b"\x89PNG\r\n\x1a\nfake"
            mock_path.return_value.unlink = lambda: None
            out2 = computer_use_linux_tool({"action": "screenshot"})
            self.assertTrue(out2["success"])
            grim_calls = [c for c in mock_run.call_args_list if c.args and "grim" in c.args[0]]
            self.assertTrue(grim_calls, "grim should have been invoked")


# ---------------------------------------------------------------------------
# Windows backend (mocked user32)
# ---------------------------------------------------------------------------

class WindowsBackendTests(unittest.TestCase):
    def setUp(self):
        os.environ["HERMES_COMPUTER_USE_ENABLED"] = "true"
        clear_kill_switch()

    def tearDown(self):
        os.environ.pop("HERMES_COMPUTER_USE_ENABLED", None)

    def _stub_user32(self):
        u = MagicMock()
        u.GetSystemMetrics.side_effect = lambda code: 1920 if code == 0 else 1080
        u.SendInput.side_effect = lambda n, arr, sz: n
        u.GetForegroundWindow.return_value = 0xABCD
        u.GetWindowTextLengthW.return_value = 5
        return u

    @patch("tools.computer_use_windows._load_user32")
    def test_screen_size(self, mock_load):
        mock_load.return_value = self._stub_user32()
        from tools.computer_use_windows import computer_use_windows_tool
        out = computer_use_windows_tool({"action": "screen_size"})
        self.assertTrue(out["success"])
        self.assertEqual(out["screen"], {"width": 1920, "height": 1080})

    @patch("tools.computer_use_windows._load_user32")
    def test_left_click_calls_sendinput(self, mock_load):
        u = self._stub_user32()
        mock_load.return_value = u
        from tools.computer_use_windows import computer_use_windows_tool
        out = computer_use_windows_tool({"action": "left_click", "x": 100, "y": 200})
        self.assertTrue(out["success"], out)
        # _click() makes 2 SendInput calls: one for move, one with [down, up].
        self.assertGreaterEqual(u.SendInput.call_count, 2)

    @patch("tools.computer_use_windows._load_user32")
    def test_key_combo_calls_sendinput(self, mock_load):
        u = self._stub_user32()
        mock_load.return_value = u
        from tools.computer_use_windows import computer_use_windows_tool
        out = computer_use_windows_tool({"action": "key", "keys": "Ctrl+T"})
        self.assertTrue(out["success"], out)
        u.SendInput.assert_called()

    @patch("tools.computer_use_windows._load_user32")
    def test_off_screen_click_rejected(self, mock_load):
        mock_load.return_value = self._stub_user32()
        from tools.computer_use_windows import computer_use_windows_tool
        out = computer_use_windows_tool({"action": "left_click", "x": 5000, "y": 5000})
        self.assertFalse(out["success"])
        self.assertIn("validation", out["error"])


# ---------------------------------------------------------------------------
# Registry integration — registration + check_fn gating
# ---------------------------------------------------------------------------

class RegistryIntegrationTests(unittest.TestCase):
    def test_all_three_register(self):
        # Module imports already happened at file top
        from tools.registry import registry
        names = registry.get_all_tool_names()
        for n in ("computer_use_macos", "computer_use_linux", "computer_use_windows"):
            self.assertIn(n, names, f"{n} not in registry")

    def test_check_fn_off_when_env_unset(self):
        os.environ.pop("HERMES_COMPUTER_USE_ENABLED", None)
        from tools.registry import registry, invalidate_check_fn_cache
        invalidate_check_fn_cache()
        for n in ("computer_use_macos", "computer_use_linux", "computer_use_windows"):
            self.assertFalse(registry.get_entry(n).check_fn(), f"{n} check_fn should be False")

    def test_only_host_os_passes_with_env(self):
        os.environ["HERMES_COMPUTER_USE_ENABLED"] = "true"
        from tools.registry import registry, invalidate_check_fn_cache
        invalidate_check_fn_cache()
        host = sys.platform
        try:
            for tool, expected_platform in [
                ("computer_use_macos", "darwin"),
                ("computer_use_linux", "linux"),
                ("computer_use_windows", "win32"),
            ]:
                check = registry.get_entry(tool).check_fn()
                if host != expected_platform:
                    self.assertFalse(check, f"{tool} should be False on host {host}")
        finally:
            os.environ.pop("HERMES_COMPUTER_USE_ENABLED", None)


if __name__ == "__main__":
    unittest.main()
