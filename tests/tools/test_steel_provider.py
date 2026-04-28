# ABOUTME: Tests for the Steel cloud browser provider and steel_scrape tool.
# ABOUTME: Live integration tests require STEEL_API_KEY + the steel CLI on PATH.
"""Tests for tools.browser_providers.steel — Steel cloud browser provider.

Browser-automation sessions go through the Steel CLI (``steel browser ...``);
unit tests below mock the CLI subprocess. Live integration tests are skipped
unless ``STEEL_API_KEY`` is set in the host shell **and** the ``steel`` CLI is
on PATH.
"""

import json
import os
import shutil
from unittest.mock import patch, MagicMock

import pytest


# Capture the real STEEL_API_KEY at module import — before tests/conftest.py's
# autouse `_hermetic_environment` fixture blanks it. Integration tests use the
# `live_steel` fixture below to restore the captured key (and skip when
# unavailable), so the credential never has to leak through the hermetic env.
_REAL_STEEL_API_KEY = os.environ.get("STEEL_API_KEY") or ""
_HAS_STEEL_CLI = shutil.which("steel") is not None


@pytest.fixture
def live_steel(monkeypatch):
    """Restore the real STEEL_API_KEY for live integration tests, or skip."""
    if not _REAL_STEEL_API_KEY:
        pytest.skip("STEEL_API_KEY not set — skipping live Steel tests")
    if not _HAS_STEEL_CLI:
        pytest.skip("steel CLI not on PATH — skipping live Steel tests")
    monkeypatch.setenv("STEEL_API_KEY", _REAL_STEEL_API_KEY)


# ---------------------------------------------------------------------------
# Unit tests — provider class behaviour (no network needed)
# ---------------------------------------------------------------------------


class TestSteelProviderUnit:
    """Tests that don't require a real API key."""

    def test_provider_name(self):
        from tools.browser_providers.steel import SteelProvider

        provider = SteelProvider()
        assert provider.provider_name() == "Steel"

    def test_is_configured_without_key(self, monkeypatch):
        monkeypatch.delenv("STEEL_API_KEY", raising=False)
        from tools.browser_providers.steel import SteelProvider

        provider = SteelProvider()
        assert provider.is_configured() is False

    def test_is_configured_with_key(self, monkeypatch):
        monkeypatch.setenv("STEEL_API_KEY", "test-key-abc")
        from tools.browser_providers.steel import SteelProvider

        provider = SteelProvider()
        assert provider.is_configured() is True

    def test_base_url_default(self, monkeypatch):
        monkeypatch.delenv("STEEL_BASE_URL", raising=False)
        from tools.browser_providers.steel import SteelProvider

        provider = SteelProvider()
        assert provider._base_url() == "https://api.steel.dev"

    def test_base_url_override(self, monkeypatch):
        monkeypatch.setenv("STEEL_BASE_URL", "https://custom.steel.example.com/")
        from tools.browser_providers.steel import SteelProvider

        provider = SteelProvider()
        # Trailing slash should be stripped
        assert provider._base_url() == "https://custom.steel.example.com"

    def test_headers_raises_without_key(self, monkeypatch):
        monkeypatch.delenv("STEEL_API_KEY", raising=False)
        from tools.browser_providers.steel import SteelProvider

        provider = SteelProvider()
        with pytest.raises(ValueError, match="STEEL_API_KEY"):
            provider._headers()

    def test_headers_with_key(self, monkeypatch):
        monkeypatch.setenv("STEEL_API_KEY", "sk-test-123")
        from tools.browser_providers.steel import SteelProvider

        provider = SteelProvider()
        headers = provider._headers()
        assert headers["Steel-Api-Key"] == "sk-test-123"
        assert headers["Content-Type"] == "application/json"

    def test_registered_in_provider_registry(self):
        from tools.browser_tool import _PROVIDER_REGISTRY
        from tools.browser_providers.steel import SteelProvider

        assert "steel" in _PROVIDER_REGISTRY
        assert _PROVIDER_REGISTRY["steel"] is SteelProvider

    def test_create_session_raises_with_install_hint_when_cli_missing(self, monkeypatch):
        """If steel CLI is not on PATH, create_session must raise an actionable error."""
        from tools.browser_providers import steel as steel_mod
        from tools.browser_providers.steel import SteelProvider

        monkeypatch.setenv("STEEL_API_KEY", "test-key")
        monkeypatch.setattr(steel_mod, "_find_steel_cli", lambda: None)

        with pytest.raises(RuntimeError, match=r"Steel CLI not found"):
            SteelProvider().create_session("task-1")

    def test_install_hint_includes_setup_url(self):
        from tools.browser_providers.steel import _STEEL_CLI_INSTALL_HINT

        assert "setup.steel.dev" in _STEEL_CLI_INSTALL_HINT
        assert "@steel-dev/cli" in _STEEL_CLI_INSTALL_HINT

    def test_create_session_invokes_steel_cli(self, monkeypatch):
        """create_session shells out to ``steel browser start --session <name>``."""
        from tools.browser_providers import steel as steel_mod
        from tools.browser_providers.steel import SteelProvider

        monkeypatch.setenv("STEEL_API_KEY", "test-key")
        monkeypatch.setattr(steel_mod, "_find_steel_cli", lambda: "/fake/steel")

        fake_cli_response = MagicMock(
            returncode=0,
            stdout=json.dumps({
                "success": True,
                "data": {
                    "id": "abc-123",
                    "name": "ignored-by-us",
                    "liveUrl": "https://app.steel.dev/sessions/abc-123",
                    "mode": "cloud",
                },
            }),
            stderr="",
        )
        with patch.object(steel_mod.subprocess, "run", return_value=fake_cli_response) as mocked:
            session = SteelProvider().create_session("task-X")

        # Verify the CLI command shape
        args, _ = mocked.call_args
        cmd = args[0]
        assert cmd[0] == "/fake/steel"
        assert cmd[1:4] == ["browser", "start", "--session"]
        assert cmd[4].startswith("hermes_task-X_")
        assert cmd[-1] == "--json"

        # Verify the session record we expose to browser_tool.py
        assert session["uses_steel_cli"] is True
        assert "cdp_url" not in session
        assert session["session_name"].startswith("hermes_task-X_")
        assert session["bb_session_id"] == session["session_name"]
        assert session["features"] == {"steel": True}
        assert session["steel_cloud_session_id"] == "abc-123"
        assert session["session_viewer_url"] == "https://app.steel.dev/sessions/abc-123"

    def test_close_session_invokes_steel_cli_stop(self, monkeypatch):
        from tools.browser_providers import steel as steel_mod
        from tools.browser_providers.steel import SteelProvider

        monkeypatch.setattr(steel_mod, "_find_steel_cli", lambda: "/fake/steel")
        fake = MagicMock(returncode=0, stdout=json.dumps({"success": True, "data": {}}), stderr="")
        with patch.object(steel_mod.subprocess, "run", return_value=fake) as mocked:
            ok = SteelProvider().close_session("hermes_task_abcdef")

        assert ok is True
        cmd = mocked.call_args[0][0]
        assert cmd[1:5] == ["browser", "stop", "--session", "hermes_task_abcdef"]

    def test_emergency_cleanup_swallows_errors(self, monkeypatch):
        from tools.browser_providers import steel as steel_mod
        from tools.browser_providers.steel import SteelProvider

        monkeypatch.setattr(steel_mod, "_find_steel_cli", lambda: None)
        # Should not raise even when CLI is absent
        SteelProvider().emergency_cleanup("hermes_task_abcdef")


