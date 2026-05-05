import platform

import pytest

from tools.environments import local
from tools.environments.local import LocalEnvironment


def test_windows_bash_resolution_prefers_git_bash_over_wsl_shim(monkeypatch):
    monkeypatch.setattr(local, "_IS_WINDOWS", True)
    monkeypatch.delenv("HERMES_GIT_BASH_PATH", raising=False)
    monkeypatch.setattr(local.shutil, "which", lambda name: r"C:\Windows\System32\bash.exe")

    def fake_isfile(path):
        return path == r"C:\Program Files\Git\bin\bash.exe"

    monkeypatch.setattr(local.os.path, "isfile", fake_isfile)
    monkeypatch.setenv("ProgramFiles", r"C:\Program Files")
    monkeypatch.setenv("ProgramFiles(x86)", r"C:\Program Files (x86)")
    monkeypatch.setenv("LOCALAPPDATA", r"C:\Users\test\AppData\Local")

    assert local._find_bash() == r"C:\Program Files\Git\bin\bash.exe"


def test_windows_popen_cwd_converts_git_bash_paths(monkeypatch):
    monkeypatch.setattr(local, "_IS_WINDOWS", True)
    monkeypatch.setattr(local.tempfile, "gettempdir", lambda: r"C:\Users\test\AppData\Local\Temp")

    assert local._windows_popen_cwd("/c/MIRIP") == r"C:\MIRIP"
    assert local._windows_popen_cwd("/mnt/c/MIRIP") == r"C:\MIRIP"
    assert local._windows_popen_cwd("/tmp/hermes") == r"C:\Users\test\AppData\Local\Temp\hermes"


@pytest.mark.skipif(platform.system() != "Windows", reason="Windows pipe behavior")
def test_windows_local_environment_captures_git_bash_stdout(monkeypatch):
    git_bash = r"C:\Program Files\Git\bin\bash.exe"
    if not local.os.path.isfile(git_bash):
        pytest.skip("Git Bash is not installed at the standard path")

    monkeypatch.setenv("HERMES_GIT_BASH_PATH", git_bash)

    env = LocalEnvironment(cwd=".", timeout=10)
    result = env.execute("echo HERMES_WINDOWS_STDOUT_OK", timeout=10)

    assert result["returncode"] == 0
    assert "HERMES_WINDOWS_STDOUT_OK" in result["output"]
