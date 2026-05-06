"""Safety surface for the computer_use_* tools.

Three concerns this module owns:

1. **Default-off env gate** — the tool refuses to register unless
   ``HERMES_COMPUTER_USE_ENABLED=true``. This mirrors the gating pattern
   used by issue #15876's containerised proposal and keeps the feature
   inert for users who haven't opted in.
2. **Append-only action log** — every action attempt is JSON-line logged
   to ``$HERMES_HOME/logs/computer_use.jsonl`` with timestamp, action name,
   parameters (without screenshot bytes), and the success bit. Useful for
   post-incident review when an agent does something unexpected.
3. **Screenshot redaction** — caller-supplied rectangles are blanked in
   the returned PNG before it reaches the model. The default ships with
   redaction OFF; the model has to actively request it via the
   ``redact_regions`` arg or the operator can wire a SOUL.md rule that
   always redacts a known sensitive zone (e.g. password manager popup).

The kill-switch hotkey hook is left as a flag-poll helper here; OS
backends that can register a global hotkey should set the flag from
their hotkey handler. A polling check is good enough — the worst case
is one in-flight action completes before the next one is blocked.
"""

from __future__ import annotations

import io
import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

logger = logging.getLogger(__name__)


COMPUTER_USE_ENV = "HERMES_COMPUTER_USE_ENABLED"


def is_enabled() -> bool:
    """True iff the operator has explicitly opted in via env var.

    Accepts ``true``/``1``/``yes`` (case-insensitive). Anything else
    (unset, ``false``, ``0``, junk) returns False.
    """
    val = os.environ.get(COMPUTER_USE_ENV, "").strip().lower()
    return val in {"true", "1", "yes", "on"}


# ---------------------------------------------------------------------------
# Kill-switch flag (process-global). OS backends call set_kill_switch() from
# their hotkey handler; handler() polls is_killed() before every action.
# ---------------------------------------------------------------------------

_kill_switch = threading.Event()


def is_killed() -> bool:
    return _kill_switch.is_set()


def set_kill_switch() -> None:
    _kill_switch.set()
    logger.warning("computer_use kill switch ENGAGED — subsequent actions will refuse")


def clear_kill_switch() -> None:
    _kill_switch.clear()


# ---------------------------------------------------------------------------
# Append-only action log.
# ---------------------------------------------------------------------------

def _log_path() -> Path:
    home = os.environ.get("HERMES_HOME", os.path.expanduser("~/.hermes"))
    log_dir = Path(home) / "logs"
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    return log_dir / "computer_use.jsonl"


_log_lock = threading.Lock()


def log_action(action: str, params: Dict[str, Any], success: bool, error: Optional[str] = None) -> None:
    """Append one JSON line describing an action attempt.

    Strips screenshot bytes / large blobs from logged params so the log
    file stays browsable.
    """
    safe_params = {
        k: ("<bytes>" if isinstance(v, (bytes, bytearray)) else v)
        for k, v in params.items()
        if k not in {"screenshot_b64"}
    }
    record = {
        "ts": time.time(),
        "action": action,
        "params": safe_params,
        "success": success,
    }
    if error:
        record["error"] = error[:500]
    line = json.dumps(record, ensure_ascii=False, default=str)
    try:
        with _log_lock:
            with _log_path().open("a", encoding="utf-8") as f:
                f.write(line + "\n")
    except OSError as e:
        logger.debug("could not write computer_use log: %s", e)


# ---------------------------------------------------------------------------
# Screenshot redaction. Pure-Python via PIL — already a Hermes dependency.
# ---------------------------------------------------------------------------

def redact_image(png_bytes: bytes, regions: Iterable[List[int]]) -> bytes:
    """Return a copy of ``png_bytes`` with each region rectangle filled black.

    Regions are ``[x1, y1, x2, y2]`` in image pixel space. Coordinates that
    fall outside the image are clamped silently. If PIL isn't importable we
    fall back to returning the original bytes (logged as a warning) so a
    missing dependency degrades to "no redaction" rather than "no screenshot".
    """
    try:
        from PIL import Image, ImageDraw  # type: ignore
    except ImportError:
        logger.warning("PIL unavailable — redaction skipped, returning unredacted screenshot")
        return png_bytes

    if not regions:
        return png_bytes

    img = Image.open(io.BytesIO(png_bytes)).convert("RGB")
    draw = ImageDraw.Draw(img)
    w, h = img.size
    for r in regions:
        if len(r) != 4:
            continue
        x1, y1, x2, y2 = (int(v) for v in r)
        x1 = max(0, min(x1, w))
        y1 = max(0, min(y1, h))
        x2 = max(0, min(x2, w))
        y2 = max(0, min(y2, h))
        if x2 <= x1 or y2 <= y1:
            continue
        draw.rectangle([x1, y1, x2, y2], fill=(0, 0, 0))

    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Common pre-action gate. Each OS backend calls this at the top of every
# action handler.
# ---------------------------------------------------------------------------

class SafetyRefusal(Exception):
    """Raised by gate() when an action must be refused for safety reasons."""


def gate(action: str) -> None:
    """Refuse the action if safety conditions aren't met."""
    if not is_enabled():
        raise SafetyRefusal(
            f"{COMPUTER_USE_ENV} is not set — computer_use is disabled. "
            f"Set the env var to true to opt in."
        )
    if is_killed():
        raise SafetyRefusal(
            "computer_use kill switch is engaged. Call clear_kill_switch() "
            "or restart the gateway to resume."
        )
