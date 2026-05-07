"""Regression tests for terminal navigation/focus escape sequences.

Ghostty/macOS window and tab navigation can deliver terminal focus reports
(CSI I / CSI O) to the running TUI. These must be consumed by the input parser,
not inserted into the prompt buffer and cleaned up later.
"""

from prompt_toolkit.input.vt100_parser import Vt100Parser
from prompt_toolkit.keys import Keys

from cli import _register_prompt_toolkit_ignored_terminal_sequences


def _parse_keys(data: str):
    events = []
    parser = Vt100Parser(events.append)
    parser.feed_and_flush(data)
    return [(event.key, event.data) for event in events]


def test_focus_events_are_parser_level_ignored_before_prompt_buffer():
    _register_prompt_toolkit_ignored_terminal_sequences()

    assert _parse_keys("\x1b[O\x1b[Ihello") == [
        (Keys.Ignore, "\x1b[O"),
        (Keys.Ignore, "\x1b[I"),
        ("h", "h"),
        ("e", "e"),
        ("l", "l"),
        ("l", "l"),
        ("o", "o"),
    ]


def test_regular_escape_shortcuts_still_parse_normally():
    _register_prompt_toolkit_ignored_terminal_sequences()

    assert _parse_keys("\x1bg") == [(Keys.Escape, "\x1b"), ("g", "g")]
