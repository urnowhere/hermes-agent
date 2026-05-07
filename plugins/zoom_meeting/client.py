"""Zoom REST client primitives for the zoom_meeting plugin."""

from __future__ import annotations

import base64
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional

import requests


class ZoomClientError(RuntimeError):
    """Raised when Zoom auth or REST calls fail."""


@dataclass
class ZoomCredentials:
    account_id: str
    client_id: str
    client_secret: str


class ZoomClient:
    """Minimal server-to-server OAuth client for Zoom meeting metadata."""

    def __init__(
        self,
        credentials: ZoomCredentials,
        *,
        api_base_url: str = "https://api.zoom.us/v2",
        oauth_base_url: str = "https://zoom.us",
        timeout: int = 20,
        session: Optional[requests.Session] = None,
    ):
        self.credentials = credentials
        self.api_base_url = api_base_url.rstrip("/")
        self.oauth_base_url = oauth_base_url.rstrip("/")
        self.timeout = timeout
        self.session = session or requests.Session()
        self._access_token = ""
        self._expires_at = 0.0

    def _basic_auth_header(self) -> str:
        raw = f"{self.credentials.client_id}:{self.credentials.client_secret}".encode("utf-8")
        return "Basic " + base64.b64encode(raw).decode("ascii")

    def get_access_token(self, *, force_refresh: bool = False) -> str:
        if not force_refresh and self._access_token and time.time() < self._expires_at - 60:
            return self._access_token

        params = {
            "grant_type": "account_credentials",
            "account_id": self.credentials.account_id,
        }
        try:
            resp = self.session.post(
                f"{self.oauth_base_url}/oauth/token",
                params=params,
                headers={"Authorization": self._basic_auth_header()},
                timeout=self.timeout,
            )
            resp.raise_for_status()
            data = resp.json() or {}
        except Exception as exc:  # pragma: no cover - network failure branch
            raise ZoomClientError(f"Zoom OAuth token request failed: {exc}") from exc

        token = str(data.get("access_token") or "")
        if not token:
            raise ZoomClientError(f"Zoom OAuth response missing access_token: {data}")
        expires_in = int(data.get("expires_in") or 3600)
        self._access_token = token
        self._expires_at = time.time() + max(60, expires_in)
        return token

    def request(self, method: str, path: str, **kwargs) -> Dict[str, Any]:
        token = self.get_access_token()
        headers = dict(kwargs.pop("headers", {}) or {})
        headers.setdefault("Authorization", f"Bearer {token}")
        headers.setdefault("Content-Type", "application/json")
        try:
            resp = self.session.request(
                method.upper(),
                f"{self.api_base_url}/{path.lstrip('/')}",
                headers=headers,
                timeout=kwargs.pop("timeout", self.timeout),
                **kwargs,
            )
            resp.raise_for_status()
        except Exception as exc:  # pragma: no cover - network failure branch
            raise ZoomClientError(f"Zoom API {method.upper()} {path} failed: {exc}") from exc

        if not resp.content:
            return {}
        return resp.json() or {}

    def fetch_meeting(self, meeting_id: str) -> Dict[str, Any]:
        return self.request("GET", f"meetings/{meeting_id}")

    def fetch_meeting_recordings(self, meeting_id: str) -> Dict[str, Any]:
        return self.request("GET", f"meetings/{meeting_id}/recordings")

