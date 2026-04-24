"""Tests that provider selection via `hermes model` always persists correctly.

Regression tests for the bug where _save_model_choice could save config.model
as a plain string, causing subsequent provider writes (which check
isinstance(model, dict)) to silently fail — leaving the provider unset and
falling back to auto-detection.
"""

import os
from unittest.mock import patch, MagicMock

import pytest


@pytest.fixture
def config_home(tmp_path, monkeypatch):
    """Isolated HERMES_HOME with a minimal string-format config."""
    home = tmp_path / "hermes"
    home.mkdir()
    config_yaml = home / "config.yaml"
    # Start with model as a plain string — the format that triggered the bug
    config_yaml.write_text("model: some-old-model\n")
    env_file = home / ".env"
    env_file.write_text("")
    monkeypatch.setenv("HERMES_HOME", str(home))
    # Clear env vars that could interfere
    monkeypatch.delenv("HERMES_MODEL", raising=False)
    monkeypatch.delenv("LLM_MODEL", raising=False)
    monkeypatch.delenv("HERMES_INFERENCE_PROVIDER", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("STEPFUN_API_KEY", raising=False)
    monkeypatch.delenv("STEPFUN_BASE_URL", raising=False)
    return home


class TestSaveModelChoiceAlwaysDict:
    def test_string_model_becomes_dict(self, config_home):
        """When config.model is a plain string, _save_model_choice must
        convert it to a dict so provider can be set afterwards."""
        from hermes_cli.auth import _save_model_choice

        _save_model_choice("kimi-k2.5")

        import yaml
        config = yaml.safe_load((config_home / "config.yaml").read_text()) or {}
        model = config.get("model")
        assert isinstance(model, dict), (
            f"Expected model to be a dict after save, got {type(model)}: {model}"
        )
        assert model["default"] == "kimi-k2.5"

    def test_dict_model_stays_dict(self, config_home):
        """When config.model is already a dict, _save_model_choice preserves it."""
        import yaml
        (config_home / "config.yaml").write_text(
            "model:\n  default: old-model\n  provider: openrouter\n"
        )
        from hermes_cli.auth import _save_model_choice

        _save_model_choice("new-model")

        config = yaml.safe_load((config_home / "config.yaml").read_text()) or {}
        model = config.get("model")
        assert isinstance(model, dict)
        assert model["default"] == "new-model"
        assert model["provider"] == "openrouter"  # preserved


class TestProviderPersistsAfterModelSave:
    def test_api_key_provider_saved_when_model_was_string(self, config_home, monkeypatch):
        """_model_flow_api_key_provider must persist the provider even when
        config.model started as a plain string."""
        from hermes_cli.auth import PROVIDER_REGISTRY

        pconfig = PROVIDER_REGISTRY.get("kimi-coding")
        if not pconfig:
            pytest.skip("kimi-coding not in PROVIDER_REGISTRY")

        # Simulate: user has a Kimi API key, model was a string
        monkeypatch.setenv("KIMI_API_KEY", "sk-kimi-test-key")

        from hermes_cli.main import _model_flow_api_key_provider
        from hermes_cli.config import load_config

        # Mock the model selection prompt to return "kimi-k2.5"
        # Also mock input() for the base URL prompt and builtins.input
        with patch("hermes_cli.auth._prompt_model_selection", return_value="kimi-k2.5"), \
             patch("hermes_cli.auth.deactivate_provider"), \
             patch("builtins.input", return_value=""):
            _model_flow_api_key_provider(load_config(), "kimi-coding", "old-model")

        import yaml
        config = yaml.safe_load((config_home / "config.yaml").read_text()) or {}
        model = config.get("model")
        assert isinstance(model, dict), f"model should be dict, got {type(model)}"
        assert model.get("provider") == "kimi-coding", (
            f"provider should be 'kimi-coding', got {model.get('provider')}"
        )
        assert model.get("default") == "kimi-k2.5"

    def test_copilot_provider_saved_when_selected(self, config_home):
        """_model_flow_copilot should persist provider/base_url/model together."""
        from hermes_cli.main import _model_flow_copilot
        from hermes_cli.config import load_config

        with patch(
            "hermes_cli.auth.resolve_api_key_provider_credentials",
            return_value={
                "provider": "copilot",
                "api_key": "gh-cli-token",
                "base_url": "https://api.githubcopilot.com",
                "source": "gh auth token",
            },
        ), patch(
            "hermes_cli.models.fetch_github_model_catalog",
            return_value=[
                {
                    "id": "gpt-4.1",
                    "capabilities": {"type": "chat", "supports": {}},
                    "supported_endpoints": ["/chat/completions"],
                },
                {
                    "id": "gpt-5.4",
                    "capabilities": {"type": "chat", "supports": {"reasoning_effort": ["low", "medium", "high"]}},
                    "supported_endpoints": ["/responses"],
                },
            ],
        ), patch(
            "hermes_cli.auth._prompt_model_selection",
            return_value="gpt-5.4",
        ), patch(
            "hermes_cli.main._prompt_reasoning_effort_selection",
            return_value="high",
        ), patch(
            "hermes_cli.auth.deactivate_provider",
        ):
            _model_flow_copilot(load_config(), "old-model")

        import yaml

        config = yaml.safe_load((config_home / "config.yaml").read_text()) or {}
        model = config.get("model")
        assert isinstance(model, dict), f"model should be dict, got {type(model)}"
        assert model.get("provider") == "copilot"
        assert model.get("base_url") == "https://api.githubcopilot.com"
        assert model.get("default") == "gpt-5.4"
        assert model.get("api_mode") == "codex_responses"
        assert config["agent"]["reasoning_effort"] == "high"

    def test_copilot_acp_provider_saved_when_selected(self, config_home):
        """_model_flow_copilot_acp should persist provider/base_url/model together."""
        from hermes_cli.main import _model_flow_copilot_acp
        from hermes_cli.config import load_config

        with patch(
            "hermes_cli.auth.get_external_process_provider_status",
            return_value={
                "resolved_command": "/usr/local/bin/copilot",
                "command": "copilot",
                "base_url": "acp://copilot",
            },
        ), patch(
            "hermes_cli.auth.resolve_external_process_provider_credentials",
            return_value={
                "provider": "copilot-acp",
                "api_key": "copilot-acp",
                "base_url": "acp://copilot",
                "command": "/usr/local/bin/copilot",
                "args": ["--acp", "--stdio"],
                "source": "process",
            },
        ), patch(
            "hermes_cli.auth.resolve_api_key_provider_credentials",
            return_value={
                "provider": "copilot",
                "api_key": "gh-cli-token",
                "base_url": "https://api.githubcopilot.com",
                "source": "gh auth token",
            },
        ), patch(
            "hermes_cli.models.fetch_github_model_catalog",
            return_value=[
                {
                    "id": "gpt-4.1",
                    "capabilities": {"type": "chat", "supports": {}},
                    "supported_endpoints": ["/chat/completions"],
                },
                {
                    "id": "gpt-5.4",
                    "capabilities": {"type": "chat", "supports": {"reasoning_effort": ["low", "medium", "high"]}},
                    "supported_endpoints": ["/responses"],
                },
            ],
        ), patch(
            "hermes_cli.auth._prompt_model_selection",
            return_value="gpt-5.4",
        ), patch(
            "hermes_cli.auth.deactivate_provider",
        ):
            _model_flow_copilot_acp(load_config(), "old-model")

        import yaml

        config = yaml.safe_load((config_home / "config.yaml").read_text()) or {}
        model = config.get("model")
        assert isinstance(model, dict), f"model should be dict, got {type(model)}"
        assert model.get("provider") == "copilot-acp"
        assert model.get("base_url") == "acp://copilot"
        assert model.get("default") == "gpt-5.4"
        assert model.get("api_mode") == "chat_completions"

    def test_opencode_go_models_are_selectable_and_persist_normalized(self, config_home, monkeypatch):
        from hermes_cli.main import _model_flow_api_key_provider
        from hermes_cli.config import load_config

        monkeypatch.setenv("OPENCODE_GO_API_KEY", "test-key")

        with patch("hermes_cli.models.fetch_api_models", return_value=["opencode-go/kimi-k2.5", "opencode-go/minimax-m2.7"]), \
             patch("hermes_cli.auth._prompt_model_selection", return_value="kimi-k2.5"), \
             patch("hermes_cli.auth.deactivate_provider"), \
             patch("builtins.input", return_value=""):
            _model_flow_api_key_provider(load_config(), "opencode-go", "opencode-go/kimi-k2.5")

        import yaml
        config = yaml.safe_load((config_home / "config.yaml").read_text()) or {}
        model = config.get("model")
        assert isinstance(model, dict)
        assert model.get("provider") == "opencode-go"
        assert model.get("default") == "kimi-k2.5"
        assert model.get("api_mode") == "chat_completions"

    def test_fireworks_provider_saved_with_curated_model(self, config_home, monkeypatch, capsys):
        """Fireworks uses the generic API-key flow but shows curated models only."""
        from hermes_cli.auth import PROVIDER_REGISTRY
        from hermes_cli.main import _model_flow_api_key_provider
        from hermes_cli.config import load_config

        pconfig = PROVIDER_REGISTRY.get("fireworks")
        assert pconfig is not None
        assert pconfig.name == "Fireworks AI"
        assert pconfig.inference_base_url == "https://api.fireworks.ai/inference/v1"
        assert pconfig.api_key_env_vars == ("FIREWORKS_API_KEY",)
        assert pconfig.base_url_env_var == "FIREWORKS_BASE_URL"

        selected = "accounts/fireworks/models/minimax-m2p7"
        monkeypatch.setenv("FIREWORKS_API_KEY", "test-key")

        with patch("agent.models_dev.list_agentic_models", return_value=[
            "accounts/fireworks/models/not-in-curated",
        ]), patch("hermes_cli.models.fetch_api_models", return_value=[
            "accounts/fireworks/models/not-in-curated-live",
        ]), patch("hermes_cli.auth._prompt_model_selection", return_value=selected) as prompt, \
             patch("hermes_cli.auth.deactivate_provider"), \
             patch("builtins.input", return_value=""):
            _model_flow_api_key_provider(load_config(), "fireworks", "old-model")

        import yaml
        config = yaml.safe_load((config_home / "config.yaml").read_text()) or {}
        model = config.get("model")
        assert isinstance(model, dict)
        assert model.get("provider") == "fireworks"
        assert model.get("base_url") == "https://api.fireworks.ai/inference/v1"
        assert model.get("default") == selected
        assert "api_mode" not in model

        model_list = prompt.call_args.args[0]
        assert model_list == [
            "accounts/fireworks/models/kimi-k2p6",
            "accounts/fireworks/routers/kimi-k2p5-turbo",
            "accounts/fireworks/models/minimax-m2p7",
            "accounts/fireworks/models/qwen3p6-plus",
            "accounts/fireworks/models/glm-5p1",
            "accounts/fireworks/models/glm-5",
            "accounts/fireworks/models/kimi-k2p5",
            "accounts/fireworks/models/deepseek-v3p2",
            "accounts/fireworks/models/deepseek-v3p1",
            "accounts/fireworks/models/minimax-m2p5",
        ]
        assert "Showing 10 curated models" in capsys.readouterr().out

    def test_fireworks_pass_provider_saved_with_router_model(self, config_home, monkeypatch):
        """Fireworks Full Access Pass is a first-class picker option for pass routers."""
        from hermes_cli.auth import PROVIDER_REGISTRY
        from hermes_cli.main import _model_flow_api_key_provider
        from hermes_cli.config import load_config

        pconfig = PROVIDER_REGISTRY.get("fireworks-pass")
        assert pconfig is not None
        assert pconfig.name == "Fireworks Full Access Pass"
        assert pconfig.inference_base_url == "https://api.fireworks.ai/inference/v1"
        assert pconfig.api_key_env_vars == ("FIREWORKS_API_KEY",)
        assert pconfig.base_url_env_var == "FIREWORKS_BASE_URL"

        selected = "accounts/fireworks/routers/kimi-k2p5-turbo"
        monkeypatch.setenv("FIREWORKS_API_KEY", "test-key")

        with patch("agent.models_dev.list_agentic_models", return_value=[]), \
             patch("hermes_cli.models.fetch_api_models", return_value=[]), \
             patch("hermes_cli.auth._prompt_model_selection", return_value=selected) as prompt, \
             patch("hermes_cli.auth.deactivate_provider"), \
             patch("builtins.input", return_value=""):
            _model_flow_api_key_provider(load_config(), "fireworks-pass", "old-model")

        import yaml
        config = yaml.safe_load((config_home / "config.yaml").read_text()) or {}
        model = config.get("model")
        assert isinstance(model, dict)
        assert model.get("provider") == "fireworks-pass"
        assert model.get("base_url") == "https://api.fireworks.ai/inference/v1"
        assert model.get("default") == selected
        assert "api_mode" not in model
        assert prompt.call_args.args[0] == [selected]

    def test_opencode_go_same_provider_switch_recomputes_api_mode(self, config_home, monkeypatch):
        from hermes_cli.main import _model_flow_api_key_provider
        from hermes_cli.config import load_config

        monkeypatch.setenv("OPENCODE_GO_API_KEY", "test-key")
        (config_home / "config.yaml").write_text(
            "model:\n"
            "  default: kimi-k2.5\n"
            "  provider: opencode-go\n"
            "  base_url: https://opencode.ai/zen/go/v1\n"
            "  api_mode: chat_completions\n"
        )

        with patch("hermes_cli.models.fetch_api_models", return_value=["opencode-go/kimi-k2.5", "opencode-go/minimax-m2.5"]), \
             patch("hermes_cli.auth._prompt_model_selection", return_value="minimax-m2.5"), \
             patch("hermes_cli.auth.deactivate_provider"), \
             patch("builtins.input", return_value=""):
            _model_flow_api_key_provider(load_config(), "opencode-go", "kimi-k2.5")

        import yaml
        config = yaml.safe_load((config_home / "config.yaml").read_text()) or {}
        model = config.get("model")
        assert isinstance(model, dict)
        assert model.get("provider") == "opencode-go"
        assert model.get("default") == "minimax-m2.5"
        assert model.get("api_mode") == "anthropic_messages"


class TestBaseUrlValidation:
    """Reject non-URL values in the base URL prompt (e.g. shell commands)."""

    def test_invalid_base_url_rejected(self, config_home, monkeypatch, capsys):
        """Typing a non-URL string should not be saved as the base URL."""
        from hermes_cli.auth import PROVIDER_REGISTRY

        pconfig = PROVIDER_REGISTRY.get("zai")
        if not pconfig:
            pytest.skip("zai not in PROVIDER_REGISTRY")

        monkeypatch.setenv("GLM_API_KEY", "test-key")

        from hermes_cli.main import _model_flow_api_key_provider
        from hermes_cli.config import load_config, get_env_value

        # User types a shell command instead of a URL at the base URL prompt
        with patch("hermes_cli.auth._prompt_model_selection", return_value="glm-5"), \
             patch("hermes_cli.auth.deactivate_provider"), \
             patch("builtins.input", return_value="nano ~/.hermes/.env"):
            _model_flow_api_key_provider(load_config(), "zai", "old-model")

        # The garbage value should NOT have been saved
        saved = get_env_value("GLM_BASE_URL") or ""
        assert not saved or saved.startswith(("http://", "https://")), \
            f"Non-URL value was saved as GLM_BASE_URL: {saved}"
        captured = capsys.readouterr()
        assert "Invalid URL" in captured.out

    def test_valid_base_url_accepted(self, config_home, monkeypatch):
        """A proper URL should be saved normally."""
        from hermes_cli.auth import PROVIDER_REGISTRY

        pconfig = PROVIDER_REGISTRY.get("zai")
        if not pconfig:
            pytest.skip("zai not in PROVIDER_REGISTRY")

        monkeypatch.setenv("GLM_API_KEY", "test-key")

        from hermes_cli.main import _model_flow_api_key_provider
        from hermes_cli.config import load_config, get_env_value

        with patch("hermes_cli.auth._prompt_model_selection", return_value="glm-5"), \
             patch("hermes_cli.auth.deactivate_provider"), \
             patch("builtins.input", return_value="https://custom.z.ai/api/paas/v4"):
            _model_flow_api_key_provider(load_config(), "zai", "old-model")

        saved = get_env_value("GLM_BASE_URL") or ""
        assert saved == "https://custom.z.ai/api/paas/v4"

    def test_empty_base_url_keeps_default(self, config_home, monkeypatch):
        """Pressing Enter (empty) should not change the base URL."""
        from hermes_cli.auth import PROVIDER_REGISTRY

        pconfig = PROVIDER_REGISTRY.get("zai")
        if not pconfig:
            pytest.skip("zai not in PROVIDER_REGISTRY")

        monkeypatch.setenv("GLM_API_KEY", "test-key")
        monkeypatch.delenv("GLM_BASE_URL", raising=False)

        from hermes_cli.main import _model_flow_api_key_provider
        from hermes_cli.config import load_config, get_env_value

        with patch("hermes_cli.auth._prompt_model_selection", return_value="glm-5"), \
             patch("hermes_cli.auth.deactivate_provider"), \
             patch("builtins.input", return_value=""):
            _model_flow_api_key_provider(load_config(), "zai", "old-model")

        saved = get_env_value("GLM_BASE_URL") or ""
        assert saved == "", "Empty input should not save a base URL"

    def test_stepfun_provider_saved_with_selected_region(self, config_home, monkeypatch):
        from hermes_cli.main import _model_flow_stepfun
        from hermes_cli.config import load_config, get_env_value

        monkeypatch.setenv("STEPFUN_API_KEY", "stepfun-test-key")

        with patch(
            "hermes_cli.main._prompt_provider_choice",
            return_value=1,
        ), patch(
            "hermes_cli.models.fetch_api_models",
            return_value=["step-3.5-flash", "step-3-agent-lite"],
        ), patch(
            "hermes_cli.auth._prompt_model_selection",
            return_value="step-3-agent-lite",
        ), patch(
            "hermes_cli.auth.deactivate_provider",
        ):
            _model_flow_stepfun(load_config(), "old-model")

        import yaml

        config = yaml.safe_load((config_home / "config.yaml").read_text()) or {}
        model = config.get("model")
        assert isinstance(model, dict)
        assert model.get("provider") == "stepfun"
        assert model.get("default") == "step-3-agent-lite"
        assert model.get("base_url") == "https://api.stepfun.com/step_plan/v1"
        assert get_env_value("STEPFUN_BASE_URL") == "https://api.stepfun.com/step_plan/v1"
