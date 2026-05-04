"""Tests for ``hermes_cli.voice_record_key``.

Regression coverage for #11387 — invalid ``voice.record_key`` values
(notably ``alt+*``) used to crash CLI startup at prompt_toolkit binding
registration time.
"""

import logging

import pytest

from hermes_cli import voice_record_key as vrk


class TestParse:
    def test_ctrl_letter(self):
        assert vrk.parse("ctrl+b") == (("c-b",), "Ctrl+B")

    def test_shift_function_key(self):
        assert vrk.parse("shift+f1") == (("s-f1",), "Shift+F1")

    def test_alt_uses_escape_prefix(self):
        # The original bug: alt+* was translated to "a-*" which prompt_toolkit
        # rejects.  Alt is an Escape-prefix sequence.
        assert vrk.parse("alt+space") == (("escape", "space"), "Alt+Space")

    def test_meta_is_alt_alias(self):
        assert vrk.parse("meta+x") == (("escape", "x"), "Alt+X")

    def test_uppercase_normalised(self):
        assert vrk.parse("CTRL+B") == (("c-b",), "Ctrl+B")

    def test_bare_key(self):
        assert vrk.parse("space") == (("space",), "Space")

    def test_empty_raises(self):
        with pytest.raises(ValueError):
            vrk.parse("")

    def test_unknown_modifier_raises(self):
        with pytest.raises(ValueError):
            vrk.parse("super+x")

    def test_too_many_parts_raises(self):
        with pytest.raises(ValueError):
            vrk.parse("ctrl+alt+b")


class TestResolve:
    def test_uses_config_value(self, monkeypatch):
        monkeypatch.setattr(
            "hermes_cli.config.load_config",
            lambda: {"voice": {"record_key": "alt+space"}},
        )
        keys, display = vrk.resolve()
        assert keys == ("escape", "space")
        assert display == "Alt+Space"

    def test_falls_back_on_invalid_value(self, monkeypatch, caplog):
        monkeypatch.setattr(
            "hermes_cli.config.load_config",
            lambda: {"voice": {"record_key": "super+x"}},
        )
        with caplog.at_level(logging.WARNING, logger="hermes_cli.voice_record_key"):
            keys, display = vrk.resolve()
        assert keys == ("c-b",)
        assert display == "Ctrl+B"
        assert "Invalid voice.record_key" in caplog.text

    def test_falls_back_when_config_unreadable(self, monkeypatch):
        def _broken():
            raise RuntimeError("config blew up")

        monkeypatch.setattr("hermes_cli.config.load_config", _broken)
        keys, display = vrk.resolve()
        assert keys == ("c-b",)
        assert display == "Ctrl+B"

    def test_default_when_key_missing(self, monkeypatch):
        monkeypatch.setattr("hermes_cli.config.load_config", lambda: {})
        keys, display = vrk.resolve()
        assert keys == ("c-b",)
        assert display == "Ctrl+B"
