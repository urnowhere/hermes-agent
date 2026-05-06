import subprocess

from hermes_cli.memory_setup import _external_dependency_available


def test_external_dependency_available_accepts_argv_list(monkeypatch):
    captured = {}

    def fake_run(argv, capture_output, timeout):
        captured["argv"] = argv
        return type("Result", (), {"returncode": 0})()

    monkeypatch.setattr(subprocess, "run", fake_run)

    assert _external_dependency_available(["docker", "--version"]) is True
    assert captured["argv"] == ["docker", "--version"]


def test_external_dependency_available_rejects_nonzero(monkeypatch):
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda argv, capture_output, timeout: type("Result", (), {"returncode": 1})(),
    )

    assert _external_dependency_available("docker --version") is False


def test_external_dependency_available_does_not_invoke_shell_fragments(monkeypatch):
    captured = {}

    monkeypatch.setattr(
        subprocess,
        "run",
        lambda argv, capture_output, timeout: (
            captured.setdefault("argv", argv),
            type("Result", (), {"returncode": 0})(),
        )[1],
    )

    assert _external_dependency_available("docker --version && touch /tmp/pwned") is True
    assert captured["argv"] == ["docker", "--version", "&&", "touch", "/tmp/pwned"]


def test_external_dependency_available_reports_timeouts(monkeypatch):
    def raise_timeout(argv, capture_output, timeout):
        raise subprocess.TimeoutExpired(cmd=argv, timeout=timeout)

    monkeypatch.setattr(subprocess, "run", raise_timeout)

    assert _external_dependency_available("docker --version") is False
