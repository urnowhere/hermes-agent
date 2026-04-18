"""Settings and auth-status service helpers for the Hermes Web Console backend."""

from __future__ import annotations

import copy
import re
from typing import Any, Callable

from hermes_cli import auth as auth_module
from hermes_cli.config import load_config, save_config

_SECRET_KEY_PATTERN = re.compile(
    r"(^|_)(api[_-]?key|token|secret|password|passwd|refresh[_-]?token|access[_-]?token|client[_-]?secret)$",
    re.IGNORECASE,
)


class SettingsService:
    """Thin wrapper around Hermes config and auth status helpers."""

    def __init__(
        self,
        *,
        config_loader: Callable[[], dict[str, Any]] = load_config,
        config_saver: Callable[[dict[str, Any]], None] = save_config,
        auth_status_getter: Callable[[str | None], dict[str, Any]] = auth_module.get_auth_status,
        active_provider_getter: Callable[[], str | None] = auth_module.get_active_provider,
        provider_registry: dict[str, Any] | None = None,
    ) -> None:
        self._config_loader = config_loader
        self._config_saver = config_saver
        self._auth_status_getter = auth_status_getter
        self._active_provider_getter = active_provider_getter
        self._provider_registry = dict(provider_registry or auth_module.PROVIDER_REGISTRY)

    def get_settings(self) -> dict[str, Any]:
        config = self._config_loader()
        return self._sanitize_payload(config)

    def update_settings(self, patch: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(patch, dict) or not patch:
            raise ValueError("Request body must include a non-empty JSON object.")
        current = self._config_loader()
        
        old_provider = current.get("provider")
        new_provider = patch.get("provider")
        if "model" in patch and isinstance(patch["model"], dict) and "provider" in patch["model"]:
            new_provider = patch["model"]["provider"]
            
        if new_provider and new_provider != old_provider:
            from hermes_cli.model_switch import detect_provider_for_model
            old_model = current.get("model", {})
            old_model_name = old_model if isinstance(old_model, str) else old_model.get("default", "")
            
            if old_model_name:
                detected, _ = detect_provider_for_model(old_model_name, new_provider)
                is_compatible = False
                if new_provider == "openai-codex" and detected in ("openai", "openai-codex", "custom"):
                    is_compatible = True
                elif detected in (new_provider, "custom"):
                    is_compatible = True
                
                if not is_compatible:
                    if "model" not in patch:
                        patch["model"] = {}
                    if isinstance(patch["model"], dict):
                        # Auto-populate a sensible default for Codex so the GUI isn't empty
                        default_model = "gpt-5.4" if new_provider == "openai-codex" else ""
                        patch["model"]["default"] = default_model
                        patch["model"]["name"] = default_model

        updated = self._deep_merge(copy.deepcopy(current), patch)
        self._config_saver(updated)
        return self._sanitize_payload(updated)

    def get_auth_status(self) -> dict[str, Any]:
        active_provider = self._active_provider_getter()
        providers: list[dict[str, Any]] = []
        for provider_id, provider_config in sorted(self._provider_registry.items()):
            raw_status = dict(self._auth_status_getter(provider_id) or {})
            providers.append(
                {
                    "provider": provider_id,
                    "name": getattr(provider_config, "name", provider_id),
                    "auth_type": getattr(provider_config, "auth_type", None),
                    "active": provider_id == active_provider,
                    "status": self._sanitize_payload(raw_status),
                }
            )

        active_status = self._sanitize_payload(dict(self._auth_status_getter(active_provider) or {})) if active_provider else {"logged_in": False}
        resolved_provider = active_provider
        if resolved_provider is None:
            for entry in providers:
                if entry["status"].get("logged_in"):
                    resolved_provider = entry["provider"]
                    break

        return {
            "active_provider": active_provider,
            "resolved_provider": resolved_provider,
            "logged_in": bool(active_status.get("logged_in")) if active_provider else any(entry["status"].get("logged_in") for entry in providers),
            "active_status": active_status,
            "providers": providers,
        }

    def _sanitize_payload(self, value: Any, *, key_path: tuple[str, ...] = ()) -> Any:
        if isinstance(value, dict):
            sanitized: dict[str, Any] = {}
            for key, item in value.items():
                key_text = str(key)
                if self._looks_secret_key(key_text):
                    sanitized[key_text] = self._redacted_value(item)
                else:
                    sanitized[key_text] = self._sanitize_payload(item, key_path=(*key_path, key_text))
            return sanitized
        if isinstance(value, list):
            return [self._sanitize_payload(item, key_path=key_path) for item in value]
        return value

    @staticmethod
    def _deep_merge(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
        for key, value in patch.items():
            if isinstance(value, dict) and isinstance(base.get(key), dict):
                base[key] = SettingsService._deep_merge(dict(base[key]), value)
            else:
                base[key] = value
        return base

    @staticmethod
    def _looks_secret_key(key: str) -> bool:
        return bool(_SECRET_KEY_PATTERN.search(key))

    @staticmethod
    def _redacted_value(value: Any) -> str | None:
        if value in (None, ""):
            return None if value is None else ""
        return "***"
