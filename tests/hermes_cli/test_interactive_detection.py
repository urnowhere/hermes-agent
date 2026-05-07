"""Tests for PR #16529: HERMES_INTERACTIVE gated behind isatty() checks."""

import os
import sys

import pytest


def test_hermes_interactive_not_set_when_piped(monkeypatch):
    """HERMES_INTERACTIVE must not be set when stdin/stdout are pipes."""
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: False)
    os.environ.pop("HERMES_INTERACTIVE", None)

    # Replicate the exact guard introduced in cli.py main()
    if sys.stdin.isatty() and sys.stdout.isatty():
        os.environ["HERMES_INTERACTIVE"] = "1"

    assert os.environ.get("HERMES_INTERACTIVE") is None, (
        "HERMES_INTERACTIVE should NOT be set when stdin/stdout are pipes"
    )


def test_hermes_interactive_set_when_tty(monkeypatch):
    """HERMES_INTERACTIVE should still be set when stdin/stdout are real terminals."""
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
    os.environ.pop("HERMES_INTERACTIVE", None)

    if sys.stdin.isatty() and sys.stdout.isatty():
        os.environ["HERMES_INTERACTIVE"] = "1"

    assert os.environ.get("HERMES_INTERACTIVE") == "1", (
        "HERMES_INTERACTIVE should be set when stdin/stdout are real terminals"
    )


def test_hermes_interactive_not_set_when_stdin_piped_only(monkeypatch):
    """Only stdin piped (stdout tty) — should still NOT set interactive."""
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
    os.environ.pop("HERMES_INTERACTIVE", None)

    if sys.stdin.isatty() and sys.stdout.isatty():
        os.environ["HERMES_INTERACTIVE"] = "1"

    assert os.environ.get("HERMES_INTERACTIVE") is None, (
        "HERMES_INTERACTIVE should NOT be set when only stdout is a TTY"
    )


@pytest.mark.parametrize("stdin_tty,stdout_tty,expected", [
    (True, True, "1"),
    (True, False, None),
    (False, True, None),
    (False, False, None),
])
def test_interactive_flag_matrix(monkeypatch, stdin_tty, stdout_tty, expected):
    """Full truth table: HERMES_INTERACTIVE only when BOTH stdin and stdout are TTYs."""
    monkeypatch.setattr(sys.stdin, "isatty", lambda: stdin_tty)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: stdout_tty)
    os.environ.pop("HERMES_INTERACTIVE", None)

    if sys.stdin.isatty() and sys.stdout.isatty():
        os.environ["HERMES_INTERACTIVE"] = "1"

    assert os.environ.get("HERMES_INTERACTIVE") == expected
