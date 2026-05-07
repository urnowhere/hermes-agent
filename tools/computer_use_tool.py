#!/usr/bin/env python3
"""Desktop-level computer-use tool for Hermes Agent."""

import base64
import json
import logging
import os
import shlex
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any, Dict

from tools.registry import registry, tool_error, tool_result

logger = logging.getLogger(__name__)

SCROT_BIN = "/usr/bin/scrot"
IMPORT_BIN = "/usr/bin/import"
CONVERT_BIN = "/usr/bin/convert"
BASE64_BIN = "/usr/bin/base64"
XDO_TOOL_BIN = "/usr/bin/xdotool"
DEFAULT_XAUTHORITY = "/home/hermes/.Xauthority"


def _get_display() -> str:
    return os.environ.get("COMPUTER_USE_DISPLAY", os.environ.get("DISPLAY", ":1"))


def _get_xauthority() -> str:
    return os.environ.get("COMPUTER_USE_XAUTHORITY", DEFAULT_XAUTHORITY)


def _get_desktop_home() -> str:
    return os.environ.get("COMPUTER_USE_HOME", "/home/hermes")


def _desktop_env_exports() -> str:
    display = shlex.quote(_get_display())
    xauthority = shlex.quote(_get_xauthority())
    home = shlex.quote(_get_desktop_home())
    return f"export HOME={home} DISPLAY={display} XAUTHORITY={xauthority}; "


def _get_screenshot_dir() -> Path:
    custom = os.environ.get("COMPUTER_USE_SCREENSHOT_DIR")
    if custom:
        path = Path(custom)
    else:
        try:
            from hermes_constants import get_hermes_home

            path = get_hermes_home() / "cache" / "computer_screenshots"
        except ImportError:
            path = Path.home() / ".hermes" / "cache" / "computer_screenshots"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _get_remote_screenshot_dir() -> str:
    return os.environ.get(
        "COMPUTER_USE_REMOTE_SCREENSHOT_DIR",
        "/home/hermes/.hermes/cache/computer_screenshots",
    )


def _get_shared_screenshot_dir() -> Path | None:
    custom = os.environ.get("COMPUTER_USE_SHARED_SCREENSHOT_DIR")
    candidates = []
    if custom:
        candidates.append(Path(custom))
    candidates.append(Path("/var/lib/docker/volumes/hermes-desktop_screenshots/_data"))
    candidates.append(Path("/var/lib/docker/volumes/hermes-desktop-build_screenshots/_data"))
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def image_to_data_uri(filepath: str | Path) -> str:
    image_path = Path(filepath)
    b64_image = base64.b64encode(image_path.read_bytes()).decode("utf-8")
    return f"data:image/png;base64,{b64_image}"


def _get_vnc_url() -> str:
    return os.environ.get("COMPUTER_USE_VNC_URL", "")


def _get_backend() -> str:
    return os.environ.get("COMPUTER_USE_BACKEND", "terminal").strip().lower()


_SCREENSHOT_MAX_AGE = 3600
_last_cleanup_time = 0.0


def _cleanup_old_screenshots() -> None:
    global _last_cleanup_time
    now = time.time()
    if now - _last_cleanup_time < 300:
        return
    _last_cleanup_time = now
    try:
        for screenshot in _get_screenshot_dir().glob("*.png"):
            if now - screenshot.stat().st_mtime > _SCREENSHOT_MAX_AGE:
                screenshot.unlink(missing_ok=True)
    except Exception as exc:
        logger.debug("Screenshot cleanup error: %s", exc)


def _terminal_handler():
    """Return the terminal registry handler across Hermes versions."""
    from tools import terminal_tool

    handler = getattr(terminal_tool, "handle_terminal", None)
    if handler is not None:
        return handler
    return getattr(terminal_tool, "_handle_terminal")


def _is_remote_backend() -> bool:
    return _get_backend() in ("terminal", "ssh", "direct_ssh")


