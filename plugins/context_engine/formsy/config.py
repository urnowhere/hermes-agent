"""Configuration management for the Formsy context engine."""

import os
import json
from pathlib import Path
from typing import Optional, Any
from dataclasses import dataclass, asdict


@dataclass
class EngineConfig:
    """Configuration for the Formsy context engine."""
    base_url: str = "https://api.formsy.ai"
    memory_search_endpoint: str = "/api/v1/query"
    api_key_env: str = "FORMALCC_API_KEY"
    repo_id: str = ""
    revision: str = "latest"
    query_budget: int = 4000
    workspace_id: str = "ws_default"
    tenant_id: Optional[str] = None
    timeout_s: int = 30
    max_retries: int = 3
    default_scene: str = "auto"

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "EngineConfig":
        return cls(**{k: v for k, v in data.items() if k in cls.__annotations__})

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class EngineConfigManager:
    """Manages configuration for the context engine."""

    def __init__(self, hermes_home: Path):
        self.hermes_home = hermes_home
        self.config_file = hermes_home / "formsy-context-engine-config.json"

    def load_config(self, hermes_config: Optional[dict] = None) -> EngineConfig:
        """Load configuration from multiple sources."""
        config_data = {}

        source_config = hermes_config or {}
        if "formsy" not in source_config:
            try:
                from hermes_cli.config import load_config
                loaded = load_config()
                if isinstance(loaded, dict):
                    source_config = loaded
            except Exception:
                source_config = hermes_config or {}

        if isinstance(source_config.get("formsy"), dict):
            config_data.update(source_config["formsy"])

        if self.config_file.exists():
            with open(self.config_file, "r") as f:
                config_data.update(json.load(f))

        env_overrides = {
            "base_url": os.environ.get("FORMALCC_BASE_URL"),
            "memory_search_endpoint": os.environ.get("FORMALCC_MEMORY_SEARCH_ENDPOINT"),
            "repo_id": os.environ.get("FORMALCC_REPO_ID"),
            "revision": os.environ.get("FORMALCC_REVISION"),
            "query_budget": os.environ.get("FORMALCC_QUERY_BUDGET"),
            "workspace_id": os.environ.get("FORMALCC_WORKSPACE_ID"),
            "tenant_id": os.environ.get("FORMALCC_TENANT_ID"),
            "timeout_s": os.environ.get("FORMALCC_TIMEOUT"),
        }

        for key, value in env_overrides.items():
            if value is not None:
                config_data[key] = int(value) if key in {"query_budget", "timeout_s"} else value

        return EngineConfig.from_dict(config_data)
