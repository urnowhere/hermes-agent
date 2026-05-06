import json
from pathlib import Path

import pytest

from tools.environments.local import LocalEnvironment
from tools.file_tools import read_file_tool
from tools.terminal_tool import terminal_tool


@pytest.fixture(autouse=True)
def _clear_terminal_state(monkeypatch):
    import tools.terminal_tool as tt
    import tools.file_tools as ft

    monkeypatch.setattr(tt, "_active_environments", {})
    monkeypatch.setattr(tt, "_last_activity", {})
    monkeypatch.setattr(tt, "_task_env_overrides", {})
    monkeypatch.setattr(ft, "_file_ops_cache", {})


def test_local_environment_falls_back_when_init_cwd_missing(tmp_path):
    missing = tmp_path / "gone"
    env = LocalEnvironment(cwd=str(missing), timeout=10)

    result = env.execute("pwd")

    assert result["returncode"] == 0
    assert result["output"].strip() == str(Path.cwd())


def test_terminal_tool_uses_workdir_when_terminal_cwd_missing(monkeypatch, tmp_path):
    import tools.terminal_tool as tt

    missing = tmp_path / "gone"
    workdir = tmp_path / "safe"
    workdir.mkdir()

    monkeypatch.setenv("TERMINAL_ENV", "local")
    monkeypatch.setenv("TERMINAL_CWD", str(missing))
    monkeypatch.setattr(tt, "_start_cleanup_thread", lambda: None)

    result = json.loads(
        terminal_tool(
            command="pwd",
            workdir=str(workdir),
            task_id="missing-cwd-terminal",
        )
    )

    assert result["exit_code"] == 0
    assert result["output"].strip() == str(workdir)


def test_read_file_tool_absolute_path_survives_stale_terminal_cwd(monkeypatch, tmp_path):
    target = tmp_path / "note.txt"
    target.write_text("hello from file tool\n", encoding="utf-8")

    monkeypatch.setenv("TERMINAL_ENV", "local")
    monkeypatch.setenv("TERMINAL_CWD", str(tmp_path / "missing"))

    result = json.loads(
        read_file_tool(str(target), task_id="missing-cwd-file")
    )

    assert "error" not in result
    assert "hello from file tool" in result["content"]
