from argparse import Namespace


def _picker_default(monkeypatch, config):
    import hermes_cli.config as config_module
    import hermes_cli.memory_setup as memory_setup

    providers = [
        ("mem0", "requires API key", object()),
        ("hindsight", "local", object()),
    ]
    selected_defaults = []

    def fake_select(title, items, default=0):
        selected_defaults.append(default)
        return len(providers)

    monkeypatch.setattr(memory_setup, "_get_available_providers", lambda: providers)
    monkeypatch.setattr(memory_setup, "_curses_select", fake_select)
    monkeypatch.setattr(config_module, "load_config", lambda: config)
    monkeypatch.setattr(config_module, "save_config", lambda cfg: None)

    memory_setup.cmd_setup(Namespace())

    return selected_defaults[0]


def test_saved_provider_defaults_to_saved_provider_index(monkeypatch):
    default = _picker_default(monkeypatch, {"memory": {"provider": "hindsight"}})

    assert default == 1


def test_unknown_saved_provider_defaults_to_builtin_index(monkeypatch):
    default = _picker_default(monkeypatch, {"memory": {"provider": "unknown"}})

    assert default == 2


def test_malformed_memory_config_defaults_to_builtin_index(monkeypatch):
    default = _picker_default(monkeypatch, {"memory": "broken"})

    assert default == 2
