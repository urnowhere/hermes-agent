"""Tests for the browser sidecar bridge health and auth surface."""

import asyncio
import json
import socket
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from gateway.browser_bridge import (
    BrowserBridgeConfig,
    BrowserBridgeServer,
    _BrowserHTTPServer,
    get_browser_bridge_setup_details,
    get_browser_bridge_token_path,
)


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _load_json(url: str, *, method: str = "GET", body: dict | None = None, headers: dict | None = None) -> dict:
    data = None if body is None else json.dumps(body).encode("utf-8")
    request = Request(url, method=method, data=data, headers=headers or {})
    with urlopen(request, timeout=5) as response:
        return json.loads(response.read().decode("utf-8"))


def test_browser_bridge_token_path_uses_active_hermes_home(tmp_path, monkeypatch):
    hermes_home = tmp_path / "profile-home"
    hermes_home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))

    assert get_browser_bridge_token_path() == hermes_home / "browser_bridge_token"


def test_health_exposes_setup_metadata(tmp_path, monkeypatch):
    hermes_home = tmp_path / "profile-home"
    hermes_home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))

    port = _free_port()
    loop = asyncio.new_event_loop()
    bridge = BrowserBridgeServer(
        loop=loop,
        handle_payload=lambda payload: {"payload": payload},
        config=BrowserBridgeConfig(host="127.0.0.1", port=port, token="secret-token"),
    )
    bridge.start()
    try:
        payload = _load_json(f"http://127.0.0.1:{port}/health")
    finally:
        bridge.stop()
        loop.close()

    assert payload["ok"] is True
    assert payload["running"] is True
    assert payload["bridge_url"] == f"http://127.0.0.1:{port}/inject"
    assert payload["bridge_session_url"] == f"http://127.0.0.1:{port}/session"
    assert payload["setup_command"] == "hermes gateway browser-token"
    assert payload["token_file_hint"].endswith("/browser_bridge_token")


def test_unauthorized_session_requests_are_rejected(tmp_path, monkeypatch):
    hermes_home = tmp_path / "profile-home"
    hermes_home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))

    port = _free_port()
    loop = asyncio.new_event_loop()
    bridge = BrowserBridgeServer(
        loop=loop,
        handle_payload=lambda payload: {"payload": payload},
        config=BrowserBridgeConfig(host="127.0.0.1", port=port, token="secret-token"),
    )
    bridge.start()
    try:
        with pytest.raises(HTTPError) as exc_info:
            _load_json(
                f"http://127.0.0.1:{port}/session",
                method="POST",
                body={"action": "state"},
                headers={"Content-Type": "application/json"},
            )
    finally:
        bridge.stop()
        loop.close()

    assert exc_info.value.code == 401


def test_setup_details_include_bridge_root_and_command(tmp_path, monkeypatch):
    hermes_home = tmp_path / "profile-home"
    hermes_home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))

    details = get_browser_bridge_setup_details(host="0.0.0.0", port=9123, enabled=True)

    assert details["bridge_root_url"] == "http://127.0.0.1:9123"
    assert details["bridge_url"] == "http://127.0.0.1:9123/inject"
    assert details["setup_command"] == "hermes gateway browser-token"


def test_browser_http_server_allows_fast_restart_reuse():
    assert _BrowserHTTPServer.allow_reuse_address is True
