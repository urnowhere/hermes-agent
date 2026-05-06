from __future__ import annotations

import json
from pathlib import Path


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _base_and_profile(tmp_path: Path) -> tuple[Path, Path]:
    base_home = tmp_path / "hermes"
    profile_home = base_home / "profiles" / "webui"
    profile_home.mkdir(parents=True)
    return base_home, profile_home


def _auth_store(cred_id: str, token: str = "sk-or-test") -> dict:
    label = cred_id.split("-", 1)[0]
    return {
        "version": 1,
        "credential_pool": {
            "openrouter": [
                {
                    "id": cred_id,
                    "label": label,
                    "source": "manual",
                    "auth_type": "api_key",
                    "access_token": token,
                    "priority": 0,
                }
            ]
        },
    }


def _empty_profile_store() -> dict:
    return {"version": 1, "credential_pool": {"openrouter": []}}


def _enable_base_sharing(profile_home: Path) -> None:
    (profile_home / "config.yaml").write_text(
        "agent:\n  credential_pool_share_base: true\n",
        encoding="utf-8",
    )


def test_read_credential_pool_falls_back_to_base_profile_when_enabled(
    tmp_path,
    monkeypatch,
):
    base_home, profile_home = _base_and_profile(tmp_path)
    monkeypatch.setenv("HERMES_HOME", str(profile_home))

    _write_json(base_home / "auth.json", _auth_store("base-cred", "sk-or-base"))
    _write_json(profile_home / "auth.json", _empty_profile_store())
    _enable_base_sharing(profile_home)

    from hermes_cli.auth import read_credential_pool

    entries = read_credential_pool("openrouter")

    assert [entry["id"] for entry in entries] == ["base-cred"]
    profile_store = json.loads((profile_home / "auth.json").read_text(encoding="utf-8"))
    assert profile_store["credential_pool"]["openrouter"] == []


def test_read_credential_pool_does_not_fallback_without_flag(tmp_path, monkeypatch):
    base_home, profile_home = _base_and_profile(tmp_path)
    monkeypatch.setenv("HERMES_HOME", str(profile_home))

    _write_json(base_home / "auth.json", _auth_store("base-cred", "sk-or-base"))
    _write_json(profile_home / "auth.json", _empty_profile_store())

    from hermes_cli.auth import read_credential_pool

    assert read_credential_pool("openrouter") == []


def test_read_credential_pool_prefers_profile_entries_when_enabled(
    tmp_path,
    monkeypatch,
):
    base_home, profile_home = _base_and_profile(tmp_path)
    monkeypatch.setenv("HERMES_HOME", str(profile_home))

    _write_json(base_home / "auth.json", _auth_store("base-cred", "sk-or-base"))
    _write_json(
        profile_home / "auth.json",
        _auth_store("profile-cred", "sk-or-profile"),
    )
    _enable_base_sharing(profile_home)

    from hermes_cli.auth import read_credential_pool

    entries = read_credential_pool("openrouter")

    assert [entry["id"] for entry in entries] == ["profile-cred"]


def test_load_pool_uses_base_entries_without_copying_to_profile(tmp_path, monkeypatch):
    base_home, profile_home = _base_and_profile(tmp_path)
    monkeypatch.setenv("HERMES_HOME", str(profile_home))
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    _write_json(base_home / "auth.json", _auth_store("base-cred", "sk-or-base"))
    _write_json(profile_home / "auth.json", _empty_profile_store())
    _enable_base_sharing(profile_home)

    from agent.credential_pool import load_pool

    pool = load_pool("openrouter")
    entry = pool.select()

    assert entry is not None
    assert entry.id == "base-cred"
    profile_store = json.loads((profile_home / "auth.json").read_text(encoding="utf-8"))
    assert profile_store["credential_pool"]["openrouter"] == []
