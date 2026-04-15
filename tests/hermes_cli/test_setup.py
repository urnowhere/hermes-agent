"""Tests for setup.py configuration flows."""
import json
import sys
import types

import pytest

from hermes_cli.auth import get_active_provider
from hermes_cli import setup as setup_mod
from hermes_cli.config import load_config, save_config
from hermes_cli.setup import setup_model_provider, setup_tts


def _maybe_keep_current_tts(question, choices):
    if question != "Select TTS provider:":
        return None
    assert choices[-1].startswith("Keep current (")
    return len(choices) - 1


def _clear_provider_env(monkeypatch):
    for key in (
        "NOUS_API_KEY",
        "OPENROUTER_API_KEY",
        "OPENAI_BASE_URL",
        "OPENAI_API_KEY",
        "LLM_MODEL",
    ):
        monkeypatch.delenv(key, raising=False)


def _stub_tts(monkeypatch):
    """Stub out TTS prompts so setup_model_provider doesn't block."""
    monkeypatch.setattr("hermes_cli.setup.prompt_choice", lambda q, c, d=0: (
        _maybe_keep_current_tts(q, c) if _maybe_keep_current_tts(q, c) is not None
        else d
    ))
    monkeypatch.setattr("hermes_cli.setup.prompt_yes_no", lambda *a, **kw: False)


def _write_model_config(tmp_path, provider, base_url="", model_name="test-model"):
    """Simulate what a _model_flow_* function writes to disk."""
    cfg = load_config()
    m = cfg.get("model")
    if not isinstance(m, dict):
        m = {"default": m} if m else {}
        cfg["model"] = m
    m["provider"] = provider
    if base_url:
        m["base_url"] = base_url
    if model_name:
        m["default"] = model_name
    save_config(cfg)


def test_setup_delegates_to_select_provider_and_model(tmp_path, monkeypatch):
    """setup_model_provider calls select_provider_and_model and syncs config."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    _clear_provider_env(monkeypatch)
    _stub_tts(monkeypatch)

    config = load_config()

    def fake_select():
        _write_model_config(tmp_path, "custom", "http://localhost:11434/v1", "qwen3.5:32b")

    monkeypatch.setattr("hermes_cli.main.select_provider_and_model", fake_select)

    setup_model_provider(config)
    save_config(config)

    reloaded = load_config()
    assert isinstance(reloaded["model"], dict)
    assert reloaded["model"]["provider"] == "custom"
    assert reloaded["model"]["base_url"] == "http://localhost:11434/v1"
    assert reloaded["model"]["default"] == "qwen3.5:32b"


def test_setup_syncs_openrouter_from_disk(tmp_path, monkeypatch):
    """When select_provider_and_model saves OpenRouter config to disk,
    the wizard's config dict picks it up."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    _clear_provider_env(monkeypatch)
    _stub_tts(monkeypatch)

    config = load_config()
    assert isinstance(config.get("model"), str)  # fresh install

    def fake_select():
        _write_model_config(tmp_path, "openrouter", model_name="anthropic/claude-opus-4.6")

    monkeypatch.setattr("hermes_cli.main.select_provider_and_model", fake_select)

    setup_model_provider(config)
    save_config(config)

    reloaded = load_config()
    assert isinstance(reloaded["model"], dict)
    assert reloaded["model"]["provider"] == "openrouter"


def test_setup_syncs_nous_from_disk(tmp_path, monkeypatch):
    """Nous OAuth writes config to disk; wizard config dict must pick it up."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    _clear_provider_env(monkeypatch)
    _stub_tts(monkeypatch)

    config = load_config()

    def fake_select():
        _write_model_config(tmp_path, "nous", "https://inference.example.com/v1", "gemini-3-flash")

    monkeypatch.setattr("hermes_cli.main.select_provider_and_model", fake_select)

    setup_model_provider(config)
    save_config(config)

    reloaded = load_config()
    assert isinstance(reloaded["model"], dict)
    assert reloaded["model"]["provider"] == "nous"
    assert reloaded["model"]["base_url"] == "https://inference.example.com/v1"


def test_setup_custom_providers_synced(tmp_path, monkeypatch):
    """custom_providers written by select_provider_and_model must survive."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    _clear_provider_env(monkeypatch)
    _stub_tts(monkeypatch)

    config = load_config()

    def fake_select():
        _write_model_config(tmp_path, "custom", "http://localhost:8080/v1", "llama3")
        cfg = load_config()
        cfg["custom_providers"] = [{"name": "Local", "base_url": "http://localhost:8080/v1"}]
        save_config(cfg)

    monkeypatch.setattr("hermes_cli.main.select_provider_and_model", fake_select)

    setup_model_provider(config)
    save_config(config)

    reloaded = load_config()
    assert reloaded.get("custom_providers") == [{"name": "Local", "base_url": "http://localhost:8080/v1"}]


