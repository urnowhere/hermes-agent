from types import SimpleNamespace
from unittest.mock import patch


def test_handle_model_switch_passes_profile_provider_config_for_arg_switch():
    """`/model foo` should resolve against the active profile's provider config."""
    import cli as cli_module
    from cli import HermesCLI

    cli_obj = HermesCLI.__new__(HermesCLI)
    cli_obj.model = "anthropic/claude-sonnet-4-20250514"
    cli_obj.provider = "openrouter"
    cli_obj.base_url = "https://openrouter.ai/api/v1"
    cli_obj.api_key = "sk-test"
    cli_obj.api_mode = "chat_completions"
    cli_obj.agent = None

    user_providers = {
        "deepseek": {
            "provider": "deepseek",
            "api_key_env": "DEEPSEEK_API_KEY",
            "base_url": "https://api.deepseek.com/v1",
            "model_name": "deepseek-chat",
        }
    }
    custom_providers = [
        {"name": "DeepSeek Direct", "base_url": "https://api.deepseek.com/v1"}
    ]

    with (
        patch("hermes_cli.model_switch.parse_model_flags", return_value=("deepseek", "", False)),
        patch(
            "hermes_cli.model_switch.switch_model",
            return_value=SimpleNamespace(success=False, error_message="boom"),
        ) as mock_switch,
        patch("hermes_cli.config.load_config", return_value={"providers": user_providers}),
        patch(
            "hermes_cli.config.get_compatible_custom_providers",
            return_value=custom_providers,
        ),
        patch.object(cli_module, "_cprint"),
    ):
        cli_obj._handle_model_switch("/model deepseek")

    mock_switch.assert_called_once_with(
        raw_input="deepseek",
        current_provider="openrouter",
        current_model="anthropic/claude-sonnet-4-20250514",
        current_base_url="https://openrouter.ai/api/v1",
        current_api_key="sk-test",
        is_global=False,
        explicit_provider="",
        user_providers=user_providers,
        custom_providers=custom_providers,
    )
