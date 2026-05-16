"""Authentication utilities for FormalCC Runtime API."""

import logging
import os
from typing import TYPE_CHECKING, Optional

from .errors import AuthenticationError

if TYPE_CHECKING:
    import httpx

logger = logging.getLogger("formsy.auth")


class AuthManager:
    """Manages authentication for Runtime API."""

    def __init__(self, api_key_env: str = "FORMALCC_API_KEY", api_key: str = ""):
        self.api_key_env = api_key_env
        # A directly-supplied key takes precedence and skips format validation,
        # since local servers may use key formats that differ from cloud keys.
        self._api_key: Optional[str] = api_key.strip() or None
        self._verified: bool = False

    def get_api_key(self) -> str:
        """Get API key from environment or direct config."""
        if self._api_key:
            return self._api_key

        api_key = os.environ.get(self.api_key_env)
        if not api_key:
            return ""

        # Validate key format only for env-sourced keys (cloud key format)
        if not (api_key.startswith("fsy_live_") or api_key.startswith("fsy_test_")):
            raise AuthenticationError(
                "Invalid API key format. Expected 'fsy_live_*' or 'fsy_test_*'"
            )

        self._api_key = api_key
        return self._api_key

    async def verify(self, client: "httpx.AsyncClient", base_url: str) -> None:
        """Verify the API key against /v1/auth/verify (lazy, called once on first use).

        Raises AuthenticationError if the server rejects the key.
        Does nothing if already verified or if no key is configured.
        """
        if self._verified:
            return

        api_key = self.get_api_key()
        if not api_key:
            return

        url = f"{base_url.rstrip('/')}/v1/auth/verify"
        try:
            response = await client.post(
                url,
                json={"api_key": api_key},
                headers={"Content-Type": "application/json"},
            )
        except Exception as exc:
            # Network errors during verify are non-fatal — log and continue.
            logger.warning("Auth verify request failed (non-fatal): %s", exc)
            return

        if response.status_code == 200:
            data = response.json() if response.content else {}
            key_name = data.get("key_name", "")
            logger.debug("API key verified: %s", key_name or "ok")
            self._verified = True
            return

        if response.status_code == 401:
            try:
                detail = response.json().get("detail", {})
                error_code = detail.get("error", "unknown")
                message = detail.get("message", "Authentication failed")
            except Exception:
                error_code = "unknown"
                message = "Authentication failed"
            raise AuthenticationError(
                f"API key rejected by server ({error_code}): {message}"
            )

        # Any other non-2xx: log and continue rather than hard-failing.
        logger.warning(
            "Auth verify returned unexpected status %s — continuing without verification",
            response.status_code,
        )

    def get_auth_headers(self) -> dict[str, str]:
        """Get authentication headers."""
        headers: dict[str, str] = {"Content-Type": "application/json"}
        api_key = self.get_api_key()
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        return headers
