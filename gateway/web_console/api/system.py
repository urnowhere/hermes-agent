"""Backup and restore API routes for the Hermes Web Console.

Provides:
  GET  /api/gui/system/backup   — stream a zip backup of ~/.hermes/ to the browser
  POST /api/gui/system/restore  — accept a zip upload and restore into ~/.hermes/
"""

from __future__ import annotations

import asyncio
import io
import logging
import os
import tempfile
import time
import zipfile
from datetime import datetime
from pathlib import Path

from aiohttp import web

logger = logging.getLogger(__name__)


def _json_error(message: str, *, status: int = 400) -> web.Response:
    return web.json_response({"ok": False, "error": message}, status=status)


async def handle_snapshot_list(request: web.Request) -> web.Response:
    """List recent quick state snapshots."""
    try:
        from hermes_cli.backup import list_quick_snapshots

        raw_limit = request.query.get("limit", "20")
        try:
            limit = max(1, min(int(raw_limit), 100))
        except (TypeError, ValueError):
            limit = 20
        snapshots = list_quick_snapshots(limit=limit)
        return web.json_response({"ok": True, "snapshots": snapshots})
    except Exception as exc:
        logger.exception("Snapshot listing failed")
        return web.json_response({"ok": False, "error": str(exc)}, status=500)


async def handle_snapshot_create(request: web.Request) -> web.Response:
    """Create a quick state snapshot."""
    try:
        payload = await request.json() if request.can_read_body else {}
    except Exception:
        payload = {}

    raw_label = payload.get("label")
    label = str(raw_label).strip() if raw_label is not None else ""
    label = label or None

    try:
        from hermes_cli.backup import create_quick_snapshot, list_quick_snapshots

        snapshot_id = create_quick_snapshot(label=label)
        if not snapshot_id:
            return _json_error("No state files found to snapshot.", status=404)

        snapshot = next((item for item in list_quick_snapshots(limit=50) if item.get("id") == snapshot_id), None)
        return web.json_response({
            "ok": True,
            "snapshot_id": snapshot_id,
            "snapshot": snapshot,
            "message": f"Snapshot created: {snapshot_id}",
        })
    except Exception as exc:
        logger.exception("Snapshot creation failed")
        return web.json_response({"ok": False, "error": str(exc)}, status=500)


async def handle_snapshot_restore(request: web.Request) -> web.Response:
    """Restore state from a quick snapshot."""
    try:
        payload = await request.json() if request.can_read_body else {}
    except Exception:
        payload = {}

    snapshot_id = str(payload.get("snapshot_id") or "").strip()
    if not snapshot_id:
        return _json_error("snapshot_id is required", status=400)

    try:
        from hermes_cli.backup import restore_quick_snapshot

        restored = bool(restore_quick_snapshot(snapshot_id))
        if not restored:
            return _json_error(f"Snapshot not found: {snapshot_id}", status=404)
        return web.json_response({
            "ok": True,
            "snapshot_id": snapshot_id,
            "message": f"Restored snapshot {snapshot_id}. Restart recommended for state.db changes to take effect.",
        })
    except Exception as exc:
        logger.exception("Snapshot restore failed")
        return web.json_response({"ok": False, "error": str(exc)}, status=500)


async def handle_snapshot_prune(request: web.Request) -> web.Response:
    """Prune old quick snapshots while keeping the newest N."""
    try:
        payload = await request.json() if request.can_read_body else {}
    except Exception:
        payload = {}

    raw_keep = payload.get("keep", 20)
    try:
        keep = max(1, min(int(raw_keep), 500))
    except (TypeError, ValueError):
        return _json_error("keep must be an integer", status=400)

    try:
        from hermes_cli.backup import prune_quick_snapshots

        deleted = int(prune_quick_snapshots(keep=keep))
        return web.json_response({
            "ok": True,
            "deleted": deleted,
            "keep": keep,
            "message": f"Pruned {deleted} old snapshot(s) (keeping {keep}).",
        })
    except Exception as exc:
        logger.exception("Snapshot prune failed")
        return web.json_response({"ok": False, "error": str(exc)}, status=500)


