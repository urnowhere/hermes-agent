from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
INSTALL_SCRIPT = REPO_ROOT / "scripts" / "install.ps1"
README = REPO_ROOT / "README.md"


def test_install_ps1_has_native_process_helper():
    content = INSTALL_SCRIPT.read_text(encoding="utf-8")

    assert "function Invoke-NativeProcess" in content
    assert "Start-Process -FilePath $FilePath" in content


def test_install_ps1_auto_installs_git_and_sets_git_bash():
    content = INSTALL_SCRIPT.read_text(encoding="utf-8")

    assert "function Install-Git" in content
    assert "function Invoke-Git" in content
    assert "Git.Git" in content
    assert "function Configure-GitBash" in content
    assert 'SetEnvironmentVariable("HERMES_GIT_BASH_PATH"' in content
    assert "function Test-GitBashPath" in content
    assert 'Join-Path $binDir "..\\cmd\\git.exe"' in content


def test_install_ps1_skips_git_update_for_zip_bootstrap_reinstalls():
    content = INSTALL_SCRIPT.read_text(encoding="utf-8")

    assert 'Invoke-NativeProcess -FilePath "git" -ArgumentList @("ls-files") -IgnoreExitCode' in content
    assert '[string]::IsNullOrWhiteSpace($trackedStdOut)' in content
    assert "bootstrapped from a ZIP snapshot" in content
    assert "would be overwritten by checkout" in content


def test_install_ps1_uses_ascii_friendly_console_output():
    content = INSTALL_SCRIPT.read_text(encoding="utf-8")

    assert "Hermes Agent Installer" in content
    assert "[OK] $Message" in content
    assert "[WARN] $Message" in content
    assert "[ERR] $Message" in content
    assert "==> $Message" in content


def test_readme_mentions_windows_powershell_install():
    content = README.read_text(encoding="utf-8")

    assert "Windows (PowerShell)" in content
    assert "powershell -ExecutionPolicy Bypass" in content
