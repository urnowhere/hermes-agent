from types import SimpleNamespace

import hermes_cli.main as main


def test_update_installs_termux_extra_on_termux(monkeypatch):
    commands = []

    def fake_run(cmd, **kwargs):
        commands.append(cmd)
        return SimpleNamespace(returncode=0)

    monkeypatch.setenv("TERMUX_VERSION", "0.118.3")
    monkeypatch.setattr(main.subprocess, "run", fake_run)

    main._install_python_dependencies_with_optional_fallback(["python", "-m", "pip"])

    assert commands == [["python", "-m", "pip", "install", "-e", ".[termux]", "--quiet"]]


def test_update_installs_all_extra_outside_termux(monkeypatch):
    commands = []

    def fake_run(cmd, **kwargs):
        commands.append(cmd)
        return SimpleNamespace(returncode=0)

    monkeypatch.delenv("TERMUX_VERSION", raising=False)
    monkeypatch.setenv("PREFIX", "/usr/local")
    monkeypatch.setattr(main.subprocess, "run", fake_run)

    main._install_python_dependencies_with_optional_fallback(["python", "-m", "pip"])

    assert commands == [["python", "-m", "pip", "install", "-e", ".[all]", "--quiet"]]
