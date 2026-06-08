from __future__ import annotations

import httpx
import pytest

from plugins.formsy.errors import RuntimeAPIError
from plugins.formsy.constraint_keeper.client import ConstraintKeeperClient


class FakeAsyncClient:
    def __init__(self, status_code: int = 200, json_data: dict | None = None):
        self.status_code = status_code
        self.json_data = json_data or {"ok": True}
        self.calls = []

    async def request(self, method, url, json=None, headers=None):
        self.calls.append({
            "method": method,
            "url": url,
            "json": json,
            "headers": headers,
        })
        return httpx.Response(
            self.status_code,
            json=self.json_data,
            request=httpx.Request(method, url, json=json, headers=headers),
        )

    async def aclose(self):
        pass


@pytest.mark.asyncio
async def test_constraint_client_task_start_posts_authenticated_payload():
    client = ConstraintKeeperClient(base_url="https://runtime.example", api_key="fsy_local_key")
    fake = FakeAsyncClient(json_data={"task_started": True})
    client._client = fake

    result = await client.task_start(
        task={"task_id": "task-1", "run_id": "run-1", "session_id": "sess-1", "case_id": "case-1"},
        workspace={"workspace_id": "local", "repo_id": "repo", "revision": "abc"},
        session_id="sess-1",
    )

    assert result == {"task_started": True}
    assert fake.calls == [{
        "method": "POST",
        "url": "https://runtime.example/v1/runtime/constraints/task_start",
        "json": {
            "task": {"task_id": "task-1", "run_id": "run-1", "session_id": "sess-1", "case_id": "case-1"},
            "workspace": {"workspace_id": "local", "repo_id": "repo", "revision": "abc"},
        },
        "headers": {
            "Content-Type": "application/json",
            "Authorization": "Bearer fsy_local_key",
            "X-Session-ID": "sess-1",
        },
    }]


@pytest.mark.asyncio
async def test_constraint_client_uses_expected_runtime_endpoints():
    client = ConstraintKeeperClient(base_url="https://runtime.example", api_key="fsy_local_key")
    fake = FakeAsyncClient()
    client._client = fake

    await client.compile_constraints({"payload": "compile"}, session_id="sess-1")
    await client.observe({"payload": "observe"}, session_id="sess-1")
    await client.recover({"payload": "recover"}, session_id="sess-1")
    await client.verify_completion({"payload": "verify"}, session_id="sess-1")
    await client.status("task-1", "run-1", session_id="sess-1")

    assert [(call["method"], call["url"]) for call in fake.calls] == [
        ("POST", "https://runtime.example/v1/runtime/constraints/compile"),
        ("POST", "https://runtime.example/v1/runtime/constraints/observe"),
        ("POST", "https://runtime.example/v1/runtime/constraints/recover"),
        ("POST", "https://runtime.example/v1/runtime/constraints/verify_completion"),
        ("GET", "https://runtime.example/v1/runtime/constraints/status/task-1/run-1"),
    ]


@pytest.mark.asyncio
async def test_constraint_client_raises_runtime_error_on_unauthorized():
    client = ConstraintKeeperClient(base_url="https://runtime.example", api_key="bad")
    client._client = FakeAsyncClient(status_code=401, json_data={"detail": "nope"})

    with pytest.raises(RuntimeAPIError) as exc_info:
        await client.observe({"payload": "observe"}, session_id="sess-1")

    assert exc_info.value.status_code == 401
