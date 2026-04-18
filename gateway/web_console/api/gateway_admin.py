"""Gateway admin API routes for the Hermes Web Console backend."""

from __future__ import annotations

import json
from typing import Any

from aiohttp import web

from gateway.web_console.services.gateway_service import GatewayService
from gateway.web_console.services.settings_service import SettingsService
from hermes_cli.config import save_env_value

GATEWAY_SERVICE_APP_KEY = web.AppKey("hermes_web_console_gateway_service", GatewayService)
SETTINGS_SERVICE_APP_KEY = web.AppKey("hermes_web_console_settings_service", SettingsService)


def _json_error(*, status: int, code: str, message: str, **extra: Any) -> web.Response:
    payload: dict[str, Any] = {"ok": False, "error": {"code": code, "message": message}}
    payload["error"].update(extra)
    return web.json_response(payload, status=status)


async def _read_json_body(request: web.Request) -> dict[str, Any] | None:
    try:
        data = await request.json()
    except (json.JSONDecodeError, ValueError, TypeError):
        return None
    if not isinstance(data, dict):
        return None
    return data


def _get_gateway_service(request: web.Request) -> GatewayService:
    service = request.app.get(GATEWAY_SERVICE_APP_KEY)
    if service is None:
        service = GatewayService()
        request.app[GATEWAY_SERVICE_APP_KEY] = service
    return service

def _get_settings_service(request: web.Request) -> SettingsService:
    service = request.app.get(SETTINGS_SERVICE_APP_KEY)
    if service is None:
        service = SettingsService()
        request.app[SETTINGS_SERVICE_APP_KEY] = service
    return service


def _require_non_empty_string(data: dict[str, Any], field_name: str) -> str | None:
    value = data.get(field_name)
    if not isinstance(value, str) or not value.strip():
        return None
    return value.strip()


async def handle_gateway_overview(request: web.Request) -> web.Response:
    service = _get_gateway_service(request)
    return web.json_response({"ok": True, "overview": service.get_overview()})


async def handle_gateway_platforms(request: web.Request) -> web.Response:
    service = _get_gateway_service(request)
    return web.json_response({"ok": True, "platforms": service.get_platforms()})


async def handle_gateway_pairing(request: web.Request) -> web.Response:
    service = _get_gateway_service(request)
    return web.json_response({"ok": True, "pairing": service.get_pairing_state()})


async def handle_gateway_pairing_approve(request: web.Request) -> web.Response:
    data = await _read_json_body(request)
    if data is None:
        return _json_error(status=400, code="invalid_json", message="Request body must be a valid JSON object.")

    platform = _require_non_empty_string(data, "platform")
    if platform is None:
        return _json_error(status=400, code="invalid_platform", message="The 'platform' field must be a non-empty string.")

    code = _require_non_empty_string(data, "code")
    if code is None:
        return _json_error(status=400, code="invalid_code", message="The 'code' field must be a non-empty string.")

    service = _get_gateway_service(request)
    approved = service.approve_pairing(platform=platform, code=code)
    if approved is None:
        return _json_error(
            status=404,
            code="pairing_not_found",
            message="No pending pairing request was found for that platform/code.",
            platform=platform.lower(),
            pairing_code=code.upper(),
        )

    return web.json_response({"ok": True, "pairing": approved})


async def handle_gateway_pairing_revoke(request: web.Request) -> web.Response:
    data = await _read_json_body(request)
    if data is None:
        return _json_error(status=400, code="invalid_json", message="Request body must be a valid JSON object.")

    platform = _require_non_empty_string(data, "platform")
    if platform is None:
        return _json_error(status=400, code="invalid_platform", message="The 'platform' field must be a non-empty string.")

    user_id = _require_non_empty_string(data, "user_id")
    if user_id is None:
        return _json_error(status=400, code="invalid_user_id", message="The 'user_id' field must be a non-empty string.")

    service = _get_gateway_service(request)
    revoked = service.revoke_pairing(platform=platform, user_id=user_id)
    if not revoked:
        return _json_error(
            status=404,
            code="paired_user_not_found",
            message="No approved pairing entry was found for that platform/user.",
            platform=platform.lower(),
            user_id=user_id,
        )

    return web.json_response(
        {
            "ok": True,
            "pairing": {
                "platform": platform.lower(),
                "user_id": user_id,
                "revoked": True,
            },
        }
    )

