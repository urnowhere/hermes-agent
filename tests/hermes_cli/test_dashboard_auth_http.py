"""HTTP integration tests for dashboard authentication."""

from __future__ import annotations

import asyncio

import httpx
import pytest

from hermes_cli.dashboard_auth import DashboardAuthManager, hash_dashboard_password
from hermes_cli import web_server


class _ASGIClient:
    """Tiny sync wrapper around httpx ASGITransport.

    Starlette's TestClient is hanging in the shared verification venv, while
    direct ASGI calls and httpx.ASGITransport behave correctly.
    """

    def __init__(self, app):
        self.app = app

    def request(self, method: str, path: str, **kwargs):
        async def _run():
            transport = httpx.ASGITransport(app=self.app)
            async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
                return await client.request(method, path, **kwargs)

        return asyncio.run(_run())

    def get(self, path: str, **kwargs):
        return self.request("GET", path, **kwargs)

    def post(self, path: str, **kwargs):
        return self.request("POST", path, **kwargs)


@pytest.fixture(autouse=True)
def reset_dashboard_auth_state():
    previous_auth = getattr(web_server.app.state, "dashboard_auth", None)
    previous_host = getattr(web_server.app.state, "bound_host", None)
    previous_port = getattr(web_server.app.state, "bound_port", None)
    yield
    if previous_auth is None:
        try:
            delattr(web_server.app.state, "dashboard_auth")
        except (AttributeError, KeyError):
            pass
    else:
        web_server.app.state.dashboard_auth = previous_auth
    web_server.app.state.bound_host = previous_host
    web_server.app.state.bound_port = previous_port


def configure_auth(auth_cfg):
    web_server.app.state.dashboard_auth = DashboardAuthManager({"dashboard": {"auth": auth_cfg}}, env={})
    web_server.app.state.bound_host = None
    web_server.app.state.bound_port = 9119
    return _ASGIClient(web_server.app)


def test_auth_status_is_public_and_reports_required_mode():
    client = configure_auth({"mode": "token", "token": "secret"})

    response = client.get("/api/auth/status")

    assert response.status_code == 200
    assert response.json()["mode"] == "token"
    assert response.json()["required"] is True


def test_protected_api_rejects_missing_token_in_token_mode():
    client = configure_auth({"mode": "token", "token": "secret"})

    response = client.get("/api/config")

    assert response.status_code == 401


def test_protected_api_accepts_bearer_token_in_token_mode():
    client = configure_auth({"mode": "token", "token": "secret"})

    response = client.get("/api/config", headers={"Authorization": "Bearer secret"})

    assert response.status_code == 200


def test_password_login_issues_session_for_protected_api():
    password_hash = hash_dashboard_password("correct", salt="testsalt", iterations=1000)
    client = configure_auth({"mode": "password", "password_hash": password_hash})

    login = client.post("/api/auth/login", json={"password": "correct"})

    assert login.status_code == 200
    session = login.json()["session_token"]
    response = client.get("/api/config", headers={"X-Hermes-Dashboard-Session": session})
    assert response.status_code == 200


def test_password_login_rejects_wrong_password():
    password_hash = hash_dashboard_password("correct", salt="testsalt", iterations=1000)
    client = configure_auth({"mode": "password", "password_hash": password_hash})

    response = client.post("/api/auth/login", json={"password": "wrong"})

    assert response.status_code == 401


def test_plugin_api_routes_are_not_blanket_public():
    client = configure_auth({"mode": "token", "token": "secret"})

    response = client.get("/api/plugins/example/anything")

    assert response.status_code == 401


def test_start_server_opens_token_bootstrap_url(monkeypatch):
    from hermes_cli import web_server

    opened: list[str] = []

    class ImmediateThread:
        def __init__(self, target, daemon=False):
            self.target = target
            self.daemon = daemon

        def start(self):
            self.target()

    monkeypatch.setattr(web_server.time, "sleep", lambda *_: None)
    monkeypatch.setattr(web_server.threading, "Thread", ImmediateThread)
    monkeypatch.setattr("webbrowser.open", lambda url: opened.append(url))
    monkeypatch.setattr("uvicorn.run", lambda *a, **kw: None)

    web_server.start_server(
        host="127.0.0.1",
        port=9123,
        open_browser=True,
        auth_config={"mode": "token", "token": "abc /?&"},
    )

    assert opened == ["http://127.0.0.1:9123/#token=abc%20%2F%3F%26"]


def test_logout_revokes_browser_session():
    client = configure_auth({"mode": "password", "password": "correct"})

    login = client.post("/api/auth/login", json={"password": "correct"})
    assert login.status_code == 200
    session = login.json()["session_token"]

    headers = {"X-Hermes-Dashboard-Session": session}
    assert client.get("/api/sessions", headers=headers).status_code == 200
    assert client.post("/api/auth/logout", headers=headers).json()["ok"] is True
    assert client.get("/api/sessions", headers=headers).status_code == 401


def test_start_server_never_puts_dashboard_token_in_query_string(monkeypatch):
    from hermes_cli import web_server

    opened: list[str] = []

    class ImmediateThread:
        def __init__(self, target, daemon=False):
            self.target = target
            self.daemon = daemon
        def start(self):
            self.target()

    monkeypatch.setattr(web_server.time, "sleep", lambda *_: None)
    monkeypatch.setattr(web_server.threading, "Thread", ImmediateThread)
    monkeypatch.setattr("webbrowser.open", lambda url: opened.append(url))
    monkeypatch.setattr("uvicorn.run", lambda *a, **kw: None)

    web_server.start_server(host="127.0.0.1", port=9124, open_browser=True, auth_config={"mode": "token", "token": "secret"})

    assert opened
    assert "?token=" not in opened[0]
    assert "#token=" in opened[0]


def test_frontend_consumes_bootstrap_token_from_fragment_and_strips_url():
    # Static regression coverage for OpenClaw reports where auth tokens leaked
    # through query strings or remained visible after redirect/bootstrap.
    source = (web_server.PROJECT_ROOT / "web" / "src" / "lib" / "dashboardAuth.ts").read_text()

    assert "window.location.hash" in source
    assert "window.sessionStorage.setItem(TOKEN_STORAGE_KEY, token)" in source
    assert "params.delete(\"token\")" in source
    assert "window.history.replaceState" in source
    assert "window.location.search" in source