def test_setup_gateway_skips_service_install_when_systemctl_missing(monkeypatch, capsys):
    env = {
        "TELEGRAM_BOT_TOKEN": "",
        "TELEGRAM_HOME_CHANNEL": "",
        "DISCORD_BOT_TOKEN": "",
        "DISCORD_HOME_CHANNEL": "",
        "SLACK_BOT_TOKEN": "",
        "SLACK_HOME_CHANNEL": "",
        "MATRIX_HOMESERVER": "https://matrix.example.com",
        "MATRIX_USER_ID": "@alice:example.com",
        "MATRIX_PASSWORD": "",
        "MATRIX_ACCESS_TOKEN": "token",
        "BLUEBUBBLES_SERVER_URL": "",
        "BLUEBUBBLES_HOME_CHANNEL": "",
        "WHATSAPP_ENABLED": "",
        "WEBHOOK_ENABLED": "",
    }

    monkeypatch.setattr(setup_mod, "get_env_value", lambda key: env.get(key, ""))
    monkeypatch.setattr(setup_mod, "prompt_yes_no", lambda *args, **kwargs: False)
    monkeypatch.setattr("platform.system", lambda: "Linux")

    import hermes_cli.gateway as gateway_mod

    monkeypatch.setattr(gateway_mod, "supports_systemd_services", lambda: False)
    monkeypatch.setattr(gateway_mod, "is_macos", lambda: False)
    monkeypatch.setattr(gateway_mod, "_is_service_installed", lambda: False)
    monkeypatch.setattr(gateway_mod, "_is_service_running", lambda: False)

    setup_mod.setup_gateway({})

    out = capsys.readouterr().out
    assert "Messaging platforms configured!" in out
    assert "Start the gateway to bring your bots online:" in out
    assert "hermes gateway" in out


def test_setup_gateway_in_container_shows_docker_guidance(monkeypatch, capsys):
    """setup_gateway() in a Docker container shows Docker-specific restart instructions."""
    env = {
        "TELEGRAM_BOT_TOKEN": "",
        "TELEGRAM_HOME_CHANNEL": "",
        "DISCORD_BOT_TOKEN": "",
        "DISCORD_HOME_CHANNEL": "",
        "SLACK_BOT_TOKEN": "",
        "SLACK_HOME_CHANNEL": "",
        "MATRIX_HOMESERVER": "https://matrix.example.com",
        "MATRIX_USER_ID": "@alice:example.com",
        "MATRIX_PASSWORD": "",
        "MATRIX_ACCESS_TOKEN": "token",
        "BLUEBUBBLES_SERVER_URL": "",
        "BLUEBUBBLES_HOME_CHANNEL": "",
        "WHATSAPP_ENABLED": "",
        "WEBHOOK_ENABLED": "",
    }

    monkeypatch.setattr(setup_mod, "get_env_value", lambda key: env.get(key, ""))
    monkeypatch.setattr(setup_mod, "prompt_yes_no", lambda *args, **kwargs: False)
    monkeypatch.setattr("platform.system", lambda: "Linux")

    import hermes_cli.gateway as gateway_mod

    monkeypatch.setattr(gateway_mod, "supports_systemd_services", lambda: False)
    monkeypatch.setattr(gateway_mod, "is_macos", lambda: False)
    monkeypatch.setattr(gateway_mod, "_is_service_installed", lambda: False)
    monkeypatch.setattr(gateway_mod, "_is_service_running", lambda: False)

    # Patch is_container at the import location in setup.py
    import hermes_constants
    monkeypatch.setattr(hermes_constants, "is_container", lambda: True)

    setup_mod.setup_gateway({})

    out = capsys.readouterr().out
    assert "Messaging platforms configured!" in out
    assert "docker" in out.lower() or "Docker" in out
    assert "restart" in out.lower()


def test_setup_syncs_custom_provider_removal_from_disk(tmp_path, monkeypatch):
    """Removing the last custom provider in model setup should persist."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    _clear_provider_env(monkeypatch)
    _stub_tts(monkeypatch)

    config = load_config()
    config["custom_providers"] = [{"name": "Local", "base_url": "http://localhost:8080/v1"}]
    save_config(config)

    def fake_select():
        cfg = load_config()
        cfg["model"] = {"provider": "openrouter", "default": "anthropic/claude-opus-4.6"}
        cfg["custom_providers"] = []
        save_config(cfg)

    monkeypatch.setattr("hermes_cli.main.select_provider_and_model", fake_select)

    setup_model_provider(config)
    save_config(config)

    reloaded = load_config()
    assert reloaded.get("custom_providers") == []


def test_setup_cancel_preserves_existing_config(tmp_path, monkeypatch):
    """When the user cancels provider selection, existing config is preserved."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    _clear_provider_env(monkeypatch)
    _stub_tts(monkeypatch)

    # Pre-set a provider
    _write_model_config(tmp_path, "openrouter", model_name="gpt-4o")

    config = load_config()
    assert config["model"]["provider"] == "openrouter"

    def fake_select():
        pass  # user cancelled — nothing written to disk

    monkeypatch.setattr("hermes_cli.main.select_provider_and_model", fake_select)

    setup_model_provider(config)
    save_config(config)

    reloaded = load_config()
    assert isinstance(reloaded["model"], dict)
    assert reloaded["model"]["provider"] == "openrouter"
    assert reloaded["model"]["default"] == "gpt-4o"