async def handle_gateway_platform_config_get(request: web.Request) -> web.Response:
    platform_name = request.match_info.get("name")
    if not platform_name:
        return _json_error(status=400, code="missing_platform", message="Platform name is required.")
    
    settings_service = _get_settings_service(request)
    settings = settings_service.get_settings()
    platforms_config = settings.get("platforms", {})
    platform_config = platforms_config.get(platform_name, {})
    
    if "home_channel" in platform_config and isinstance(platform_config["home_channel"], dict):
        platform_config["home_channel"] = platform_config["home_channel"].get("chat_id", "")
        
    from hermes_cli.config import get_env_value

    env_map = {
        "telegram": "TELEGRAM_BOT_TOKEN",
        "discord": "DISCORD_BOT_TOKEN",
        "slack": "SLACK_BOT_TOKEN",
        "mattermost": "MATTERMOST_TOKEN",
        "matrix": "MATRIX_ACCESS_TOKEN",
        "homeassistant": "HASS_TOKEN",
    }
    
    env_var = env_map.get(platform_name.lower())
    if env_var:
        val = get_env_value(env_var)
        if val:
            platform_config["token"] = val

    if platform_name.lower() == "feishu":
        for key, env_var in [
            ("app_id", "FEISHU_APP_ID"),
            ("app_secret", "FEISHU_APP_SECRET"),
            ("encrypt_key", "FEISHU_ENCRYPT_KEY"),
            ("verification_token", "FEISHU_VERIFICATION_TOKEN"),
        ]:
            val = get_env_value(env_var)
            if val:
                platform_config[key] = val
    elif platform_name.lower() == "wecom":
        for key, env_var in [
            ("bot_id", "WECOM_BOT_ID"),
            ("secret", "WECOM_SECRET"),
        ]:
            val = get_env_value(env_var)
            if val:
                platform_config[key] = val
                
    return web.json_response({"ok": True, "config": platform_config})


async def handle_gateway_platform_config_patch(request: web.Request) -> web.Response:
    platform_name = request.match_info.get("name")
    if not platform_name:
        return _json_error(status=400, code="missing_platform", message="Platform name is required.")
        
    data = await _read_json_body(request)
    if data is None:
        return _json_error(status=400, code="invalid_json", message="Request body must be a valid JSON object.")
        
    settings_service = _get_settings_service(request)
    
    # Platform-specific env extraction
    if platform_name.lower() == "feishu":
        for key, env_var in [
            ("app_id", "FEISHU_APP_ID"),
            ("app_secret", "FEISHU_APP_SECRET"),
            ("encrypt_key", "FEISHU_ENCRYPT_KEY"),
            ("verification_token", "FEISHU_VERIFICATION_TOKEN"),
        ]:
            if key in data:
                save_env_value(env_var, data.pop(key))
    elif platform_name.lower() == "wecom":
        for key, env_var in [
            ("bot_id", "WECOM_BOT_ID"),
            ("secret", "WECOM_SECRET"),
        ]:
            if key in data:
                save_env_value(env_var, data.pop(key))

    home_channel_val = data.get("home_channel")
    if home_channel_val is not None:
        if isinstance(home_channel_val, str):
            if home_channel_val.strip():
                data["home_channel"] = {
                    "platform": platform_name.lower(),
                    "chat_id": home_channel_val,
                    "name": "Home"
                }
            else:
                data.pop("home_channel", None)

    # We must patch the token separately via env vars if it exists so it goes to .env
    token = data.pop("token", None)
    if token is not None:
        # Standardize env var names based on common conventions
        env_map = {
            "telegram": "TELEGRAM_BOT_TOKEN",
            "discord": "DISCORD_BOT_TOKEN",
            "slack": "SLACK_BOT_TOKEN",
            "mattermost": "MATTERMOST_TOKEN",
            "matrix": "MATRIX_ACCESS_TOKEN",
            "homeassistant": "HASS_TOKEN",
        }
        env_var = env_map.get(platform_name.lower())
        if env_var:
            save_env_value(env_var, token)
        else:
            # Fallback to saving in config if no standard env var
            data["token"] = token
            
    patch_payload = {
        "platforms": {
            platform_name: data
        }
    }
    
    try:
        updated = settings_service.update_settings(patch_payload)
    except Exception as exc:
        return _json_error(status=400, code="invalid_patch", message=str(exc))
        
    return web.json_response({
        "ok": True,
        "config": updated.get("platforms", {}).get(platform_name, {}),
        "reload_required": True
    })

