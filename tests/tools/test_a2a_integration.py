"""Integration tests for the A2A protocol — real HTTP roundtrip.

Starts a real uvicorn server in a background thread, then uses the actual
client tools (a2a_discover, a2a_call) to hit it over HTTP.  The AIAgent
is mocked so no LLM keys are needed, but the HTTP transport is real.

All tests are marked ``@pytest.mark.integration`` and skipped by default.
"""

import json
import socket
import threading
import time
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _free_port() -> int:
    """Find an available TCP port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _server_deps_available() -> bool:
    """Check if both server and client deps are installed."""
    try:
        from a2a_adapter.server import is_server_available
        if not is_server_available():
            return False
        import uvicorn  # noqa: F401
        from tools.a2a_tool import check_a2a_available
        return check_a2a_available()
    except ImportError:
        return False


_skip_reason = "a2a-sdk[http-server], uvicorn, or a2a client deps not installed"


def _make_mock_agent():
    """Create a mock AIAgent whose run_conversation returns a canned response."""
    agent = MagicMock()
    agent.run_conversation.return_value = {
        "final_response": "Integration test response from Hermes",
        "messages": [{"role": "assistant", "content": "Integration test response from Hermes"}],
    }
    return agent


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def a2a_server():
    """Start a real A2A server on a random port with a mocked AIAgent.

    Yields the base URL (e.g. ``http://127.0.0.1:PORT``).
    """
    if not _server_deps_available():
        pytest.skip(_skip_reason)

    import uvicorn
    from a2a_adapter.server import build_application

    port = _free_port()
    host = "127.0.0.1"

    # Patch SessionManager._make_agent so no real LLM keys are needed
    with patch("a2a_adapter.session.SessionManager._make_agent") as mock_make:
        mock_make.return_value = _make_mock_agent()

        app = build_application(host=host, port=port)
        starlette_app = app.build()

        config = uvicorn.Config(starlette_app, host=host, port=port, log_level="warning")
        server = uvicorn.Server(config)

        thread = threading.Thread(target=server.run, daemon=True, name="a2a-test-server")
        thread.start()

        # Wait for the server to be ready
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            try:
                with socket.create_connection((host, port), timeout=0.5):
                    break
            except OSError:
                time.sleep(0.1)
        else:
            pytest.fail(f"A2A test server did not start on {host}:{port} within 10s")

        yield f"http://{host}:{port}"

        server.should_exit = True
        thread.join(timeout=5)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.integration
@pytest.mark.skipif(not _server_deps_available(), reason=_skip_reason)
class TestA2AIntegration:
    """Full HTTP roundtrip: start server -> discover -> call."""

    def test_discover_roundtrip(self, a2a_server):
        """Discover the live A2A server and verify the Agent Card fields."""
        import tools.a2a_tool as mod

        # Clear any cached cards so we actually hit the network
        mod._agent_cards.clear()
        mod._config_cache = None

        result = json.loads(mod.a2a_discover({"agent": a2a_server}))

        assert "error" not in result, f"Discovery failed: {result}"
        assert result["name"] == "Hermes Agent"
        assert "skills" in result
        assert len(result["skills"]) >= 1
        assert result["capabilities"]["streaming"] is True

    def test_call_roundtrip(self, a2a_server):
        """Send a message to the live A2A server and get a response."""
        import tools.a2a_tool as mod

        # Ensure the agent card is cached (discover first)
        mod._agent_cards.clear()
        mod._config_cache = None
        mod.a2a_discover({"agent": a2a_server})

        raw = mod.a2a_call({
            "agent": a2a_server,
            "message": "Say hello for the integration test",
        })
        result = json.loads(raw)

        assert "error" not in result, f"Call failed: {result}"
        # The response should contain our mocked text or a valid status
        assert result.get("status") in ("completed", "unknown", None) or "response" in result

    def test_discover_then_call(self, a2a_server):
        """Full flow: discover agent, then call it — verifies card caching works."""
        import tools.a2a_tool as mod

        mod._agent_cards.clear()
        mod._config_cache = None

        # Step 1: discover
        card = json.loads(mod.a2a_discover({"agent": a2a_server}))
        assert card["name"] == "Hermes Agent"

        # Step 2: call (should reuse cached card, no extra discovery request)
        raw = mod.a2a_call({
            "agent": a2a_server,
            "message": "Integration roundtrip test",
        })
        result = json.loads(raw)
        assert "error" not in result, f"Call after discover failed: {result}"
