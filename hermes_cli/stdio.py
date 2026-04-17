"""Process-wide stdio bootstrap helpers for Hermes CLI entrypoints."""

from __future__ import annotations

import os
import sys


def ensure_utf8_stdio() -> None:
    """Reconfigure stdout/stderr to use UTF-8 and tolerate unencodable output.

    Hermes prints a number of Unicode symbols and box-drawing characters during
    startup. On Windows services and other legacy console environments the
    default stream encoding can be cp1252 (or otherwise non-UTF-8), which would
    otherwise raise ``UnicodeEncodeError`` and abort startup.

    The helper is intentionally idempotent and best-effort: if a stream does not
    support ``reconfigure`` it is left untouched.
    """
    os.environ["PYTHONIOENCODING"] = "utf-8"
    os.environ["PYTHONUTF8"] = "1"

    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except (OSError, ValueError, TypeError):
                # Keep startup resilient even if a wrapper stream refuses to
                # be reconfigured (e.g. some test doubles or redirected pipes).
                pass
