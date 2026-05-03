"""Gateway max-iteration resolution tests."""

from gateway.run import _resolve_gateway_max_iterations


def test_gateway_max_iterations_prefers_agent_max_turns_over_legacy_env(monkeypatch):
    """config.yaml agent.max_turns is the source of truth for gateway agents."""
    monkeypatch.setenv("HERMES_MAX_ITERATIONS", "200")

    value = _resolve_gateway_max_iterations({"agent": {"max_turns": 600}})

    assert value == 600


def test_gateway_max_iterations_uses_legacy_env_when_config_missing(monkeypatch):
    """Keep old env-only deployments working when config has no max_turns."""
    monkeypatch.setenv("HERMES_MAX_ITERATIONS", "200")

    value = _resolve_gateway_max_iterations({})

    assert value == 200


def test_gateway_max_iterations_ignores_invalid_values(monkeypatch):
    monkeypatch.setenv("HERMES_MAX_ITERATIONS", "not-an-int")

    value = _resolve_gateway_max_iterations({"agent": {"max_turns": ""}}, default=90)

    assert value == 90
