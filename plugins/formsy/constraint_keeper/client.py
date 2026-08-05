"""HTTP client for FormSy Runtime Constraint Keeper APIs."""

from __future__ import annotations

from typing import Any

import httpx

from plugins.formsy.auth import AuthManager
from plugins.formsy.errors import RuntimeAPIError, TimeoutError as FormSyTimeoutError


class ConstraintKeeperClient:
    """Small client for `/v1/runtime/constraints/*` endpoints.

    This stays separate from the memory RuntimeClient because Constraint Keeper
    calls have different payload sensitivity, timeout, and fail-closed behavior.
    """

    def __init__(
        self,
        *,
        base_url: str,
        api_key_env: str = "FORMSY_API_KEY",
        api_key: str = "",
        timeout_s: float = 30.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_s = timeout_s
        self.auth_manager = AuthManager(api_key_env=api_key_env, api_key=api_key)
        self._client: httpx.AsyncClient | Any | None = None

    async def __aenter__(self) -> "ConstraintKeeperClient":
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(self.timeout_s),
            follow_redirects=True,
        )
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if self._client is not None:
            await self._client.aclose()
        self._client = None

    async def task_start(
        self,
        *,
        task: dict[str, Any],
        workspace: dict[str, Any],
        session_id: str = "",
    ) -> dict[str, Any]:
        return await self._request(
            "POST",
            "/v1/runtime/constraints/task_start",
            data={"task": task, "workspace": workspace},
            session_id=session_id,
        )

    async def compile_constraints(
        self,
        payload: dict[str, Any],
        *,
        session_id: str = "",
    ) -> dict[str, Any]:
        return await self._request(
            "POST",
            "/v1/runtime/constraints/compile",
            data=payload,
            session_id=session_id,
        )

    async def observe(
        self,
        payload: dict[str, Any],
        *,
        session_id: str = "",
    ) -> dict[str, Any]:
        return await self._request(
            "POST",
            "/v1/runtime/constraints/observe",
            data=payload,
            session_id=session_id,
        )

    async def recover(
        self,
        payload: dict[str, Any],
        *,
        session_id: str = "",
    ) -> dict[str, Any]:
        return await self._request(
            "POST",
            "/v1/runtime/constraints/recover",
            data=payload,
            session_id=session_id,
        )

    async def verify_completion(
        self,
        payload: dict[str, Any],
        *,
        session_id: str = "",
    ) -> dict[str, Any]:
        return await self._request(
            "POST",
            "/v1/runtime/constraints/verify_completion",
            data=payload,
            session_id=session_id,
        )

    async def request_human_review(
        self,
        payload: dict[str, Any],
        *,
        session_id: str = "",
    ) -> dict[str, Any]:
        return await self._request(
            "POST",
            "/v1/runtime/constraints/human_review",
            data=payload,
            session_id=session_id,
        )

    async def status(
        self,
        task_id: str,
        run_id: str,
        *,
        session_id: str = "",
    ) -> dict[str, Any]:
        return await self._request(
            "GET",
            f"/v1/runtime/constraints/status/{task_id}/{run_id}",
            data=None,
            session_id=session_id,
        )

    async def _request(
        self,
        method: str,
        endpoint: str,
        *,
        data: dict[str, Any] | None,
        session_id: str = "",
    ) -> dict[str, Any]:
        if self._client is None:
            raise RuntimeError("ConstraintKeeperClient is not initialized")

        url = f"{self.base_url}{endpoint}"
        headers = self.auth_manager.get_auth_headers()
        if session_id:
            headers["X-Session-ID"] = session_id

        try:
            response = await self._client.request(
                method,
                url,
                json=data,
                headers=headers,
            )
        except httpx.TimeoutException as exc:
            raise FormSyTimeoutError(f"Request timed out after {self.timeout_s}s") from exc
        except httpx.HTTPError as exc:
            raise RuntimeAPIError(f"HTTP error: {exc}") from exc

        if response.status_code >= 400:
            raise RuntimeAPIError(
                f"Constraint Keeper API error: {response.status_code}",
                status_code=response.status_code,
                response_data=self._response_data(response),
            )
        return self._response_data(response) or {}

    @staticmethod
    def _response_data(response: httpx.Response) -> Any:
        if not response.content:
            return {}
        try:
            return response.json()
        except ValueError:
            return {"text": response.text}
