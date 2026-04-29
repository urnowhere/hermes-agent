from unittest.mock import MagicMock, mock_open, patch

from tools.environments.local import (
    LocalEnvironment,
    _bash_path_to_windows,
    _find_bash,
    _prepend_shell_init,
    _windows_path_to_bash,
)


def test_windows_path_to_bash_drive_path(monkeypatch):
    monkeypatch.setattr("tools.environments.local._IS_WINDOWS", True)
    assert _windows_path_to_bash(r"C:\work\tree") == "/c/work/tree"


def test_bash_path_to_windows_drive_path(monkeypatch):
    monkeypatch.setattr("tools.environments.local._IS_WINDOWS", True)
    assert _bash_path_to_windows("/c/work/tree") == r"C:\work\tree"


def test_prepend_shell_init_uses_bash_paths_on_windows(monkeypatch):
    monkeypatch.setattr("tools.environments.local._IS_WINDOWS", True)
    wrapped = _prepend_shell_init("echo hi", [r"C:\Users\me\.bashrc"])
    assert "/c/Users/me/.bashrc" in wrapped


def test_find_bash_skips_system32_wsl_stub(monkeypatch):
    monkeypatch.setattr("tools.environments.local._IS_WINDOWS", True)
    monkeypatch.setenv("SystemRoot", r"C:\Windows")

    def fake_which(name: str):
        if name == "bash":
            return r"C:\Windows\System32\bash.exe"
        if name == "git":
            return None
        return None

    monkeypatch.setattr("tools.environments.local.shutil.which", fake_which)
    monkeypatch.setattr(
        "tools.environments.local.os.path.isfile",
        lambda path: path == r"C:\Program Files\Git\usr\bin\bash.exe",
    )

    assert _find_bash() == r"C:\Program Files\Git\usr\bin\bash.exe"


def test_run_bash_uses_native_windows_cwd(monkeypatch):
    monkeypatch.setattr("tools.environments.local._IS_WINDOWS", True)
    monkeypatch.setattr("tools.environments.local._find_bash", lambda: r"C:\Git\bin\bash.exe")
    monkeypatch.setattr("tools.environments.local._make_run_env", lambda env: {})

    popen_calls = {}

    def fake_popen(*args, **kwargs):
        popen_calls["cwd"] = kwargs.get("cwd")
        proc = MagicMock()
        proc.stdout = MagicMock()
        return proc

    monkeypatch.setattr("tools.environments.local.subprocess.Popen", fake_popen)

    with patch.object(LocalEnvironment, "init_session", autospec=True, return_value=None):
        env = LocalEnvironment(cwd="/c/work/tree", timeout=10)

    env._run_bash("echo hi")
    assert popen_calls["cwd"] == r"C:\work\tree"


def test_update_cwd_reads_native_path_from_bash_tempfile(monkeypatch):
    monkeypatch.setattr("tools.environments.local._IS_WINDOWS", True)

    with patch.object(LocalEnvironment, "init_session", autospec=True, return_value=None):
        env = LocalEnvironment(cwd=r"C:\start", timeout=10)

    env._cwd_file = "/c/temp/hermes-cwd-123.txt"
    result = {"output": ""}

    with patch("builtins.open", mock_open(read_data="/c/next/path\n")) as mocked_open:
        env._update_cwd(result)

    mocked_open.assert_called_once_with(r"C:\temp\hermes-cwd-123.txt")
    assert env.cwd == r"C:\next\path"

