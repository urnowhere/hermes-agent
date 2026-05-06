"""CLI wiring tests for dashboard authentication flags."""

from __future__ import annotations

import argparse
import sys
from unittest.mock import MagicMock, patch

from hermes_cli.main import cmd_dashboard


def _ns(**kw):
    defaults = dict(
        port=9119,
        host="127.0.0.1",
        no_open=True,
        insecure=False,
        tui=False,
        stop=False,
        status=False,
        auth=None,
        auth_token=None,
        auth_password=None,
        auth_password_hash=None,
        trusted_proxy_user_header=None,
        trusted_proxy_email_header=None,
        tailscale_user_header=None,
    )
    defaults.update(kw)
    return argparse.Namespace(**defaults)


def test_dashboard_auth_flags_are_passed_to_start_server(monkeypatch):
    fake_ws = MagicMock()
    monkeypatch.setitem(sys.modules, "hermes_cli.web_server", fake_ws)
    monkeypatch.setattr("hermes_cli.main._build_web_ui", lambda *a, **kw: True)

    cmd_dashboard(_ns(auth="token", auth_token="secret"))

    fake_ws.start_server.assert_called_once()
    auth_config = fake_ws.start_server.call_args.kwargs["auth_config"]
    assert auth_config["mode"] == "token"
    assert auth_config["token"] == "secret"


def test_dashboard_trusted_proxy_headers_are_passed_to_start_server(monkeypatch):
    fake_ws = MagicMock()
    monkeypatch.setitem(sys.modules, "hermes_cli.web_server", fake_ws)
    monkeypatch.setattr("hermes_cli.main._build_web_ui", lambda *a, **kw: True)

    cmd_dashboard(_ns(
        auth="trusted-proxy",
        trusted_proxy_user_header="X-User",
        trusted_proxy_email_header="X-Email",
    ))

    auth_config = fake_ws.start_server.call_args.kwargs["auth_config"]
    assert auth_config["trusted_proxy"]["user_header"] == "X-User"
    assert auth_config["trusted_proxy"]["email_header"] == "X-Email"


def test_dashboard_auth_config_reads_persistent_config(monkeypatch):
    fake_ws = MagicMock()
    monkeypatch.setitem(sys.modules, "hermes_cli.web_server", fake_ws)
    monkeypatch.setattr("hermes_cli.main._build_web_ui", lambda *a, **kw: True)
    monkeypatch.setattr(
        "hermes_cli.config.load_config",
        lambda: {"dashboard": {"auth": {"mode": "token", "token": "from-config"}}},
    )

    cmd_dashboard(_ns())

    auth_config = fake_ws.start_server.call_args.kwargs["auth_config"]
    assert auth_config["mode"] == "token"
    assert auth_config["token"] == "from-config"


def test_dashboard_auth_cli_overrides_persistent_config(monkeypatch):
    fake_ws = MagicMock()
    monkeypatch.setitem(sys.modules, "hermes_cli.web_server", fake_ws)
    monkeypatch.setattr("hermes_cli.main._build_web_ui", lambda *a, **kw: True)
    monkeypatch.setattr(
        "hermes_cli.config.load_config",
        lambda: {"dashboard": {"auth": {"mode": "token", "token": "from-config"}}},
    )

    cmd_dashboard(_ns(auth="none"))

    auth_config = fake_ws.start_server.call_args.kwargs["auth_config"]
    assert auth_config["mode"] == "none"
    assert auth_config["token"] == "from-config"
