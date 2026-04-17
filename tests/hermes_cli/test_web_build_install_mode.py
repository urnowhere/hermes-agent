import subprocess

from hermes_cli.main import _build_web_ui


def test_build_web_ui_uses_npm_ci_when_package_lock_exists(tmp_path, monkeypatch):
    web_dir = tmp_path / "web"
    web_dir.mkdir()
    (web_dir / "package.json").write_text("{}", encoding="utf-8")
    (web_dir / "package-lock.json").write_text("{}", encoding="utf-8")

    calls = []

    def fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/npm" if name == "npm" else None)
    monkeypatch.setattr(subprocess, "run", fake_run)

    assert _build_web_ui(web_dir) is True
    assert calls[0][0] == ["/usr/bin/npm", "ci", "--silent"]
    assert calls[1][0] == ["/usr/bin/npm", "run", "build"]
    assert calls[0][1]["cwd"] == web_dir


def test_build_web_ui_uses_npm_install_without_lockfile(tmp_path, monkeypatch):
    web_dir = tmp_path / "web"
    web_dir.mkdir()
    (web_dir / "package.json").write_text("{}", encoding="utf-8")

    calls = []

    def fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/npm" if name == "npm" else None)
    monkeypatch.setattr(subprocess, "run", fake_run)

    assert _build_web_ui(web_dir) is True
    assert calls[0][0] == ["/usr/bin/npm", "install", "--silent"]
    assert calls[1][0] == ["/usr/bin/npm", "run", "build"]


def test_build_web_ui_fatal_message_recommends_npm_ci_with_lockfile(tmp_path, monkeypatch, capsys):
    web_dir = tmp_path / "web"
    web_dir.mkdir()
    (web_dir / "package.json").write_text("{}", encoding="utf-8")
    (web_dir / "package-lock.json").write_text("{}", encoding="utf-8")

    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/npm" if name == "npm" else None)

    def fake_run(cmd, **kwargs):
        if cmd[:2] == ["/usr/bin/npm", "ci"]:
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="boom")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    assert _build_web_ui(web_dir, fatal=True) is False
    out = capsys.readouterr().out
    assert "npm ci" in out
    assert "npm install" not in out


def test_build_web_ui_missing_npm_uses_install_hint_without_lockfile(tmp_path, monkeypatch, capsys):
    web_dir = tmp_path / "web"
    web_dir.mkdir()
    (web_dir / "package.json").write_text("{}", encoding="utf-8")

    monkeypatch.setattr("shutil.which", lambda name: None)

    assert _build_web_ui(web_dir, fatal=True) is False
    out = capsys.readouterr().out
    assert "npm install" in out
    assert "npm ci" not in out
