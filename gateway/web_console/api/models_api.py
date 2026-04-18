"""Models API routes for the Hermes Web Console backend.

Provides:
  GET  /api/gui/models/catalog  — authenticated providers with curated models
  GET  /api/gui/models/active   — currently active model + provider
  POST /api/gui/models/switch   — live model/provider switch (session or global)
"""

from __future__ import annotations

import logging
import yaml
from pathlib import Path
from aiohttp import web

from hermes_constants import get_hermes_home

logger = logging.getLogger(__name__)


def _read_model_config() -> dict:
    """Read model/provider/base_url/providers from config.yaml."""
    config_path = get_hermes_home() / "config.yaml"
    result = {
        "model": "",
        "provider": "openrouter",
        "base_url": "",
        "api_key": "",
        "user_providers": None,
    }
    try:
        if config_path.exists():
            with open(config_path, encoding="utf-8") as f:
                cfg = yaml.safe_load(f) or {}
            model_cfg = cfg.get("model", {})
            if isinstance(model_cfg, dict):
                result["model"] = model_cfg.get("name", "") or model_cfg.get("default", "")
                result["provider"] = model_cfg.get("provider", "openrouter")
                result["base_url"] = model_cfg.get("base_url", "")
            elif isinstance(model_cfg, str):
                result["model"] = model_cfg
            result["user_providers"] = cfg.get("providers")
    except Exception:
        logger.debug("Failed to read model config", exc_info=True)
    return result


async def handle_get_models_catalog(request: web.Request) -> web.Response:
    """GET /api/gui/models/catalog — authenticated providers with curated models."""
    from hermes_cli.model_switch import list_authenticated_providers
    from hermes_cli.providers import get_label

    cfg = _read_model_config()
    current_model = cfg["model"]
    current_provider = cfg["provider"]

    try:
        providers = list_authenticated_providers(
            current_provider=current_provider,
            user_providers=cfg["user_providers"],
            max_models=20,
        )
    except Exception as exc:
        logger.warning("list_authenticated_providers failed: %s", exc)
        providers = []

    return web.json_response({
        "ok": True,
        "current_model": current_model,
        "current_provider": current_provider,
        "current_provider_label": get_label(current_provider),
        "providers": providers,
    })


async def handle_get_models_active(request: web.Request) -> web.Response:
    """GET /api/gui/models/active — current model + provider + metadata."""
    from hermes_cli.providers import get_label
    from agent.models_dev import get_model_info, get_model_capabilities

    cfg = _read_model_config()
    current_model = cfg["model"]
    current_provider = cfg["provider"]
    provider_label = get_label(current_provider)

    # Try to get rich metadata from models.dev
    metadata: dict = {}
    try:
        mi = get_model_info(current_provider, current_model)
        if mi:
            metadata["context_window"] = mi.context_window or 0
            metadata["max_output"] = mi.max_output or 0
            if mi.has_cost_data():
                metadata["cost"] = mi.format_cost()
            metadata["capabilities"] = mi.format_capabilities()
    except Exception:
        pass

    return web.json_response({
        "ok": True,
        "model": current_model,
        "provider": current_provider,
        "provider_label": provider_label,
        **metadata,
    })


async def handle_post_models_switch(request: web.Request) -> web.Response:
    """POST /api/gui/models/switch — live model/provider switch."""
    from hermes_cli.model_switch import switch_model as _switch_model
    from hermes_cli.providers import determine_api_mode

    try:
        body = await request.json()
    except Exception:
        return web.json_response({"ok": False, "error": "Invalid JSON body"}, status=400)

    model_input = body.get("model", "").strip()
    explicit_provider = body.get("provider", "").strip()
    persist_global = body.get("global", False)

    if not model_input and not explicit_provider:
        return web.json_response(
            {"ok": False, "error": "Provide 'model' and/or 'provider'"},
            status=400,
        )

    cfg = _read_model_config()

    result = _switch_model(
        raw_input=model_input,
        current_provider=cfg["provider"],
        current_model=cfg["model"],
        current_base_url=cfg["base_url"],
        current_api_key=cfg["api_key"],
        is_global=persist_global,
        explicit_provider=explicit_provider,
        user_providers=cfg["user_providers"],
    )

    if not result.success:
        return web.json_response({
            "ok": False,
            "error": result.error_message,
        }, status=400)

    # Validate provider compatibility (specifically for Codex)
    codex_compatible = ("openai", "openai-codex", "custom")
    # Guard 1: Currently on Codex — new model must be OpenAI-compatible
    if cfg.get("provider") == "openai-codex":
        if result.target_provider not in codex_compatible:
            return web.json_response({
                "ok": False,
                "error": f"The '{result.new_model}' model is not supported when using Codex with a ChatGPT account. Please select an OpenAI model or change your active provider to {result.target_provider}."
            }, status=400)
    # Guard 2: Switching TO Codex — resolved model must be OpenAI-compatible
    if result.target_provider == "openai-codex":
        if cfg.get("provider") not in codex_compatible and result.target_provider not in codex_compatible:
            return web.json_response({
                "ok": False,
                "error": f"Cannot switch to Codex with model '{result.new_model}' (resolved provider: {result.target_provider}). Only OpenAI models are compatible with Codex."
            }, status=400)

    # Persist to config.yaml if global
    if persist_global:
        try:
            config_path = get_hermes_home() / "config.yaml"
            if config_path.exists():
                with open(config_path, encoding="utf-8") as f:
                    file_cfg = yaml.safe_load(f) or {}
            else:
                file_cfg = {}
            
            # Ensure model is a dict before setting properties
            model_cfg = file_cfg.get("model", {})
            if isinstance(model_cfg, str):
                model_cfg = {"default": model_cfg}
            elif not isinstance(model_cfg, dict):
                model_cfg = {}
            
            model_cfg["default"] = result.new_model
            model_cfg["provider"] = result.target_provider
            model_cfg["name"] = result.new_model  # Keep name for backward compatibility in UI
            if result.base_url:
                model_cfg["base_url"] = result.base_url
            
            file_cfg["model"] = model_cfg
            
            from hermes_cli.config import save_config
            save_config(file_cfg)
        except Exception as exc:
            logger.warning("Failed to persist model switch: %s", exc)

    # Build rich response
    response: dict = {
        "ok": True,
        "new_model": result.new_model,
        "provider": result.target_provider,
        "provider_label": result.provider_label or result.target_provider,
        "provider_changed": result.provider_changed,
        "is_global": persist_global,
    }

    # Add model metadata
    mi = result.model_info
    if mi:
        if mi.context_window:
            response["context_window"] = mi.context_window
        if mi.max_output:
            response["max_output"] = mi.max_output
        if mi.has_cost_data():
            response["cost"] = mi.format_cost()
        response["capabilities"] = mi.format_capabilities()

    # Cache status
    cache_enabled = (
        ("openrouter" in (result.base_url or "").lower()
         and "claude" in result.new_model.lower())
        or result.api_mode == "anthropic_messages"
    )
    response["cache_enabled"] = cache_enabled

    if result.warning_message:
        response["warning"] = result.warning_message

    if result.resolved_via_alias:
        response["resolved_via_alias"] = result.resolved_via_alias

    return web.json_response(response)


def register_models_api_routes(app: web.Application) -> None:
    app.router.add_get("/api/gui/models/catalog", handle_get_models_catalog)
    app.router.add_get("/api/gui/models/active", handle_get_models_active)
    app.router.add_post("/api/gui/models/switch", handle_post_models_switch)
