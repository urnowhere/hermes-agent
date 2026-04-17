"""Tests for stdio bootstrap helpers."""

from __future__ import annotations

import io
import os
import sys


def test_ensure_utf8_stdio_reconfigures_streams(monkeypatch):
    from hermes_cli.stdio import ensure_utf8_stdio

    stdout_buffer = io.BytesIO()
    stderr_buffer = io.BytesIO()
    stdout = io.TextIOWrapper(stdout_buffer, encoding="cp1252", errors="strict")
    stderr = io.TextIOWrapper(stderr_buffer, encoding="cp1252", errors="strict")

    monkeypatch.setattr(sys, "stdout", stdout)
    monkeypatch.setattr(sys, "stderr", stderr)
    monkeypatch.delenv("PYTHONIOENCODING", raising=False)
    monkeypatch.delenv("PYTHONUTF8", raising=False)

    ensure_utf8_stdio()

    assert sys.stdout.encoding.lower().replace("-", "") == "utf8"
    assert sys.stderr.encoding.lower().replace("-", "") == "utf8"
    assert os.environ["PYTHONIOENCODING"] == "utf-8"
    assert os.environ["PYTHONUTF8"] == "1"

    print("┌──┐ ✓ ⚠")
    sys.stdout.flush()
    assert stdout_buffer.getvalue() == "┌──┐ ✓ ⚠\n".encode("utf-8")
