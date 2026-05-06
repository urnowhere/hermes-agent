"""Config defaults for dashboard authentication."""

from hermes_cli.config import DEFAULT_CONFIG


def test_dashboard_auth_defaults_include_openclaw_modes():
    auth = DEFAULT_CONFIG["dashboard"]["auth"]

    assert auth["mode"] == "none"
    assert auth["rate_limit"]["max_attempts"] == 10
    assert auth["trusted_proxy"]["user_header"] == "X-Forwarded-User"
    assert auth["tailscale"]["user_header"] == "Tailscale-User-Login"
