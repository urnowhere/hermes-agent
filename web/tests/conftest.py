"""
Pytest configuration for web/tests directory.
"""
import os
import sys
import pytest

# Add project root to Python path for imports
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


@pytest.fixture(autouse=True)
def isolated_env(tmp_path, monkeypatch):
    """Isolate tests by setting a temporary HERMES_HOME."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / ".hermes"))
    yield


@pytest.fixture
def session_token():
    """Return the session token from web_server."""
    from hermes_cli.web_server import _SESSION_TOKEN
    return _SESSION_TOKEN


@pytest.fixture
def auth_headers(session_token: str) -> dict:
    """Return authentication headers."""
    return {"Authorization": f"Bearer {session_token}"}


@pytest.fixture
def make_request(session_token: str):
    """Make HTTP requests to the test server."""
    from starlette.testclient import TestClient
    from hermes_cli.web_server import app

    def _request(method: str, path: str, headers: dict = None, json: dict = None):
        with TestClient(app) as client:
            if headers is None:
                headers = {"Authorization": f"Bearer {session_token}"}
            if json:
                return client.request(method, path, json=json, headers=headers)
            return client.request(method, path, headers=headers)

    return _request
