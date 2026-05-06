"""Unit + integration tests for ObscuraProvider.

Hermetic by default (subprocess.Popen and requests.get are mocked). One
opt-in integration test runs against the real obscura binary when
``OBSCURA_BINARY_PATH`` (or ``obscura`` on PATH) resolves AND
``OBSCURA_TEST_REAL=1`` is set in the environment. CI defaults skip the
real-binary path so tests stay fast and offline.
"""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest

from tools.browser_providers.obscura import (
    ObscuraProvider,
    _allocate_ephemeral_port,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mocked_cdp_response(port: int) -> MagicMock:
    """Build a fake requests.Response for /json/version that the provider accepts."""
    resp = MagicMock()
    resp.ok = True
    resp.json.return_value = {
        "Browser": "Obscura/0.1.0",
        "Protocol-Version": "1.3",
        "webSocketDebuggerUrl": f"ws://127.0.0.1:{port}/devtools/browser",
    }
    return resp


# ---------------------------------------------------------------------------
# is_configured
# ---------------------------------------------------------------------------


class TestIsConfigured:
    def test_returns_false_when_binary_absent(self):
        with patch("tools.browser_providers.obscura.shutil.which", return_value=None):
            prov = ObscuraProvider()
            assert prov.is_configured() is False

    def test_returns_true_when_binary_on_path(self):
        with patch(
            "tools.browser_providers.obscura.shutil.which",
            return_value="/usr/local/bin/obscura",
        ):
            prov = ObscuraProvider()
            assert prov.is_configured() is True

    def test_uses_OBSCURA_BINARY_PATH_env(self, monkeypatch):
        monkeypatch.setenv("OBSCURA_BINARY_PATH", "/opt/custom/obscura")
        with patch(
            "tools.browser_providers.obscura.shutil.which",
            return_value="/opt/custom/obscura",
        ) as which:
            prov = ObscuraProvider()
            assert prov.is_configured() is True
            which.assert_called_with("/opt/custom/obscura")


# ---------------------------------------------------------------------------
# Helper: ephemeral port allocation
# ---------------------------------------------------------------------------


class TestAllocateEphemeralPort:
    def test_returns_a_port_in_valid_range(self):
        port = _allocate_ephemeral_port()
        assert isinstance(port, int)
        assert 1024 <= port <= 65535


# ---------------------------------------------------------------------------
# create_session (mocked subprocess + CDP)
# ---------------------------------------------------------------------------


class TestCreateSession:
    def test_happy_path_mocked(self, monkeypatch):
        monkeypatch.setenv("OBSCURA_BINARY_PATH", "obscura")
        monkeypatch.setenv("OBSCURA_PORT", "59999")  # deterministic for assertion

        fake_proc = MagicMock()
        fake_proc.pid = 12345

        with (
            patch(
                "tools.browser_providers.obscura.shutil.which",
                return_value="/usr/local/bin/obscura",
            ),
            patch(
                "tools.browser_providers.obscura.subprocess.Popen",
                return_value=fake_proc,
            ) as mock_popen,
            patch(
                "tools.browser_providers.obscura.requests.get",
                return_value=_mocked_cdp_response(59999),
            ),
        ):
            prov = ObscuraProvider()
            sess = prov.create_session(task_id="t-abc")

        assert sess["cdp_url"] == "ws://127.0.0.1:59999/devtools/browser"
        assert sess["features"]["obscura"] is True
        assert sess["features"]["local"] is True
        assert sess["features"]["port"] == 59999
        assert sess["session_name"].startswith("hermes_t-abc_")
        # Verify Popen was called with the expected CLI shape.
        cmd = mock_popen.call_args[0][0]
        assert cmd[0] == "obscura"
        assert "serve" in cmd
        assert "--port" in cmd and "59999" in cmd

    def test_stealth_disabled_omits_flag(self, monkeypatch):
        monkeypatch.setenv("OBSCURA_BINARY_PATH", "obscura")
        monkeypatch.setenv("OBSCURA_PORT", "59998")
        monkeypatch.setenv("OBSCURA_STEALTH", "false")

        with (
            patch(
                "tools.browser_providers.obscura.shutil.which",
                return_value="/usr/local/bin/obscura",
            ),
            patch(
                "tools.browser_providers.obscura.subprocess.Popen",
                return_value=MagicMock(),
            ) as mock_popen,
            patch(
                "tools.browser_providers.obscura.requests.get",
                return_value=_mocked_cdp_response(59998),
            ),
        ):
            prov = ObscuraProvider()
            prov.create_session(task_id="t-stealth-off")
            cmd = mock_popen.call_args[0][0]
            assert "--stealth" not in cmd

    def test_raises_when_binary_missing(self, monkeypatch):
        monkeypatch.setenv("OBSCURA_BINARY_PATH", "obscura")
        with patch(
            "tools.browser_providers.obscura.shutil.which", return_value=None
        ):
            prov = ObscuraProvider()
            with pytest.raises(ValueError, match="Obscura binary not found"):
                prov.create_session(task_id="t-missing")

    def test_raises_when_cdp_never_ready(self, monkeypatch):
        monkeypatch.setenv("OBSCURA_BINARY_PATH", "obscura")
        monkeypatch.setenv("OBSCURA_STARTUP_TIMEOUT", "0.5")

        # Fake CDP that never responds OK
        bad_resp = MagicMock()
        bad_resp.ok = False
        bad_resp.json.return_value = {}

        fake_proc = MagicMock()
        fake_proc.stderr = None

        with (
            patch(
                "tools.browser_providers.obscura.shutil.which",
                return_value="/usr/local/bin/obscura",
            ),
            patch(
                "tools.browser_providers.obscura.subprocess.Popen",
                return_value=fake_proc,
            ),
            patch(
                "tools.browser_providers.obscura.requests.get", return_value=bad_resp
            ),
        ):
            prov = ObscuraProvider()
            with pytest.raises(RuntimeError, match="did not become ready"):
                prov.create_session(task_id="t-timeout")
            # Provider must have terminated the doomed subprocess.
            assert fake_proc.terminate.called or fake_proc.kill.called


# ---------------------------------------------------------------------------
# close_session / emergency_cleanup
# ---------------------------------------------------------------------------


class TestTeardown:
    def test_close_unknown_session_returns_false(self):
        prov = ObscuraProvider()
        assert prov.close_session("never-existed") is False

    def test_close_session_terminates_known_process(self, monkeypatch):
        monkeypatch.setenv("OBSCURA_BINARY_PATH", "obscura")
        monkeypatch.setenv("OBSCURA_PORT", "59997")

        fake_proc = MagicMock()

        with (
            patch(
                "tools.browser_providers.obscura.shutil.which",
                return_value="/usr/local/bin/obscura",
            ),
            patch(
                "tools.browser_providers.obscura.subprocess.Popen",
                return_value=fake_proc,
            ),
            patch(
                "tools.browser_providers.obscura.requests.get",
                return_value=_mocked_cdp_response(59997),
            ),
        ):
            prov = ObscuraProvider()
            sess = prov.create_session(task_id="t-close")
            assert prov.close_session(sess["bb_session_id"]) is True
            assert fake_proc.terminate.called

    def test_emergency_cleanup_swallows_unknown_id(self):
        prov = ObscuraProvider()
        # Should not raise.
        prov.emergency_cleanup("not-a-real-id")


# ---------------------------------------------------------------------------
# Optional integration test against the real binary.
# Run with: OBSCURA_TEST_REAL=1 pytest tests/tools/test_browser_obscura_provider.py
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    os.environ.get("OBSCURA_TEST_REAL") != "1",
    reason="set OBSCURA_TEST_REAL=1 to run against the real obscura binary",
)
class TestRealBinary:
    def test_full_lifecycle_against_real_obscura(self):
        import requests

        prov = ObscuraProvider()
        if not prov.is_configured():
            pytest.skip("obscura binary not found on PATH or OBSCURA_BINARY_PATH")

        sess = prov.create_session(task_id="real-it")
        try:
            port = sess["features"]["port"]
            r = requests.get(f"http://127.0.0.1:{port}/json/version", timeout=5)
            assert r.ok
            data = r.json()
            assert data.get("webSocketDebuggerUrl", "").startswith("ws://127.0.0.1:")
        finally:
            assert prov.close_session(sess["bb_session_id"]) is True