def test_setup_exception_in_select_gracefully_handled(tmp_path, monkeypatch):
    """If select_provider_and_model raises, setup continues with existing config."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    _clear_provider_env(monkeypatch)
    _stub_tts(monkeypatch)

    config = load_config()

    def fake_select():
        raise RuntimeError("something broke")

    monkeypatch.setattr("hermes_cli.main.select_provider_and_model", fake_select)

    # Should not raise
    setup_model_provider(config)


def test_setup_keyboard_interrupt_gracefully_handled(tmp_path, monkeypatch):
    """KeyboardInterrupt during provider selection is handled."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    _clear_provider_env(monkeypatch)
    _stub_tts(monkeypatch)

    config = load_config()

    def fake_select():
        raise KeyboardInterrupt()

    monkeypatch.setattr("hermes_cli.main.select_provider_and_model", fake_select)

    setup_model_provider(config)


def test_select_provider_and_model_warns_if_named_custom_provider_disappears(
    tmp_path, monkeypatch, capsys
):
    """If a saved custom provider is deleted mid-selection, show a warning instead of silently doing nothing."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    _clear_provider_env(monkeypatch)

    cfg = load_config()
    cfg["custom_providers"] = [{"name": "Local", "base_url": "http://localhost:8080/v1"}]
    save_config(cfg)

    def fake_prompt_provider_choice(choices, default=0):
        current = load_config()
        current["custom_providers"] = []
        save_config(current)
        return next(i for i, label in enumerate(choices) if label.startswith("Local (localhost:8080/v1)"))

    monkeypatch.setattr("hermes_cli.auth.resolve_provider", lambda provider: None)
    monkeypatch.setattr("hermes_cli.main._prompt_provider_choice", fake_prompt_provider_choice)
    monkeypatch.setattr(
        "hermes_cli.main._model_flow_named_custom",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("named custom flow should not run")),
    )

    from hermes_cli.main import select_provider_and_model

    select_provider_and_model()

    out = capsys.readouterr().out
    assert "selected saved custom provider is no longer available" in out


def test_codex_setup_uses_runtime_access_token_for_live_model_list(tmp_path, monkeypatch):
    """Codex model list fetching uses the runtime access token."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-test-key")
    _clear_provider_env(monkeypatch)
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-test-key")

    config = load_config()
    _stub_tts(monkeypatch)

    def fake_select():
        _write_model_config(tmp_path, "openai-codex", "https://api.openai.com/v1", "gpt-4o")

    monkeypatch.setattr("hermes_cli.main.select_provider_and_model", fake_select)

    setup_model_provider(config)
    save_config(config)

    reloaded = load_config()
    assert isinstance(reloaded["model"], dict)
    assert reloaded["model"]["provider"] == "openai-codex"


