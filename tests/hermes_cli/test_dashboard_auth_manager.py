"""Tests for configurable dashboard authentication manager."""

from __future__ import annotations

import pytest

from hermes_cli.dashboard_auth import DashboardAuthManager, DashboardIdentity, hash_dashboard_password


class DummyRequest:
    def __init__(self, headers=None, client_host="127.0.0.1"):
        self.headers = headers or {}
        self.client = type("Client", (), {"host": client_host})()


def manager(auth_cfg, *, now=1000.0):
    clock = {"now": now}
    def _clock():
        return clock["now"]
    m = DashboardAuthManager({"dashboard": {"auth": auth_cfg}}, env={}, clock=_clock)
    m._test_clock = clock
    return m


def test_none_mode_allows_request():
    m = manager({"mode": "none"})

    result = m.authenticate_request(DummyRequest())

    assert result.ok is True
    assert result.identity is not None
    assert result.identity.source == "none"


def test_token_mode_rejects_missing_token_without_counting_rate_limit():
    m = manager({"mode": "token", "token": "secret"})

    result = m.authenticate_request(DummyRequest())

    assert result.ok is False
    assert result.status_code == 401
    assert m.failure_count("127.0.0.1", "token") == 0


def test_token_mode_accepts_bearer_token():
    m = manager({"mode": "token", "token": "secret"})

    result = m.authenticate_request(DummyRequest({"authorization": "Bearer secret"}))

    assert result.ok is True
    assert result.identity.source == "token"


def test_token_mode_accepts_dashboard_header():
    m = manager({"mode": "token", "token": "secret"})

    result = m.authenticate_request(DummyRequest({"x-hermes-dashboard-token": "secret"}))

    assert result.ok is True
    assert result.identity.source == "token"


def test_token_mode_rejects_wrong_token_and_counts_failure():
    m = manager({"mode": "token", "token": "secret"})

    result = m.authenticate_request(DummyRequest({"authorization": "Bearer wrong"}))

    assert result.ok is False
    assert result.status_code == 401
    assert m.failure_count("127.0.0.1", "token") == 1


def test_password_mode_login_rejects_wrong_password_and_counts_failure():
    password_hash = hash_dashboard_password("correct", salt="testsalt", iterations=1000)
    m = manager({"mode": "password", "password_hash": password_hash})

    result = m.login_password("wrong", client_id="127.0.0.1")

    assert result.ok is False
    assert result.status_code == 401
    assert m.failure_count("127.0.0.1", "password") == 1




def test_generated_password_hash_verifies_without_custom_salt():
    password_hash = hash_dashboard_password("correct")
    m = manager({"mode": "password", "password_hash": password_hash})

    login = m.login_password("correct", client_id="127.0.0.1")

    assert login.ok is True
    assert login.set_session_token

def test_password_mode_login_accepts_correct_password_and_issues_session():
    password_hash = hash_dashboard_password("correct", salt="testsalt", iterations=1000)
    m = manager({"mode": "password", "password_hash": password_hash, "session_ttl_seconds": 60})

    login = m.login_password("correct", client_id="127.0.0.1")

    assert login.ok is True
    assert login.set_session_token
    authed = m.authenticate_request(DummyRequest({"x-hermes-dashboard-session": login.set_session_token}))
    assert authed.ok is True
    assert authed.identity.source == "password"


def test_trusted_proxy_requires_identity_header():
    m = manager({"mode": "trusted-proxy", "trusted_proxy": {"user_header": "X-Forwarded-User"}})

    result = m.authenticate_request(DummyRequest())

    assert result.ok is False
    assert result.status_code == 401


def test_trusted_proxy_accepts_allowed_user():
    m = manager({
        "mode": "trusted-proxy",
        "trusted_proxy": {"user_header": "X-Forwarded-User", "allowed_users": ["rana"]},
    })

    result = m.authenticate_request(DummyRequest({"x-forwarded-user": "rana"}))

    assert result.ok is True
    assert result.identity.user == "rana"
    assert result.identity.source == "trusted-proxy"


def test_trusted_proxy_rejects_disallowed_user():
    m = manager({
        "mode": "trusted-proxy",
        "trusted_proxy": {"user_header": "X-Forwarded-User", "allowed_users": ["rana"]},
    })

    result = m.authenticate_request(DummyRequest({"x-forwarded-user": "other"}))

    assert result.ok is False
    assert result.status_code == 403


