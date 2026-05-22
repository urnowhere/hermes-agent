"""HTTP client for FormalCC Runtime API."""

import json
import logging
from typing import Optional, Any
import httpx

from .auth import AuthManager
from .errors import RuntimeAPIError, TimeoutError as FormalCCTimeoutError
from .models import (
    MemoryPrefetchRequest,
    MemoryPrefetchResponse,
    MemorySyncTurnRequest,
    SessionEndRequest,
)
from .utils import generate_request_id

logger = logging.getLogger("formalcc.runtime_client")
_LOG_BODY_LIMIT = 4000


class RuntimeClient:
    """Client for FormalCC Runtime API."""

    def __init__(
        self,
        base_url: str,
        memory_search_endpoint: str = "/api/v1/query",
        api_key_env: str = "FORMSY_API_KEY",
        api_key: str = "",
        timeout_s: int = 30,
        max_retries: int = 3,
    ):
        self.base_url = base_url.rstrip("/")
        self.memory_search_endpoint = self._normalize_endpoint(memory_search_endpoint)
        self.timeout_s = timeout_s
        self.max_retries = max_retries
        self.auth_manager = AuthManager(api_key_env, api_key=api_key)
        self._client: Optional[httpx.AsyncClient] = None

    @staticmethod
    def _normalize_endpoint(endpoint: str) -> str:
        endpoint = str(endpoint or "").strip()
        if not endpoint:
            return "/api/v1/query"
        return endpoint if endpoint.startswith("/") else f"/{endpoint}"

    async def __aenter__(self):
        """Async context manager entry."""
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(self.timeout_s),
            follow_redirects=True,
        )
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit."""
        if self._client:
            await self._client.aclose()

    def _get_headers(self, session_id: Optional[str] = None) -> dict[str, str]:
        """Get request headers."""
        headers = self.auth_manager.get_auth_headers()
        headers["X-Request-ID"] = generate_request_id()
        if session_id:
            headers["X-Session-ID"] = session_id
        return headers
    
    async def _request(
        self,
        method: str,
        endpoint: str,
        data: Optional[dict[str, Any]] = None,
        session_id: Optional[str] = None,
    ) -> dict[str, Any]:
        """Make HTTP request to Runtime API."""
        if not self._client:
            raise RuntimeError("Client not initialized. Use async context manager.")

        # Lazy auth verification: call /v1/auth/verify once on first real request,
        # skipping the verify endpoint itself to avoid infinite recursion.
        if endpoint != "/v1/auth/verify":
            await self.auth_manager.verify(self._client, self.base_url)

        url = f"{self.base_url}{endpoint}"
        headers = self._get_headers(session_id)
        
        try:
            response = await self._client.request(
                method=method,
                url=url,
                json=data,
                headers=headers,
            )
            
            # Handle different status codes
            if response.status_code == 401:
                self._log_http_error(method, url, headers, data, response=response)
                raise RuntimeAPIError("Authentication failed", status_code=401)
            elif response.status_code == 404:
                self._log_http_error(method, url, headers, data, response=response)
                raise RuntimeAPIError("Endpoint not found", status_code=404)
            elif response.status_code == 503:
                self._log_http_error(method, url, headers, data, response=response)
                raise RuntimeAPIError("Service unavailable", status_code=503)
            elif response.status_code >= 500:
                self._log_http_error(method, url, headers, data, response=response)
                raise RuntimeAPIError(
                    f"Server error: {response.status_code}",
                    status_code=response.status_code,
                    response_data=self._response_body_for_error(response),
                )
            elif response.status_code >= 400:
                response_data = self._response_body_for_error(response)
                self._log_http_error(method, url, headers, data, response=response)
                raise RuntimeAPIError(
                    f"Client error: {response.status_code}",
                    status_code=response.status_code,
                    response_data=response_data,
                )
            
            response.raise_for_status()
            return response.json() if response.content else {}
        
        except httpx.TimeoutException as e:
            self._log_http_error(method, url, headers, data, error=e)
            raise FormalCCTimeoutError(f"Request timed out after {self.timeout_s}s") from e
        except httpx.HTTPError as e:
            self._log_http_error(method, url, headers, data, error=e)
            raise RuntimeAPIError(f"HTTP error: {e}") from e

    @staticmethod
    def _redact_headers(headers: dict[str, str]) -> dict[str, str]:
        redacted = {}
        for key, value in (headers or {}).items():
            lowered = key.lower()
            if lowered in {"authorization", "x-api-key", "api-key"} or "token" in lowered or "secret" in lowered:
                if lowered == "authorization" and str(value).lower().startswith("bearer "):
                    redacted[key] = "Bearer ***"
                else:
                    redacted[key] = "***"
            else:
                redacted[key] = str(value)
        return redacted

    @staticmethod
    def _truncate(value: str, limit: int = _LOG_BODY_LIMIT) -> str:
        if len(value) <= limit:
            return value
        return f"{value[:limit]}...<truncated {len(value) - limit} chars>"

    @classmethod
    def _json_for_log(cls, value: Any) -> str:
        if value is None:
            return "null"
        try:
            return cls._truncate(json.dumps(value, ensure_ascii=False, sort_keys=True))
        except (TypeError, ValueError):
            return cls._truncate(str(value))

    @classmethod
    def _response_body_for_error(cls, response: httpx.Response) -> Any:
        if not response.content:
            return None
        try:
            return response.json()
        except ValueError:
            return cls._truncate(response.text)

    @classmethod
    def _response_text_for_log(cls, response: httpx.Response) -> str:
        if not response.content:
            return ""
        try:
            return cls._json_for_log(response.json())
        except ValueError:
            return cls._truncate(response.text)

    def _log_http_error(
        self,
        method: str,
        url: str,
        headers: dict[str, str],
        request_body: Optional[dict[str, Any]],
        *,
        response: Optional[httpx.Response] = None,
        error: Optional[BaseException] = None,
    ) -> None:
        logger.error(
            "Runtime API request failed: %s %s status_code=%s error=%s "
            "request_headers=%s request_body=%s response_body=%s",
            method,
            url,
            response.status_code if response is not None else None,
            repr(error) if error is not None else None,
            self._json_for_log(self._redact_headers(headers)),
            self._json_for_log(request_body),
            self._response_text_for_log(response) if response is not None else "",
        )
    
    async def memory_prefetch(
        self, request: MemoryPrefetchRequest
    ) -> MemoryPrefetchResponse:
        """Call memory prefetch endpoint."""
        logger.debug(f"Memory prefetch: session={request.session_id}, turn={request.turn_id}")
        
        response_data = await self._request(
            "POST",
            "/v1/runtime/memory_prefetch",
            data=request.model_dump(mode="json"),
            session_id=request.session_id,
        )
        
        return MemoryPrefetchResponse(**response_data)
    
    async def memory_sync_turn(self, request: MemorySyncTurnRequest) -> None:
        """Call memory sync turn endpoint (non-blocking)."""
        logger.debug(f"Memory sync turn: session={request.session_id}, turn={request.turn_id}")
        
        await self._request(
            "POST",
            "/v1/runtime/memory_sync_turn",
            data=request.model_dump(mode="json"),
            session_id=request.session_id,
        )
    
    async def session_end(self, request: SessionEndRequest) -> None:
        """Call session end endpoint."""
        logger.debug(f"Session end: session={request.session_id}")
        
        await self._request(
            "POST",
            "/v1/runtime/session_end",
            data=request.model_dump(mode="json"),
            session_id=request.session_id,
        )
    
    async def compile_repo(
        self,
        repo_id: str,
        files: list[dict[str, Any]],
        revision: str | None = None,
        metadata: Optional[dict[str, Any]] = None,
        mode: str = "replace",
        removed_paths: Optional[list[str]] = None,
        enable_w2: bool = False,
        session_id: Optional[str] = None,
    ) -> dict[str, Any]:
        """Compile repository source for memory search/read endpoints."""
        logger.debug(f"Compile repo: repo_id={repo_id}, files={len(files)}")

        return await self._request(
            "POST",
            "/api/v1/compile",
            data={
                "repo_id": repo_id,
                "files": files,
                "revision": revision,
                "mode": mode,
                "removed_paths": removed_paths or [],
                "enable_w2": enable_w2,
                "metadata": metadata or {},
            },
            session_id=session_id,
        )
    
    async def memory_search(
        self,
        repo_id: str,
        session_id: str,
        query: str,
        revision: str = "latest",
        budget: int = 4000,
        enable_profiling: bool = False,
        profiling_top_n: int = 20,
        metadata: Optional[dict[str, Any]] = None,
        identity: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """Call memory search endpoint (for tool calls)."""
        logger.debug(f"Memory search: query={query[:50]}...")
        request_body = {
            "repo_id": repo_id,
            "query": query,
            "revision": revision,
            "budget": budget,
            "enable_profiling": enable_profiling,
            "profiling_top_n": profiling_top_n,
            "metadata": metadata or {"instance_id": repo_id},
        }
        if identity:
            request_body["identity"] = identity
        
        return await self._request(
            "POST",
            self.memory_search_endpoint,
            data=request_body,
            session_id=session_id,
        )

    async def memory_read(
        self,
        repo_id: str,
        session_id: str,
        path: str,
        revision: str = "latest",
        start_line: int | None = None,
        end_line: int | None = None,
        identity: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """Call repository source read endpoint (for tool calls)."""
        logger.debug(f"Memory read: path={path}...")
        request_body: dict[str, Any] = {
            "repo_id": repo_id,
            "revision": revision,
            "path": path,
        }
        if start_line is not None:
            request_body["start_line"] = start_line
        if end_line is not None:
            request_body["end_line"] = end_line
        if identity:
            request_body["identity"] = identity

        return await self._request(
            "POST",
            "/api/v1/read",
            data=request_body,
            session_id=session_id,
        )
