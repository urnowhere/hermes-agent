from __future__ import annotations

from plugins.formsy.identity import derive_formsy_identity


def test_identity_prefers_explicit_case_id_for_task_id(monkeypatch):
    monkeypatch.setenv("FORMSY_CASE_ID", "django__django-11099")
    monkeypatch.delenv("FORMSY_TASK_ID", raising=False)
    monkeypatch.delenv("FORMSY_RUN_ID", raising=False)

    identity = derive_formsy_identity(
        session_id="sess-1",
        user_message="fix django issue",
        repo_id="django__django",
        revision="abc123",
        workspace_id="local",
    )

    assert identity.task_id == "django__django-11099"
    assert identity.case_id == "django__django-11099"
    assert identity.run_id.startswith("run_")
    assert identity.session_id == "sess-1"
    assert identity.workspace_id == "local"
    assert identity.repo_id == "django__django"
    assert identity.revision == "abc123"


def test_identity_uses_explicit_run_id_without_rehashing(monkeypatch):
    monkeypatch.setenv("FORMSY_TASK_ID", "task-explicit")
    monkeypatch.setenv("FORMSY_RUN_ID", "run-explicit")

    identity = derive_formsy_identity(session_id="sess-1")

    assert identity.task_id == "task-explicit"
    assert identity.run_id == "run-explicit"


def test_identity_fallback_task_id_is_short_and_stable(monkeypatch):
    monkeypatch.delenv("FORMSY_TASK_ID", raising=False)
    monkeypatch.delenv("FORMSY_CASE_ID", raising=False)
    monkeypatch.delenv("FORMSY_RUN_ID", raising=False)

    first = derive_formsy_identity(
        session_id="sess-a",
        user_message="Fix parser handling for quoted strings",
        repo_id="demo__repo",
    )
    second = derive_formsy_identity(
        session_id="sess-b",
        user_message="Fix parser handling for quoted strings",
        repo_id="demo__repo",
    )

    assert first.task_id == second.task_id
    assert first.task_id.startswith("demo__repo-")
    assert not first.task_id.startswith("sha256:")
    assert len(first.task_id) <= 80


def test_identity_parses_swebench_case_id_from_message(monkeypatch):
    monkeypatch.delenv("FORMSY_TASK_ID", raising=False)
    monkeypatch.delenv("FORMSY_CASE_ID", raising=False)

    identity = derive_formsy_identity(
        session_id="sess-1",
        user_message="Please solve django__django-14053 and submit the patch.",
        repo_id="django__django",
    )

    assert identity.task_id == "django__django-14053"
    assert identity.case_id == "django__django-14053"


def test_identity_serializes_task_and_workspace_refs(monkeypatch):
    monkeypatch.setenv("FORMSY_TASK_ID", "task-1")
    monkeypatch.setenv("FORMSY_RUN_ID", "run-1")

    identity = derive_formsy_identity(
        session_id="sess-1",
        workspace_id="ws-1",
        repo_id="repo-1",
        revision="rev-1",
    )

    assert identity.to_task_ref() == {
        "task_id": "task-1",
        "run_id": "run-1",
        "session_id": "sess-1",
        "case_id": "task-1",
    }
    assert identity.to_workspace_ref() == {
        "workspace_id": "ws-1",
        "repo_id": "repo-1",
        "revision": "rev-1",
    }
    assert identity.to_runtime_identity()["task_id"] == "task-1"
