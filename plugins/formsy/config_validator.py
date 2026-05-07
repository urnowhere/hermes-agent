"""Enhanced configuration validation and management."""

import os
import json
from pathlib import Path
from typing import Optional, Any, Dict
from dataclasses import dataclass, asdict, field
import logging

logger = logging.getLogger("formalcc.config.validator")


@dataclass
class ConfigValidationResult:
    """Result of configuration validation."""
    valid: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


class ConfigValidator:
    """Validates FormalCC configuration."""

    @staticmethod
    def validate_api_key(api_key: str) -> ConfigValidationResult:
        """Validate API key format."""
        result = ConfigValidationResult(valid=True)

        if not api_key:
            result.valid = False
            result.errors.append("API key is required")
            return result

        if not (api_key.startswith("fsy_live_") or api_key.startswith("fsy_test_")):
            result.valid = False
            result.errors.append(
                "Invalid API key format. Expected 'fsy_live_*' or 'fsy_test_*'"
            )

        if len(api_key) < 20:
            result.valid = False
            result.errors.append("API key is too short")

        if api_key.startswith("fsy_test_"):
            result.warnings.append("Using test API key (not for production)")

        return result

    @staticmethod
    def validate_base_url(base_url: str) -> ConfigValidationResult:
        """Validate base URL format."""
        result = ConfigValidationResult(valid=True)

        if not base_url:
            result.valid = False
            result.errors.append("Base URL is required")
            return result

        if not base_url.startswith(("http://", "https://")):
            result.valid = False
            result.errors.append("Base URL must start with http:// or https://")

        if base_url.startswith("http://") and "localhost" not in base_url:
            result.warnings.append("Using HTTP (not HTTPS) for non-localhost URL")

        if base_url.endswith("/"):
            result.warnings.append("Base URL should not end with /")

        return result

    @staticmethod
    def validate_timeout(timeout_s: int) -> ConfigValidationResult:
        """Validate timeout value."""
        result = ConfigValidationResult(valid=True)

        if timeout_s <= 0:
            result.valid = False
            result.errors.append("Timeout must be positive")

        if timeout_s < 5:
            result.warnings.append("Timeout is very short (< 5s)")

        if timeout_s > 120:
            result.warnings.append("Timeout is very long (> 120s)")

        return result

    @staticmethod
    def validate_workspace_id(workspace_id: str) -> ConfigValidationResult:
        """Validate workspace ID format."""
        result = ConfigValidationResult(valid=True)

        if not workspace_id:
            result.valid = False
            result.errors.append("Workspace ID is required")
            return result

        if not workspace_id.startswith("ws_"):
            result.warnings.append("Workspace ID should start with 'ws_'")

        return result

    @classmethod
    def validate_config(cls, config: Dict[str, Any]) -> ConfigValidationResult:
        """Validate entire configuration."""
        result = ConfigValidationResult(valid=True)

        # Validate API key from environment
        api_key_env = config.get("api_key_env", "FORMALCC_API_KEY")
        api_key = os.environ.get(api_key_env)
        if api_key:
            key_result = cls.validate_api_key(api_key)
            result.errors.extend(key_result.errors)
            result.warnings.extend(key_result.warnings)
            if not key_result.valid:
                result.valid = False
        else:
            result.warnings.append(f"API key not found in ${api_key_env}")

        # Validate base URL
        if "base_url" in config:
            url_result = cls.validate_base_url(config["base_url"])
            result.errors.extend(url_result.errors)
            result.warnings.extend(url_result.warnings)
            if not url_result.valid:
                result.valid = False

        # Validate timeout
        if "timeout_s" in config:
            timeout_result = cls.validate_timeout(config["timeout_s"])
            result.errors.extend(timeout_result.errors)
            result.warnings.extend(timeout_result.warnings)
            if not timeout_result.valid:
                result.valid = False

        # Validate workspace ID
        if "workspace_id" in config:
            ws_result = cls.validate_workspace_id(config["workspace_id"])
            result.errors.extend(ws_result.errors)
            result.warnings.extend(ws_result.warnings)
            if not ws_result.valid:
                result.valid = False

        return result


class ConfigGenerator:
    """Generates configuration files."""

    @staticmethod
    def generate_default_config() -> Dict[str, Any]:
        """Generate default configuration."""
        return {
            "base_url": "https://api.formsy.ai",
            "api_key_env": "FORMALCC_API_KEY",
            "workspace_id": "ws_default",
            "tenant_id": None,
            "timeout_s": 30,
            "max_retries": 3,
            "enable_memory_tools": True,
            "enable_diagnostics": True,
        }

    @staticmethod
    def generate_config_file(path: Path, overrides: Optional[Dict[str, Any]] = None) -> None:
        """Generate configuration file with optional overrides."""
        config = ConfigGenerator.generate_default_config()
        if overrides:
            config.update(overrides)

        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as f:
            json.dump(config, f, indent=2)

        logger.info(f"Generated config file: {path}")

    @staticmethod
    def generate_env_template(path: Path) -> None:
        """Generate .env template file."""
        template = """# FormalCC Hermes Plugin Configuration

# Required: API key for FormalCC Runtime API
FORMALCC_API_KEY=fsy_live_your_key_here

# Optional: Override base URL (default: https://api.formsy.ai)
# FORMALCC_BASE_URL=https://api.formsy.ai

# Optional: Override workspace ID (default: ws_default)
# FORMALCC_WORKSPACE_ID=ws_default

# Optional: Set tenant ID
# FORMALCC_TENANT_ID=your_tenant

# Optional: Override timeout in seconds (default: 30)
# FORMALCC_TIMEOUT=30
"""
        with open(path, "w") as f:
            f.write(template)

        logger.info(f"Generated .env template: {path}")