def _ssh_exec(command: str, timeout: int = 15) -> Dict[str, Any]:
    """Execute a desktop command over SSH, bypassing terminal approval prompts."""
    host = os.environ.get("TERMINAL_SSH_HOST", "localhost")
    port = os.environ.get("TERMINAL_SSH_PORT", "2222")
    user = os.environ.get("TERMINAL_SSH_USER", "hermes")
    remote_command = f"{_desktop_env_exports()}{command}"
    ssh_cmd = [
        "ssh",
        "-o",
        "BatchMode=yes",
        "-o",
        "StrictHostKeyChecking=accept-new",
        "-o",
        "ConnectTimeout=5",
        "-p",
        str(port),
        f"{user}@{host}",
        remote_command,
    ]
    last_error = ""
    for attempt in range(5):
        try:
            proc = subprocess.run(
                ssh_cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            stderr = proc.stderr.strip()
            if proc.returncode == 0:
                return {"success": True, "output": proc.stdout.strip(), "error": stderr}
            last_error = stderr
            transient = any(
                marker in stderr.lower()
                for marker in (
                    "connection reset",
                    "connection refused",
                    "connection timed out",
                    "kex_exchange_identification",
                    "connection closed",
                )
            )
            if not transient or attempt == 4:
                return {"success": False, "output": proc.stdout.strip(), "error": stderr}
            time.sleep(1 + attempt)
        except subprocess.TimeoutExpired:
            last_error = f"SSH command timed out after {timeout}s"
            if attempt == 4:
                return {"success": False, "output": "", "error": last_error}
            time.sleep(1 + attempt)
        except Exception as exc:
            last_error = str(exc)
            if attempt == 4:
                return {"success": False, "output": "", "error": last_error}
            time.sleep(1 + attempt)
    return {"success": False, "output": "", "error": last_error}


def _run_on_desktop(command: str, timeout: int = 15) -> Dict[str, Any]:
    backend = _get_backend()
    desktop_command = f"{_desktop_env_exports()}{command}"

    if backend in ("ssh", "direct_ssh"):
        return _ssh_exec(command, timeout=timeout)

    full_cmd = f"/bin/bash -lc {shlex.quote(desktop_command)}"
    if backend == "terminal":
        try:
            result_str = _terminal_handler()({"command": full_cmd, "timeout": timeout})
            result = json.loads(result_str) if isinstance(result_str, str) else result_str
            exit_code = result.get("exit_code")
            return {
                "success": exit_code == 0 if exit_code is not None else not result.get("error"),
                "output": (result.get("output") or result.get("stdout") or "").strip(),
                "error": result.get("error") or result.get("stderr") or "",
            }
        except Exception as exc:
            return {"success": False, "output": "", "error": str(exc)}

    try:
        env = os.environ.copy()
        env["DISPLAY"] = _get_display()
        env["XAUTHORITY"] = _get_xauthority()
        env["HOME"] = _get_desktop_home()
        proc = subprocess.run(
            ["bash", "-c", desktop_command],
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
        )
        return {
            "success": proc.returncode == 0,
            "output": proc.stdout.strip(),
            "error": proc.stderr.strip(),
        }
    except subprocess.TimeoutExpired:
        return {"success": False, "output": "", "error": f"Command timed out after {timeout}s"}
    except Exception as exc:
        return {"success": False, "output": "", "error": str(exc)}


def _annotate_remote_screenshot(path: str) -> Dict[str, Any]:
    """Create a coordinate-grid copy of a screenshot on the desktop backend."""
    source = Path(path)
    annotated = source.with_name(f"{source.stem}_annotated{source.suffix}")
    draw_parts = []
    for x in range(0, 2001, 200):
        draw_parts.append(f"line {x},0 {x},2000")
        if x > 0:
            draw_parts.append(f'text {x + 4},24 "x={x}"')
    for y in range(0, 2001, 200):
        draw_parts.append(f"line 0,{y} 3000,{y}")
        if y > 0:
            draw_parts.append(f'text 4,{y - 6} "y={y}"')

    command = (
        f"{CONVERT_BIN} {shlex.quote(str(source))} "
        "-stroke 'rgba(255,0,0,0.55)' -strokewidth 1 "
        "-fill 'rgba(255,255,255,0.85)' -font DejaVu-Sans -pointsize 18 "
        f"-draw {shlex.quote(' '.join(draw_parts))} "
        f"{shlex.quote(str(annotated))}"
    )
    result = _run_on_desktop(command, timeout=15)
    if not result["success"]:
        return {"success": False, "error": result["error"] or "Failed to annotate screenshot"}
    return {"success": True, "path": str(annotated)}


def _take_screenshot(annotate: bool = False) -> Dict[str, Any]:
    _cleanup_old_screenshots()
    screenshot_dir = _get_screenshot_dir()
    filename = f"desktop_{uuid.uuid4().hex[:10]}.png"
    if _is_remote_backend():
        remote_dir = _get_remote_screenshot_dir()
        remote_path = f"{remote_dir}/{filename}"
    else:
        remote_path = str(screenshot_dir / filename)

    result = _run_on_desktop(f"mkdir -p '{Path(remote_path).parent}' && {SCROT_BIN} -o '{remote_path}'", timeout=10)
    if not result["success"]:
        result = _run_on_desktop(f"mkdir -p '{Path(remote_path).parent}' && {IMPORT_BIN} -window root '{remote_path}'", timeout=10)
    if not result["success"]:
        return {"success": False, "error": f"Screenshot failed: {result['error']}"}

    if annotate:
        annotated = _annotate_remote_screenshot(remote_path)
        if annotated["success"]:
            remote_path = annotated["path"]
            filename = Path(remote_path).name
        else:
            logger.debug("Screenshot annotation failed: %s", annotated["error"])
            return {"success": False, "error": f"Screenshot annotation failed: {annotated['error']}"}

    if _is_remote_backend():
        shared_dir = _get_shared_screenshot_dir()
        if shared_dir is not None:
            shared_path = shared_dir / filename
            if shared_path.exists():
                local_path = screenshot_dir / filename
                local_path.write_bytes(shared_path.read_bytes())
                return {"success": True, "path": str(local_path), "remote_path": str(remote_path)}

        read_result = _run_on_desktop(f"{BASE64_BIN} '{remote_path}'", timeout=15)
        if not read_result["success"] or not read_result["output"]:
            return {"success": False, "error": f"Failed to retrieve screenshot: {read_result['error']}"}
        local_path = screenshot_dir / filename
        try:
            local_path.write_bytes(base64.b64decode(read_result["output"]))
        except Exception as exc:
            return {"success": False, "error": f"Failed to decode screenshot: {exc}"}
        return {"success": True, "path": str(local_path), "remote_path": str(remote_path)}

    return {"success": True, "path": str(remote_path)}


def _analyze_screenshot(screenshot_path: str, question: str) -> str:
    try:
        from agent.auxiliary_client import call_llm
    except ImportError:
        return "Vision analysis unavailable: auxiliary_client not found."

    try:
        image_data_uri = image_to_data_uri(screenshot_path)
    except Exception as exc:
        return f"Failed to read screenshot: {exc}"

    messages = [
        {
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": (
                        f"{question}\n\n"
                        "You are looking at a desktop screenshot. Describe visible windows, text, "
                        "buttons, and approximate coordinates for useful clickable targets."
                    ),
                },
                {
                    "type": "image_url",
                    "image_url": {"url": image_data_uri, "detail": "high"},
                },
            ],
        }
    ]

    try:
        response = call_llm(task="vision", messages=messages, max_tokens=1500)
        if hasattr(response, "choices") and response.choices:
            return response.choices[0].message.content or "No analysis returned."
        if isinstance(response, str):
            return response
        return "Vision model returned empty response."
    except Exception as exc:
        return f"Vision analysis failed: {exc}"


