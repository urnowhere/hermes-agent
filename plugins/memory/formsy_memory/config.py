"""Configuration management for formsy_memory provider."""

import os
import json
from pathlib import Path
from typing import Optional, Any
from dataclasses import dataclass, asdict


@dataclass
class MemoryConfig:
    """Configuration for formsy_memory provider."""
    base_url: str = "https://api.formsy.ai"
    api_key: str = ""
    api_key_env: str = "FORMSY_API_KEY"
    workspace_id: str = "ws_default"
    tenant_id: Optional[str] = None
    timeout_s: int = 30
    max_retries: int = 3
    enable_memory_tools: bool = True
    enable_diagnostics: bool = True
    repo_id: str = ""
    revision: str = ""
    query_budget: int = 1200
    search_top_k: int = 5

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "MemoryConfig":
        """Create config from dictionary."""
        return cls(**{k: v for k, v in data.items() if k in cls.__annotations__})

    def to_dict(self) -> dict[str, Any]:
        """Convert config to dictionary."""
        return asdict(self)


class ConfigManager:
    """Manages configuration loading and saving."""

    def __init__(self, hermes_home: Path):
        self.hermes_home = hermes_home
        self.config_file = hermes_home / "formsy-memory-config.json"
        self.legacy_config_file = hermes_home / "formalcc-config.json"

    def load_config(self, hermes_config: Optional[dict] = None) -> MemoryConfig:
        """Load configuration from multiple sources."""
        # Start with defaults
        config_data = {}

        # If no hermes_config dict was passed, try loading config.yaml from hermes_home
        if hermes_config is None:
            main_config_path = self.hermes_home / "config.yaml"
            if main_config_path.exists():
                try:
                    import yaml
                    with open(main_config_path, "r") as f:
                        hermes_config = yaml.safe_load(f) or {}
                except Exception:
                    hermes_config = {}

        # Load from Hermes config if provided
        if hermes_config:
            if "formsy" in hermes_config:
                config_data.update(hermes_config["formsy"])
            elif "formalcc" in hermes_config:
                config_data.update(hermes_config["formalcc"])

        # Load from local config file
        if self.config_file.exists():
            with open(self.config_file, "r") as f:
                config_data.update(json.load(f))
        elif self.legacy_config_file.exists():
            with open(self.legacy_config_file, "r") as f:
                config_data.update(json.load(f))

        # Override with environment variables
        env_overrides = {
            "api_key_env": "FORMSY_API_KEY" if os.environ.get("FORMSY_API_KEY") else None,
            "base_url": os.environ.get("FORMSY_BASE_URL"),
            "workspace_id": os.environ.get("FORMSY_WORKSPACE_ID"),
            "tenant_id": os.environ.get("FORMSY_TENANT_ID"),
            "timeout_s": os.environ.get("FORMSY_TIMEOUT"),
            "repo_id": os.environ.get("FORMSY_REPO_ID"),
            "revision": os.environ.get("FORMSY_REVISION"),
            "query_budget": os.environ.get("FORMSY_QUERY_BUDGET"),
        }

        for key, value in env_overrides.items():
            if value is not None:
                if key in {"timeout_s", "query_budget"}:
                    config_data[key] = int(value)
                else:
                    config_data[key] = value

        return MemoryConfig.from_dict(config_data)
