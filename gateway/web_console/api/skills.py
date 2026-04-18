"""Skills API routes for the Hermes Web Console backend."""

from __future__ import annotations

import json
from typing import Any

from aiohttp import web

from gateway.web_console.services.skill_service import SkillService

SKILLS_SERVICE_APP_KEY = web.AppKey("hermes_web_console_skills_service", SkillService)


def _json_error(*, status: int, code: str, message: str, **extra: Any) -> web.Response:
    payload: dict[str, Any] = {
        "ok": False,
        "error": {
            "code": code,
            "message": message,
        },
    }
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


def _get_skill_service(request: web.Request) -> SkillService:
    service = request.app.get(SKILLS_SERVICE_APP_KEY)
    if service is None:
        service = SkillService()
        request.app[SKILLS_SERVICE_APP_KEY] = service
    return service


async def handle_list_skills(request: web.Request) -> web.Response:
    service = _get_skill_service(request)
    try:
        payload = service.list_skills()
    except Exception as exc:
        return _json_error(status=500, code="skills_list_failed", message=str(exc))
    return web.json_response({"ok": True, **payload})


async def handle_get_skill(request: web.Request) -> web.Response:
    service = _get_skill_service(request)
    name = request.match_info["name"]
    try:
        skill = service.get_skill(name)
    except FileNotFoundError:
        return _json_error(status=404, code="skill_not_found", message="No skill was found for the provided name.", name=name)
    except ValueError as exc:
        return _json_error(status=400, code="skill_unavailable", message=str(exc), name=name)
    except Exception as exc:
        return _json_error(status=500, code="skill_lookup_failed", message=str(exc), name=name)
    return web.json_response({"ok": True, "skill": skill})


async def handle_load_skill(request: web.Request) -> web.Response:
    data = await _read_json_body(request)
    if data is None:
        return _json_error(status=400, code="invalid_json", message="Request body must be a valid JSON object.")

    session_id = data.get("session_id")
    if not isinstance(session_id, str) or not session_id.strip():
        return _json_error(status=400, code="invalid_session_id", message="The 'session_id' field must be a non-empty string.")

    service = _get_skill_service(request)
    name = request.match_info["name"]
    try:
        payload = service.load_skill_for_session(session_id.strip(), name)
    except LookupError:
        return _json_error(status=404, code="session_not_found", message="No session was found for the provided session_id.", session_id=session_id)
    except FileNotFoundError:
        return _json_error(status=404, code="skill_not_found", message="No skill was found for the provided name.", name=name)
    except ValueError as exc:
        return _json_error(status=400, code="skill_unavailable", message=str(exc), name=name)
    except Exception as exc:
        return _json_error(status=500, code="skill_load_failed", message=str(exc), name=name, session_id=session_id)
    return web.json_response({"ok": True, **payload})


async def handle_list_session_skills(request: web.Request) -> web.Response:
    service = _get_skill_service(request)
    session_id = request.match_info["session_id"]
    try:
        payload = service.list_session_skills(session_id)
    except LookupError:
        return _json_error(status=404, code="session_not_found", message="No session was found for the provided session_id.", session_id=session_id)
    except Exception as exc:
        return _json_error(status=500, code="session_skills_lookup_failed", message=str(exc), session_id=session_id)
    return web.json_response({"ok": True, **payload})


async def handle_unload_skill(request: web.Request) -> web.Response:
    service = _get_skill_service(request)
    session_id = request.match_info["session_id"]
    name = request.match_info["name"]
    try:
        payload = service.unload_skill_for_session(session_id, name)
    except LookupError:
        return _json_error(status=404, code="session_not_found", message="No session was found for the provided session_id.", session_id=session_id)
    except Exception as exc:
        return _json_error(status=500, code="skill_unload_failed", message=str(exc), session_id=session_id, name=name)
    return web.json_response({"ok": True, **payload})


async def handle_hub_search(request: web.Request) -> web.Response:
    query = request.query.get("q", "")
    from tools.skills_hub import GitHubAuth, create_source_router, unified_search
    try:
        auth = GitHubAuth()
        sources = create_source_router(auth)
        results = unified_search(query, sources, source_filter="all", limit=20)
        payload = {"results": [{"name": r.name, "description": r.description, "source": r.source, "trust_level": r.trust_level, "identifier": r.identifier, "tags": r.tags} for r in results]}
    except Exception as exc:
        return _json_error(status=500, code="hub_search_failed", message=str(exc))
    return web.json_response({"ok": True, **payload})