async def handle_gateway_platform_start(request: web.Request) -> web.Response:
    platform_name = request.match_info.get("name")
    if not platform_name:
        return _json_error(status=400, code="missing_platform", message="Platform name is required.")
    
    settings_service = _get_settings_service(request)
    patch_payload = {
        "platforms": {
            platform_name: {"enabled": True}
        }
    }
    try:
        settings_service.update_settings(patch_payload)
    except Exception as exc:
        return _json_error(status=400, code="invalid_patch", message=str(exc))
        
    return web.json_response({"ok": True, "reload_required": True})

async def handle_gateway_platform_stop(request: web.Request) -> web.Response:
    platform_name = request.match_info.get("name")
    if not platform_name:
        return _json_error(status=400, code="missing_platform", message="Platform name is required.")
    
    settings_service = _get_settings_service(request)
    patch_payload = {
        "platforms": {
            platform_name: {"enabled": False}
        }
    }
    try:
        settings_service.update_settings(patch_payload)
    except Exception as exc:
        return _json_error(status=400, code="invalid_patch", message=str(exc))
        
    return web.json_response({"ok": True, "reload_required": True})


async def handle_gateway_restart(request: web.Request) -> web.Response:
    from gateway.web_console.routes import ADAPTER_APP_KEY

    adapter = request.app.get(ADAPTER_APP_KEY)
    message_handler = getattr(adapter, "_message_handler", None)
    runner = getattr(message_handler, "__self__", None)
    request_restart = getattr(runner, "request_restart", None)
    if not callable(request_restart):
        return _json_error(
            status=501,
            code="restart_unsupported",
            message="Gateway restart is not available from this web-console host.",
        )

    try:
        accepted = bool(request_restart(detached=True, via_service=False))
    except Exception as exc:
        return _json_error(status=500, code="restart_failed", message=str(exc))

    if not accepted:
        return web.json_response({
            "ok": True,
            "accepted": False,
            "message": "Gateway restart is already in progress.",
        })

    return web.json_response({
        "ok": True,
        "accepted": True,
        "message": "Gateway restart requested. Active runs will drain before restart.",
    })


def register_gateway_admin_api_routes(app: web.Application) -> None:
    if app.get(GATEWAY_SERVICE_APP_KEY) is None:
        app[GATEWAY_SERVICE_APP_KEY] = GatewayService()

    app.router.add_get("/api/gui/gateway/overview", handle_gateway_overview)
    app.router.add_get("/api/gui/gateway/platforms", handle_gateway_platforms)
    app.router.add_get("/api/gui/gateway/platforms/{name}/config", handle_gateway_platform_config_get)
    app.router.add_patch("/api/gui/gateway/platforms/{name}/config", handle_gateway_platform_config_patch)
    app.router.add_post("/api/gui/gateway/platforms/{name}/start", handle_gateway_platform_start)
    app.router.add_post("/api/gui/gateway/platforms/{name}/stop", handle_gateway_platform_stop)
    app.router.add_post("/api/gui/gateway/restart", handle_gateway_restart)
    app.router.add_get("/api/gui/gateway/pairing", handle_gateway_pairing)
    app.router.add_post("/api/gui/gateway/pairing/approve", handle_gateway_pairing_approve)
    app.router.add_post("/api/gui/gateway/pairing/revoke", handle_gateway_pairing_revoke)