def _click(x: int, y: int, button: int = 1) -> Dict[str, Any]:
    return _run_on_desktop(f"{XDO_TOOL_BIN} mousemove {x} {y} && {XDO_TOOL_BIN} click {button}")


def _double_click(x: int, y: int) -> Dict[str, Any]:
    return _run_on_desktop(f"{XDO_TOOL_BIN} mousemove {x} {y} && {XDO_TOOL_BIN} click --repeat 2 --delay 100 1")


def _type_text(text: str) -> Dict[str, Any]:
    safe_text = shlex.quote(text)
    return _run_on_desktop(f"{XDO_TOOL_BIN} type --clearmodifiers --delay 12 {safe_text}")


def _press_key(key: str) -> Dict[str, Any]:
    return _run_on_desktop(f"{XDO_TOOL_BIN} key --clearmodifiers {shlex.quote(key)}")


def _scroll(direction: str, amount: int = 3, x: int | None = None, y: int | None = None) -> Dict[str, Any]:
    button = 4 if direction == "up" else 5
    if x is not None and y is not None:
        cmd = f"{XDO_TOOL_BIN} mousemove {x} {y} && {XDO_TOOL_BIN} click --repeat {amount} --delay 50 {button}"
    else:
        cmd = f"{XDO_TOOL_BIN} click --repeat {amount} --delay 50 {button}"
    return _run_on_desktop(cmd)


