"""Tests for Formsy RuntimeClient diagnostics."""

import logging

import httpx
import pytest

from plugins.formsy.errors import RuntimeAPIError
from plugins.formsy.runtime_client import RuntimeClient


@pytest.mark.asyncio
async def test_runtime_client_logs_http_error_context(caplog, monkeypatch):
    monkeypatch.setenv("FORMSY_API_KEY", "fsy_test_secret_token")
    client = RuntimeClient(base_url="https://runtime.example", api_key_env="FORMSY_API_KEY")

    class FakeAsyncClient:
        async def request(self, method, url, json=None, headers=None):
            return httpx.Response(
                500,
                json={"error": "broken"},
                request=httpx.Request(method, url, json=json, headers=headers),
            )

    client._client = FakeAsyncClient()

    with caplog.at_level(logging.ERROR, logger="formalcc.runtime_client"):
        with pytest.raises(RuntimeAPIError):
            await client._request(
                "POST",
                "/v1/runtime/memory/search",
                data={"query": "parser"},
                session_id="session-1",
            )

    log_text = caplog.text
    assert "POST https://runtime.example/v1/runtime/memory/search" in log_text
    assert "status_code=500" in log_text
    assert '"query": "parser"' in log_text
    assert '"error": "broken"' in log_text
    assert "X-Session-ID" in log_text
    assert "Authorization" in log_text
    assert "Bearer ***" in log_text


@pytest.mark.asyncio
async def test_runtime_client_memory_search_uses_configured_endpoint(monkeypatch):
    monkeypatch.setenv("FORMSY_API_KEY", "fsy_test_secret_token")
    client = RuntimeClient(
        base_url="https://runtime.example",
        memory_search_endpoint="api/v1/query",
        api_key_env="FORMSY_API_KEY",
    )
    calls = []

    class FakeAsyncClient:
        async def request(self, method, url, json=None, headers=None):
            calls.append({
                "method": method,
                "url": url,
                "json": json,
                "headers": headers,
            })
            return httpx.Response(
                200,
                json={"matches": []},
                request=httpx.Request(method, url, json=json, headers=headers),
            )

    client._client = FakeAsyncClient()

    result = await client.memory_search(
        repo_id="django__django-14053",
        session_id="session-1",
        query="parser",
        revision="latest",
        budget=4000,
    )

    assert result == {"matches": []}
    assert calls[0]["method"] == "POST"
    assert calls[0]["url"] == "https://runtime.example/api/v1/query"
    assert calls[0]["json"] == {
        "repo_id": "django__django-14053",
        "query": "parser",
        "revision": "latest",
        "budget": 4000,
        "enable_profiling": False,
        "profiling_top_n": 20,
        "metadata": {"instance_id": "django__django-14053"},
    }


@pytest.mark.asyncio
async def test_runtime_client_memory_search_forwards_metadata(monkeypatch):
    monkeypatch.setenv("FORMSY_API_KEY", "fsy_test_secret_token")
    client = RuntimeClient(
        base_url="https://runtime.example",
        api_key_env="FORMSY_API_KEY",
    )
    calls = []

    class FakeAsyncClient:
        async def request(self, method, url, json=None, headers=None):
            calls.append({
                "method": method,
                "url": url,
                "json": json,
                "headers": headers,
            })
            return httpx.Response(
                200,
                json={"matches": []},
                request=httpx.Request(method, url, json=json, headers=headers),
            )

    client._client = FakeAsyncClient()

    result = await client.memory_search(
        repo_id="django__django-14053",
        session_id="session-1",
        query="parser",
        revision="latest",
        budget=4000,
        metadata={
            "retrieval_mode": "symbolic",
            "grounding_phase": "seed",
            "response_format": "bundle",
            "trace_id": "trace-1",
            "case_id": "case-1",
        },
    )

    assert result == {"matches": []}
    assert calls[0]["json"]["metadata"] == {
        "retrieval_mode": "symbolic",
        "grounding_phase": "seed",
        "response_format": "bundle",
        "trace_id": "trace-1",
        "case_id": "case-1",
    }


@pytest.mark.asyncio
async def test_runtime_client_memory_read_uses_read_endpoint(monkeypatch):
    monkeypatch.setenv("FORMSY_API_KEY", "fsy_test_secret_token")
    client = RuntimeClient(
        base_url="https://runtime.example",
        api_key_env="FORMSY_API_KEY",
    )
    calls = []

    class FakeAsyncClient:
        async def request(self, method, url, json=None, headers=None):
            calls.append({
                "method": method,
                "url": url,
                "json": json,
                "headers": headers,
            })
            return httpx.Response(
                200,
                json={"content": "def parser():\n    pass"},
                request=httpx.Request(method, url, json=json, headers=headers),
            )

    client._client = FakeAsyncClient()

    result = await client.memory_read(
        repo_id="django__django-14053",
        session_id="session-1",
        path="django/contrib/staticfiles/storage.py",
        revision="latest",
        start_line=203,
        end_line=249,
    )

    assert result == {"content": "def parser():\n    pass"}
    assert calls[0]["method"] == "POST"
    assert calls[0]["url"] == "https://runtime.example/api/v1/read"
    assert calls[0]["json"] == {
        "repo_id": "django__django-14053",
        "revision": "latest",
        "path": "django/contrib/staticfiles/storage.py",
        "start_line": 203,
        "end_line": 249,
    }


@pytest.mark.asyncio
async def test_runtime_client_compile_repo_uses_compile_endpoint(monkeypatch):
    monkeypatch.setenv("FORMSY_API_KEY", "fsy_test_secret_token")
    client = RuntimeClient(
        base_url="https://runtime.example",
        api_key_env="FORMSY_API_KEY",
    )
    calls = []

    class FakeAsyncClient:
        async def request(self, method, url, json=None, headers=None):
            calls.append({
                "method": method,
                "url": url,
                "json": json,
                "headers": headers,
            })
            return httpx.Response(
                200,
                json={
                    "repo_id": "urnowhere__hermes-agent",
                    "revision": "abc123def456",
                    "parsed_file_count": 1,
                },
                request=httpx.Request(method, url, json=json, headers=headers),
            )

    client._client = FakeAsyncClient()

    result = await client.compile_repo(
        repo_id="urnowhere__hermes-agent",
        files=[{
            "path": "pkg/mod.py",
            "content": "x = 1\n",
            "language": "python",
            "is_test": False,
        }],
        revision="abc123def456",
        metadata={"instance_id": "urnowhere__hermes-agent"},
    )

    assert result == {
        "repo_id": "urnowhere__hermes-agent",
        "revision": "abc123def456",
        "parsed_file_count": 1,
    }
    assert calls[0]["method"] == "POST"
    assert calls[0]["url"] == "https://runtime.example/api/v1/compile"
    assert calls[0]["json"] == {
        "repo_id": "urnowhere__hermes-agent",
        "files": [{
            "path": "pkg/mod.py",
            "content": "x = 1\n",
            "language": "python",
            "is_test": False,
        }],
        "revision": "abc123def456",
        "mode": "replace",
        "removed_paths": [],
        "enable_w2": False,
        "metadata": {"instance_id": "urnowhere__hermes-agent"},
    }
