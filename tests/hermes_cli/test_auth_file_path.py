"""Tests for ``_auth_file_path`` env-var override (HERMES_AUTH_FILE).

The override lets multiple Hermes profiles (or processes) point at a single
auth.json so the existing ``_auth_store_lock`` flock — which is keyed off the
auth file path — serializes OAuth refresh across all consumers.
"""

import json
from pathlib import Path

import pytest

from hermes_cli.auth import (
    _auth_file_path,
    _auth_lock_path,
    _load_auth_store,
    _save_auth_store,
)


def test_auth_file_path_defaults_to_hermes_home(tmp_path, monkeypatch):
    home = tmp_path / "hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.delenv("HERMES_AUTH_FILE", raising=False)

    assert _auth_file_path() == home / "auth.json"


def test_auth_file_path_honors_hermes_auth_file_env(tmp_path, monkeypatch):
    home = tmp_path / "hermes"
    home.mkdir()
    shared = tmp_path / "shared" / "auth.json"
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("HERMES_AUTH_FILE", str(shared))

    assert _auth_file_path() == shared


def test_auth_file_path_expands_user_in_override(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes"))
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("HERMES_AUTH_FILE", "~/shared-auth.json")

    assert _auth_file_path() == tmp_path / "shared-auth.json"


def test_auth_file_path_blank_override_falls_through(tmp_path, monkeypatch):
    home = tmp_path / "hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("HERMES_AUTH_FILE", "   ")  # whitespace-only

    assert _auth_file_path() == home / "auth.json"


def test_auth_lock_path_follows_override(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes"))
    shared = tmp_path / "shared" / "auth.json"
    monkeypatch.setenv("HERMES_AUTH_FILE", str(shared))

    # _auth_lock_path is derived from _auth_file_path, so two profiles using
    # the same HERMES_AUTH_FILE will block on the same lock path — which is
    # what serializes refresh across them.
    assert _auth_lock_path() == shared.with_suffix(".lock")


def test_save_and_load_round_trip_through_override(tmp_path, monkeypatch):
    """Two distinct HERMES_HOME values pointing at one HERMES_AUTH_FILE
    should observe the same persisted state — proving profiles share.
    """
    profile_a = tmp_path / "profile_a"
    profile_b = tmp_path / "profile_b"
    profile_a.mkdir()
    profile_b.mkdir()
    shared = tmp_path / "shared" / "auth.json"
    shared.parent.mkdir()

    monkeypatch.setenv("HERMES_AUTH_FILE", str(shared))

    monkeypatch.setenv("HERMES_HOME", str(profile_a))
    _save_auth_store({"version": 1, "providers": {"x": {"v": "from-a"}}})

    monkeypatch.setenv("HERMES_HOME", str(profile_b))
    loaded = _load_auth_store()
    assert loaded["providers"]["x"]["v"] == "from-a"

    # Sanity: the per-profile auth.json files were never written.
    assert not (profile_a / "auth.json").exists()
    assert not (profile_b / "auth.json").exists()
    assert shared.exists()


def test_two_profiles_one_lock_path(tmp_path, monkeypatch):
    """Both profiles must compute the SAME lock path so flock actually
    serializes them. This is the property that prevents the refresh race.
    """
    shared = tmp_path / "shared" / "auth.json"
    monkeypatch.setenv("HERMES_AUTH_FILE", str(shared))

    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "profile_a"))
    lock_a = _auth_lock_path()
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "profile_b"))
    lock_b = _auth_lock_path()

    assert lock_a == lock_b == shared.with_suffix(".lock")
