import sys
import types
from types import SimpleNamespace


def test_oneshot_resolves_direct_model_alias_with_explicit_provider(monkeypatch):
    captured = {}

    class _Agent:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        def chat(self, prompt):
            return "ok"

    fake_run_agent = types.ModuleType("run_agent")
    fake_run_agent.AIAgent = _Agent
    monkeypatch.setitem(sys.modules, "run_agent", fake_run_agent)
    monkeypatch.setattr("hermes_cli.config.load_config", lambda: {"model": {}})
    monkeypatch.setattr(
        "hermes_cli.model_switch.resolve_direct_alias",
        lambda model: SimpleNamespace(
            model="claude-opus-4-6",
            provider="custom",
            base_url="https://custom.example/v1",
        ) if model == "opus" else None,
    )

    def _runtime_resolve(**kwargs):
        assert kwargs["requested"] == "custom"
        assert kwargs["explicit_base_url"] == "https://custom.example/v1"
        assert kwargs["target_model"] == "claude-opus-4-6"
        return {
            "provider": "custom",
            "api_mode": "chat_completions",
            "base_url": "https://custom.example/v1",
            "api_key": "test-key",
        }

    monkeypatch.setattr("hermes_cli.runtime_provider.resolve_runtime_provider", _runtime_resolve)

    from hermes_cli.oneshot import _run_agent

    assert _run_agent(
        "hello",
        model="opus",
        provider="custom",
        toolsets=[],
        use_config_toolsets=False,
    ) == "ok"
    assert captured["model"] == "claude-opus-4-6"
    assert captured["provider"] == "custom"
    assert captured["base_url"] == "https://custom.example/v1"
