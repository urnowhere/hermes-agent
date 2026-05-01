"""
Pytest configuration for E2E tests with Playwright.
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


@pytest.fixture(scope="session")
def base_url():
    """Get base URL for E2E tests."""
    return os.getenv("HERMES_E2E_BASE_URL", "http://localhost:9119")


@pytest.fixture(scope="session")
def browser_args():
    """Configure browser launch arguments for E2E tests."""
    return [
        "--no-sandbox",
        "--disable-setuid-sandbox",
        "--disable-dev-shm-usage",
        "--disable-gpu",
    ]
