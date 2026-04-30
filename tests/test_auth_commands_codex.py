from types import SimpleNamespace

import pytest

from agent.credential_pool import AUTH_TYPE_OAUTH
from hermes_cli import auth_commands


class _FakePool:
    def __init__(self):
        self._entries = []

    def entries(self):
        return list(self._entries)

    def add_entry(self, entry):
        self._entries.append(entry)


def _args(**overrides):
    data = {
        "provider": "openai-codex",
        "auth_type": "oauth",
        "label": "codex-test",
        "api_key": None,
        "portal_url": None,
        "inference_url": None,
        "client_id": None,
        "scope": None,
        "auth_method": None,
        "no_browser": False,
        "timeout": None,
        "insecure": False,
        "ca_bundle": None,
    }
    data.update(overrides)
    return SimpleNamespace(**data)


def _creds(auth_mode):
    return {
        "tokens": {
            "access_token": f"access-{auth_mode}",
            "refresh_token": f"refresh-{auth_mode}",
        },
        "base_url": "https://chatgpt.com/backend-api/codex",
        "last_refresh": "2026-04-30T00:00:00Z",
        "auth_mode": auth_mode,
        "account_id": "acct_123",
        "expires_at_ms": 1234567890000,
        "expires": 1234567890,
    }


def _patch_codex_auth(monkeypatch):
    pool = _FakePool()
    calls = []
    saved = []

    monkeypatch.setattr(auth_commands, "load_pool", lambda provider: pool)
    monkeypatch.setattr(auth_commands.auth_mod, "unsuppress_credential_source", lambda provider, source: calls.append(("unsuppress", provider, source)))
    monkeypatch.setattr(auth_commands.auth_mod, "_save_codex_tokens", lambda *args, **kwargs: saved.append((args, kwargs)))
    monkeypatch.setattr(auth_commands.auth_mod, "_codex_device_code_login", lambda: calls.append(("device",)) or _creds("device_code"))
    monkeypatch.setattr(
        auth_commands.auth_mod,
        "_codex_browser_oauth_login",
        lambda **kwargs: calls.append(("browser", kwargs)) or _creds("browser_oauth_pkce"),
    )
    return pool, calls, saved


def test_auth_add_openai_codex_oauth_prompts_and_uses_browser_choice(monkeypatch, capsys):
    pool, calls, saved = _patch_codex_auth(monkeypatch)
    monkeypatch.setattr("builtins.input", lambda prompt="": "2")

    auth_commands.auth_add_command(_args(no_browser=True))

    assert ("browser", {"open_browser": False}) in calls
    assert ("device",) not in calls
    assert saved[0][1]["auth_mode"] == "browser_oauth_pkce"
    assert len(pool.entries()) == 1
    entry = pool.entries()[0]
    assert entry.auth_type == AUTH_TYPE_OAUTH
    assert entry.source == "manual:device_code"
    assert entry.access_token == "access-browser_oauth_pkce"
    assert entry.extra["auth_mode"] == "browser_oauth_pkce"
    assert "Choose OpenAI Codex auth method" in capsys.readouterr().out


def test_auth_add_openai_codex_oauth_defaults_to_device_choice(monkeypatch):
    pool, calls, saved = _patch_codex_auth(monkeypatch)
    monkeypatch.setattr("builtins.input", lambda prompt="": "")

    auth_commands.auth_add_command(_args())

    assert ("device",) in calls
    assert not any(call[0] == "browser" for call in calls)
    assert saved[0][1]["auth_mode"] == "device_code"
    entry = pool.entries()[0]
    assert entry.access_token == "access-device_code"
    assert entry.extra["auth_mode"] == "device_code"


@pytest.mark.parametrize("alias", ["browser", "browser_oauth", "browser_oauth_pkce", "pkce"])
def test_auth_add_openai_codex_auth_method_flag_uses_browser_without_prompt(monkeypatch, alias):
    pool, calls, _saved = _patch_codex_auth(monkeypatch)
    monkeypatch.setattr("builtins.input", lambda prompt="": pytest.fail("should not prompt when auth_method is explicit"))

    auth_commands.auth_add_command(_args(auth_method=alias))

    assert any(call[0] == "browser" for call in calls)
    assert pool.entries()[0].extra["auth_mode"] == "browser_oauth_pkce"


def test_auth_add_openai_codex_auth_method_flag_uses_device_without_prompt(monkeypatch):
    pool, calls, _saved = _patch_codex_auth(monkeypatch)
    monkeypatch.setattr("builtins.input", lambda prompt="": pytest.fail("should not prompt when auth_method is explicit"))

    auth_commands.auth_add_command(_args(auth_method="device"))

    assert ("device",) in calls
    assert pool.entries()[0].extra["auth_mode"] == "device_code"
