from hermes_cli.model_switch import switch_model


_MOCK_VALIDATION = {
    "accepted": True,
    "persist": True,
    "recognized": True,
    "message": None,
}


def test_switch_model_normalizes_opencode_go_minimax_base_url(monkeypatch):
    monkeypatch.setattr(
        "hermes_cli.runtime_provider.resolve_runtime_provider",
        lambda requested: {
            "api_key": "test-key",
            "base_url": "https://opencode.ai/zen/go/v1",
            "api_mode": "chat_completions",
        },
    )
    monkeypatch.setattr(
        "hermes_cli.models.validate_requested_model",
        lambda *a, **k: _MOCK_VALIDATION,
    )
    monkeypatch.setattr("hermes_cli.model_switch.get_model_info", lambda *a, **k: None)
    monkeypatch.setattr("hermes_cli.model_switch.get_model_capabilities", lambda *a, **k: None)

    result = switch_model(
        raw_input="minimax-m2.7",
        current_provider="openai-codex",
        current_model="gpt-5.4",
        current_base_url="https://chatgpt.com/backend-api/codex",
        current_api_key="",
        explicit_provider="opencode-go",
        user_providers={},
        custom_providers=[],
    )

    assert result.success is True
    assert result.target_provider == "opencode-go"
    assert result.new_model == "minimax-m2.7"
    assert result.api_mode == "anthropic_messages"
    assert result.base_url == "https://opencode.ai/zen/go"
