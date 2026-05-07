"""Helpers for Telegram Bot API chat identifiers."""

from __future__ import annotations

import re
from typing import Any

_TELEGRAM_USERNAME_RE = re.compile(r"@[A-Za-z0-9_]{5,32}")


def normalize_telegram_chat_id(chat_id: Any) -> int | str:
    """Return a Telegram Bot API-compatible chat_id value."""
    chat_id_str = str(chat_id).strip()
    try:
        return int(chat_id_str)
    except ValueError:
        return chat_id_str


def telegram_chat_id_key(chat_id: Any) -> str:
    return str(normalize_telegram_chat_id(chat_id))


def looks_like_telegram_username(chat_id: Any) -> bool:
    return bool(_TELEGRAM_USERNAME_RE.fullmatch(str(chat_id).strip()))


def parse_telegram_username_target(target_ref: str) -> str | None:
    value = str(target_ref).strip()
    return value if looks_like_telegram_username(value) else None
