"""Configuration management for formalcc-memory provider."""

import os
import json
from pathlib import Path
from typing import Optional, Any
from dataclasses import dataclass, asdict


@dataclass
class MemoryConfig:
    """Configuration for formalcc-memory provider."""
    base_url: str = "https://api.formsy.ai"
    api_key_env: str = "FORMALCC_API_KEY"
    workspace_id: str = "ws_default"
    tenant_id: Optional[str] = None
    timeout_s: int = 30
    max_retries: int = 3
    enable_memory_tools: bool = True
    enable_diagnostics: bool = True

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
        self.config_file = hermes_home / "formalcc-config.json"

    def load_config(self, hermes_config: Optional[dict] = None) -> MemoryConfig:
        """Load configuration from multiple sources."""
        # Start with defaults
        config_data = {}

        # Load from Hermes config if provided
        if hermes_config and "formalcc" in hermes_config:
            config_data.update(hermes_config["formalcc"])

        # Load from local config file
        if self.config_file.exists():
            with open(self.config_file, "r") as f:
                config_data.update(json.load(f))

        # Override with environment variables
        env_overrides = {
            "base_url": os.environ.get("FORMALCC_BASE_URL"),
            "workspace_id": os.environ.get("FORMALCC_WORKSPACE_ID"),
            "tenant_id": os.environ.get("FORMALCC_TENANT_ID"),
            "timeout_s": os.environ.get("FORMALCC_TIMEOUT"),
        }

        for key, value in env_overrides.items():
            if value is not None:
                if key == "timeout_s":
                    config_data[key] = int(value)
                else:
                    config_data[key] = value

        return MemoryConfig.from_dict(config_data)