async def handle_system_reload(request: web.Request) -> web.Response:
    """Reload ~/.hermes/.env into the running process environment."""
    try:
        from hermes_cli.config import reload_env

        updated = int(reload_env())
        return web.json_response({
            "ok": True,
            "updated": updated,
            "message": f"Reloaded .env ({updated} var(s) updated)",
        })
    except Exception as exc:
        logger.exception("Reload .env failed")
        return web.json_response({"ok": False, "error": str(exc)}, status=500)


async def handle_system_debug(request: web.Request) -> web.Response:
    """Collect a debug report and either upload it or return it locally."""
    try:
        payload = await request.json() if request.can_read_body else {}
    except Exception:
        payload = {}

    lines = payload.get("lines", 200)
    expire = payload.get("expire", 7)
    local = bool(payload.get("local", False))
    try:
        lines = max(10, min(int(lines), 2000))
    except (TypeError, ValueError):
        lines = 200
    try:
        expire = max(1, min(int(expire), 365))
    except (TypeError, ValueError):
        expire = 7

    loop = asyncio.get_running_loop()

    def _collect_debug_payload() -> dict:
        from hermes_cli.debug import _capture_dump, _read_full_log, collect_debug_report, upload_to_pastebin

        dump_text = _capture_dump()
        report = collect_debug_report(log_lines=lines, dump_text=dump_text)
        agent_log = _read_full_log("agent")
        gateway_log = _read_full_log("gateway")

        if agent_log:
            agent_log = dump_text + "\n\n--- full agent.log ---\n" + agent_log
        if gateway_log:
            gateway_log = dump_text + "\n\n--- full gateway.log ---\n" + gateway_log

        if local:
            return {
                "ok": True,
                "mode": "local",
                "report": report,
                "agent_log": agent_log,
                "gateway_log": gateway_log,
            }

        failures: list[str] = []
        report_url = upload_to_pastebin(report, expiry_days=expire)
        agent_log_url = None
        gateway_log_url = None

        if agent_log:
            try:
                agent_log_url = upload_to_pastebin(agent_log, expiry_days=expire)
            except Exception as exc:
                failures.append(f"agent.log: {exc}")

        if gateway_log:
            try:
                gateway_log_url = upload_to_pastebin(gateway_log, expiry_days=expire)
            except Exception as exc:
                failures.append(f"gateway.log: {exc}")

        return {
            "ok": True,
            "mode": "upload",
            "report_url": report_url,
            "agent_log_url": agent_log_url,
            "gateway_log_url": gateway_log_url,
            "failures": failures,
        }

    try:
        result = await loop.run_in_executor(None, _collect_debug_payload)
        return web.json_response(result)
    except Exception as exc:
        logger.exception("Debug report generation failed")
        return web.json_response({"ok": False, "error": str(exc)}, status=500)


async def handle_system_backup(request: web.Request) -> web.StreamResponse:
    """Stream a zip backup of HERMES_HOME to the browser."""
    loop = asyncio.get_running_loop()

    def _create_backup_bytes() -> tuple[bytes, int, str]:
        from hermes_constants import get_default_hermes_root
        from hermes_cli.backup import _should_exclude, _EXCLUDED_DIRS

        hermes_root = get_default_hermes_root()
        if not hermes_root.is_dir():
            raise FileNotFoundError(f"Hermes home not found: {hermes_root}")

        stamp = datetime.now().strftime("%Y-%m-%d-%H%M%S")
        filename = f"hermes-backup-{stamp}.zip"

        buf = io.BytesIO()
        file_count = 0
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
            for dirpath, dirnames, filenames in os.walk(hermes_root, followlinks=False):
                dp = Path(dirpath)
                rel_dir = dp.relative_to(hermes_root)
                dirnames[:] = [d for d in dirnames if d not in _EXCLUDED_DIRS]

                for fname in filenames:
                    fpath = dp / fname
                    rel = fpath.relative_to(hermes_root)
                    if _should_exclude(rel):
                        continue
                    try:
                        zf.write(fpath, arcname=str(rel))
                        file_count += 1
                    except (PermissionError, OSError):
                        continue

        return buf.getvalue(), file_count, filename

    try:
        data, file_count, filename = await loop.run_in_executor(None, _create_backup_bytes)
    except FileNotFoundError as e:
        return web.json_response({"ok": False, "error": str(e)}, status=404)
    except Exception as e:
        logger.exception("Backup creation failed")
        return web.json_response({"ok": False, "error": str(e)}, status=500)

    response = web.StreamResponse(
        status=200,
        headers={
            "Content-Type": "application/zip",
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Content-Length": str(len(data)),
            "X-Backup-Files": str(file_count),
        },
    )
    await response.prepare(request)
    await response.write(data)
    await response.write_eof()
    return response


