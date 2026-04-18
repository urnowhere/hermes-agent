from aiohttp import web
from dataclasses import asdict
from hermes_cli.profiles import (
    list_profiles,
    create_profile,
    delete_profile,
    get_active_profile,
    set_active_profile,
    export_profile,
    import_profile,
)
import tempfile
import os

async def handle_get_profiles(request: web.Request) -> web.Response:
    profiles = list_profiles()
    active_profile = get_active_profile()
    
    # Convert ProfileInfo dataclasses to dicts
    profile_list = []
    for p in profiles:
        pd = asdict(p)
        pd["path"] = str(pd["path"])
        if pd["alias_path"]:
            pd["alias_path"] = str(pd["alias_path"])
        pd["is_active"] = p.name == active_profile
        profile_list.append(pd)
        
    return web.json_response({"ok": True, "profiles": profile_list, "active_profile": active_profile})


async def handle_post_profile(request: web.Request) -> web.Response:
    data = await request.json()
    name = data.get("name")
    if not name:
        return web.json_response({"ok": False, "error": "Profile name is required"}, status=400)
        
    try:
        new_dir = create_profile(
            name=name,
            clone_from=data.get("clone_from"),
            clone_all=data.get("clone_all", False),
            clone_config=data.get("clone_config", False),
            no_alias=data.get("no_alias", False),
        )
        return web.json_response({"ok": True, "path": str(new_dir)})
    except Exception as e:
        return web.json_response({"ok": False, "error": str(e)}, status=400)


async def handle_delete_profile(request: web.Request) -> web.Response:
    name = request.match_info.get("name")
    if not name:
        return web.json_response({"ok": False, "error": "Profile name is required"}, status=400)
    
    try:
        deleted_dir = delete_profile(name, yes=True)
        return web.json_response({"ok": True, "path": str(deleted_dir)})
    except Exception as e:
        return web.json_response({"ok": False, "error": str(e)}, status=400)


async def handle_set_active_profile(request: web.Request) -> web.Response:
    data = await request.json()
    name = data.get("name")
    if not name:
        return web.json_response({"ok": False, "error": "Profile name is required"}, status=400)
        
    try:
        set_active_profile(name)
        return web.json_response({"ok": True})
    except Exception as e:
        return web.json_response({"ok": False, "error": str(e)}, status=400)

async def handle_export_profile(request: web.Request) -> web.Response:
    name = request.match_info.get("name")
    if not name:
        return web.json_response({"ok": False, "error": "Profile name is required"}, status=400)
    
    fd, temp_path = tempfile.mkstemp(suffix=".tar.gz")
    os.close(fd)
    
    try:
        export_profile(name, temp_path)
        
        with open(temp_path, "rb") as f:
            data = f.read()
            
        response = web.Response(body=data)
        response.headers['Content-Disposition'] = f'attachment; filename="profile_{name}.tar.gz"'
        response.headers['Content-Type'] = 'application/gzip'
        return response
    except Exception as e:
        return web.json_response({"ok": False, "error": str(e)}, status=400)
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)

async def handle_import_profile(request: web.Request) -> web.Response:
    try:
        reader = await request.multipart()
        name = None
        file_data = None
        
        while True:
            field = await reader.next()
            if field is None:
                break
            
            if field.name == 'name':
                name = await field.read()
                name = name.decode('utf-8').strip()
            elif field.name == 'file':
                file_data = await field.read()
                
        if not file_data:
            return web.json_response({"ok": False, "error": "No file data uploaded"}, status=400)
            
        fd, temp_path = tempfile.mkstemp(suffix=".tar.gz")
        os.close(fd)
        
        try:
            with open(temp_path, "wb") as f:
                f.write(file_data)
                
            # If name is empty string or None, import_profile infers it
            import_name = name if name else None
            imported_dir = import_profile(temp_path, import_name)
            
            return web.json_response({"ok": True, "path": str(imported_dir)})
        except Exception as e:
            return web.json_response({"ok": False, "error": str(e)}, status=400)
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)
    except Exception as e:
        return web.json_response({"ok": False, "error": str(e)}, status=400)


def register_profiles_api_routes(app: web.Application) -> None:
    app.router.add_get("/api/gui/profiles", handle_get_profiles)
    app.router.add_post("/api/gui/profiles", handle_post_profile)
    app.router.add_delete("/api/gui/profiles/{name}", handle_delete_profile)
    app.router.add_post("/api/gui/profiles/active", handle_set_active_profile)
    app.router.add_get("/api/gui/profiles/{name}/export", handle_export_profile)
    app.router.add_post("/api/gui/profiles/import", handle_import_profile)
