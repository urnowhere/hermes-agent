"""Credential Pool API routes for the Hermes Web Console backend.

Provides:
  GET    /api/gui/credentials/pool           — list all credential pool entries for a provider
  POST   /api/gui/credentials/pool           — add a new credential to the pool
  DELETE /api/gui/credentials/pool/:entry_id — remove a credential from the pool
  POST   /api/gui/credentials/device-auth    — initiate device code auth flow (Codex/ChatGPT)
  POST   /api/gui/credentials/device-auth/poll — poll for device code completion
"""

from __future__ import annotations

import logging
import time
from aiohttp import web

logger = logging.getLogger(__name__)


async def handle_list_pool(request: web.Request) -> web.Response:
    """GET /api/gui/credentials/pool?provider=openai-codex — list credential pool entries."""
    provider = request.query.get("provider", "openai-codex")

    try:
        from hermes_cli.auth import read_credential_pool, _load_auth_store
        raw_entries = read_credential_pool(provider)

        active_token = None
        if provider == "openai-codex":
            try:
                from hermes_cli.auth import _read_codex_tokens
                codex_store = _read_codex_tokens()
                active_token = codex_store.get("tokens", {}).get("access_token")
            except Exception:
                pass
        else:
            try:
                store = _load_auth_store()
                provider_data = store.get("providers", {}).get(provider, {})
                if isinstance(provider_data, dict):
                    active_token = provider_data.get("access_token")
            except Exception:
                pass

        entries = []
        if isinstance(raw_entries, list):
            for entry in raw_entries:
                if not isinstance(entry, dict):
                    continue
                entries.append({
                    "id": entry.get("id", ""),
                    "label": entry.get("label", ""),
                    "auth_type": entry.get("auth_type", ""),
                    "source": entry.get("source", ""),
                    "priority": entry.get("priority", 0),
                    "last_status": entry.get("last_status"),
                    "last_status_at": entry.get("last_status_at"),
                    "last_error_code": entry.get("last_error_code"),
                    "last_error_reason": entry.get("last_error_reason"),
                    "request_count": entry.get("request_count", 0),
                    "has_token": bool(entry.get("access_token")),
                    "is_active": bool(active_token and entry.get("access_token") == active_token),
                })

        return web.json_response({"ok": True, "provider": provider, "entries": entries})
    except Exception as exc:
        logger.warning("list_pool failed: %s", exc)
        return web.json_response({"ok": False, "error": str(exc)}, status=500)


async def handle_delete_pool_entry(request: web.Request) -> web.Response:
    """DELETE /api/gui/credentials/pool/:entry_id — remove a credential."""
    entry_id = request.match_info.get("entry_id", "")
    provider = request.query.get("provider", "openai-codex")

    if not entry_id:
        return web.json_response({"ok": False, "error": "entry_id is required"}, status=400)

    try:
        from hermes_cli.auth import read_credential_pool, write_credential_pool
        raw_entries = read_credential_pool(provider)
        if not isinstance(raw_entries, list):
            return web.json_response({"ok": False, "error": "No pool found"}, status=404)

        filtered = [e for e in raw_entries if isinstance(e, dict) and e.get("id") != entry_id]
        if len(filtered) == len(raw_entries):
            return web.json_response({"ok": False, "error": "Entry not found"}, status=404)

        write_credential_pool(provider, filtered)
        return web.json_response({"ok": True, "removed": entry_id})
    except Exception as exc:
        logger.warning("delete_pool_entry failed: %s", exc)
        return web.json_response({"ok": False, "error": str(exc)}, status=500)


async def handle_activate_pool_entry(request: web.Request) -> web.Response:
    """POST /api/gui/credentials/pool/:entry_id/activate — set credential as active."""
    entry_id = request.match_info.get("entry_id", "")
    provider = request.query.get("provider", "openai-codex")

    if not entry_id:
        return web.json_response({"ok": False, "error": "entry_id is required"}, status=400)

    try:
        from hermes_cli.auth import read_credential_pool
        raw_entries = read_credential_pool(provider)
        if not isinstance(raw_entries, list):
            return web.json_response({"ok": False, "error": "No pool found"}, status=404)

        entry = next((e for e in raw_entries if isinstance(e, dict) and e.get("id") == entry_id), None)
        if not entry:
            return web.json_response({"ok": False, "error": "Entry not found"}, status=404)

        if provider == "openai-codex":
            from hermes_cli.auth import _save_codex_tokens
            _save_codex_tokens(
                {
                    "access_token": entry.get("access_token"),
                    "refresh_token": entry.get("refresh_token")
                },
                last_refresh=entry.get("last_refresh") or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            )
            
            # Switch the active provider to openai-codex natively
            try:
                from hermes_cli.config import load_config, save_config
                from hermes_cli.model_switch import detect_provider_for_model
                cfg = load_config()
                cfg["provider"] = "openai-codex"
                
                old_model = cfg.get("model", {})
                old_model_name = old_model if isinstance(old_model, str) else old_model.get("default", "")
                
                if old_model_name:
                    detected, _ = detect_provider_for_model(old_model_name, "openai-codex")
                    if detected not in ("openai", "openai-codex", "custom"):
                        if "model" not in cfg:
                            cfg["model"] = {}
                        if isinstance(cfg["model"], dict):
                            cfg["model"]["default"] = "gpt-5.4"
                            cfg["model"]["name"] = "gpt-5.4"
                            
                save_config(cfg)
            except Exception as exc:
                logger.warning("Failed to update config.yaml during codex activation: %s", exc)
        else:
            from hermes_cli.auth import _save_provider_state, _load_auth_store, _save_auth_store
            store = _load_auth_store()
            _save_provider_state(store, provider, {
                "access_token": entry.get("access_token"),
                "refresh_token": entry.get("refresh_token"),
                "last_refresh": entry.get("last_refresh"),
                "auth_mode": entry.get("source", "oauth"),
                "source": "gui",
            })
            _save_auth_store(store)
        return web.json_response({"ok": True, "activated": entry_id})
    except Exception as exc:
        logger.warning("activate_pool_entry failed: %s", exc)
        return web.json_response({"ok": False, "error": str(exc)}, status=500)