async def handle_hub_install(request: web.Request) -> web.Response:
    data = await _read_json_body(request)
    if not data or not data.get("identifier"):
        return _json_error(status=400, code="invalid_request", message="Missing identifier")
    identifier = data["identifier"]
    
    try:
        from tools.skills_hub import GitHubAuth, create_source_router, ensure_hub_dirs, quarantine_bundle, install_from_quarantine, HubLockFile
        from tools.skills_guard import scan_skill, should_allow_install
        import shutil

        ensure_hub_dirs()
        auth = GitHubAuth()
        sources = create_source_router(auth)
        
        from hermes_cli.skills_hub import _resolve_source_meta_and_bundle, _resolve_short_name
        if "/" not in identifier:
            from rich.console import Console
            identifier = _resolve_short_name(identifier, sources, Console(quiet=True))
            if not identifier:
                return _json_error(status=404, code="skill_not_found", message="Skill not found or ambiguous")
                
        meta, bundle, matched_source = _resolve_source_meta_and_bundle(identifier, sources)
        if not bundle:
            return _json_error(status=404, code="fetch_failed", message="Could not fetch bundle")

        category = ""
        if bundle.source == "official":
            id_parts = bundle.identifier.split("/")
            if len(id_parts) >= 3:
                category = id_parts[1]

        lock = HubLockFile()
        if lock.get_installed(bundle.name):
            return _json_error(status=409, code="already_installed", message="Skill is already installed")

        q_path = quarantine_bundle(bundle)
        scan_source = getattr(bundle, "identifier", identifier)
        result = scan_skill(q_path, source=scan_source)
        allowed, reason = should_allow_install(result, force=False)
        
        if not allowed:
            shutil.rmtree(q_path, ignore_errors=True)
            return _json_error(status=403, code="scan_failed", message=reason, scan_result=result.verdict)

        install_dir = install_from_quarantine(q_path, bundle.name, category, bundle, result)
        
        try:
            from agent.prompt_builder import clear_skills_system_prompt_cache
            clear_skills_system_prompt_cache(clear_snapshot=True)
        except Exception:
            pass

        return web.json_response({"ok": True, "installed": bundle.name, "path": str(install_dir)})
    except Exception as exc:
        return _json_error(status=500, code="install_error", message=str(exc))

async def handle_skill_create(request: web.Request) -> web.Response:
    data = await _read_json_body(request)
    if not data or not data.get("name") or not data.get("content"):
        return _json_error(status=400, code="invalid_request", message="Missing name or content")
    
    name = data["name"].strip()
    content = data["content"].strip()
    
    import re
    if not re.match(r"^[a-zA-Z0-9_-]+$", name):
        return _json_error(status=400, code="invalid_name", message="Invalid skill name (alphanumeric, dash, underscore only)")
        
    from tools.skills_hub import SKILLS_DIR
    skill_path = SKILLS_DIR / name
    if skill_path.exists():
        return _json_error(status=409, code="already_exists", message=f"Skill directory {name} already exists")
        
    try:
        skill_path.mkdir(parents=True, exist_ok=True)
        (skill_path / "SKILL.md").write_text(content, encoding="utf-8")
        
        try:
            from agent.prompt_builder import clear_skills_system_prompt_cache
            clear_skills_system_prompt_cache(clear_snapshot=True)
        except Exception:
            pass
            
        return web.json_response({"ok": True, "name": name})
    except Exception as exc:
        return _json_error(status=500, code="create_error", message=str(exc))

async def handle_skill_config_vars(request: web.Request) -> web.Response:
    """GET /api/gui/skills/config — list all skill config variables with current values."""
    try:
        from agent.skill_utils import discover_all_skill_config_vars, resolve_skill_config_values
        config_vars = discover_all_skill_config_vars()
        values = resolve_skill_config_values(config_vars)
        items = []
        for var in config_vars:
            items.append({
                "key": var["key"],
                "description": var["description"],
                "default": var.get("default"),
                "prompt": var.get("prompt"),
                "skill": var.get("skill"),
                "value": values.get(var["key"]),
            })
        return web.json_response({"ok": True, "config_vars": items, "count": len(items)})
    except Exception as exc:
        return _json_error(status=500, code="config_vars_failed", message=str(exc))


async def handle_skill_config_save(request: web.Request) -> web.Response:
    """POST /api/gui/skills/config — save a skill config variable value."""
    data = await _read_json_body(request)
    if not data or "key" not in data or "value" not in data:
        return _json_error(status=400, code="invalid_request", message="Missing 'key' or 'value'")
    key = str(data["key"]).strip()
    value = data["value"]
    if not key:
        return _json_error(status=400, code="invalid_key", message="Config key must be non-empty")
    try:
        from agent.skill_utils import SKILL_CONFIG_PREFIX
        from hermes_constants import get_hermes_home
        import yaml

        config_path = get_hermes_home() / "config.yaml"
        config = {}
        if config_path.exists():
            raw = config_path.read_text(encoding="utf-8")
            parsed = yaml.safe_load(raw)
            if isinstance(parsed, dict):
                config = parsed

        # Navigate to the storage path: skills.config.<key>
        storage_key = f"{SKILL_CONFIG_PREFIX}.{key}"
        parts = storage_key.split(".")
        current = config
        for part in parts[:-1]:
            if part not in current or not isinstance(current.get(part), dict):
                current[part] = {}
            current = current[part]
        current[parts[-1]] = value

        config_path.write_text(yaml.dump(config, default_flow_style=False), encoding="utf-8")
        return web.json_response({"ok": True, "key": key, "value": value})
    except Exception as exc:
        return _json_error(status=500, code="config_save_failed", message=str(exc))


def register_skills_api_routes(app: web.Application) -> None:
    if app.get(SKILLS_SERVICE_APP_KEY) is None:
        app[SKILLS_SERVICE_APP_KEY] = SkillService()

    app.router.add_get("/api/gui/skills", handle_list_skills)
    app.router.add_get("/api/gui/skills/config", handle_skill_config_vars)
    app.router.add_post("/api/gui/skills/config", handle_skill_config_save)
    app.router.add_get("/api/gui/skills/hub/search", handle_hub_search)
    app.router.add_post("/api/gui/skills/hub/install", handle_hub_install)
    app.router.add_post("/api/gui/skills/create", handle_skill_create)
    app.router.add_get("/api/gui/skills/{name}", handle_get_skill)
    app.router.add_post("/api/gui/skills/{name}/load", handle_load_skill)
    app.router.add_get("/api/gui/skills/session/{session_id}", handle_list_session_skills)
    app.router.add_delete("/api/gui/skills/session/{session_id}/{name}", handle_unload_skill)

