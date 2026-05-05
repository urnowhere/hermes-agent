"""Tests for Anthropic credential persistence helpers."""

from hermes_cli.config import load_env


def test_save_anthropic_oauth_token_uses_token_slot_and_clears_api_key(tmp_path, monkeypatch):
    home = tmp_path / "hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))

    from hermes_cli.config import save_anthropic_oauth_token

    save_anthropic_oauth_token("sk-ant-oat01-test-token")

    env_vars = load_env()
    assert env_vars["ANTHROPIC_TOKEN"] == "sk-ant-oat01-test-token"
    assert env_vars["ANTHROPIC_API_KEY"] == ""


def test_use_anthropic_claude_code_credentials_clears_env_slots(tmp_path, monkeypatch):
    home = tmp_path / "hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))

    from hermes_cli.config import save_anthropic_oauth_token, use_anthropic_claude_code_credentials

    save_anthropic_oauth_token("sk-ant-oat01-token")
    use_anthropic_claude_code_credentials()

    env_vars = load_env()
    assert env_vars["ANTHROPIC_TOKEN"] == ""
    assert env_vars["ANTHROPIC_API_KEY"] == ""


def test_save_anthropic_api_key_uses_api_key_slot_and_clears_token(tmp_path, monkeypatch):
    home = tmp_path / "hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))

    from hermes_cli.config import save_anthropic_api_key

    save_anthropic_api_key("not-a-placeholder-key")

    env_vars = load_env()
    assert env_vars["ANTHROPIC_API_KEY"] == "not-a-placeholder-key"
    assert env_vars["ANTHROPIC_TOKEN"] == ""


def test_get_auth_status_reports_anthropic_wif(tmp_path, monkeypatch):
    home = tmp_path / "hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))

    identity_file = tmp_path / "identity.jwt"
    identity_file.write_text("header.payload.signature", encoding="utf-8")
    auth_payload = {
        "version": 1,
        "providers": {
            "anthropic": {
                "auth_type": "wif",
                "source": "manual:wif",
                "label": "anthropic-wif",
                "federation_rule_id": "fdrl_test123",
                "organization_id": "org_test456",
                "service_account_id": "svac_test789",
                "identity_token_file": str(identity_file),
                "api_base_url": "https://api.anthropic.com",
            }
        },
    }
    (home / "auth.json").write_text(__import__("json").dumps(auth_payload), encoding="utf-8")

    from hermes_cli.auth import get_auth_status

    status = get_auth_status("anthropic")
    assert status["auth_type"] == "wif"
    assert status["logged_in"] is True
    assert status["identity_token_file"] == str(identity_file)
    assert status["identity_token_file_exists"] is True
    assert status["federation_rule_id"] == "fdrl_test123"
    assert status["service_account_id"] == "svac_test789"