# --- Device Code Auth Flow ---

# In-flight device code sessions (keyed by device_code)
_device_sessions: dict[str, dict] = {}


async def handle_device_auth_start(request: web.Request) -> web.Response:
    """POST /api/gui/credentials/device-auth — start a Codex device code flow.

    Returns the user_code and verification_uri for the user to authorize in browser.
    """
    try:
        body = await request.json()
    except Exception:
        body = {}

    provider = body.get("provider", "openai-codex")

    if provider != "openai-codex":
        return web.json_response(
            {"ok": False, "error": f"Device auth not supported for {provider}"},
            status=400,
        )

    try:
        from hermes_cli.auth import _codex_device_code_request
        result = _codex_device_code_request()

        # Store the session for polling
        device_auth_id = result.get("device_auth_id", "")
        # OpenAI doesn't return these in the new schema, so we hardcode them
        verification_uri = "https://auth.openai.com/codex/device"
        user_code = result.get("user_code", "")
        interval = int(result.get("interval", "5"))

        _device_sessions[device_auth_id] = {
            "provider": provider,
            "device_code": device_auth_id,
            "user_code": user_code,
            "verification_uri": verification_uri,
            "verification_uri_complete": verification_uri,
            "expires_in": 900,  # 15 min default
            "interval": interval,
            "started_at": time.time(),
        }

        return web.json_response({
            "ok": True,
            "device_code": device_auth_id,
            "user_code": user_code,
            "verification_uri": verification_uri,
            "verification_uri_complete": verification_uri,
            "expires_in": 900,
            "interval": interval,
        })
    except Exception as exc:
        logger.warning("device_auth_start failed: %s", exc)
        return web.json_response({"ok": False, "error": str(exc)}, status=500)


async def handle_device_auth_poll(request: web.Request) -> web.Response:
    """POST /api/gui/credentials/device-auth/poll — poll for device auth completion.

    Body: {"device_code": "..."}
    Returns: {"status": "pending|complete|expired|error", ...}
    """
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"ok": False, "error": "Invalid JSON"}, status=400)

    device_code = body.get("device_code", "")
    label = body.get("label", "")

    session = _device_sessions.get(device_code)
    if not session:
        return web.json_response(
            {"ok": False, "status": "error", "error": "Unknown device code session"},
            status=404,
        )

    # Check expiration
    elapsed = time.time() - session["started_at"]
    if elapsed > session["expires_in"]:
        _device_sessions.pop(device_code, None)
        return web.json_response({"ok": True, "status": "expired"})

    try:
        from hermes_cli.auth import _codex_device_code_poll
        poll_result = _codex_device_code_poll(device_code, session["user_code"])

        if poll_result is None:
            return web.json_response({"ok": True, "status": "pending"})

        # Success! We got tokens
        access_token = poll_result.get("access_token", "")
        refresh_token = poll_result.get("refresh_token", "")

        # Auto-label from JWT
        if not label:
            try:
                from hermes_cli.auth import _decode_jwt_claims
                claims = _decode_jwt_claims(access_token)
                label = claims.get("email") or claims.get("preferred_username") or "ChatGPT account"
            except Exception:
                label = "ChatGPT account"

        # Add to credential pool
        import uuid
        entry_id = uuid.uuid4().hex[:6]
        from hermes_cli.auth import read_credential_pool, write_credential_pool
        existing = read_credential_pool("openai-codex")
        if not isinstance(existing, list):
            existing = []

        new_entry = {
            "id": entry_id,
            "label": label,
            "auth_type": "oauth",
            "source": "chatgpt",
            "priority": len(existing),
            "access_token": access_token,
            "refresh_token": refresh_token,
            "last_refresh": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "request_count": 0,
        }
        existing.append(new_entry)
        write_credential_pool("openai-codex", existing)

        # Also persist to the legacy provider state for backward compatibility
        try:
            from hermes_cli.auth import _save_codex_tokens
            _save_codex_tokens({
                "access_token": access_token,
                "refresh_token": refresh_token,
            }, last_refresh=new_entry["last_refresh"])
        except Exception as exc:
            logger.warning("Failed to save codex tokens: %s", exc)

        _device_sessions.pop(device_code, None)

        return web.json_response({
            "ok": True,
            "status": "complete",
            "entry_id": entry_id,
            "label": label,
        })
    except Exception as exc:
        logger.warning("device_auth_poll failed: %s", exc)
        return web.json_response({
            "ok": True,
            "status": "error",
            "error": str(exc),
        })


def register_credentials_api_routes(app: web.Application) -> None:
    app.router.add_get("/api/gui/credentials/pool", handle_list_pool)
    app.router.add_delete("/api/gui/credentials/pool/{entry_id}", handle_delete_pool_entry)
    app.router.add_post("/api/gui/credentials/pool/{entry_id}/activate", handle_activate_pool_entry)
    app.router.add_post("/api/gui/credentials/device-auth", handle_device_auth_start)
    app.router.add_post("/api/gui/credentials/device-auth/poll", handle_device_auth_poll)
