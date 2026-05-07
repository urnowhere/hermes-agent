"""Tests for the Docker root-execution guard (issue #16480)."""

from __future__ import annotations

import os
import sys
from unittest import mock

import pytest

from hermes_cli import docker_guard


@pytest.fixture(autouse=True)
def _clear_disable_env(monkeypatch):
    monkeypatch.delenv(docker_guard._DISABLE_ENV, raising=False)
    monkeypatch.delenv("HERMES_DOCKER_GUARD_REEXEC", raising=False)
    yield


def _patch_env(monkeypatch, *, in_container=True, root=True, uid="10000", gid="10000"):
    monkeypatch.setattr(docker_guard, "_is_linux", lambda: True)
    monkeypatch.setattr(docker_guard, "_running_as_root", lambda: root)
    monkeypatch.setattr(docker_guard, "_in_container", lambda: in_container)
    if uid is None:
        monkeypatch.delenv("HERMES_UID", raising=False)
    else:
        monkeypatch.setenv("HERMES_UID", uid)
    if gid is None:
        monkeypatch.delenv("HERMES_GID", raising=False)
    else:
        monkeypatch.setenv("HERMES_GID", gid)


def test_noop_on_non_linux(monkeypatch):
    monkeypatch.setattr(docker_guard, "_is_linux", lambda: False)
    monkeypatch.setattr(docker_guard, "_running_as_root", lambda: True)
    monkeypatch.setattr(docker_guard, "_in_container", lambda: True)
    # Must not raise / exit / exec.
    docker_guard.enforce_docker_non_root(["hermes", "chat"])


def test_noop_when_not_root(monkeypatch):
    _patch_env(monkeypatch, root=False)
    docker_guard.enforce_docker_non_root(["hermes", "chat"])


def test_noop_when_not_in_container(monkeypatch):
    _patch_env(monkeypatch, in_container=False)
    docker_guard.enforce_docker_non_root(["hermes", "chat"])


def test_noop_when_no_target_uid(monkeypatch):
    _patch_env(monkeypatch, uid=None)
    docker_guard.enforce_docker_non_root(["hermes", "chat"])


def test_noop_when_target_uid_is_root(monkeypatch):
    _patch_env(monkeypatch, uid="0")
    docker_guard.enforce_docker_non_root(["hermes", "chat"])


def test_disable_env_skips_guard(monkeypatch):
    _patch_env(monkeypatch)
    monkeypatch.setenv(docker_guard._DISABLE_ENV, "1")
    # Even though all conditions are met, env override skips the guard.
    docker_guard.enforce_docker_non_root(["hermes", "chat"])


def test_version_subcommand_bypasses(monkeypatch):
    _patch_env(monkeypatch)
    # version / help shouldn't trigger refuse-or-exec
    docker_guard.enforce_docker_non_root(["hermes", "version"])
    docker_guard.enforce_docker_non_root(["hermes", "--version"])
    docker_guard.enforce_docker_non_root(["hermes", "--help"])


def test_reexec_marker_breaks_loop(monkeypatch):
    _patch_env(monkeypatch)
    monkeypatch.setenv("HERMES_DOCKER_GUARD_REEXEC", "1")
    # Should return without trying to exec or exit.
    docker_guard.enforce_docker_non_root(["hermes", "chat"])


def test_reexecs_via_gosu(monkeypatch):
    _patch_env(monkeypatch)
    monkeypatch.setattr(docker_guard, "_find_dropper", lambda: ["/usr/local/bin/gosu"])

    captured: dict = {}

    def fake_execvpe(prog, argv, env):
        captured["prog"] = prog
        captured["argv"] = argv
        captured["env"] = env
        raise SystemExit(0)  # simulate exec replacing the process

    monkeypatch.setattr(docker_guard.os, "execvpe", fake_execvpe)

    with pytest.raises(SystemExit) as exc:
        docker_guard.enforce_docker_non_root(["hermes", "chat"])
    assert exc.value.code == 0
    assert captured["prog"] == "/usr/local/bin/gosu"
    assert captured["argv"] == [
        "/usr/local/bin/gosu",
        "10000:10000",
        "hermes",
        "chat",
    ]
    assert captured["env"]["HERMES_DOCKER_GUARD_REEXEC"] == "1"


def test_refuses_when_no_dropper(monkeypatch, capsys):
    _patch_env(monkeypatch)
    monkeypatch.setattr(docker_guard, "_find_dropper", lambda: None)

    with pytest.raises(SystemExit) as exc:
        docker_guard.enforce_docker_non_root(["hermes", "chat"])
    assert exc.value.code == 1

    err = capsys.readouterr().err
    assert "refuses to run as root" in err
    assert "10000:10000" in err
    assert docker_guard._DISABLE_ENV in err


def test_invalid_uid_is_ignored(monkeypatch):
    _patch_env(monkeypatch, uid="not-an-int")
    # Should not raise / exit even though we're root in a container.
    docker_guard.enforce_docker_non_root(["hermes", "chat"])


def test_invalid_gid_falls_back_to_uid(monkeypatch):
    _patch_env(monkeypatch, uid="10000", gid="bogus")
    monkeypatch.setattr(docker_guard, "_find_dropper", lambda: ["/usr/bin/gosu"])

    captured: dict = {}

    def fake_execvpe(prog, argv, env):
        captured["argv"] = argv
        raise SystemExit(0)

    monkeypatch.setattr(docker_guard.os, "execvpe", fake_execvpe)

    with pytest.raises(SystemExit):
        docker_guard.enforce_docker_non_root(["hermes", "chat"])
    # GID should have fallen back to UID.
    assert captured["argv"][1] == "10000:10000"


def test_in_container_detects_dockerenv(monkeypatch, tmp_path):
    monkeypatch.setattr(os.path, "exists", lambda p: p == "/.dockerenv")
    monkeypatch.delenv("HERMES_DOCKER", raising=False)
    assert docker_guard._in_container() is True


def test_in_container_detects_hermes_docker_env(monkeypatch):
    monkeypatch.setattr(os.path, "exists", lambda p: False)
    monkeypatch.setenv("HERMES_DOCKER", "1")
    assert docker_guard._in_container() is True


def test_in_container_false_on_bare_host(monkeypatch):
    monkeypatch.setattr(os.path, "exists", lambda p: False)
    monkeypatch.delenv("HERMES_DOCKER", raising=False)
    fake_open = mock.mock_open(read_data="0::/user.slice/user-1000.slice")
    with mock.patch("builtins.open", fake_open):
        assert docker_guard._in_container() is False