def test_tailscale_mode_extracts_identity():
    m = manager({"mode": "tailscale"})

    result = m.authenticate_request(DummyRequest({
        "tailscale-user-login": "rana@example.com",
        "tailscale-user-name": "Rana",
        "tailscale-user-profile-pic": "https://example.com/pic.png",
    }))

    assert result.ok is True
    assert result.identity.user == "rana@example.com"
    assert result.identity.name == "Rana"
    assert result.identity.profile_pic == "https://example.com/pic.png"
    assert result.identity.source == "tailscale"


def test_rate_limit_locks_after_failed_attempts():
    m = manager({
        "mode": "token",
        "token": "secret",
        "rate_limit": {"max_attempts": 2, "window_seconds": 60, "lockout_seconds": 300},
    })

    assert m.authenticate_request(DummyRequest({"authorization": "Bearer wrong"})).status_code == 401
    assert m.authenticate_request(DummyRequest({"authorization": "Bearer wrong"})).status_code == 401
    locked = m.authenticate_request(DummyRequest({"authorization": "Bearer wrong"}))

    assert locked.ok is False
    assert locked.status_code == 429


def test_correct_token_resets_failure_counter_before_lockout():
    m = manager({
        "mode": "token",
        "token": "secret",
        "rate_limit": {"max_attempts": 3, "window_seconds": 60, "lockout_seconds": 300},
    })
    assert m.authenticate_request(DummyRequest({"authorization": "Bearer wrong"})).status_code == 401

    assert m.authenticate_request(DummyRequest({"authorization": "Bearer secret"})).ok is True

    assert m.failure_count("127.0.0.1", "token") == 0


def test_token_mode_accepts_issued_browser_session():
    manager = DashboardAuthManager({"dashboard": {"auth": {"mode": "token", "token": "secret"}}})
    session = manager.issue_session(DashboardIdentity(source="token"))

    result = manager.authenticate_request(DummyRequest(headers={"X-Hermes-Dashboard-Session": session}))

    assert result.ok is True
    assert result.identity.source == "token"


def test_issued_session_expires_after_ttl():
    now = [1000.0]
    m = DashboardAuthManager({"dashboard": {"auth": {"mode": "password", "password": "correct", "session_ttl_seconds": 5}}}, env={}, clock=lambda: now[0])
    login = m.login_password("correct", client_id="127.0.0.1")
    assert login.set_session_token

    assert m.verify_session(login.set_session_token) is not None
    now[0] = 1006.0
    assert m.verify_session(login.set_session_token) is None


def test_environment_token_overrides_config_token_without_accepting_stale_config_token():
    # Mirrors OpenClaw stale env/config split-brain reports: env credentials win
    # deterministically, and the config token must not also authenticate.
    m = DashboardAuthManager(
        {"dashboard": {"auth": {"mode": "token", "token": "from-config"}}},
        env={"HERMES_DASHBOARD_TOKEN": "from-env"},
    )

    assert m.authenticate_request(DummyRequest({"authorization": "Bearer from-env"})).ok is True
    assert m.authenticate_request(DummyRequest({"authorization": "Bearer from-config"})).status_code == 401


def test_environment_mode_overrides_config_mode_for_dashboard_auth():
    m = DashboardAuthManager(
        {"dashboard": {"auth": {"mode": "none", "token": "secret"}}},
        env={"HERMES_DASHBOARD_AUTH_MODE": "token", "HERMES_DASHBOARD_TOKEN": "secret"},
    )

    assert m.mode().value == "token"
    assert m.authenticate_request(DummyRequest()).status_code == 401
    assert m.authenticate_request(DummyRequest({"authorization": "Bearer secret"})).ok is True


def test_rate_limit_lockout_expires_and_allows_correct_token_again():
    now = [1000.0]
    m = DashboardAuthManager(
        {"dashboard": {"auth": {
            "mode": "token",
            "token": "secret",
            "rate_limit": {"max_attempts": 1, "window_seconds": 60, "lockout_seconds": 5},
        }}},
        env={},
        clock=lambda: now[0],
    )

    assert m.authenticate_request(DummyRequest({"authorization": "Bearer wrong"})).status_code == 401
    assert m.authenticate_request(DummyRequest({"authorization": "Bearer secret"})).status_code == 429
    now[0] = 1006.0
    assert m.authenticate_request(DummyRequest({"authorization": "Bearer secret"})).ok is True
