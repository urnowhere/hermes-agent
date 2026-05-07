"""Authentication utilities for FormalCC Runtime API."""

import os
from typing import Optional
from .errors import AuthenticationError


class AuthManager:
    """Manages authentication for Runtime API."""

    def __init__(self, api_key_env: str = "FORMALCC_API_KEY"):
        self.api_key_env = api_key_env
        self._api_key: Optional[str] = None

    def get_api_key(self) -> str:
        """Get API key from environment."""
        if self._api_key:
            return self._api_key

        api_key = os.environ.get(self.api_key_env)
        if not api_key:
            raise AuthenticationError(
                f"API key not found in environment variable: {self.api_key_env}"
            )

        # Validate key format
        if not (api_key.startswith("fsy_live_") or api_key.startswith("fsy_test_")):
            raise AuthenticationError(
                "Invalid API key format. Expected 'fsy_live_*' or 'fsy_test_*'"
            )

        self._api_key = api_key
        return self._api_key

    def get_auth_headers(self) -> dict[str, str]:
        """Get authentication headers."""
        return {
            "Authorization": f"Bearer {self.get_api_key()}",
            "Content-Type": "application/json",
        }