def _get_cursor_position() -> Dict[str, Any]:
    result = _run_on_desktop(f"{XDO_TOOL_BIN} getmouselocation --shell")
    if result["success"]:
        pos = {}
        for line in result["output"].splitlines():
            if "=" in line:
                key, value = line.split("=", 1)
                pos[key.strip()] = value.strip()
        return {"success": True, "x": int(pos.get("X", 0)), "y": int(pos.get("Y", 0))}
    return result


def _get_screen_size() -> Dict[str, Any]:
    result = _run_on_desktop(f"{XDO_TOOL_BIN} getdisplaygeometry")
    if result["success"]:
        parts = result["output"].split()
        if len(parts) >= 2:
            return {"success": True, "width": int(parts[0]), "height": int(parts[1])}
    return {"success": False, "error": "Could not determine screen size"}


def _move_mouse(x: int, y: int) -> Dict[str, Any]:
    return _run_on_desktop(f"{XDO_TOOL_BIN} mousemove {x} {y}")


def _drag(start_x: int, start_y: int, end_x: int, end_y: int) -> Dict[str, Any]:
    return _run_on_desktop(
        f"{XDO_TOOL_BIN} mousemove {start_x} {start_y} mousedown 1 "
        f"{XDO_TOOL_BIN} mousemove --sync {end_x} {end_y} mouseup 1"
    )


