"""Tests for _cli_prog() — the function that supplies prog= to ArgumentParser.

Profile wrappers set HERMES_CLI_NAME so that ``june --help`` shows
``usage: june`` instead of ``usage: hermes``.
"""

import sys
from unittest.mock import patch

from hermes_cli.main import _cli_prog


class TestCliProg:
    def test_returns_hermes_cli_name_env_var(self, monkeypatch):
        monkeypatch.setenv("HERMES_CLI_NAME", "june")
        assert _cli_prog() == "june"

    def test_falls_back_to_argv0_basename(self, monkeypatch):
        monkeypatch.delenv("HERMES_CLI_NAME", raising=False)
        with patch.object(sys, "argv", ["/usr/local/bin/june", "--help"]):
            assert _cli_prog() == "june"

    def test_ultimate_fallback_is_hermes(self, monkeypatch):
        monkeypatch.delenv("HERMES_CLI_NAME", raising=False)
        with patch.object(sys, "argv", [""]):
            assert _cli_prog() == "hermes"

    def test_env_var_takes_precedence_over_argv(self, monkeypatch):
        monkeypatch.setenv("HERMES_CLI_NAME", "luna")
        with patch.object(sys, "argv", ["/usr/bin/cid"]):
            assert _cli_prog() == "luna"
