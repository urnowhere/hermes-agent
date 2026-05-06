"""Regression tests for #15768: setup wizard hangs at API key prompt on Ubuntu.

The failure mode was that ``getpass.getpass()`` in ``hermes_cli.auth_commands``
read raw bytes from a terminal that had been left in non-canonical mode by
an earlier curses prompt.  The fix introduces ``reset_terminal_mode`` in
``hermes_cli.curses_ui`` and routes the API-key prompt through a wrapper
(``_prompt_api_key``) that resets the tty before calling ``getpass`` and
falls back to a direct ``/dev/tty`` read when ``getpass`` raises ``OSError``.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch


# --- reset_terminal_mode -----------------------------------------------------


def test_reset_terminal_mode_no_op_on_non_tty():
    """Non-TTY stdin -> reset_terminal_mode returns without touching termios."""
    from hermes_cli import curses_ui

    fake_stdin = MagicMock()
    fake_stdin.isatty.return_value = False

    fake_termios = MagicMock()

    with patch.object(curses_ui, "sys") as mock_sys:
        mock_sys.stdin = fake_stdin
        with patch.dict("sys.modules", {"termios": fake_termios}):
            curses_ui.reset_terminal_mode()

    assert not fake_termios.tcgetattr.called
    assert not fake_termios.tcsetattr.called


def test_reset_terminal_mode_restores_canonical_and_echo():
    """TTY stdin -> reset_terminal_mode OR-s ICANON|ECHO into local flags."""
    from hermes_cli import curses_ui

    icanon_bit = 0x2
    echo_bit = 0x8
    cflag_local = 0  # No ICANON, no ECHO -- the broken state.
    attrs = [0, 0, 0, cflag_local, 0, 0, []]

    fake_stdin = MagicMock()
    fake_stdin.isatty.return_value = True
    fake_stdin.fileno.return_value = 0

    fake_termios = MagicMock()
    fake_termios.tcgetattr.return_value = list(attrs)
    fake_termios.ICANON = icanon_bit
    fake_termios.ECHO = echo_bit
    fake_termios.TCSANOW = 0
    fake_termios.TCIFLUSH = 0

    with patch.object(curses_ui, "sys") as mock_sys:
        mock_sys.stdin = fake_stdin
        with patch.dict("sys.modules", {"termios": fake_termios}):
            curses_ui.reset_terminal_mode()

    fake_termios.tcgetattr.assert_called_once_with(0)
    written = fake_termios.tcsetattr.call_args[0][2]
    assert written[3] & icanon_bit, "ICANON not restored"
    assert written[3] & echo_bit, "ECHO not restored"


def test_reset_terminal_mode_swallows_termios_errors():
    """termios raising should not propagate out of reset_terminal_mode."""
    from hermes_cli import curses_ui

    fake_stdin = MagicMock()
    fake_stdin.isatty.return_value = True
    fake_stdin.fileno.return_value = 0

    fake_termios = MagicMock()
    fake_termios.tcgetattr.side_effect = OSError("Inappropriate ioctl for device")
    fake_termios.ICANON = 0x2
    fake_termios.ECHO = 0x8
    fake_termios.TCSANOW = 0
    fake_termios.TCIFLUSH = 0

    with patch.object(curses_ui, "sys") as mock_sys:
        mock_sys.stdin = fake_stdin
        with patch.dict("sys.modules", {"termios": fake_termios}):
            curses_ui.reset_terminal_mode()  # must not raise

    assert not fake_termios.tcsetattr.called


# --- _prompt_api_key ---------------------------------------------------------


def test_paste_api_key_calls_reset_terminal_mode_before_getpass():
    """The prompt path resets the tty BEFORE calling getpass."""
    from hermes_cli import auth_commands

    call_order: list[str] = []

    def fake_reset():
        call_order.append("reset")

    def fake_getpass(prompt):
        call_order.append("getpass")
        return "sk-abc"

    with (
        patch.object(auth_commands, "reset_terminal_mode", fake_reset),
        patch.object(auth_commands, "getpass", fake_getpass),
    ):
        result = auth_commands._prompt_api_key()

    assert result == "sk-abc"
    assert call_order == ["reset", "getpass"], (
        "reset_terminal_mode must run before getpass; saw %r" % call_order
    )


def test_paste_api_key_falls_back_to_dev_tty_when_getpass_oserror():
    """If getpass raises OSError, _prompt_api_key reads from /dev/tty directly."""
    from hermes_cli import auth_commands

    def raising_getpass(prompt):
        raise OSError(25, "Inappropriate ioctl for device")

    with (
        patch.object(auth_commands, "reset_terminal_mode", lambda: None),
        patch.object(auth_commands, "getpass", raising_getpass),
        patch.object(
            auth_commands, "_read_secret_from_tty", return_value="fallback-key"
        ) as fallback,
    ):
        result = auth_commands._prompt_api_key()

    fallback.assert_called_once_with("Paste your API key: ")
    assert result == "fallback-key"


def test_read_secret_from_tty_disables_echo_and_restores_state():
    """The /dev/tty fallback must disable ECHO during read and restore on exit."""
    from hermes_cli import auth_commands

    icanon_bit = 0x2
    echo_bit = 0x8
    initial_attrs = [0, 0, 0, icanon_bit | echo_bit, 0, 0, []]

    fake_tty = MagicMock()
    fake_tty.fileno.return_value = 99
    fake_tty.readline.return_value = "secret-key\n"
    fake_tty.__enter__ = MagicMock(return_value=fake_tty)
    fake_tty.__exit__ = MagicMock(return_value=False)

    fake_termios = MagicMock()
    fake_termios.tcgetattr.return_value = list(initial_attrs)
    fake_termios.ICANON = icanon_bit
    fake_termios.ECHO = echo_bit
    fake_termios.TCSANOW = 0

    with (
        patch("builtins.open", return_value=fake_tty),
        patch.dict("sys.modules", {"termios": fake_termios}),
    ):
        result = auth_commands._read_secret_from_tty("Paste your API key: ")

    assert result == "secret-key"
    assert fake_termios.tcsetattr.call_count == 2
    written_first = fake_termios.tcsetattr.call_args_list[0][0][2]
    assert not (written_first[3] & echo_bit), "ECHO not disabled during read"
    written_restore = fake_termios.tcsetattr.call_args_list[1][0][2]
    assert written_restore[3] & echo_bit, "ECHO not restored after read"
