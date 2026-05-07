"""Tests for Formsy RuntimeClient diagnostics."""

import logging

import httpx
import pytest

from plugins.formsy.errors import RuntimeAPIError
from plugins.formsy.runtime_client import RuntimeClient


@pytest.mark.asyncio
async def test_runtime_client_logs_http_error_context(caplog, monkeypatch):
    monkeypatch.setenv("FORMALCC_API_KEY", "fsy_test_secret_token")
    client = RuntimeClient(base_url="https://runtime.example", api_key_env="FORMALCC_API_KEY")

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
