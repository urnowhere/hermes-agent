from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from urllib.parse import urlparse


DOCKER_TOKEN = "docker-token"  # nosec B105
DOCKER_PASSWORD = "docker-password"  # nosec B105


def require(condition: bool, detail: object) -> None:
    if not condition:
        raise RuntimeError(str(detail))


def request(method: str, url: str, body: dict | None = None, headers: dict | None = None) -> tuple[int, dict]:
    parsed = urlparse(url)
    if parsed.scheme != "http" or not parsed.netloc:
        raise ValueError(f"unexpected smoke-test URL: {url}")
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method=method, headers=headers or {})
    if body is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=5) as res:  # nosec B310
            return res.status, json.loads(res.read().decode() or "{}")
    except urllib.error.HTTPError as exc:
        try:
            payload = json.loads(exc.read().decode() or "{}")
        except Exception:
            payload = {"detail": str(exc)}
        return exc.code, payload


def wait_status(url: str) -> dict:
    last_error: object = None
    for _ in range(60):
        try:
            code, data = request("GET", url)
            if code == 200:
                return data
            last_error = (code, data)
        except urllib.error.URLError as exc:
            last_error = exc
        time.sleep(1)
    raise RuntimeError(f"not healthy: {url}: {last_error}")


def assert_status_auth(url: str, mode: str, required: bool):
    data = wait_status(f"{url}/api/auth/status")
    require(data["mode"] == mode, data)
    require(data["required"] is required, data)


def main() -> None:
    assert_status_auth("http://hermes-none:9119", "none", False)
    code, _ = request("GET", "http://hermes-none:9119/api/sessions")
    require(code == 200, code)

    code, token_status = request("GET", "http://hermes-token:9119/api/auth/status")
    require(code == 200 and token_status["mode"] == "token" and token_status["required"] is True, (code, token_status))
    code, _ = request("GET", "http://hermes-token:9119/api/sessions")
    require(code == 401, code)
    code, bad_login = request("POST", "http://hermes-token:9119/api/auth/login", {"token": "wrong-token"})
    require(code == 401 and not bad_login.get("session_token"), (code, bad_login))
    code, login = request("POST", "http://hermes-token:9119/api/auth/login", {"token": DOCKER_TOKEN})
    require(code == 200 and bool(login.get("session_token")), (code, login))
    code, _ = request("GET", "http://hermes-token:9119/api/sessions", headers={"X-Hermes-Dashboard-Session": login["session_token"]})
    require(code == 200, code)
    code, _ = request("POST", "http://hermes-token:9119/api/auth/logout", headers={"X-Hermes-Dashboard-Session": login["session_token"]})
    require(code == 200, code)
    code, _ = request("GET", "http://hermes-token:9119/api/sessions", headers={"X-Hermes-Dashboard-Session": login["session_token"]})
    require(code == 401, code)

    code, password_status = request("GET", "http://hermes-password:9119/api/auth/status")
    require(code == 200 and password_status["mode"] == "password" and password_status["required"] is True, (code, password_status))
    code, bad_login = request("POST", "http://hermes-password:9119/api/auth/login", {"password": "wrong-password"})
    require(code == 401 and not bad_login.get("session_token"), (code, bad_login))
    code, login = request("POST", "http://hermes-password:9119/api/auth/login", {"password": DOCKER_PASSWORD})
    require(code == 200 and bool(login.get("session_token")), (code, login))

    code, direct_proxy_status = request("GET", "http://hermes-trusted-proxy:9119/api/auth/status")
    require(code == 200 and direct_proxy_status["authenticated"] is False, (code, direct_proxy_status))
    code, status = request("GET", "http://trusted-proxy:8080/api/auth/status")
    require(code == 200 and status["authenticated"] is True and status["identity"]["user"] == "docker-user", (code, status))

    code, direct_tailscale_status = request("GET", "http://hermes-tailscale:9119/api/auth/status")
    require(code == 200 and direct_tailscale_status["authenticated"] is False, (code, direct_tailscale_status))
    code, status = request("GET", "http://tailscale-mock:8080/api/auth/status")
    require(code == 200 and status["authenticated"] is True and status["identity"]["user"] == "docker-user@example.test", (code, status))
    print("dashboard auth docker smoke passed")


if __name__ == "__main__":
    main()
