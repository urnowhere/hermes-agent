"""Default Constraint Keeper coordinator factory shared by Hermes adapters."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any

from .client import ConstraintKeeperClient
from .coordinator import ConstraintKeeperCoordinator

_default_coordinator: ConstraintKeeperCoordinator | None = None


def get_default_coordinator() -> ConstraintKeeperCoordinator:
    global _default_coordinator
    if _default_coordinator is None:
        _default_coordinator = build_default_coordinator()
    return _default_coordinator


def is_default_constraint_keeper_enabled() -> bool:
    env = os.getenv("FORMSY_CONSTRAINT_KEEPER_ENABLED")
    if env is not None:
        return env.strip().lower() in {"1", "true", "yes", "on"}
    config = _load_hermes_config()
    plugins = config.get("plugins")
    if not isinstance(plugins, dict):
        return False
    enabled = plugins.get("enabled")
    return isinstance(enabled, list) and "formsy-constraint-keeper" in enabled


def reset_default_coordinator() -> None:
    global _default_coordinator
    _default_coordinator = None


def build_default_coordinator() -> ConstraintKeeperCoordinator:
    cfg = _formsy_config()
    base_url = (
        os.getenv("FORMSY_BASE_URL")
        or str(cfg.get("base_url") or "")
        or "http://127.0.0.1:8000"
    )
    api_key_env = str(cfg.get("api_key_env") or "FORMSY_API_KEY")
    api_key = str(os.getenv("FORMSY_CONSTRAINT_KEEPER_API_KEY") or cfg.get("api_key") or "")
    timeout_s = float(os.getenv("FORMSY_CONSTRAINT_KEEPER_TIMEOUT", cfg.get("timeout_s") or 30))
    client = ConstraintKeeperClient(
        base_url=base_url,
        api_key_env=api_key_env,
        api_key=api_key,
        timeout_s=timeout_s,
    )
    ConstraintKeeperCoordinator._run_async(client.__aenter__())
    return ConstraintKeeperCoordinator(
        client=client,
        spool_root=_spool_root(),
        diff_provider=_git_diff,
        source_provider=_read_changed_sources,
        fail_closed_on_submit=_truthy_env("FORMSY_CONSTRAINT_KEEPER_FAIL_CLOSED_ON_SUBMIT", True),
    )


def _formsy_config() -> dict[str, Any]:
    config = _load_hermes_config()
    formsy = config.get("formsy")
    return formsy if isinstance(formsy, dict) else {}


def _load_hermes_config() -> dict[str, Any]:
    config_path = _get_hermes_home() / "config.yaml"
    if not config_path.exists():
        return {}
    try:
        import yaml

        config = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}
    return config if isinstance(config, dict) else {}


def _get_hermes_home() -> Path:
    try:
        from hermes_constants import get_hermes_home

        return get_hermes_home()
    except Exception:
        return Path(os.getenv("HERMES_HOME", "~/.hermes")).expanduser()


def _spool_root() -> Path:
    configured = os.getenv("FORMSY_CONSTRAINT_KEEPER_SPOOL_DIR")
    if configured:
        return Path(configured).expanduser()
    return _get_hermes_home() / "formsy" / "constraint-keeper" / "spool"


def _git_diff() -> str:
    try:
        result = subprocess.run(
            ["git", "diff", "--no-ext-diff"],
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        return ""
    return result.stdout if result.returncode in {0, 1} else ""


def _read_changed_sources(paths: list[str]) -> dict[str, str]:
    sources: dict[str, str] = {}
    for path in paths[:8]:
        if not isinstance(path, str) or not path:
            continue
        candidate = Path(path)
        if candidate.is_absolute() or ".." in candidate.parts:
            continue
        try:
            if not candidate.is_file():
                continue
            data = candidate.read_bytes()[:256 * 1024]
        except Exception:
            continue
        sources[path] = data.decode("utf-8", errors="replace")
    return sources


def _truthy_env(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}
