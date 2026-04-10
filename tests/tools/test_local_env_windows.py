import os
from pathlib import Path
from unittest.mock import patch

from tools.environments.local import LocalEnvironment, _make_run_env


def test_make_run_env_uses_windows_path_separator(monkeypatch):
    windows_env = {"PATH": r"C:\Windows\System32;C:\Tools"}
    with patch.dict(os.environ, windows_env, clear=True):
        monkeypatch.setattr("tools.environments.local._IS_WINDOWS", True)
        result = _make_run_env({})

    assert result["PATH"].startswith(r"C:\Windows\System32;C:\Tools")
    assert ":/opt/homebrew/bin" not in result["PATH"]


def test_windows_shell_path_roundtrip(monkeypatch):
    monkeypatch.setattr("tools.environments.local._IS_WINDOWS", True)

    assert LocalEnvironment._normalize_cwd_for_shell(r"C:\Users\Alice\project") == "/c/Users/Alice/project"
    assert LocalEnvironment._normalize_cwd_from_shell("/c/Users/Alice/project") == r"C:\Users\Alice\project"


def test_update_cwd_normalizes_git_bash_path_on_windows(tmp_path, monkeypatch):
    monkeypatch.setattr("tools.environments.local._IS_WINDOWS", True)
    with patch.object(LocalEnvironment, "init_session", autospec=True, return_value=None):
        env = LocalEnvironment(cwd=str(tmp_path), timeout=10)

    env._cwd_file = str(tmp_path / "cwd.txt")
    cwd_file = Path(env._cwd_file)
    cwd_file.write_text("/c/Users/Alice/project\n", encoding="utf-8")
    result = {"output": "hello\n"}

    env._update_cwd(result)

    assert env.cwd == r"C:\Users\Alice\project"
