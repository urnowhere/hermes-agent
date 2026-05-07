"""Installer support for selecting npm or pnpm via environment variable."""

from __future__ import annotations

import re
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
INSTALL_SH = REPO_ROOT / "scripts" / "install.sh"
INSTALL_PS1 = REPO_ROOT / "scripts" / "install.ps1"


def _extract_shell_function(name: str) -> str:
    text = INSTALL_SH.read_text()
    match = re.search(
        rf"^{re.escape(name)}\(\)\s*\{{\s*\n(?P<body>.*?)^\}}",
        text,
        re.MULTILINE | re.DOTALL,
    )
    assert match is not None, f"{name}() not found in scripts/install.sh"
    return match["body"]


def _extract_powershell_function(name: str) -> str:
    text = INSTALL_PS1.read_text()
    match = re.search(
        rf"^function\s+{re.escape(name)}\s*\{{\s*\n(?P<body>.*?)^\}}",
        text,
        re.MULTILINE | re.DOTALL,
    )
    assert match is not None, f"{name} function not found in scripts/install.ps1"
    return match["body"]


def test_install_sh_node_dependencies_respect_env_package_manager() -> None:
    helper = _extract_shell_function("install_node_package_deps")
    install_body = _extract_shell_function("install_node_deps")

    assert "HERMES_NODE_PACKAGE_MANAGER" in helper
    assert "npm" in helper
    assert "pnpm" in helper
    assert 'command -v "$package_manager"' in helper
    assert '"$package_manager" install --silent' in helper

    assert 'install_node_package_deps "$INSTALL_DIR" "browser tools may not work"' in install_body
    assert (
        'install_node_package_deps "$INSTALL_DIR/ui-tui" "hermes --tui may not work"'
        in install_body
    )
    assert not re.search(r"(?m)^\s*npm\s+install\s+--silent\b", install_body)


def test_install_ps1_node_dependencies_respect_env_package_manager() -> None:
    helper = _extract_powershell_function("Invoke-NodePackageInstall")
    install_body = _extract_powershell_function("Install-NodeDeps")

    assert "HERMES_NODE_PACKAGE_MANAGER" in helper
    assert "npm" in helper
    assert "pnpm" in helper
    assert "Get-Command $packageManager" in helper
    assert "& $packageManager install --silent" in helper

    assert "Invoke-NodePackageInstall -Directory $InstallDir" in install_body
    assert "Invoke-NodePackageInstall -Directory $tuiDir" in install_body
    assert not re.search(r"(?m)^\s*npm\s+install\s+--silent\b", install_body)
