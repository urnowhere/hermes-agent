"""Per-OS key-combo string parser.

Models reach for a familiar grammar when typing keys: ``Cmd+Tab`` on macOS,
``ctrl+alt+t`` on Linux, ``win+s`` on Windows. We parse the same
``mod+mod+key`` string into per-OS native primitives so each backend can
dispatch with the right call:

* macOS    → list of ``(modifier_flag, keycode)`` for ``CGEventCreateKeyboardEvent``
* Linux X11 → xdotool key string (``"ctrl+shift+t"``, lowercase canonical)
* Linux Wayland → ydotool key code sequence (Linux input event codes)
* Windows  → list of Win32 virtual-key codes for ``SendInput``

The grammar is forgiving: case-insensitive modifier names, both ``+`` and
``-`` accepted as separators, and the special token ``Return``/``Enter``
maps to the same key on each platform. Unknown keys raise ``KeyParseError``
with a helpful suggestion list.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Set, Tuple

KeyParseError = ValueError


# ---------------------------------------------------------------------------
# Canonical modifier names — all lowercase. Aliases collapse to the same
# canonical token so "Cmd"/"command"/"meta" all mean the same modifier.
# ---------------------------------------------------------------------------

_MODIFIER_ALIASES: Dict[str, str] = {
    "ctrl": "ctrl", "control": "ctrl",
    "shift": "shift",
    "alt": "alt", "option": "alt", "opt": "alt",
    "cmd": "cmd", "command": "cmd", "meta": "cmd",
    "win": "cmd", "windows": "cmd", "super": "cmd",
    "fn": "fn",
}

CANONICAL_MODIFIERS: Set[str] = {"ctrl", "shift", "alt", "cmd", "fn"}


# ---------------------------------------------------------------------------
# Canonical key names — lowercase. Cover the keys models actually use:
# letters, digits, function keys, navigation, editing, and a few common
# whitespace/symbol names.
# ---------------------------------------------------------------------------

_KEY_ALIASES: Dict[str, str] = {
    "return": "return", "enter": "return",
    "esc": "escape", "escape": "escape",
    "tab": "tab",
    "space": "space", "spacebar": "space",
    "backspace": "backspace", "bksp": "backspace",
    "delete": "delete", "del": "delete",
    "up": "up", "down": "down", "left": "left", "right": "right",
    "home": "home", "end": "end",
    "pageup": "pageup", "pgup": "pageup",
    "pagedown": "pagedown", "pgdn": "pagedown",
    "insert": "insert", "ins": "insert",
    "capslock": "capslock", "caps": "capslock",
    "printscreen": "printscreen", "prtsc": "printscreen",
    "minus": "minus", "-": "minus",
    "equals": "equals", "=": "equals",
    "comma": "comma", ",": "comma",
    "period": "period", ".": "period",
    "slash": "slash", "/": "slash",
    "backslash": "backslash", "\\": "backslash",
    "semicolon": "semicolon", ";": "semicolon",
    "quote": "quote", "'": "quote",
    "leftbracket": "leftbracket", "[": "leftbracket",
    "rightbracket": "rightbracket", "]": "rightbracket",
    "backtick": "backtick", "`": "backtick",
}

# Function keys F1-F24
for _i in range(1, 25):
    _KEY_ALIASES[f"f{_i}"] = f"f{_i}"

# Single letters and digits — the canonical form is the lowercase character.
for _c in "abcdefghijklmnopqrstuvwxyz0123456789":
    _KEY_ALIASES[_c] = _c


@dataclass
class ParsedKey:
    """Canonical form of a parsed key combo.

    Attributes
    ----------
    modifiers
        Subset of {"ctrl", "shift", "alt", "cmd", "fn"} — order is irrelevant.
    key
        Canonical lowercase key name (e.g. "t", "tab", "f5", "return").
    raw
        Original input string, kept for error messages and logging.
    """

    modifiers: Set[str] = field(default_factory=set)
    key: str = ""
    raw: str = ""


def parse_combo(combo: str) -> ParsedKey:
    """Parse ``"Cmd+Shift+T"`` (or ``"cmd-shift-t"``) into a ParsedKey.

    Raises KeyParseError on unknown tokens with the offending part included.
    """
    if not isinstance(combo, str) or not combo.strip():
        raise KeyParseError("empty key combo")

    parts = re.split(r"[+\-]", combo.strip())
    parts = [p.strip() for p in parts if p.strip()]
    if not parts:
        raise KeyParseError(f"could not parse key combo {combo!r}")

    parsed = ParsedKey(raw=combo)
    # Last token is the key; everything before is modifiers.
    *mods, last = parts
    for m in mods:
        canon = _MODIFIER_ALIASES.get(m.lower())
        if canon is None:
            raise KeyParseError(
                f"unknown modifier {m!r} in {combo!r}; "
                f"valid: {sorted(CANONICAL_MODIFIERS)}"
            )
        parsed.modifiers.add(canon)

    canon_key = _KEY_ALIASES.get(last.lower())
    if canon_key is None:
        if len(last) == 1:
            # Allow any single printable character; backends type it literally.
            canon_key = last.lower()
        else:
            raise KeyParseError(
                f"unknown key {last!r} in {combo!r}; "
                f"recognised tokens include letters, digits, function keys, "
                f"and navigation/editing keys (return, tab, escape, etc.)"
            )
    parsed.key = canon_key
    return parsed


# ---------------------------------------------------------------------------
# macOS — Quartz keycodes + modifier flags.
#
# Keycodes come from /System/Library/Frameworks/Carbon.framework
# (kVK_ANSI_*). We hardcode the mapping rather than depend on Carbon to
# keep this module importable on non-macOS hosts.
# ---------------------------------------------------------------------------

# kCGEventFlagMask* values from Quartz/CGEventTypes.h
MAC_FLAG = {
    "shift": 0x00020000,
    "ctrl":  0x00040000,
    "alt":   0x00080000,
    "cmd":   0x00100000,
    "fn":    0x00800000,
}

MAC_KEYCODE: Dict[str, int] = {
    "a": 0x00, "s": 0x01, "d": 0x02, "f": 0x03, "h": 0x04, "g": 0x05,
    "z": 0x06, "x": 0x07, "c": 0x08, "v": 0x09, "b": 0x0B, "q": 0x0C,
    "w": 0x0D, "e": 0x0E, "r": 0x0F, "y": 0x10, "t": 0x11,
    "1": 0x12, "2": 0x13, "3": 0x14, "4": 0x15, "6": 0x16, "5": 0x17,
    "equals": 0x18, "9": 0x19, "7": 0x1A, "minus": 0x1B, "8": 0x1C, "0": 0x1D,
    "rightbracket": 0x1E, "o": 0x1F, "u": 0x20, "leftbracket": 0x21,
    "i": 0x22, "p": 0x23, "l": 0x25, "j": 0x26, "quote": 0x27, "k": 0x28,
    "semicolon": 0x29, "backslash": 0x2A, "comma": 0x2B, "slash": 0x2C,
    "n": 0x2D, "m": 0x2E, "period": 0x2F, "backtick": 0x32,
    "return": 0x24, "tab": 0x30, "space": 0x31, "backspace": 0x33,
    "escape": 0x35, "capslock": 0x39,
    "left": 0x7B, "right": 0x7C, "down": 0x7D, "up": 0x7E,
    "home": 0x73, "end": 0x77, "pageup": 0x74, "pagedown": 0x79,
    "delete": 0x75,
    "f1": 0x7A, "f2": 0x78, "f3": 0x63, "f4": 0x76, "f5": 0x60, "f6": 0x61,
    "f7": 0x62, "f8": 0x64, "f9": 0x65, "f10": 0x6D, "f11": 0x67, "f12": 0x6F,
    "f13": 0x69, "f14": 0x6B, "f15": 0x71, "f16": 0x6A, "f17": 0x40, "f18": 0x4F,
    "f19": 0x50, "f20": 0x5A,
}


def to_macos(parsed: ParsedKey) -> Tuple[int, int]:
    """Return ``(flags, keycode)`` for ``CGEventCreateKeyboardEvent``."""
    flags = 0
    for m in parsed.modifiers:
        flags |= MAC_FLAG.get(m, 0)
    keycode = MAC_KEYCODE.get(parsed.key)
    if keycode is None:
        raise KeyParseError(f"no macOS keycode for {parsed.key!r}")
    return flags, keycode


# ---------------------------------------------------------------------------
# Linux X11 — xdotool string. Modifiers and key names map directly.
# ---------------------------------------------------------------------------

XDOTOOL_MOD: Dict[str, str] = {
    "ctrl": "ctrl",
    "shift": "shift",
    "alt": "alt",
    "cmd": "super",  # macOS Cmd → Linux Super (Win) key
    "fn": "",         # Linux has no separate Fn modifier; drop silently
}

XDOTOOL_KEY: Dict[str, str] = {
    "return": "Return", "tab": "Tab", "escape": "Escape", "space": "space",
    "backspace": "BackSpace", "delete": "Delete",
    "up": "Up", "down": "Down", "left": "Left", "right": "Right",
    "home": "Home", "end": "End", "pageup": "Prior", "pagedown": "Next",
    "insert": "Insert", "capslock": "Caps_Lock", "printscreen": "Print",
    "minus": "minus", "equals": "equal", "comma": "comma", "period": "period",
    "slash": "slash", "backslash": "backslash", "semicolon": "semicolon",
    "quote": "apostrophe", "leftbracket": "bracketleft",
    "rightbracket": "bracketright", "backtick": "grave",
}
for _i in range(1, 25):
    XDOTOOL_KEY[f"f{_i}"] = f"F{_i}"


def to_xdotool(parsed: ParsedKey) -> str:
    """Return an xdotool ``key`` argument like ``ctrl+shift+t``."""
    parts: List[str] = []
    for m in ("ctrl", "alt", "shift", "cmd"):  # canonical order
        if m in parsed.modifiers:
            x = XDOTOOL_MOD.get(m, "")
            if x:
                parts.append(x)
    key = XDOTOOL_KEY.get(parsed.key, parsed.key)
    parts.append(key)
    return "+".join(parts)


# ---------------------------------------------------------------------------
# Linux Wayland (ydotool) — Linux input event codes from
# /usr/include/linux/input-event-codes.h. ydotool's ``key`` syntax accepts
# decimal codes or symbolic names; we use the codes for portability.
# ---------------------------------------------------------------------------

YDOTOOL_MOD: Dict[str, int] = {
    "ctrl":  29,   # KEY_LEFTCTRL
    "shift": 42,   # KEY_LEFTSHIFT
    "alt":   56,   # KEY_LEFTALT
    "cmd":   125,  # KEY_LEFTMETA
}

YDOTOOL_KEY: Dict[str, int] = {
    "a": 30, "b": 48, "c": 46, "d": 32, "e": 18, "f": 33, "g": 34, "h": 35,
    "i": 23, "j": 36, "k": 37, "l": 38, "m": 50, "n": 49, "o": 24, "p": 25,
    "q": 16, "r": 19, "s": 31, "t": 20, "u": 22, "v": 47, "w": 17, "x": 45,
    "y": 21, "z": 44,
    "1": 2, "2": 3, "3": 4, "4": 5, "5": 6,
    "6": 7, "7": 8, "8": 9, "9": 10, "0": 11,
    "return": 28, "tab": 15, "space": 57, "escape": 1, "backspace": 14,
    "delete": 111, "insert": 110, "capslock": 58,
    "up": 103, "down": 108, "left": 105, "right": 106,
    "home": 102, "end": 107, "pageup": 104, "pagedown": 109,
    "minus": 12, "equals": 13, "leftbracket": 26, "rightbracket": 27,
    "backslash": 43, "semicolon": 39, "quote": 40, "backtick": 41,
    "comma": 51, "period": 52, "slash": 53,
}
# Linux F1-F10 are KEY_F1=59 through KEY_F10=68; F11/F12 jump to 87/88
# (per /usr/include/linux/input-event-codes.h). F13+ also non-contiguous;
# we cap at F12 since that covers every standard keyboard.
for _i in range(1, 11):
    YDOTOOL_KEY[f"f{_i}"] = 58 + _i
YDOTOOL_KEY["f11"] = 87
YDOTOOL_KEY["f12"] = 88


def to_ydotool(parsed: ParsedKey) -> List[int]:
    """Return [keycode, ...] press order for ydotool. Caller emits :1 for press, :0 for release."""
    codes = [YDOTOOL_MOD[m] for m in ("ctrl", "alt", "shift", "cmd") if m in parsed.modifiers]
    key_code = YDOTOOL_KEY.get(parsed.key)
    if key_code is None:
        raise KeyParseError(f"no Linux input code for {parsed.key!r}")
    codes.append(key_code)
    return codes


# ---------------------------------------------------------------------------
# Windows — Win32 virtual-key codes.
# ---------------------------------------------------------------------------

WIN_MOD_VK: Dict[str, int] = {
    "ctrl":  0x11,  # VK_CONTROL
    "shift": 0x10,  # VK_SHIFT
    "alt":   0x12,  # VK_MENU
    "cmd":   0x5B,  # VK_LWIN
}

WIN_VK: Dict[str, int] = {
    "return": 0x0D, "tab": 0x09, "space": 0x20, "escape": 0x1B,
    "backspace": 0x08, "delete": 0x2E, "insert": 0x2D, "capslock": 0x14,
    "up": 0x26, "down": 0x28, "left": 0x25, "right": 0x27,
    "home": 0x24, "end": 0x23, "pageup": 0x21, "pagedown": 0x22,
    "printscreen": 0x2C,
    "minus": 0xBD, "equals": 0xBB, "comma": 0xBC, "period": 0xBE,
    "slash": 0xBF, "backslash": 0xDC, "semicolon": 0xBA, "quote": 0xDE,
    "leftbracket": 0xDB, "rightbracket": 0xDD, "backtick": 0xC0,
}
# Letters: A-Z VK codes are 0x41-0x5A
for _c in "abcdefghijklmnopqrstuvwxyz":
    WIN_VK[_c] = 0x41 + (ord(_c) - ord("a"))
# Digits: 0x30-0x39
for _c in "0123456789":
    WIN_VK[_c] = 0x30 + (ord(_c) - ord("0"))
# Function keys F1-F24
for _i in range(1, 25):
    WIN_VK[f"f{_i}"] = 0x6F + _i  # VK_F1=0x70


def to_windows(parsed: ParsedKey) -> List[int]:
    """Return [vk_code, ...] press order for SendInput."""
    codes = [WIN_MOD_VK[m] for m in ("ctrl", "alt", "shift", "cmd") if m in parsed.modifiers]
    vk = WIN_VK.get(parsed.key)
    if vk is None:
        raise KeyParseError(f"no Windows VK code for {parsed.key!r}")
    codes.append(vk)
    return codes
