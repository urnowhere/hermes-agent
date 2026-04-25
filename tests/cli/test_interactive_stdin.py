"""Regression tests for interactive CLI stdin handling."""

import errno

import cli


class _FakeStdin:
    def __init__(self, is_tty):
        self._is_tty = is_tty

    def isatty(self):
        return self._is_tty


def test_prompt_toolkit_stdin_probe_rejects_non_tty(monkeypatch):
    monkeypatch.setattr(cli.sys, "stdin", _FakeStdin(False))

    assert cli._stdin_usable_for_prompt_toolkit() is False


def test_prompt_toolkit_stdin_probe_rejects_selector_invalid_argument(monkeypatch):
    monkeypatch.setattr(cli.sys, "stdin", _FakeStdin(True))
    monkeypatch.setattr(cli.os, "fstat", lambda fd: object())

    class _Selector:
        def register(self, fd, event):
            raise OSError(errno.EINVAL, "Invalid argument")

        def close(self):
            pass

    monkeypatch.setattr(cli.selectors, "DefaultSelector", lambda: _Selector())

    assert cli._stdin_usable_for_prompt_toolkit() is False


def test_interactive_stdin_rebinds_to_dev_tty_when_current_stdin_is_unusable(monkeypatch):
    calls = []
    usable_results = iter([False, True])

    monkeypatch.setattr(cli, "_stdin_usable_for_prompt_toolkit", lambda: next(usable_results))
    monkeypatch.setattr(cli.os.path, "exists", lambda path: path == "/dev/tty")
    monkeypatch.setattr(cli.os, "access", lambda path, mode: path == "/dev/tty")
    monkeypatch.setattr(cli.os, "dup", lambda fd: calls.append(("dup", fd)) or 41)
    monkeypatch.setattr(cli.os, "open", lambda path, flags: calls.append(("open", path, flags)) or 42)
    monkeypatch.setattr(cli.os, "dup2", lambda src, dst: calls.append(("dup2", src, dst)))
    monkeypatch.setattr(cli.os, "close", lambda fd: calls.append(("close", fd)))

    with cli._interactive_stdin():
        calls.append(("inside",))

    assert calls == [
        ("dup", 0),
        ("open", "/dev/tty", cli.os.O_RDWR),
        ("dup2", 42, 0),
        ("inside",),
        ("dup2", 41, 0),
        ("close", 41),
        ("close", 42),
    ]
