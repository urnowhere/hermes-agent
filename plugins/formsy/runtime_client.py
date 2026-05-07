"""HTTP client for FormalCC Runtime API."""

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
    CompileRequest,
    CompileResponse,
)
from .utils import generate_request_id

logger = logging.getLogger("formalcc.runtime_client")


class RuntimeClient:
    """Client for FormalCC Runtime API."""

    def __init__(
        self,
        base_url: str,
        api_key_env: str = "FORMALCC_API_KEY",
        timeout_s: int = 30,
        max_retries: int = 3,
    ):
        self.base_url = base_url.rstrip("/")
        self.timeout_s = timeout_s
        self.max_retries = max_retries
        self.auth_manager = AuthManager(api_key_env)
        self._client: Optional[httpx.AsyncClient] = None

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
                raise RuntimeAPIError("Authentication failed", status_code=401)
            elif response.status_code == 404:
                raise RuntimeAPIError("Endpoint not found", status_code=404)
            elif response.status_code == 503:
                raise RuntimeAPIError("Service unavailable", status_code=503)
            elif response.status_code >= 500:
                raise RuntimeAPIError(
                    f"Server error: {response.status_code}",
                    status_code=response.status_code,
                )
            elif response.status_code >= 400:
                raise RuntimeAPIError(
                    f"Client error: {response.status_code}",
                    status_code=response.status_code,
                    response_data=response.json() if response.content else None,
                )
            
            response.raise_for_status()
            return response.json() if response.content else {}
        
        except httpx.TimeoutException as e:
            logger.warning(f"Request timeout: {url}")
            raise FormalCCTimeoutError(f"Request timed out after {self.timeout_s}s") from e
        except httpx.HTTPError as e:
            logger.error(f"HTTP error: {e}")
            raise RuntimeAPIError(f"HTTP error: {e}") from e
    
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
    
    async def compile(self, request: CompileRequest) -> CompileResponse:
        """Call compile endpoint."""
        logger.debug(f"Compile: session={request.session_id}, scene={request.scene}")
        
        response_data = await self._request(
            "POST",
            "/v1/runtime/compile",
            data=request.model_dump(mode="json"),
            session_id=request.session_id,
        )
        if "bundle" in response_data:
            return CompileResponse(bundle=response_data["bundle"])
        return CompileResponse(bundle=response_data)
    
    async def memory_search(
        self, workspace_id: str, session_id: str, query: str, top_k: int = 5, limit: Optional[int] = None
    ) -> dict[str, Any]:
        """Call memory search endpoint (for tool calls)."""
        logger.debug(f"Memory search: query={query[:50]}...")
        effective_top_k = limit if limit is not None else top_k
        
        return await self._request(
            "POST",
            "/v1/runtime/memory/search",
            data={
                "workspace_id": workspace_id,
                "session_id": session_id,
                "query": query,
                "top_k": effective_top_k,
            },
            session_id=session_id,
        )