def handle_computer_use(args: dict, **kwargs) -> str:
    action = args.get("action", "screenshot")

    if action == "screenshot":
        question = args.get("question", "Describe what you see on the screen.")
        annotate = bool(args.get("annotate", False))
        screenshot = _take_screenshot(annotate=annotate)
        if not screenshot["success"]:
            return tool_error(screenshot["error"])

        result_data: dict = {"screenshot_path": screenshot["path"]}
        if annotate:
            result_data["annotated_screenshot_path"] = screenshot["path"]
            result_data["annotation_note"] = "Annotated grid lines are spaced every 200 pixels for coordinate targeting."
        if "remote_path" in screenshot:
            result_data["remote_path"] = screenshot["remote_path"]
        if _get_vnc_url():
            result_data["vnc_url"] = _get_vnc_url()

        analysis = _analyze_screenshot(screenshot["path"], question)
        if analysis:
            result_data["analysis"] = analysis

        return tool_result(result_data)

    if action == "click":
        x, y = args.get("x"), args.get("y")
        if x is None or y is None:
            return tool_error("click requires 'x' and 'y' coordinates")
        button = args.get("button", "left")
        result = _click(int(x), int(y), {"left": 1, "middle": 2, "right": 3}.get(button, 1))
        return tool_result(success=True, action="click", x=x, y=y, button=button) if result["success"] else tool_error(result["error"])

    if action == "double_click":
        x, y = args.get("x"), args.get("y")
        if x is None or y is None:
            return tool_error("double_click requires 'x' and 'y' coordinates")
        result = _double_click(int(x), int(y))
        return tool_result(success=True, action="double_click", x=x, y=y) if result["success"] else tool_error(result["error"])

    if action == "type":
        text = args.get("text", "")
        if not text:
            return tool_error("type requires 'text'")
        result = _type_text(text)
        return tool_result(success=True, action="type", length=len(text)) if result["success"] else tool_error(result["error"])

    if action == "key":
        key = args.get("key", "")
        if not key:
            return tool_error("key requires 'key'")
        result = _press_key(key)
        return tool_result(success=True, action="key", key=key) if result["success"] else tool_error(result["error"])

    if action == "scroll":
        direction = args.get("direction", "down")
        amount = int(args.get("amount", 3))
        sx = int(args["x"]) if args.get("x") is not None else None
        sy = int(args["y"]) if args.get("y") is not None else None
        result = _scroll(direction, amount, x=sx, y=sy)
        return tool_result(success=True, action="scroll", direction=direction, amount=amount) if result["success"] else tool_error(result["error"])

    if action == "cursor_position":
        result = _get_cursor_position()
        return tool_result(x=result["x"], y=result["y"]) if result["success"] else tool_error(result.get("error", "Failed to get cursor position"))

    if action == "screen_size":
        result = _get_screen_size()
        return tool_result(width=result["width"], height=result["height"]) if result["success"] else tool_error(result.get("error", "Failed to get screen size"))

    if action == "move":
        x, y = args.get("x"), args.get("y")
        if x is None or y is None:
            return tool_error("move requires 'x' and 'y' coordinates")
        result = _move_mouse(int(x), int(y))
        return tool_result(success=True, action="move", x=x, y=y) if result["success"] else tool_error(result["error"])

    if action == "drag":
        required = [args.get("start_x"), args.get("start_y"), args.get("end_x"), args.get("end_y")]
        if any(value is None for value in required):
            return tool_error("drag requires start_x, start_y, end_x, end_y")
        result = _drag(int(required[0]), int(required[1]), int(required[2]), int(required[3]))
        return tool_result(success=True, action="drag") if result["success"] else tool_error(result["error"])

    if action == "vnc_url":
        vnc_url = _get_vnc_url()
        return tool_result(vnc_url=vnc_url) if vnc_url else tool_error("No VNC URL configured. Set COMPUTER_USE_VNC_URL.")

    return tool_error(
        "Unknown action: "
        f"{action}. Valid: screenshot, click, double_click, type, key, scroll, "
        "cursor_position, screen_size, move, drag, vnc_url"
    )


def _check_computer_use() -> bool:
    try:
        return os.environ.get("COMPUTER_USE_ENABLED", "").lower() in ("true", "1", "yes")
    except Exception:
        return False


registry.register(
    name="computer_use",
    toolset="computer_use",
    schema={
        "name": "computer_use",
        "description": (
            "Control a desktop computer at the OS level: see the screen, click, type, "
            "scroll, and press keys. Always start with action='screenshot'."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": [
                        "screenshot",
                        "click",
                        "double_click",
                        "type",
                        "key",
                        "scroll",
                        "cursor_position",
                        "screen_size",
                        "move",
                        "drag",
                        "vnc_url",
                    ],
                },
                "question": {"type": "string"},
                "annotate": {
                    "type": "boolean",
                    "description": "For screenshot: overlay a coordinate grid on the image when precise clicking is needed.",
                },
                "x": {"type": "integer"},
                "y": {"type": "integer"},
                "button": {"type": "string", "enum": ["left", "middle", "right"]},
                "text": {"type": "string"},
                "key": {"type": "string"},
                "direction": {"type": "string", "enum": ["up", "down"]},
                "amount": {"type": "integer"},
                "start_x": {"type": "integer"},
                "start_y": {"type": "integer"},
                "end_x": {"type": "integer"},
                "end_y": {"type": "integer"},
            },
            "required": ["action"],
        },
    },
    handler=handle_computer_use,
    check_fn=_check_computer_use,
    description="Control a desktop computer at OS level",
    emoji="🖥️",
)
