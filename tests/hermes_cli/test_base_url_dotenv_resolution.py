"""Regression tests for issue #18757.

`base_url_env_var` resolution must consult ``~/.hermes/.env`` via
``get_env_value()`` — not just ``os.getenv()`` — so that providers configured
exclusively in the dotenv file (with no shell export) end up using the right
endpoint instead of silently falling back to ``inference_base_url``.

Before the fix, ``resolve_api_key_provider_credentials("xiaomi")`` would
return the registry default (``api.xiaomimimo.com``) even when the user had
written ``XIAOMI_BASE_URL=https://token-plan-cn.xiaomimimo.com/v1`` to
``~/.hermes/.env``, causing 401s on auxiliary tasks (issue #18757). The same
class of bug existed in five additional places — this module exercises each
of them.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterator

import pytest


# ---------------------------------------------------------------------------
# Fixture: a real ~/.hermes/.env file containing only the base URL override.
# ---------------------------------------------------------------------------


@pytest.fixture
def hermes_home_with_dotenv_base_url(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[Path]:
    """Write a Hermes home with .env that overrides XIAOMI_BASE_URL only.

    The shell environment is intentionally cleared of XIAOMI_BASE_URL so the
    test can prove that resolution falls through to the dotenv file.
    """
    home = tmp_path / "hermes_home"
    home.mkdir()
    env_file = home / ".env"
    env_file.write_text(
        "XIAOMI_API_KEY=sk-from-dotenv\n"
        "XIAOMI_BASE_URL=https://token-plan-cn.xiaomimimo.com/v1\n"
    )

    # Point Hermes at this directory and clear cached lookups.
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.delenv("XIAOMI_BASE_URL", raising=False)
    monkeypatch.delenv("XIAOMI_API_KEY", raising=False)

    # Bust the in-process cache that load_env() uses, if present.
    try:
        from hermes_cli import config as _config

        if hasattr(_config, "_ENV_CACHE"):
            _config._ENV_CACHE = None  # type: ignore[attr-defined]
    except Exception:
        pass

    yield home


# ---------------------------------------------------------------------------
# auth.py — both API-key paths
# ---------------------------------------------------------------------------


class TestAuthApiKeyResolution:
    """resolve_api_key_provider_credentials() / get_api_key_provider_status()."""

    def test_resolve_credentials_reads_base_url_from_dotenv(
        self, hermes_home_with_dotenv_base_url: Path
    ) -> None:
        from hermes_cli.auth import resolve_api_key_provider_credentials

        creds = resolve_api_key_provider_credentials("xiaomi")
        assert creds["base_url"] == "https://token-plan-cn.xiaomimimo.com/v1"
        assert creds["api_key"] == "sk-from-dotenv"

    def test_status_reads_base_url_from_dotenv(
        self, hermes_home_with_dotenv_base_url: Path
    ) -> None:
        from hermes_cli.auth import get_api_key_provider_status

        status = get_api_key_provider_status("xiaomi")
        assert status["base_url"] == "https://token-plan-cn.xiaomimimo.com/v1"
        assert status["configured"] is True

    def test_shell_env_still_wins_when_set(
        self,
        hermes_home_with_dotenv_base_url: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Explicit shell exports continue to win — get_env_value() prefers
        os.environ, matching how API keys are already resolved."""
        from hermes_cli.auth import resolve_api_key_provider_credentials

        monkeypatch.setenv("XIAOMI_BASE_URL", "https://shell.example/v1")
        creds = resolve_api_key_provider_credentials("xiaomi")
        assert creds["base_url"] == "https://shell.example/v1"


# ---------------------------------------------------------------------------
# auth.py — external-process providers (Copilot ACP)
# ---------------------------------------------------------------------------


class TestAuthExternalProcessResolution:
    """resolve_external_process_provider_credentials() / *_status()."""

    def _write_dotenv(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
        home = tmp_path / "hermes_home"
        home.mkdir()
        (home / ".env").write_text(
            "COPILOT_ACP_BASE_URL=acp+tcp://127.0.0.1:9999\n"
        )
        monkeypatch.setenv("HERMES_HOME", str(home))
        monkeypatch.delenv("COPILOT_ACP_BASE_URL", raising=False)
        try:
            from hermes_cli import config as _config

            if hasattr(_config, "_ENV_CACHE"):
                _config._ENV_CACHE = None  # type: ignore[attr-defined]
        except Exception:
            pass
        return home

    def test_resolve_credentials_reads_base_url_from_dotenv(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._write_dotenv(tmp_path, monkeypatch)
        from hermes_cli.auth import resolve_external_process_provider_credentials

        creds = resolve_external_process_provider_credentials("copilot-acp")
        assert creds["base_url"] == "acp+tcp://127.0.0.1:9999"

    def test_status_reads_base_url_from_dotenv(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._write_dotenv(tmp_path, monkeypatch)
        from hermes_cli.auth import get_external_process_provider_status

        status = get_external_process_provider_status("copilot-acp")
        assert status["base_url"] == "acp+tcp://127.0.0.1:9999"


# ---------------------------------------------------------------------------
# runtime_provider.py — the one PR #17246 missed entirely
# ---------------------------------------------------------------------------


class TestRuntimeProviderResolution:
    """The runtime resolver path used by gateway / auxiliary clients."""

    def test_runtime_resolver_reads_base_url_from_dotenv(
        self, hermes_home_with_dotenv_base_url: Path
    ) -> None:
        from hermes_cli.runtime_provider import resolve_runtime_provider

        runtime = resolve_runtime_provider(requested="xiaomi")
        assert runtime["base_url"].rstrip("/") == (
            "https://token-plan-cn.xiaomimimo.com/v1"
        )


# ---------------------------------------------------------------------------
# model_switch.py — built-in endpoint dedup helper
# ---------------------------------------------------------------------------


class TestModelSwitchBuiltinEndpointDedup:
    """The built-in endpoint dedup logic must read ~/.hermes/.env so that
    user-defined custom_providers entries pointing at the same host get
    deduplicated correctly (issue #18757).

    The helper is a closure inside _refresh_curated_models. Inspect the
    source of the patched module to confirm it now goes through
    ``get_env_value`` rather than ``os.environ.get`` directly.
    """

    def test_dedup_helper_uses_get_env_value(self) -> None:
        import inspect
        from hermes_cli import model_switch as ms

        src = inspect.getsource(ms)
        # The fixed call path must reference get_env_value when reading
        # base_url_env_var. Direct os.environ.get(pcfg.base_url_env_var ...)
        # is the regression we're guarding against.
        assert "get_env_value(pcfg.base_url_env_var)" in src, (
            "model_switch must resolve base_url_env_var via get_env_value "
            "so ~/.hermes/.env is honoured (issue #18757)."
        )
