"""Tests for agent-settings copy in the interactive setup wizard."""

from hermes_cli.setup import _apply_default_agent_settings, setup_agent_settings


def _patch_io(monkeypatch, prompt_answers, *, env_max_iter=""):
    """Wire monkeypatches shared across the wizard tests."""
    answers = iter(prompt_answers)
    saved_env: list[tuple] = []
    monkeypatch.setattr(
        "hermes_cli.setup.get_env_value",
        lambda key: env_max_iter if key == "HERMES_MAX_ITERATIONS" else "",
    )
    monkeypatch.setattr(
        "hermes_cli.setup.prompt", lambda *args, **kwargs: next(answers)
    )
    monkeypatch.setattr("hermes_cli.setup.prompt_choice", lambda *args, **kwargs: 4)
    monkeypatch.setattr(
        "hermes_cli.setup.save_env_value",
        lambda key, value, **kwargs: saved_env.append((key, value)),
    )
    monkeypatch.setattr("hermes_cli.setup.save_config", lambda *args, **kwargs: None)
    return saved_env


def test_setup_agent_settings_helper_text_matches_config_value(
    tmp_path, monkeypatch, capsys
):
    """Helper text must reflect the config.yaml value, not a stale env override."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))

    config = {
        "agent": {"max_turns": 90},
        "display": {"tool_progress": "all"},
        "compression": {"threshold": 0.50},
        "session_reset": {"mode": "both", "idle_minutes": 1440, "at_hour": 4},
    }

    # A stale env value (e.g. from a previous wizard run that wrote both stores)
    # must not override config.yaml in the prompt display. See issue #17534.
    _patch_io(monkeypatch, ["60", "all", "0.5"], env_max_iter="60")

    setup_agent_settings(config)

    out = capsys.readouterr().out
    assert "Press Enter to keep 90." in out
    assert "Press Enter to keep 60." not in out


def test_setup_agent_settings_does_not_write_max_iterations_env_var(
    tmp_path, monkeypatch
):
    """The wizard must not dual-write max_turns to .env (issue #17534).

    Before the fix, both ``setup_agent_settings`` and
    ``_apply_default_agent_settings`` called ``save_env_value`` for
    ``HERMES_MAX_ITERATIONS`` in addition to setting ``agent.max_turns`` in
    config.yaml. A later edit to only one store left the other as a stale ghost,
    which surfaced inconsistent iteration limits across CLI vs gateway sessions.
    """
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))

    config = {
        "agent": {"max_turns": 90},
        "display": {"tool_progress": "all"},
        "compression": {"threshold": 0.50},
        "session_reset": {"mode": "both", "idle_minutes": 1440, "at_hour": 4},
    }

    saved_env = _patch_io(monkeypatch, ["120", "all", "0.5"])
    setup_agent_settings(config)

    written_keys = {key for key, _ in saved_env}
    assert "HERMES_MAX_ITERATIONS" not in written_keys
    assert config["agent"]["max_turns"] == 120


def test_apply_default_agent_settings_does_not_write_max_iterations_env_var(
    tmp_path, monkeypatch
):
    """Recommended-defaults path must not dual-write to .env either."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))

    config: dict = {}
    saved_env = _patch_io(monkeypatch, [])
    _apply_default_agent_settings(config)

    written_keys = {key for key, _ in saved_env}
    assert "HERMES_MAX_ITERATIONS" not in written_keys
    assert config["agent"]["max_turns"] == 90
