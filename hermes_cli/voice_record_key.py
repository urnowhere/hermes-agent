"""Translate ``voice.record_key`` config values into prompt_toolkit key bindings.

The CLI accepts hotkeys in a friendly ``ctrl+b`` / ``alt+space`` form.
prompt_toolkit expects its own spelling (``c-b``, plus ``escape``-prefix
sequences for Alt-modified keys).  This module is the single bridge.

Kept free of prompt_toolkit imports so its tests stay cheap.
"""

from __future__ import annotations

import logging
from typing import Tuple

logger = logging.getLogger(__name__)

DEFAULT = "ctrl+b"

_SPECIAL = {
    "space": "Space",
    "enter": "Enter",
    "tab": "Tab",
    "escape": "Esc",
    "backspace": "Backspace",
}


def _pretty(key: str) -> str:
    return _SPECIAL.get(key, key.upper() if len(key) == 1 else key.title())


def parse(raw_key: str) -> Tuple[Tuple[str, ...], str]:
    """Return ``(prompt_toolkit_keys, display_label)`` for *raw_key*.

    Supported shapes:

    - ``key`` (single key, e.g. ``"space"``)
    - ``ctrl+key``  → ``("c-key",)`` displayed as ``Ctrl+Key``
    - ``shift+key`` → ``("s-key",)`` displayed as ``Shift+Key``
    - ``alt+key`` / ``meta+key`` → ``("escape", key)`` displayed as
      ``Alt+Key``.  prompt_toolkit treats Alt as an Escape-prefix
      sequence rather than a single ``a-`` key, which is why the original
      ``"a-x"`` translation crashed at startup (#11387).

    Raises ``ValueError`` on empty input or unsupported modifiers.
    """
    parts = tuple(p for p in (raw_key or "").strip().lower().split("+") if p)
    if not parts:
        raise ValueError("voice.record_key is empty")
    if len(parts) == 1:
        key = parts[0]
        return (key,), _pretty(key)
    if len(parts) == 2:
        modifier, key = parts
        if modifier == "ctrl":
            return (f"c-{key}",), f"Ctrl+{_pretty(key)}"
        if modifier == "shift":
            return (f"s-{key}",), f"Shift+{_pretty(key)}"
        if modifier in ("alt", "meta"):
            return ("escape", key), f"Alt+{_pretty(key)}"
    raise ValueError(f"unsupported voice.record_key {raw_key!r}")


def resolve() -> Tuple[Tuple[str, ...], str]:
    """Load ``voice.record_key`` from config; fall back to the default on any error."""
    raw_key = DEFAULT
    try:
        from hermes_cli.config import load_config

        raw_key = (load_config() or {}).get("voice", {}).get("record_key", DEFAULT)
        return parse(raw_key)
    except Exception as exc:
        logger.warning(
            "Invalid voice.record_key %r; falling back to %s: %s",
            raw_key,
            DEFAULT,
            exc,
        )
        return parse(DEFAULT)