def test_modal_setup_can_use_nous_subscription_without_modal_creds(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("hermes_cli.setup.managed_nous_tools_enabled", lambda: True)
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    config = load_config()

    def fake_prompt_choice(question, choices, default=0):
        if question == "Select terminal backend:":
            return 2
        if question == "Select how Modal execution should be billed:":
            return 0
        raise AssertionError(f"Unexpected prompt_choice call: {question}")

    def fake_prompt(message, *args, **kwargs):
        assert "Modal Token" not in message
        raise AssertionError(f"Unexpected prompt call: {message}")

    monkeypatch.setattr("hermes_cli.setup.prompt_choice", fake_prompt_choice)
    monkeypatch.setattr("hermes_cli.setup.prompt", fake_prompt)
    monkeypatch.setattr("hermes_cli.setup._prompt_container_resources", lambda config: None)
    monkeypatch.setattr(
        "hermes_cli.setup.get_nous_subscription_features",
        lambda config: type("Features", (), {"nous_auth_present": True})(),
    )
    monkeypatch.setitem(
        sys.modules,
        "tools.managed_tool_gateway",
        types.SimpleNamespace(
            is_managed_tool_gateway_ready=lambda vendor: vendor == "modal",
            resolve_managed_tool_gateway=lambda vendor: None,
        ),
    )

    from hermes_cli.setup import setup_terminal_backend

    setup_terminal_backend(config)

    out = capsys.readouterr().out
    assert config["terminal"]["backend"] == "modal"
    assert config["terminal"]["modal_mode"] == "managed"
    assert "bill to your subscription" in out


def test_modal_setup_persists_direct_mode_when_user_chooses_their_own_account(tmp_path, monkeypatch):
    monkeypatch.setattr("hermes_cli.setup.managed_nous_tools_enabled", lambda: True)
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.delenv("MODAL_TOKEN_ID", raising=False)
    monkeypatch.delenv("MODAL_TOKEN_SECRET", raising=False)
    config = load_config()

    def fake_prompt_choice(question, choices, default=0):
        if question == "Select terminal backend:":
            return 2
        if question == "Select how Modal execution should be billed:":
            return 1
        raise AssertionError(f"Unexpected prompt_choice call: {question}")

    prompt_values = iter(["token-id", "token-secret", ""])

    monkeypatch.setattr("hermes_cli.setup.prompt_choice", fake_prompt_choice)
    monkeypatch.setattr("hermes_cli.setup.prompt", lambda *args, **kwargs: next(prompt_values))
    monkeypatch.setattr("hermes_cli.setup._prompt_container_resources", lambda config: None)
    monkeypatch.setattr(
        "hermes_cli.setup.get_nous_subscription_features",
        lambda config: type("Features", (), {"nous_auth_present": True})(),
    )
    monkeypatch.setitem(
        sys.modules,
        "tools.managed_tool_gateway",
        types.SimpleNamespace(
            is_managed_tool_gateway_ready=lambda vendor: vendor == "modal",
            resolve_managed_tool_gateway=lambda vendor: None,
        ),
    )
    monkeypatch.setitem(sys.modules, "swe_rex", object())

    from hermes_cli.setup import setup_terminal_backend

    setup_terminal_backend(config)

    assert config["terminal"]["backend"] == "modal"
    assert config["terminal"]["modal_mode"] == "direct"


def test_resolve_hermes_chat_argv_prefers_which(monkeypatch):
    from hermes_cli import setup as setup_mod

    monkeypatch.setattr(setup_mod.shutil, "which", lambda name: "/usr/local/bin/hermes" if name == "hermes" else None)

    assert setup_mod._resolve_hermes_chat_argv() == ["/usr/local/bin/hermes", "chat"]


def test_resolve_hermes_chat_argv_falls_back_to_module(monkeypatch):
    from hermes_cli import setup as setup_mod

    monkeypatch.setattr(setup_mod.shutil, "which", lambda _name: None)
    monkeypatch.setattr(setup_mod.importlib.util, "find_spec", lambda name: object() if name == "hermes_cli" else None)

    assert setup_mod._resolve_hermes_chat_argv() == [sys.executable, "-m", "hermes_cli.main", "chat"]


def test_offer_launch_chat_execs_fresh_process(monkeypatch):
    from hermes_cli import setup as setup_mod

    monkeypatch.setattr(setup_mod, "prompt_yes_no", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(setup_mod, "_resolve_hermes_chat_argv", lambda: ["/usr/local/bin/hermes", "chat"])

    exec_calls = []

    def fake_execvp(path, argv):
        exec_calls.append((path, argv))
        raise SystemExit(0)

    monkeypatch.setattr(setup_mod.os, "execvp", fake_execvp)

    with pytest.raises(SystemExit):
        setup_mod._offer_launch_chat()

    assert exec_calls == [("/usr/local/bin/hermes", ["/usr/local/bin/hermes", "chat"])]


def test_offer_launch_chat_manual_fallback_when_unresolvable(monkeypatch, capsys):
    from hermes_cli import setup as setup_mod

    monkeypatch.setattr(setup_mod, "prompt_yes_no", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(setup_mod, "_resolve_hermes_chat_argv", lambda: None)

    setup_mod._offer_launch_chat()

    captured = capsys.readouterr()
    assert "Run 'hermes chat' manually" in captured.out

def test_setup_tts_can_configure_piper_with_model_name(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    config = load_config()

    def fake_prompt_choice(question, choices, default=0):
        if question == "Select TTS provider:":
            return choices.index("Piper (local on-device, free, 40+ languages, 150+ voice models)")
        if question == "How should Hermes use Piper models?":
            return 0
        if question == "Select Piper language:":
            return choices.index("Polish")
        if question == "Select Piper voice for Polish:":
            return choices.index("Gosia (medium, ~63MB)")
        raise AssertionError(f"Unexpected prompt_choice call: {question}")

    monkeypatch.setattr("hermes_cli.setup.prompt_choice", fake_prompt_choice)
    monkeypatch.setattr("hermes_cli.setup.prompt", lambda question, default=None, password=False: "piper")
    monkeypatch.setattr("hermes_cli.setup.prompt_yes_no", lambda question, default=True: True)
    monkeypatch.setattr("tools.tts_tool._resolve_piper_binary", lambda cfg: "/usr/local/bin/piper")
    monkeypatch.setattr("hermes_cli.setup._download_piper_model_during_setup", lambda cfg: (True, None))
    monkeypatch.setattr("hermes_cli.setup._check_piper_status", lambda cfg: (True, None))

    setup_tts(config)
    save_config(config)

    reloaded = load_config()
    assert reloaded["tts"]["provider"] == "piper"
    assert reloaded["tts"]["piper"]["model"] == "pl_PL-gosia-medium"
    assert reloaded["tts"]["piper"]["model_path"] == ""


def test_setup_tts_can_configure_piper_with_local_model_path(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    model_path = tmp_path / "voice.onnx"
    model_path.write_bytes(b"fake")
    config = load_config()

    def fake_prompt_choice(question, choices, default=0):
        if question == "Select TTS provider:":
            return choices.index("Piper (local on-device, free, 40+ languages, 150+ voice models)")
        if question == "How should Hermes use Piper models?":
            return choices.index("Local model path (manual/custom)")
        raise AssertionError(f"Unexpected prompt_choice call: {question}")

    prompt_answers = {
        "Piper binary path": "/usr/local/bin/piper",
        "Piper model path": str(model_path),
    }

    monkeypatch.setattr("hermes_cli.setup.prompt_choice", fake_prompt_choice)
    monkeypatch.setattr("hermes_cli.setup.prompt", lambda question, default=None, password=False: prompt_answers[question])
    monkeypatch.setattr("tools.tts_tool._resolve_piper_binary", lambda cfg: "/usr/local/bin/piper")
    monkeypatch.setattr("hermes_cli.setup._check_piper_status", lambda cfg: (True, None))

    setup_tts(config)
    save_config(config)

    reloaded = load_config()
    assert reloaded["tts"]["provider"] == "piper"
    assert reloaded["tts"]["piper"]["model_path"] == str(model_path)


def test_setup_tts_keeps_current_provider_when_piper_binary_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    config = load_config()

    def fake_prompt_choice(question, choices, default=0):
        if question == "Select TTS provider:":
            return choices.index("Piper (local on-device, free, 40+ languages, 150+ voice models)")
        raise AssertionError(f"Unexpected prompt_choice call: {question}")

    monkeypatch.setattr("hermes_cli.setup.prompt_choice", fake_prompt_choice)
    monkeypatch.setattr("hermes_cli.setup.prompt", lambda question, default=None, password=False: "piper")
    monkeypatch.setattr("hermes_cli.setup.prompt_yes_no", lambda question, default=True: False)
    monkeypatch.setattr(
        "tools.tts_tool._resolve_piper_binary",
        lambda cfg: (_ for _ in ()).throw(FileNotFoundError("Piper binary not found: piper")),
    )

    setup_tts(config)

    assert config["tts"]["provider"] == "edge"


def test_setup_tts_can_install_missing_piper_into_active_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    config = load_config()
    install_state = {"installed": False}

    def fake_prompt_choice(question, choices, default=0):
        if question == "Select TTS provider:":
            return choices.index("Piper (local on-device, free, 40+ languages, 150+ voice models)")
        if question == "How should Hermes use Piper models?":
            return 0
        if question == "Select Piper language:":
            return choices.index("Polish")
        if question == "Select Piper voice for Polish:":
            return choices.index("Gosia (medium, ~63MB)")
        raise AssertionError(f"Unexpected prompt_choice call: {question}")

    def fake_resolve_binary(_cfg):
        if not install_state["installed"]:
            raise FileNotFoundError("Piper binary not found: piper")
        return "/tmp/venv/bin/piper"

    monkeypatch.setattr("hermes_cli.setup.prompt_choice", fake_prompt_choice)
    monkeypatch.setattr("hermes_cli.setup.prompt", lambda question, default=None, password=False: "piper")
    monkeypatch.setattr("hermes_cli.setup.prompt_yes_no", lambda question, default=True: True)
    monkeypatch.setattr("tools.tts_tool._resolve_piper_binary", fake_resolve_binary)
    monkeypatch.setattr(
        "hermes_cli.setup._install_piper_cli",
        lambda: install_state.__setitem__("installed", True) or True,
    )
    monkeypatch.setattr("hermes_cli.setup._download_piper_model_during_setup", lambda cfg: (True, None))
    monkeypatch.setattr("hermes_cli.setup._check_piper_status", lambda cfg: (True, None))

    setup_tts(config)
    save_config(config)

    reloaded = load_config()
    assert install_state["installed"] is True
    assert reloaded["tts"]["provider"] == "piper"
    assert reloaded["tts"]["piper"]["model"] == "pl_PL-gosia-medium"


def test_setup_tts_accepts_custom_piper_model_id(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    config = load_config()

    def fake_prompt_choice(question, choices, default=0):
        if question == "Select TTS provider:":
            return choices.index("Piper (local on-device, free, 40+ languages, 150+ voice models)")
        if question == "How should Hermes use Piper models?":
            return 0
        if question == "Select Piper language:":
            return choices.index("Other / any Piper model")
        raise AssertionError(f"Unexpected prompt_choice call: {question}")

    def fake_prompt(question, default=None, password=False):
        if question == "Piper binary path":
            return "piper"
        if question == "Piper model id":
            return "en_US-amy-medium"
        raise AssertionError(f"Unexpected prompt call: {question}")

    monkeypatch.setattr("hermes_cli.setup.prompt_choice", fake_prompt_choice)
    monkeypatch.setattr("hermes_cli.setup.prompt", fake_prompt)
    monkeypatch.setattr("hermes_cli.setup.prompt_yes_no", lambda question, default=True: False)
    monkeypatch.setattr("tools.tts_tool._resolve_piper_binary", lambda cfg: "/usr/local/bin/piper")
    monkeypatch.setattr("hermes_cli.setup._check_piper_status", lambda cfg: (True, None))

    setup_tts(config)
    save_config(config)

    reloaded = load_config()
    assert reloaded["tts"]["provider"] == "piper"
    assert reloaded["tts"]["piper"]["model"] == "en_US-amy-medium"


def test_setup_tts_can_select_ukrainian_piper_model_from_primary_menu(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    config = load_config()

    def fake_prompt_choice(question, choices, default=0):
        if question == "Select TTS provider:":
            return choices.index("Piper (local on-device, free, 40+ languages, 150+ voice models)")
        if question == "How should Hermes use Piper models?":
            return 0
        if question == "Select Piper language:":
            return choices.index("Ukrainian")
        if question == "Select Piper voice for Ukrainian:":
            return choices.index("Ukrainian Tts (medium, ~77MB)")
        raise AssertionError(f"Unexpected prompt_choice call: {question}")

    monkeypatch.setattr("hermes_cli.setup.prompt_choice", fake_prompt_choice)
    monkeypatch.setattr("hermes_cli.setup.prompt", lambda question, default=None, password=False: "piper")
    monkeypatch.setattr("hermes_cli.setup.prompt_yes_no", lambda question, default=True: False)
    monkeypatch.setattr("tools.tts_tool._resolve_piper_binary", lambda cfg: "/usr/local/bin/piper")
    monkeypatch.setattr("hermes_cli.setup._check_piper_status", lambda cfg: (True, None))

    setup_tts(config)
    save_config(config)

    reloaded = load_config()
    assert reloaded["tts"]["provider"] == "piper"
    assert reloaded["tts"]["piper"]["model"] == "uk_UA-ukrainian_tts-medium"


def test_setup_tts_can_select_russian_piper_model_from_more_languages(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    config = load_config()

    def fake_prompt_choice(question, choices, default=0):
        if question == "Select TTS provider:":
            return choices.index("Piper (local on-device, free, 40+ languages, 150+ voice models)")
        if question == "How should Hermes use Piper models?":
            return 0
        if question == "Select Piper language:":
            return choices.index("More languages")
        if question == "Select Piper language (more):":
            return choices.index("Russian")
        if question == "Select Piper voice for Russian:":
            return choices.index("Denis (medium, ~63MB)")
        raise AssertionError(f"Unexpected prompt_choice call: {question}")

    monkeypatch.setattr("hermes_cli.setup.prompt_choice", fake_prompt_choice)
    monkeypatch.setattr("hermes_cli.setup.prompt", lambda question, default=None, password=False: "piper")
    monkeypatch.setattr("hermes_cli.setup.prompt_yes_no", lambda question, default=True: False)
    monkeypatch.setattr("tools.tts_tool._resolve_piper_binary", lambda cfg: "/usr/local/bin/piper")
    monkeypatch.setattr("hermes_cli.setup._check_piper_status", lambda cfg: (True, None))

    setup_tts(config)
    save_config(config)

    reloaded = load_config()
    assert reloaded["tts"]["provider"] == "piper"
    assert reloaded["tts"]["piper"]["model"] == "ru_RU-denis-medium"


def test_setup_tts_can_go_back_from_piper_more_languages(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    config = load_config()

    calls = {"more": 0}

    def fake_prompt_choice(question, choices, default=0):
        if question == "Select TTS provider:":
            return choices.index("Piper (local on-device, free, 40+ languages, 150+ voice models)")
        if question == "How should Hermes use Piper models?":
            return 0
        if question == "Select Piper language:":
            return choices.index("More languages") if calls["more"] == 0 else choices.index("Polish")
        if question == "Select Piper language (more):":
            calls["more"] += 1
            return choices.index("Back")
        if question == "Select Piper voice for Polish:":
            return choices.index("Gosia (medium, ~63MB)")
        raise AssertionError(f"Unexpected prompt_choice call: {question}")

    monkeypatch.setattr("hermes_cli.setup.prompt_choice", fake_prompt_choice)
    monkeypatch.setattr("hermes_cli.setup.prompt", lambda question, default=None, password=False: "piper")
    monkeypatch.setattr("hermes_cli.setup.prompt_yes_no", lambda question, default=True: False)
    monkeypatch.setattr("tools.tts_tool._resolve_piper_binary", lambda cfg: "/usr/local/bin/piper")
    monkeypatch.setattr("hermes_cli.setup._check_piper_status", lambda cfg: (True, None))

    setup_tts(config)
    save_config(config)

    reloaded = load_config()
    assert reloaded["tts"]["provider"] == "piper"
    assert reloaded["tts"]["piper"]["model"] == "pl_PL-gosia-medium"


def test_setup_tts_can_go_back_from_local_piper_models(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    local_models_dir = tmp_path / "tts" / "piper"
    local_models_dir.mkdir(parents=True)
    (local_models_dir / "pl_PL-meski_wg_glos-medium.onnx").write_bytes(b"fake")
    (local_models_dir / "pl_PL-meski_wg_glos-medium.onnx.json").write_text("{}")
    config = load_config()

    mode_calls = {"count": 0}

    def fake_prompt_choice(question, choices, default=0):
        if question == "Select TTS provider:":
            return choices.index("Piper (local on-device, free, 40+ languages, 150+ voice models)")
        if question == "How should Hermes use Piper models?":
            mode_calls["count"] += 1
            if mode_calls["count"] == 1:
                return choices.index("Local downloaded models")
            return choices.index("Local model path (manual/custom)")
        if question == "Select local Piper model:":
            return choices.index("Back")
        raise AssertionError(f"Unexpected prompt_choice call: {question}")

    def fake_prompt(question, default=None, password=False):
        if question == "Piper binary path":
            return "piper"
        if question == "Piper model path":
            return "/tmp/manual-piper.onnx"
        raise AssertionError(f"Unexpected prompt call: {question}")

    monkeypatch.setattr("hermes_cli.setup.prompt_choice", fake_prompt_choice)
    monkeypatch.setattr("hermes_cli.setup.prompt", fake_prompt)
    monkeypatch.setattr("tools.tts_tool._resolve_piper_binary", lambda cfg: "/usr/local/bin/piper")
    monkeypatch.setattr("hermes_cli.setup._check_piper_status", lambda cfg: (True, None))

    setup_tts(config)
    save_config(config)

    reloaded = load_config()
    assert reloaded["tts"]["provider"] == "piper"
    assert reloaded["tts"]["piper"]["model_path"] == "/tmp/manual-piper.onnx"


def test_setup_tts_can_go_back_from_piper_voice_selection(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    config = load_config()

    calls = {"voice": 0}

    def fake_prompt_choice(question, choices, default=0):
        if question == "Select TTS provider:":
            return choices.index("Piper (local on-device, free, 40+ languages, 150+ voice models)")
        if question == "How should Hermes use Piper models?":
            return 0
        if question == "Select Piper language:":
            return choices.index("Polish") if calls["voice"] == 0 else choices.index("German")
        if question == "Select Piper voice for Polish:":
            calls["voice"] += 1
            return choices.index("Back")
        if question == "Select Piper voice for German:":
            return choices.index("Eva K (x_low, ~21MB)")
        raise AssertionError(f"Unexpected prompt_choice call: {question}")

    monkeypatch.setattr("hermes_cli.setup.prompt_choice", fake_prompt_choice)
    monkeypatch.setattr("hermes_cli.setup.prompt", lambda question, default=None, password=False: "piper")
    monkeypatch.setattr("hermes_cli.setup.prompt_yes_no", lambda question, default=True: False)
    monkeypatch.setattr("tools.tts_tool._resolve_piper_binary", lambda cfg: "/usr/local/bin/piper")
    monkeypatch.setattr("hermes_cli.setup._check_piper_status", lambda cfg: (True, None))

    setup_tts(config)
    save_config(config)

    reloaded = load_config()
    assert reloaded["tts"]["provider"] == "piper"
    assert reloaded["tts"]["piper"]["model"] == "de_DE-eva_k-x_low"


def test_setup_tts_existing_ready_piper_starts_from_model_menu(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    config = load_config()
    config["tts"]["provider"] = "piper"
    config["tts"]["piper"] = {
        "binary_path": "/usr/local/bin/piper",
        "model": "pl_PL-gosia-medium",
        "model_path": "",
        "config_path": "",
    }

    def fake_prompt_choice(question, choices, default=0):
        if question == "Select TTS provider:":
            return choices.index("Piper (local on-device, free, 40+ languages, 150+ voice models)")
        if question == "How should Hermes use Piper models?":
            return choices.index("Keep current Piper settings")
        raise AssertionError(f"Unexpected prompt_choice call: {question}")

    def fail_prompt(question, default=None, password=False):
        if question == "Piper binary path":
            raise AssertionError("Existing ready Piper setup should not prompt for binary path first")
        raise AssertionError(f"Unexpected prompt call: {question}")

    monkeypatch.setattr("hermes_cli.setup.prompt_choice", fake_prompt_choice)
    monkeypatch.setattr("hermes_cli.setup.prompt", fail_prompt)
    monkeypatch.setattr("tools.tts_tool._resolve_piper_binary", lambda cfg: "/usr/local/bin/piper")
    monkeypatch.setattr("hermes_cli.setup._check_piper_status", lambda cfg: (True, None))

    setup_tts(config)
    save_config(config)

    reloaded = load_config()
    assert reloaded["tts"]["provider"] == "piper"
    assert reloaded["tts"]["piper"]["binary_path"] == "/usr/local/bin/piper"
    assert reloaded["tts"]["piper"]["model"] == "pl_PL-gosia-medium"


def test_setup_tts_existing_ready_piper_can_change_binary_path_via_advanced_settings(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    config = load_config()
    config["tts"]["provider"] = "piper"
    config["tts"]["piper"] = {
        "binary_path": "/usr/local/bin/piper",
        "model": "pl_PL-gosia-medium",
        "model_path": "",
        "config_path": "",
    }
    prompt_calls = []
    mode_calls = {"count": 0}

    def fake_prompt_choice(question, choices, default=0):
        if question == "Select TTS provider:":
            return choices.index("Piper (local on-device, free, 40+ languages, 150+ voice models)")
        if question == "How should Hermes use Piper models?":
            mode_calls["count"] += 1
            if mode_calls["count"] == 1:
                return choices.index("Advanced settings")
            return choices.index("Keep current Piper settings")
        raise AssertionError(f"Unexpected prompt_choice call: {question}")

    def fake_prompt(question, default=None, password=False):
        prompt_calls.append(question)
        if question == "Piper binary path":
            return "/opt/piper/piper"
        raise AssertionError(f"Unexpected prompt call: {question}")

    monkeypatch.setattr("hermes_cli.setup.prompt_choice", fake_prompt_choice)
    monkeypatch.setattr("hermes_cli.setup.prompt", fake_prompt)
    monkeypatch.setattr("tools.tts_tool._resolve_piper_binary", lambda cfg: str(cfg.get("binary_path") or ""))
    monkeypatch.setattr("hermes_cli.setup._check_piper_status", lambda cfg: (True, None))

    setup_tts(config)
    save_config(config)

    reloaded = load_config()
    assert prompt_calls == ["Piper binary path"]
    assert reloaded["tts"]["piper"]["binary_path"] == "/opt/piper/piper"


def test_setup_tts_can_select_local_downloaded_piper_model(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    local_models_dir = tmp_path / "tts" / "piper"
    local_models_dir.mkdir(parents=True)
    (local_models_dir / "pl_PL-meski_wg_glos-medium.onnx").write_bytes(b"fake")
    (local_models_dir / "pl_PL-meski_wg_glos-medium.onnx.json").write_text("{}")
    config = load_config()

    def fake_prompt_choice(question, choices, default=0):
        if question == "Select TTS provider:":
            return choices.index("Piper (local on-device, free, 40+ languages, 150+ voice models)")
        if question == "How should Hermes use Piper models?":
            return choices.index("Local downloaded models")
        if question == "Select local Piper model:":
            return 0
        raise AssertionError(f"Unexpected prompt_choice call: {question}")

    monkeypatch.setattr("hermes_cli.setup.prompt_choice", fake_prompt_choice)
    monkeypatch.setattr(
        "hermes_cli.setup.prompt",
        lambda question, default=None, password=False: "piper" if question == "Piper binary path" else "",
    )
    monkeypatch.setattr("tools.tts_tool._resolve_piper_binary", lambda cfg: "/usr/local/bin/piper")
    monkeypatch.setattr("hermes_cli.setup._check_piper_status", lambda cfg: (True, None))

    setup_tts(config)
    save_config(config)

    reloaded = load_config()
    assert reloaded["tts"]["provider"] == "piper"
    assert reloaded["tts"]["piper"]["model"] == "pl_PL-meski_wg_glos-medium"
    assert reloaded["tts"]["piper"]["model_path"] == ""