async def handle_system_restore(request: web.Request) -> web.Response:
    """Accept a zip upload and restore it into HERMES_HOME."""
    reader = await request.multipart()
    if reader is None:
        return web.json_response(
            {"ok": False, "error": "Expected multipart/form-data with a 'file' part"},
            status=400,
        )

    zip_data = None
    while True:
        part = await reader.next()
        if part is None:
            break
        if part.name == "file":
            zip_data = await part.read(decode=False)
            break

    if zip_data is None:
        return web.json_response(
            {"ok": False, "error": "No 'file' part found in upload"},
            status=400,
        )

    loop = asyncio.get_running_loop()

    def _do_restore() -> dict:
        from hermes_constants import get_default_hermes_root
        from hermes_cli.backup import _validate_backup_zip, _detect_prefix

        hermes_root = get_default_hermes_root()
        hermes_root.mkdir(parents=True, exist_ok=True)

        buf = io.BytesIO(zip_data)
        if not zipfile.is_zipfile(buf):
            return {"ok": False, "error": "Uploaded data is not a valid zip file"}

        buf.seek(0)
        with zipfile.ZipFile(buf, "r") as zf:
            ok, reason = _validate_backup_zip(zf)
            if not ok:
                return {"ok": False, "error": reason}

            prefix = _detect_prefix(zf)
            members = [n for n in zf.namelist() if not n.endswith("/")]

            errors = []
            restored = 0

            for member in members:
                if prefix and member.startswith(prefix):
                    rel = member[len(prefix):]
                else:
                    rel = member

                if not rel:
                    continue

                target = hermes_root / rel

                # Security: reject absolute paths and traversals
                try:
                    target.resolve().relative_to(hermes_root.resolve())
                except ValueError:
                    errors.append(f"{rel}: path traversal blocked")
                    continue

                try:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with zf.open(member) as src, open(target, "wb") as dst:
                        dst.write(src.read())
                    restored += 1
                except (PermissionError, OSError) as exc:
                    errors.append(f"{rel}: {exc}")

        return {
            "ok": True,
            "restored": restored,
            "total": len(members),
            "errors": errors[:20],
        }

    try:
        result = await loop.run_in_executor(None, _do_restore)
    except Exception as e:
        logger.exception("Restore failed")
        return web.json_response({"ok": False, "error": str(e)}, status=500)

    return web.json_response(result)


def register_system_api_routes(app: web.Application) -> None:
    """Register system backup/restore API routes."""
    app.router.add_get("/api/gui/system/snapshots", handle_snapshot_list)
    app.router.add_post("/api/gui/system/snapshots", handle_snapshot_create)
    app.router.add_post("/api/gui/system/snapshots/restore", handle_snapshot_restore)
    app.router.add_post("/api/gui/system/snapshots/prune", handle_snapshot_prune)
    app.router.add_post("/api/gui/system/reload", handle_system_reload)
    app.router.add_post("/api/gui/system/debug", handle_system_debug)
    app.router.add_get("/api/gui/system/backup", handle_system_backup)
    app.router.add_post("/api/gui/system/restore", handle_system_restore)
