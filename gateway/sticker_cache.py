"""
Sticker description cache for Telegram.

When users send stickers, we describe them via the vision tool and cache
the descriptions keyed by file_unique_id so we don't re-analyze the same
sticker image on every send. Descriptions are concise (1-2 sentences).

Cache location: ~/.hermes/sticker_cache.json
"""

import json
import time
import threading
from contextlib import contextmanager
from typing import Optional

from hermes_cli.config import get_hermes_home
from utils import atomic_json_write

try:
    import fcntl
except Exception:
    fcntl = None
try:
    import msvcrt
except Exception:
    msvcrt = None


CACHE_PATH = get_hermes_home() / "sticker_cache.json"
_CACHE_LOCK_TIMEOUT_SECONDS = 5.0
_cache_lock_holder = threading.local()
_fallback_lock = threading.RLock()

# Vision prompt for describing stickers -- kept concise to save tokens
STICKER_VISION_PROMPT = (
    "Describe this sticker in 1-2 sentences. Focus on what it depicts -- "
    "character, action, emotion. Be concise and objective."
)


def _cache_lock_path():
    return CACHE_PATH.with_suffix(".lock")


@contextmanager
def _cache_store_lock(timeout_seconds: float = _CACHE_LOCK_TIMEOUT_SECONDS):
    """Cross-process advisory lock for cache read-modify-write operations."""
    if getattr(_cache_lock_holder, "depth", 0) > 0:
        _cache_lock_holder.depth += 1
        try:
            yield
        finally:
            _cache_lock_holder.depth -= 1
        return

    if fcntl is None and msvcrt is None:
        with _fallback_lock:
            _cache_lock_holder.depth = 1
            try:
                yield
            finally:
                _cache_lock_holder.depth = 0
        return

    lock_path = _cache_lock_path()
    lock_path.parent.mkdir(parents=True, exist_ok=True)

    # On Windows, msvcrt.locking requires at least one byte in the file.
    if msvcrt and (not lock_path.exists() or lock_path.stat().st_size == 0):
        lock_path.write_text(" ", encoding="utf-8")

    with lock_path.open("r+" if msvcrt else "a+") as lock_file:
        deadline = time.time() + max(1.0, timeout_seconds)
        while True:
            try:
                if fcntl:
                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                else:
                    lock_file.seek(0)
                    msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
                break
            except (BlockingIOError, OSError, PermissionError):
                if time.time() >= deadline:
                    raise TimeoutError("Timed out waiting for sticker cache lock")
                time.sleep(0.05)

        _cache_lock_holder.depth = 1
        try:
            yield
        finally:
            _cache_lock_holder.depth = 0
            if fcntl:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
            elif msvcrt:
                try:
                    lock_file.seek(0)
                    msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
                except (OSError, IOError):
                    pass


def _load_cache() -> dict:
    """Load the sticker cache from disk."""
    if CACHE_PATH.exists():
        try:
            return json.loads(CACHE_PATH.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def _save_cache(cache: dict) -> None:
    """Save the sticker cache to disk atomically."""
    atomic_json_write(CACHE_PATH, cache, indent=2)


def get_cached_description(file_unique_id: str) -> Optional[dict]:
    """
    Look up a cached sticker description.

    Returns:
        dict with keys {description, emoji, set_name, cached_at} or None.
    """
    cache = _load_cache()
    return cache.get(file_unique_id)


def cache_sticker_description(
    file_unique_id: str,
    description: str,
    emoji: str = "",
    set_name: str = "",
) -> None:
    """
    Store a sticker description in the cache.

    Args:
        file_unique_id: Telegram's stable sticker identifier.
        description:    Vision-generated description text.
        emoji:          Associated emoji (e.g. "😀").
        set_name:       Sticker set name if available.
    """
    with _cache_store_lock():
        cache = _load_cache()
        cache[file_unique_id] = {
            "description": description,
            "emoji": emoji,
            "set_name": set_name,
            "cached_at": time.time(),
        }
        _save_cache(cache)


def build_sticker_injection(
    description: str,
    emoji: str = "",
    set_name: str = "",
) -> str:
    """
    Build the warm-style injection text for a sticker description.

    Returns a string like:
      [The user sent a sticker 😀 from "MyPack"~ It shows: "A cat waving" (=^.w.^=)]
    """
    context = ""
    if set_name and emoji:
        context = f" {emoji} from \"{set_name}\""
    elif emoji:
        context = f" {emoji}"

    return f"[The user sent a sticker{context}~ It shows: \"{description}\" (=^.w.^=)]"


def build_animated_sticker_injection(emoji: str = "") -> str:
    """
    Build injection text for animated/video stickers we can't analyze.
    """
    if emoji:
        return (
            f"[The user sent an animated sticker {emoji}~ "
            f"I can't see animated ones yet, but the emoji suggests: {emoji}]"
        )
    return "[The user sent an animated sticker~ I can't see animated ones yet]"
