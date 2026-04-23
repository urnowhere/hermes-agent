import hermes_cli.providers as providers_mod

from hermes_cli.model_switch import list_authenticated_providers


def _make_provider_rows(monkeypatch, **kwargs):
    monkeypatch.setattr("agent.models_dev.fetch_models_dev", lambda: {"anthropic": {}})
    monkeypatch.setattr(
        "agent.credential_pool.load_pool",
        lambda *_args, **_kwargs: _NoCredentialsPool(),
    )
    monkeypatch.setattr("hermes_cli.auth._load_auth_store", lambda: {})
    monkeypatch.setattr(providers_mod, "HERMES_OVERLAYS", {})
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    return list_authenticated_providers(
        current_provider="anthropic",
        user_providers={
            "local-ollama": {
                "name": "Local Ollama",
                "api": "http://localhost:11434/v1",
                "default_model": "llama3",
            }
        },
        custom_providers=[
            {
                "name": "Remote Cloud",
                "base_url": "https://example.com/v1",
                "model": "gpt-5.4",
            }
        ],
        max_models=50,
        **kwargs,
    )


class _NoCredentialsPool:
    def has_credentials(self):
        return False


def _slugs(providers):
    return [provider["slug"] for provider in providers]


def test_no_picker_filter_leaves_all_provider_families_visible(monkeypatch):
    providers = _make_provider_rows(monkeypatch)

    assert sorted(_slugs(providers)) == [
        "anthropic",
        "custom:remote-cloud",
        "local-ollama",
    ]


def test_empty_picker_filter_list_disables_filtering(monkeypatch):
    providers = _make_provider_rows(monkeypatch, picker_providers=[])

    assert sorted(_slugs(providers)) == [
        "anthropic",
        "custom:remote-cloud",
        "local-ollama",
    ]


def test_picker_filter_custom_keeps_only_user_defined_rows(monkeypatch):
    providers = _make_provider_rows(monkeypatch, picker_providers=["custom"])

    assert sorted(_slugs(providers)) == [
        "custom:remote-cloud",
        "local-ollama",
    ]


def test_picker_filter_builtin_only_excludes_user_defined_rows(monkeypatch):
    providers = _make_provider_rows(monkeypatch, picker_providers=["anthropic"])

    assert _slugs(providers) == ["anthropic"]


def test_picker_filter_mixed_families_keeps_builtin_and_custom(monkeypatch):
    providers = _make_provider_rows(
        monkeypatch,
        picker_providers=["anthropic", "custom"],
    )

    assert sorted(_slugs(providers)) == [
        "anthropic",
        "custom:remote-cloud",
        "local-ollama",
    ]


def test_malformed_picker_filter_disables_filtering(monkeypatch):
    providers = _make_provider_rows(monkeypatch, picker_providers="custom")

    assert sorted(_slugs(providers)) == [
        "anthropic",
        "custom:remote-cloud",
        "local-ollama",
    ]


def test_unknown_picker_filter_values_are_ignored(monkeypatch):
    providers = _make_provider_rows(monkeypatch, picker_providers=["anthorpic"])

    assert sorted(_slugs(providers)) == [
        "anthropic",
        "custom:remote-cloud",
        "local-ollama",
    ]
