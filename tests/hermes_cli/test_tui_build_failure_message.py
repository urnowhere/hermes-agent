"""Surfaced error messages when ``_make_tui_argv`` aborts on a build failure.

Regression for #20500: in Docker images where ``/opt/hermes/ui-tui`` is
root-owned but the dashboard runs as the unprivileged ``hermes`` user,
``npm run build`` inside ``ui-tui`` fails with EACCES on the ``dist/``
write step, ``_make_tui_argv`` previously called ``sys.exit(1)`` (no
arg), and ``hermes_cli.web_server.pty_ws`` rendered the unhelpful
``Chat unavailable: 1`` banner over the WebSocket.

The new behavior raises ``SystemExit`` with an actionable string so
``pty_ws`` surfaces ``Chat unavailable: TUI build failed (permission
denied writing to dist/). ...`` instead.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture
def main_mod():
    import hermes_cli.main as m

    return m


def _stub_tui_dir(tmp_path: Path) -> Path:
    """Build a tui_dir that lets ``_make_tui_argv`` reach the build step."""
    tui_dir = tmp_path / "ui-tui"
    tui_dir.mkdir()
    # node_modules must exist so _tui_need_npm_install short-circuits to False
    # for the lockfile branch.  Touch the @hermes/ink sentinel.
    ink = tui_dir / "node_modules" / "@hermes" / "ink" / "package.json"
    ink.parent.mkdir(parents=True, exist_ok=True)
    ink.write_text("{}")
    # No package-lock.json → _tui_need_npm_install returns False (no lock file).
    return tui_dir


def test_eacces_failure_yields_actionable_systemexit(
    monkeypatch, tmp_path, main_mod
):
    """An esbuild EACCES on dist/ → SystemExit message mentions permission +
    points to issue #20500 + the chown remedy."""
    tui_dir = _stub_tui_dir(tmp_path)

    monkeypatch.setattr(main_mod, "_ensure_tui_node", lambda: None)
    monkeypatch.setattr(main_mod, "_tui_need_npm_install", lambda _p: False)
    monkeypatch.setattr(main_mod, "_tui_build_needed", lambda _p: True)
    # Provide a deterministic node/npm binary discovery shim so we don't
    # depend on the host environment.
    monkeypatch.setattr(main_mod.shutil, "which", lambda _bin: "/usr/bin/" + _bin)

    def _fake_run(*_args, **_kwargs):
        return SimpleNamespace(
            returncode=1,
            stdout="",
            stderr=(
                "> hermes-tui@0.0.1 build\n"
                "✘ [ERROR] Failed to write to output file:\n"
                "   open /opt/hermes/ui-tui/packages/hermes-ink/dist/"
                "entry-exports.js: permission denied\n"
            ),
        )

    monkeypatch.setattr(subprocess, "run", _fake_run)

    with pytest.raises(SystemExit) as excinfo:
        main_mod._make_tui_argv(tui_dir, tui_dev=False)

    msg = str(excinfo.value)
    assert "permission denied" in msg.lower()
    assert "20500" in msg
    # Must NOT collapse to the bare ``1`` integer that produced the
    # unhelpful ``Chat unavailable: 1`` banner.
    assert msg != "1"
    assert excinfo.value.code != 1  # str arg → code is the str, not 1


def test_generic_build_failure_yields_descriptive_systemexit(
    monkeypatch, tmp_path, main_mod
):
    """Non-permission build failures still get a descriptive SystemExit so
    ``Chat unavailable:`` is followed by a hint, not a stray ``1``."""
    tui_dir = _stub_tui_dir(tmp_path)

    monkeypatch.setattr(main_mod, "_ensure_tui_node", lambda: None)
    monkeypatch.setattr(main_mod, "_tui_need_npm_install", lambda _p: False)
    monkeypatch.setattr(main_mod, "_tui_build_needed", lambda _p: True)
    monkeypatch.setattr(main_mod.shutil, "which", lambda _bin: "/usr/bin/" + _bin)

    def _fake_run(*_args, **_kwargs):
        return SimpleNamespace(
            returncode=2,
            stdout="",
            stderr="✘ [ERROR] Could not resolve 'react'\n",
        )

    monkeypatch.setattr(subprocess, "run", _fake_run)

    with pytest.raises(SystemExit) as excinfo:
        main_mod._make_tui_argv(tui_dir, tui_dev=False)

    msg = str(excinfo.value)
    assert "TUI build failed" in msg
    assert msg != "1"
