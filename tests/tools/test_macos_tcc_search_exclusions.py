"""Regression tests for macOS TCC-protected search exclusions.

Wide home-directory file walks on macOS can trigger repeated TCC permission
prompts when they recurse into app containers and privacy-protected data stores.
These tests use synthetic temp homes only; they never touch the real ~/Library.
"""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import tools.file_operations as file_ops_mod
from tools.file_operations import ShellFileOperations


REQUIRED_TCC_GLOBS = {
    "Library/Containers/**",
    "Library/Group Containers/**",
    "Library/Mail/**",
    "Library/Messages/**",
    "Library/Calendars/**",
    "Library/Reminders/**",
    "Library/Mobile Documents/**",
    "Library/CloudStorage/**",
    "Library/Application Support/com.apple.sharedfilelist/**",
    "Library/Application Support/AddressBook/**",
    "Pictures/Photos Library.photoslibrary/**",
}


@pytest.fixture
def synthetic_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(file_ops_mod.sys, "platform", "darwin")
    return home


@pytest.fixture
def file_ops():
    env = MagicMock()
    env.cwd = "/tmp/test"
    env.execute.return_value = {"output": "", "returncode": 0}
    return ShellFileOperations(env)


def _exec_result(stdout="", exit_code=0, stderr=""):
    return SimpleNamespace(stdout=stdout, exit_code=exit_code, stderr=stderr)


def test_macos_home_search_default_excludes_required_tcc_globs(synthetic_home):
    excludes = set(file_ops_mod._macos_tcc_excluded_globs_for_root(str(synthetic_home)))

    assert REQUIRED_TCC_GLOBS.issubset(excludes)


def test_macos_library_search_excludes_protected_children(synthetic_home):
    excludes = set(
        file_ops_mod._macos_tcc_excluded_globs_for_root(str(synthetic_home / "Library"))
    )

    assert "Containers/**" in excludes
    assert "Group Containers/**" in excludes
    assert "Messages/**" in excludes
    assert "Mail/**" in excludes


def test_explicit_protected_path_search_is_not_silently_hidden(synthetic_home):
    excludes = file_ops_mod._macos_tcc_excluded_globs_for_root(
        str(synthetic_home / "Library" / "Containers")
    )

    assert excludes == []


def test_linux_does_not_add_macos_tcc_exclusions(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setattr(file_ops_mod.sys, "platform", "linux")

    assert file_ops_mod._macos_tcc_excluded_globs_for_root(str(tmp_path)) == []


def test_rg_file_search_includes_tcc_exclusion_globs(file_ops, synthetic_home):
    file_ops._exec = MagicMock(return_value=_exec_result(""))
    file_ops._search_files_rg("config.yaml", str(synthetic_home), 10, 0, ["Library/Containers/**"])

    cmd = file_ops._exec.call_args[0][0]
    assert "--glob '!Library/Containers/**'" in cmd


def test_rg_content_search_includes_tcc_exclusion_globs(file_ops, synthetic_home):
    file_ops._exec = MagicMock(return_value=_exec_result(""))
    file_ops._search_with_rg(
        "needle",
        str(synthetic_home),
        None,
        10,
        0,
        "content",
        0,
        ["Library/Containers/**"],
    )

    cmd = file_ops._exec.call_args[0][0]
    assert "--glob '!Library/Containers/**'" in cmd


def test_find_fallback_prunes_tcc_paths_by_default(file_ops, synthetic_home):
    file_ops._has_command = MagicMock(side_effect=lambda cmd: cmd == "find")
    file_ops._exec = MagicMock(return_value=_exec_result(""))

    file_ops.search("config.yaml", path=str(synthetic_home), target="files")

    cmd = file_ops._exec.call_args[0][0]
    assert "-prune" in cmd
    assert "Library/Containers" in cmd
    assert "Library/Group Containers" in cmd


def test_grep_fallback_uses_find_prefilter_for_tcc_paths(file_ops, synthetic_home):
    file_ops._exec = MagicMock(return_value=_exec_result(""))
    file_ops._search_with_grep(
        "needle",
        str(synthetic_home),
        None,
        10,
        0,
        "content",
        0,
        ["Library/Containers/**"],
    )

    cmd = file_ops._exec.call_args[0][0]
    assert cmd.startswith("find ")
    assert "-prune" in cmd
    assert "Library/Containers" in cmd
    assert "xargs -0 grep" in cmd


def test_per_call_include_tcc_paths_disables_default_globs(file_ops, synthetic_home):
    calls = []

    def fake_exec(cmd, *args, **kwargs):
        calls.append(cmd)
        if "test -e" in cmd:
            return _exec_result("exists\n")
        return _exec_result("")

    file_ops._has_command = MagicMock(side_effect=lambda cmd: cmd == "rg")
    file_ops._exec = MagicMock(side_effect=fake_exec)

    file_ops.search(
        "config.yaml",
        path=str(synthetic_home),
        target="files",
        include_tcc_paths=True,
    )

    rg_cmds = [cmd for cmd in calls if cmd.startswith("rg --files")]
    assert rg_cmds
    assert all("Library/Containers" not in cmd for cmd in rg_cmds)


def test_config_include_tcc_paths_disables_default_globs(file_ops, synthetic_home, monkeypatch):
    monkeypatch.setattr(
        "hermes_cli.config.load_config",
        lambda: {"agent": {"search": {"include_tcc_paths": True}}},
    )
    calls = []

    def fake_exec(cmd, *args, **kwargs):
        calls.append(cmd)
        if "test -e" in cmd:
            return _exec_result("exists\n")
        return _exec_result("")

    file_ops._has_command = MagicMock(side_effect=lambda cmd: cmd == "rg")
    file_ops._exec = MagicMock(side_effect=fake_exec)

    file_ops.search("config.yaml", path=str(synthetic_home), target="files")

    rg_cmds = [cmd for cmd in calls if cmd.startswith("rg --files")]
    assert rg_cmds
    assert all("Library/Containers" not in cmd for cmd in rg_cmds)
