"""Configurable authentication for the Hermes web dashboard.

This module deliberately avoids FastAPI globals so it can be tested in
isolation and reused by both HTTP middleware and WebSocket handlers.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Mapping, MutableMapping, Optional


class DashboardAuthMode(str, Enum):
    NONE = "none"
    TOKEN = "token"  # nosec B105
    PASSWORD = "password"  # nosec B105
    TRUSTED_PROXY = "trusted-proxy"
    TAILSCALE = "tailscale"


@dataclass
class DashboardIdentity:
    user: str | None = None
    email: str | None = None
    name: str | None = None
    profile_pic: str | None = None
    source: str = "anonymous"


@dataclass
class DashboardAuthResult:
    ok: bool
    status_code: int = 401
    reason: str = "Unauthorized"
    identity: DashboardIdentity | None = None
    set_session_token: str | None = None


@dataclass
class _FailureBucket:
    failures: list[float]
    locked_until: float = 0.0


@dataclass
class _Session:
    identity: DashboardIdentity
    expires_at: float


_HEADER_DASHBOARD_TOKEN = "x-hermes-dashboard-token"  # nosec B105
_HEADER_DASHBOARD_SESSION = "x-hermes-dashboard-session"
_HEADER_LEGACY_SESSION = "x-hermes-session-token"


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _unb64(data: str) -> bytes:
    padding = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + padding)


def hash_dashboard_password(password: str, *, salt: str | None = None, iterations: int = 260_000) -> str:
    """Return a PBKDF2-SHA256 password hash suitable for config storage."""
    if salt is None:
        salt_bytes = secrets.token_bytes(16)
        salt_part = _b64(salt_bytes)
    else:
        salt_part = salt
        salt_bytes = salt.encode("utf-8")
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt_bytes, iterations)
    return f"pbkdf2_sha256${iterations}${salt_part}${_b64(digest)}"


def verify_dashboard_password(password: str, encoded_hash: str) -> bool:
    try:
        scheme, iter_s, salt, expected = encoded_hash.split("$", 3)
        if scheme != "pbkdf2_sha256":
            return False
        iterations = int(iter_s)
        password_bytes = password.encode("utf-8")

        # hash_dashboard_password() stores generated salts as URL-safe base64,
        # while tests/admin tooling may pass a deterministic plain-text salt.
        # Accept both encodings so generated hashes always verify and old
        # deterministic hashes remain valid.
        salt_candidates = [salt.encode("utf-8")]
        try:
            decoded = _unb64(salt)
        except Exception:
            decoded = b""
        if decoded and decoded not in salt_candidates:
            salt_candidates.append(decoded)

        for salt_bytes in salt_candidates:
            digest = hashlib.pbkdf2_hmac("sha256", password_bytes, salt_bytes, iterations)
            candidate = _b64(digest)
            if hmac.compare_digest(candidate.encode(), expected.encode()):
                return True
        return False
    except Exception:
        return False


def _nested_get(data: Mapping[str, Any], *keys: str, default: Any = None) -> Any:
    cur: Any = data
    for key in keys:
        if not isinstance(cur, Mapping):
            return default
        cur = cur.get(key)
    return default if cur is None else cur


def _headers(obj: Any) -> dict[str, str]:
    raw = getattr(obj, "headers", obj) or {}
    return {str(k).lower(): str(v) for k, v in dict(raw).items()}


def _client_host(obj: Any, fallback: str = "unknown") -> str:
    client = getattr(obj, "client", None)
    return str(getattr(client, "host", "") or fallback)


class DashboardAuthManager:
    """Authenticate dashboard HTTP and WebSocket requests for all auth modes."""

    def __init__(
        self,
        config: Mapping[str, Any] | None = None,
        *,
        env: Mapping[str, str] | None = None,
        clock: Callable[[], float] | None = None,
        runtime_token: str | None = None,
    ) -> None:
        self.config = config or {}
        self.env = env if env is not None else os.environ
        self.clock = clock or time.time
        self.runtime_token = runtime_token
        self._failures: dict[tuple[str, str], _FailureBucket] = {}
        self._sessions: dict[str, _Session] = {}

    @property
    def auth_config(self) -> Mapping[str, Any]:
        return _nested_get(self.config, "dashboard", "auth", default={}) or {}

    def mode(self) -> DashboardAuthMode:
        raw = self.env.get("HERMES_DASHBOARD_AUTH_MODE") or self.auth_config.get("mode") or "none"
        try:
            return DashboardAuthMode(str(raw).strip().lower())
        except ValueError:
            return DashboardAuthMode.NONE

    def status_payload(self, request: Any | None = None) -> dict[str, Any]:
        mode = self.mode().value
        result = self.authenticate_request(request) if request is not None else DashboardAuthResult(mode == "none")
        return {
            "mode": mode,
            "required": mode != "none",
            "authenticated": bool(result.ok),
            "supports_password_login": mode == "password",
            "supports_token_login": mode == "token",
            "identity": self._identity_payload(result.identity) if result.identity else None,
        }

    def authenticate_request(self, request: Any) -> DashboardAuthResult:
        mode = self.mode()
        if mode == DashboardAuthMode.NONE:
            return DashboardAuthResult(True, 200, "OK", DashboardIdentity(source="none"))

        client = _client_host(request)
        if self._is_locked(client, mode.value):
            return DashboardAuthResult(False, 429, "Too many failed authentication attempts")

        if mode == DashboardAuthMode.TOKEN:
            return self._authenticate_token(request, client)
        if mode == DashboardAuthMode.PASSWORD:
            return self._authenticate_session(request, client, source="password")
        if mode == DashboardAuthMode.TRUSTED_PROXY:
            return self._authenticate_trusted_proxy(request, client)
        if mode == DashboardAuthMode.TAILSCALE:
            return self._authenticate_tailscale(request, client)
        return DashboardAuthResult(False, 401, "Unauthorized")

    def authenticate_websocket(self, ws: Any) -> DashboardAuthResult:
        # Starlette WebSocket exposes headers and query_params; support both.
        result = self.authenticate_request(ws)
        if result.ok:
            return result
        mode = self.mode()
        if mode in {DashboardAuthMode.TOKEN, DashboardAuthMode.PASSWORD}:
            token = ""  # nosec B105
            query = getattr(ws, "query_params", {}) or {}
            try:
                token = str(query.get("token") or query.get("session") or "")
            except AttributeError:
                token = ""  # nosec B105
            if token:
                headers = {_HEADER_DASHBOARD_SESSION: token}
                if mode == DashboardAuthMode.TOKEN and self._token_matches(token):
                    self._reset_failures(_client_host(ws), "token")
                    return DashboardAuthResult(True, 200, "OK", DashboardIdentity(source="token"))
                return self._authenticate_session(headers, _client_host(ws), source="password")
        return result

    def login_password(self, password: str, *, client_id: str) -> DashboardAuthResult:
        if self.mode() != DashboardAuthMode.PASSWORD:
            return DashboardAuthResult(False, 400, "Password login is not enabled")
        if self._is_locked(client_id, "password"):
            return DashboardAuthResult(False, 429, "Too many failed authentication attempts")

        expected_hash = str(self.env.get("HERMES_DASHBOARD_PASSWORD_HASH") or self.auth_config.get("password_hash") or "")
        expected_plain = str(self.env.get("HERMES_DASHBOARD_PASSWORD") or self.auth_config.get("password") or "")
        ok = False
        if expected_hash:
            ok = verify_dashboard_password(password, expected_hash)
        elif expected_plain:
            ok = hmac.compare_digest(password.encode(), expected_plain.encode())

        if not ok:
            self._record_failure(client_id, "password")
            return DashboardAuthResult(False, 401, "Unauthorized")

        self._reset_failures(client_id, "password")
        identity = DashboardIdentity(source="password")
        session = self.issue_session(identity)
        return DashboardAuthResult(True, 200, "OK", identity, set_session_token=session)

    def issue_session(self, identity: DashboardIdentity | None) -> str:
        token = secrets.token_urlsafe(32)
        ttl = int(self.auth_config.get("session_ttl_seconds") or 8 * 60 * 60)
        self._sessions[token] = _Session(identity=identity or DashboardIdentity(source="session"), expires_at=self.clock() + ttl)
        return token

    def verify_session(self, token: str) -> DashboardIdentity | None:
        if not token:
            return None
        session = self._sessions.get(token)
        if not session:
            return None
        if session.expires_at < self.clock():
            self._sessions.pop(token, None)
            return None
        return session.identity

    def revoke_session(self, token: str) -> bool:
        return self._sessions.pop(token, None) is not None

    def failure_count(self, client_id: str, mode: str) -> int:
        bucket = self._failures.get((client_id, mode))
        if not bucket:
            return 0
        self._prune_bucket(bucket)
        return len(bucket.failures)

    def _authenticate_token(self, request: Any, client: str) -> DashboardAuthResult:
        token = self._extract_token(request) or self._extract_session(request)
        if not token:
            return DashboardAuthResult(False, 401, "Unauthorized")
        if self._token_matches(token):
            self._reset_failures(client, "token")
            return DashboardAuthResult(True, 200, "OK", DashboardIdentity(source="token"))
        identity = self.verify_session(token)
        if identity:
            self._reset_failures(client, "token")
            return DashboardAuthResult(True, 200, "OK", identity)
        self._record_failure(client, "token")
        return DashboardAuthResult(False, 401, "Unauthorized")

    def _authenticate_session(self, request: Any, client: str, *, source: str) -> DashboardAuthResult:
        token = self._extract_session(request)
        if not token:
            return DashboardAuthResult(False, 401, "Unauthorized")
        identity = self.verify_session(token)
        if identity:
            return DashboardAuthResult(True, 200, "OK", identity)
        self._record_failure(client, source)
        return DashboardAuthResult(False, 401, "Unauthorized")

    def _authenticate_trusted_proxy(self, request: Any, client: str) -> DashboardAuthResult:
        cfg = self.auth_config.get("trusted_proxy") or {}
        headers = _headers(request)
        user = headers.get(str(cfg.get("user_header") or "X-Forwarded-User").lower())
        email = headers.get(str(cfg.get("email_header") or "X-Forwarded-Email").lower())
        name = headers.get(str(cfg.get("name_header") or "X-Forwarded-Name").lower())
        require_identity = cfg.get("require_identity", True)
        if require_identity and not (user or email):
            return DashboardAuthResult(False, 401, "Unauthorized")
        if not self._allowed(user, cfg.get("allowed_users")) or not self._allowed(email, cfg.get("allowed_emails")):
            self._record_failure(client, "trusted-proxy")
            return DashboardAuthResult(False, 403, "Forbidden")
        return DashboardAuthResult(True, 200, "OK", DashboardIdentity(user=user, email=email, name=name, source="trusted-proxy"))

    def _authenticate_tailscale(self, request: Any, client: str) -> DashboardAuthResult:
        cfg = self.auth_config.get("tailscale") or {}
        headers = _headers(request)
        user = headers.get(str(cfg.get("user_header") or "Tailscale-User-Login").lower())
        name = headers.get(str(cfg.get("name_header") or "Tailscale-User-Name").lower())
        pic = headers.get(str(cfg.get("profile_pic_header") or "Tailscale-User-Profile-Pic").lower())
        if not user:
            return DashboardAuthResult(False, 401, "Unauthorized")
        if not self._allowed(user, cfg.get("allowed_users")):
            self._record_failure(client, "tailscale")
            return DashboardAuthResult(False, 403, "Forbidden")
        return DashboardAuthResult(True, 200, "OK", DashboardIdentity(user=user, name=name, profile_pic=pic, source="tailscale"))

    def _extract_token(self, request: Any) -> str:
        headers = _headers(request)
        auth = headers.get("authorization", "")
        if auth.lower().startswith("bearer "):
            return auth[7:].strip()
        return headers.get(_HEADER_DASHBOARD_TOKEN, "") or headers.get(_HEADER_LEGACY_SESSION, "")

    def _extract_session(self, request: Any) -> str:
        headers = _headers(request)
        return headers.get(_HEADER_DASHBOARD_SESSION, "") or headers.get(_HEADER_LEGACY_SESSION, "")

    def _token_matches(self, token: str) -> bool:
        expected = str(self.env.get("HERMES_DASHBOARD_TOKEN") or self.auth_config.get("token") or self.runtime_token or "")
        if not expected:
            return False
        return hmac.compare_digest(token.encode(), expected.encode())

    def _rate_cfg(self) -> Mapping[str, Any]:
        return self.auth_config.get("rate_limit") or {}

    def _prune_bucket(self, bucket: _FailureBucket) -> None:
        window = int(self._rate_cfg().get("window_seconds") or 60)
        cutoff = self.clock() - window
        bucket.failures[:] = [t for t in bucket.failures if t >= cutoff]

    def _is_locked(self, client: str, mode: str) -> bool:
        bucket = self._failures.get((client, mode))
        if not bucket:
            return False
        if bucket.locked_until and bucket.locked_until > self.clock():
            return True
        if bucket.locked_until and bucket.locked_until <= self.clock():
            bucket.locked_until = 0.0
            bucket.failures.clear()
        return False

    def _record_failure(self, client: str, mode: str) -> None:
        bucket = self._failures.setdefault((client, mode), _FailureBucket([]))
        self._prune_bucket(bucket)
        bucket.failures.append(self.clock())
        max_attempts = int(self._rate_cfg().get("max_attempts") or 10)
        if len(bucket.failures) >= max_attempts:
            lockout = int(self._rate_cfg().get("lockout_seconds") or 300)
            bucket.locked_until = self.clock() + lockout

    def _reset_failures(self, client: str, mode: str) -> None:
        self._failures.pop((client, mode), None)

    @staticmethod
    def _allowed(value: str | None, allowed: Any) -> bool:
        if not allowed:
            return True
        if value is None:
            return False
        return value in {str(v) for v in allowed}

    @staticmethod
    def _identity_payload(identity: DashboardIdentity) -> dict[str, Any]:
        return {
            "user": identity.user,
            "email": identity.email,
            "name": identity.name,
            "profile_pic": identity.profile_pic,
            "source": identity.source,
        }