# ---------------------------------------------------------------------------
# Live integration tests — real Steel CLI + real Steel API
# ---------------------------------------------------------------------------


class TestSteelProviderIntegration:
    """Tests that create/release real Steel sessions via the steel CLI."""

    def test_create_and_release_session(self, live_steel):
        from tools.browser_providers.steel import SteelProvider

        provider = SteelProvider()
        session = provider.create_session("test_integration")
        try:
            assert session["session_name"].startswith("hermes_test_integration_")
            assert session["bb_session_id"] == session["session_name"]
            assert session["uses_steel_cli"] is True
            assert "cdp_url" not in session
            assert session["features"]["steel"] is True
            if session.get("session_viewer_url"):
                assert "steel.dev" in session["session_viewer_url"]
            assert session.get("steel_cloud_session_id"), "expected cloud session id"
        finally:
            assert provider.close_session(session["bb_session_id"]) is True


# ---------------------------------------------------------------------------
# Steel scrape tool tests
# ---------------------------------------------------------------------------


class TestSteelScrapeUnit:
    """Tests for the scrape tool that don't need an API key."""

    def test_scrape_blocks_private_urls_before_network(self, monkeypatch):
        monkeypatch.setenv("STEEL_API_KEY", "test-key")
        from tools.steel_scrape_tool import steel_scrape

        result = json.loads(steel_scrape("http://127.0.0.1:8000"))
        assert result == {"error": "Blocked: URL targets a private or internal address"}

    def test_scrape_returns_error_without_key(self, monkeypatch):
        monkeypatch.delenv("STEEL_API_KEY", raising=False)
        from tools.steel_scrape_tool import steel_scrape

        result = json.loads(steel_scrape("https://example.com"))
        assert "error" in result
        assert "STEEL_API_KEY" in result["error"]

    def test_check_requirements_without_key(self, monkeypatch):
        monkeypatch.delenv("STEEL_API_KEY", raising=False)
        from tools.steel_scrape_tool import check_steel_scrape_requirements

        assert check_steel_scrape_requirements() is False

    def test_check_requirements_with_key(self, monkeypatch):
        monkeypatch.setenv("STEEL_API_KEY", "test-key")
        from tools.steel_scrape_tool import check_steel_scrape_requirements

        assert check_steel_scrape_requirements() is True

    def test_scrape_registered_in_browser_toolset(self):
        import tools.steel_scrape_tool  # noqa: F401 — triggers registry.register()
        from tools.registry import registry

        assert registry.get_schema("steel_scrape") is not None
        assert registry.get_toolset_for_tool("steel_scrape") == "browser"


class TestSteelScrapeIntegration:
    """Live API tests for Steel scrape (no CLI needed — pure HTTP)."""

    def _scrape_live(self, monkeypatch):
        if not _REAL_STEEL_API_KEY:
            pytest.skip("STEEL_API_KEY not set — skipping live Steel scrape tests")
        monkeypatch.setenv("STEEL_API_KEY", _REAL_STEEL_API_KEY)

    def test_scrape_returns_markdown(self, monkeypatch):
        self._scrape_live(monkeypatch)
        from tools.steel_scrape_tool import steel_scrape

        result = json.loads(steel_scrape("https://example.com", format="markdown"))
        assert "error" not in result
        assert "content" in result
        assert len(result["content"]) > 0

    def test_scrape_returns_metadata(self, monkeypatch):
        self._scrape_live(monkeypatch)
        from tools.steel_scrape_tool import steel_scrape

        result = json.loads(steel_scrape("https://example.com"))
        assert "title" in result or "content" in result
