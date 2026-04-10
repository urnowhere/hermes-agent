from pathlib import Path
import re


SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "install.ps1"


def _script_text() -> str:
    return SCRIPT_PATH.read_text(encoding="utf-8")


def _function_body(name: str) -> str:
    text = _script_text()
    match = re.search(rf"function\s+{re.escape(name)}\s*\{{(.*?)^\}}", text, re.S | re.M)
    assert match, f"Function {name} not found"
    return match.group(1)


def test_test_python_uses_native_process_helper_for_uv_install():
    helper = _function_body("Invoke-NativeProcess")
    body = _function_body("Test-Python")
    assert "Start-Process" in helper
    assert "Invoke-NativeProcess" in body
    assert "python install" in body
    assert "& $UvCmd python install $PythonVersion 2>&1" not in body


def test_test_git_has_noninteractive_fallback_install_path():
    body = _function_body("Test-Git")
    assert "winget install Git.Git" in body or "PortableGit-" in body
    assert "HERMES_GIT_BASH_PATH" in body
    assert "attempting automatic install" in body
    assert "Portable Git install failed" in body
