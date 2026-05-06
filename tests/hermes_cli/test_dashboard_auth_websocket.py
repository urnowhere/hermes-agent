"""WebSocket authentication coverage for dashboard auth manager."""

from __future__ import annotations

from types import SimpleNamespace

from hermes_cli.dashboard_auth import DashboardAuthManager


class FakeWebSocket:
    def __init__(self, *, headers=None, query=None):
        self.headers = headers or {}
        self.query_params = query or {}
        self.client = SimpleNamespace(host="127.0.0.1")


def test_websocket_accepts_token_query_for_token_mode():
    manager = DashboardAuthManager({"dashboard": {"auth": {"mode": "token", "token": "secret"}}})

    result = manager.authenticate_websocket(FakeWebSocket(query={"token": "secret"}))

    assert result.ok is True


def test_websocket_rejects_wrong_token_query_for_token_mode():
    manager = DashboardAuthManager({"dashboard": {"auth": {"mode": "token", "token": "secret"}}})

    result = manager.authenticate_websocket(FakeWebSocket(query={"token": "wrong"}))

    assert result.ok is False
    assert result.status_code == 401


def test_websocket_accepts_session_query_for_password_mode():
    manager = DashboardAuthManager({"dashboard": {"auth": {"mode": "password", "password": "pw"}}})
    session = manager.login_password("pw", client_id="127.0.0.1").set_session_token

    result = manager.authenticate_websocket(FakeWebSocket(query={"token": session}))

    assert result.ok is True


def test_websocket_accepts_trusted_proxy_headers():
    manager = DashboardAuthManager({"dashboard": {"auth": {"mode": "trusted-proxy"}}})

    result = manager.authenticate_websocket(FakeWebSocket(headers={"X-Forwarded-User": "rana"}))

    assert result.ok is True
    assert result.identity.user == "rana"
