"""Bounded MCP facade for Hermes Kanban.

This module intentionally does *not* expose the dispatcher-only ``kanban_*``
worker tools. External MCP clients get a separate, board-scoped facade that
routes every write through ``hermes_cli.kanban_db`` APIs and never touches raw
SQLite directly.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import sys
import threading
from typing import Any, Callable, Optional

from hermes_cli import kanban_db as kb

logger = logging.getLogger("hermes.kanban_mcp")

WRITE_MODE_READONLY = "readonly"
WRITE_MODE_SAFE = "safe"
WRITE_MODE_OPERATOR = "operator"
_ALLOWED_WRITE_MODES = {WRITE_MODE_READONLY, WRITE_MODE_SAFE, WRITE_MODE_OPERATOR}

_MCP_SERVER_AVAILABLE = False
_BOARD_ENV_LOCK = threading.RLock()
try:
    from mcp.server.fastmcp import FastMCP

    _MCP_SERVER_AVAILABLE = True
except ImportError:  # pragma: no cover - exercised by runtime error path
    FastMCP = None  # type: ignore[assignment,misc]


def _write_mode() -> str:
    raw = os.environ.get("HERMES_KANBAN_MCP_WRITE_MODE", WRITE_MODE_SAFE)
    mode = str(raw or "").strip().lower() or WRITE_MODE_SAFE
    if mode not in _ALLOWED_WRITE_MODES:
        return WRITE_MODE_READONLY
    return mode


def _allowed_boards() -> Optional[set[str]]:
    """Return the optional MCP board allowlist from the environment.

    ``HERMES_KANBAN_MCP_ALLOWED_BOARDS`` is the specific knob for this facade;
    ``HERMES_KANBAN_ALLOWED_BOARDS`` is accepted for compatibility with early
    design notes and external config snippets.
    """

    raw = os.environ.get("HERMES_KANBAN_MCP_ALLOWED_BOARDS")
    if raw is None:
        raw = os.environ.get("HERMES_KANBAN_ALLOWED_BOARDS")
    if raw is None or not str(raw).strip():
        return None
    allowed: set[str] = set()
    for part in str(raw).split(","):
        name = part.strip()
        if not name:
            continue
        try:
            allowed.add(kb._normalize_board_slug(name))
        except ValueError:
            logger.warning("Ignoring invalid Kanban MCP allowed board slug %r", name)
    return allowed


def _task_to_dict(t: kb.Task) -> dict[str, Any]:
    return {
        "id": t.id,
        "title": t.title,
        "body": t.body,
        "assignee": t.assignee,
        "status": t.status,
        "priority": t.priority,
        "tenant": t.tenant,
        "workspace_kind": t.workspace_kind,
        "workspace_path": t.workspace_path,
        "claim_lock": t.claim_lock,
        "claim_expires": t.claim_expires,
        "created_by": t.created_by,
        "created_at": t.created_at,
        "started_at": t.started_at,
        "completed_at": t.completed_at,
        "result": t.result,
        "idempotency_key": t.idempotency_key,
        "spawn_failures": t.spawn_failures,
        "worker_pid": t.worker_pid,
        "last_spawn_error": t.last_spawn_error,
        "max_runtime_seconds": t.max_runtime_seconds,
        "last_heartbeat_at": t.last_heartbeat_at,
        "current_run_id": t.current_run_id,
        "workflow_template_id": t.workflow_template_id,
        "current_step_key": t.current_step_key,
        "skills": list(t.skills) if t.skills else [],
    }


def _comment_to_dict(c: kb.Comment) -> dict[str, Any]:
    return {
        "id": c.id,
        "task_id": c.task_id,
        "author": c.author,
        "body": c.body,
        "created_at": c.created_at,
    }


def _event_to_dict(e: kb.Event) -> dict[str, Any]:
    return {
        "id": e.id,
        "task_id": e.task_id,
        "kind": e.kind,
        "payload": e.payload,
        "created_at": e.created_at,
        "run_id": e.run_id,
    }


def _run_to_dict(r: kb.Run) -> dict[str, Any]:
    return {
        "id": r.id,
        "task_id": r.task_id,
        "profile": r.profile,
        "step_key": r.step_key,
        "status": r.status,
        "claim_lock": r.claim_lock,
        "claim_expires": r.claim_expires,
        "worker_pid": r.worker_pid,
        "max_runtime_seconds": r.max_runtime_seconds,
        "last_heartbeat_at": r.last_heartbeat_at,
        "started_at": r.started_at,
        "ended_at": r.ended_at,
        "outcome": r.outcome,
        "summary": r.summary,
        "metadata": r.metadata,
        "error": r.error,
    }


def _dispatch_to_dict(result: kb.DispatchResult, *, dry_run: bool) -> dict[str, Any]:
    return {
        "dry_run": dry_run,
        "reclaimed": result.reclaimed,
        "promoted": result.promoted,
        "spawned": [
            {"task_id": tid, "assignee": assignee, "workspace_path": workspace}
            for tid, assignee, workspace in result.spawned
        ],
        "skipped_unassigned": list(result.skipped_unassigned),
        "skipped_nonspawnable": list(result.skipped_nonspawnable),
        "crashed": list(result.crashed),
        "auto_blocked": list(result.auto_blocked),
        "timed_out": list(result.timed_out),
    }


@contextlib.contextmanager
def _board_env(board: str):
    """Pin kanban_db to ``board`` and neutralise legacy DB/path overrides.

    ``kanban_db_path(board=...)`` intentionally honours ``HERMES_KANBAN_DB``
    for dispatcher/worker handoff compatibility. MCP board scope must be a hard
    boundary, so the facade clears those legacy env pins while operating.
    """

    keys = (
        "HERMES_KANBAN_BOARD",
        "HERMES_KANBAN_DB",
        "HERMES_KANBAN_WORKSPACES_ROOT",
    )
    with _BOARD_ENV_LOCK:
        previous = {k: os.environ.get(k) for k in keys}
        os.environ.pop("HERMES_KANBAN_DB", None)
        os.environ.pop("HERMES_KANBAN_WORKSPACES_ROOT", None)
        os.environ["HERMES_KANBAN_BOARD"] = board
        try:
            yield
        finally:
            for key, value in previous.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value


class KanbanMCPFacade:
    """Board-scoped Kanban API exposed through MCP tools."""

    def _normalise_board(self, board: str) -> tuple[Optional[str], Optional[dict[str, Any]]]:
        if not board or not str(board).strip():
            raise ValueError("board is required")
        try:
            slug = kb._normalize_board_slug(board)
        except ValueError as exc:
            return None, {"error": "invalid_board", "board": board, "message": str(exc)}
        if not slug:
            raise ValueError("board is required")
        allowed = _allowed_boards()
        if allowed is not None and slug not in allowed:
            return None, {
                "error": "board_not_allowed",
                "board": slug,
                "allowed_boards": sorted(allowed),
            }
        with _board_env(slug):
            if not kb.board_exists(slug):
                return None, {"error": "board_not_found", "board": slug}
        return slug, None

    def _safe_write_denied(self) -> Optional[dict[str, Any]]:
        mode = _write_mode()
        if mode == WRITE_MODE_READONLY:
            return {
                "error": "write_disabled",
                "write_mode": mode,
                "message": "Set HERMES_KANBAN_MCP_WRITE_MODE=safe or operator to enable Kanban MCP writes.",
            }
        return None

    def _operator_denied(self) -> Optional[dict[str, Any]]:
        mode = _write_mode()
        if mode != WRITE_MODE_OPERATOR:
            return {
                "error": "operator_mode_required",
                "write_mode": mode,
                "message": "Set HERMES_KANBAN_MCP_WRITE_MODE=operator to enable dangerous Kanban MCP operations.",
            }
        return None

    def boards_list(self, include_archived: bool = False) -> dict[str, Any]:
        allowed = _allowed_boards()
        boards = [
            board
            for board in kb.list_boards(include_archived=include_archived)
            if allowed is None or board.get("slug") in allowed
        ]
        for board in boards:
            slug = board.get("slug")
            if not slug:
                continue
            try:
                with _board_env(slug):
                    if kb.kanban_db_path(board=slug).exists():
                        with kb.connect(board=slug) as conn:
                            board["counts"] = _board_counts(conn)
                    else:
                        board["counts"] = {}
            except Exception:
                board["counts"] = {}
            board["total"] = sum(board.get("counts", {}).values())
        return {"count": len(boards), "boards": boards}

    def tasks_list(
        self,
        *,
        board: str,
        status: Optional[str] = None,
        assignee: Optional[str] = None,
        tenant: Optional[str] = None,
        include_archived: bool = False,
        limit: int = 100,
    ) -> dict[str, Any]:
        slug, err = self._normalise_board(board)
        if err:
            return err
        assert slug is not None
        limit = max(1, min(int(limit or 100), 500))
        with _board_env(slug), kb.connect(board=slug) as conn:
            tasks = kb.list_tasks(
                conn,
                assignee=assignee,
                status=status,
                tenant=tenant,
                include_archived=include_archived,
            )[:limit]
        return {"board": slug, "count": len(tasks), "tasks": [_task_to_dict(t) for t in tasks]}

    def task_show(self, *, board: str, task_id: str) -> dict[str, Any]:
        slug, err = self._normalise_board(board)
        if err:
            return err
        assert slug is not None
        with _board_env(slug), kb.connect(board=slug) as conn:
            task = kb.get_task(conn, task_id)
            if not task:
                return {"error": "task_not_found", "board": slug, "task_id": task_id}
            comments = kb.list_comments(conn, task_id)
            events = kb.list_events(conn, task_id)
            parents = kb.parent_ids(conn, task_id)
            children = kb.child_ids(conn, task_id)
            runs = kb.list_runs(conn, task_id)
        return {
            "board": slug,
            "task": _task_to_dict(task),
            "parents": parents,
            "children": children,
            "comments": [_comment_to_dict(c) for c in comments],
            "events": [_event_to_dict(e) for e in events],
            "runs": [_run_to_dict(r) for r in runs],
        }

    def task_create(
        self,
        *,
        board: str,
        title: str,
        body: Optional[str] = None,
        assignee: Optional[str] = None,
        created_by: str = "kanban-mcp",
        parents: Optional[list[str]] = None,
        tenant: Optional[str] = None,
        priority: int = 0,
        workspace_kind: str = "scratch",
        workspace_path: Optional[str] = None,
        triage: bool = False,
        idempotency_key: Optional[str] = None,
        max_runtime_seconds: Optional[int] = None,
        skills: Optional[list[str]] = None,
    ) -> dict[str, Any]:
        denied = self._safe_write_denied()
        if denied:
            return denied
        slug, err = self._normalise_board(board)
        if err:
            return err
        assert slug is not None
        try:
            with _board_env(slug), kb.connect(board=slug) as conn:
                task_id = kb.create_task(
                    conn,
                    title=title,
                    body=body,
                    assignee=assignee,
                    created_by=created_by or "kanban-mcp",
                    workspace_kind=workspace_kind,
                    workspace_path=workspace_path,
                    tenant=tenant,
                    priority=int(priority or 0),
                    parents=tuple(parents or ()),
                    triage=bool(triage),
                    idempotency_key=idempotency_key,
                    max_runtime_seconds=max_runtime_seconds,
                    skills=skills,
                )
                task = kb.get_task(conn, task_id)
        except Exception as exc:
            return {"error": "create_failed", "board": slug, "message": str(exc)}
        return {"ok": True, "board": slug, "task": _task_to_dict(task)}

    def task_comment(
        self,
        *,
        board: str,
        task_id: str,
        body: str,
        author: str = "kanban-mcp",
    ) -> dict[str, Any]:
        denied = self._safe_write_denied()
        if denied:
            return denied
        slug, err = self._normalise_board(board)
        if err:
            return err
        assert slug is not None
        try:
            with _board_env(slug), kb.connect(board=slug) as conn:
                comment_id = kb.add_comment(conn, task_id, author or "kanban-mcp", body)
        except Exception as exc:
            return {"error": "comment_failed", "board": slug, "task_id": task_id, "message": str(exc)}
        return {"ok": True, "board": slug, "task_id": task_id, "comment_id": comment_id}

    def task_assign(self, *, board: str, task_id: str, assignee: Optional[str]) -> dict[str, Any]:
        denied = self._safe_write_denied()
        if denied:
            return denied
        slug, err = self._normalise_board(board)
        if err:
            return err
        assert slug is not None
        try:
            with _board_env(slug), kb.connect(board=slug) as conn:
                ok = kb.assign_task(conn, task_id, assignee)
                task = kb.get_task(conn, task_id) if ok else None
        except Exception as exc:
            return {"error": "assign_failed", "board": slug, "task_id": task_id, "message": str(exc)}
        if not ok or not task:
            return {"error": "task_not_found", "board": slug, "task_id": task_id}
        return {"ok": True, "board": slug, "task": _task_to_dict(task)}

    def task_link(self, *, board: str, parent_id: str, child_id: str) -> dict[str, Any]:
        denied = self._safe_write_denied()
        if denied:
            return denied
        slug, err = self._normalise_board(board)
        if err:
            return err
        assert slug is not None
        try:
            with _board_env(slug), kb.connect(board=slug) as conn:
                kb.link_tasks(conn, parent_id, child_id)
        except Exception as exc:
            return {"error": "link_failed", "board": slug, "message": str(exc)}
        return {"ok": True, "board": slug, "parent_id": parent_id, "child_id": child_id}

    def task_unlink(self, *, board: str, parent_id: str, child_id: str) -> dict[str, Any]:
        denied = self._safe_write_denied()
        if denied:
            return denied
        slug, err = self._normalise_board(board)
        if err:
            return err
        assert slug is not None
        with _board_env(slug), kb.connect(board=slug) as conn:
            ok = kb.unlink_tasks(conn, parent_id, child_id)
        return {"ok": bool(ok), "board": slug, "parent_id": parent_id, "child_id": child_id}

    def stats(self, *, board: str) -> dict[str, Any]:
        slug, err = self._normalise_board(board)
        if err:
            return err
        assert slug is not None
        with _board_env(slug), kb.connect(board=slug) as conn:
            stats = kb.board_stats(conn)
        return {"board": slug, "stats": stats}

    def dispatch_dry_run(self, *, board: str, max_spawn: Optional[int] = None) -> dict[str, Any]:
        slug, err = self._normalise_board(board)
        if err:
            return err
        assert slug is not None
        with _board_env(slug), kb.connect(board=slug) as conn:
            result = kb.dispatch_once(conn, dry_run=True, max_spawn=max_spawn, board=slug)
        out = _dispatch_to_dict(result, dry_run=True)
        out["board"] = slug
        return out

    def task_complete(
        self,
        *,
        board: str,
        task_id: str,
        result: Optional[str] = None,
        summary: Optional[str] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        denied = self._operator_denied()
        if denied:
            return denied
        slug, err = self._normalise_board(board)
        if err:
            return err
        assert slug is not None
        try:
            with _board_env(slug), kb.connect(board=slug) as conn:
                ok = kb.complete_task(conn, task_id, result=result, summary=summary, metadata=metadata)
                task = kb.get_task(conn, task_id) if ok else None
        except Exception as exc:
            return {"error": "complete_failed", "board": slug, "task_id": task_id, "message": str(exc)}
        if not ok or not task:
            return {"error": "task_not_found", "board": slug, "task_id": task_id}
        return {"ok": True, "board": slug, "task": _task_to_dict(task)}

    def task_block(self, *, board: str, task_id: str, reason: str) -> dict[str, Any]:
        denied = self._operator_denied()
        if denied:
            return denied
        slug, err = self._normalise_board(board)
        if err:
            return err
        assert slug is not None
        try:
            with _board_env(slug), kb.connect(board=slug) as conn:
                ok = kb.block_task(conn, task_id, reason=reason)
                task = kb.get_task(conn, task_id) if ok else None
        except Exception as exc:
            return {"error": "block_failed", "board": slug, "task_id": task_id, "message": str(exc)}
        if not ok or not task:
            return {"error": "task_not_found", "board": slug, "task_id": task_id}
        return {"ok": True, "board": slug, "task": _task_to_dict(task)}

    def task_unblock(self, *, board: str, task_id: str) -> dict[str, Any]:
        denied = self._operator_denied()
        if denied:
            return denied
        slug, err = self._normalise_board(board)
        if err:
            return err
        assert slug is not None
        with _board_env(slug), kb.connect(board=slug) as conn:
            ok = kb.unblock_task(conn, task_id)
            task = kb.get_task(conn, task_id) if ok else None
        if not ok or not task:
            return {"error": "task_not_found_or_not_blocked", "board": slug, "task_id": task_id}
        return {"ok": True, "board": slug, "task": _task_to_dict(task)}

    def task_archive(self, *, board: str, task_id: str) -> dict[str, Any]:
        denied = self._operator_denied()
        if denied:
            return denied
        slug, err = self._normalise_board(board)
        if err:
            return err
        assert slug is not None
        with _board_env(slug), kb.connect(board=slug) as conn:
            ok = kb.archive_task(conn, task_id)
            task = kb.get_task(conn, task_id) if ok else None
        if not ok or not task:
            return {"error": "task_not_found", "board": slug, "task_id": task_id}
        return {"ok": True, "board": slug, "task": _task_to_dict(task)}

    def dispatch(
        self,
        *,
        board: str,
        max_spawn: Optional[int] = None,
        _spawn_fn: Optional[Callable[..., Optional[int]]] = None,
    ) -> dict[str, Any]:
        denied = self._operator_denied()
        if denied:
            return denied
        slug, err = self._normalise_board(board)
        if err:
            return err
        assert slug is not None
        with _board_env(slug), kb.connect(board=slug) as conn:
            result = kb.dispatch_once(
                conn,
                spawn_fn=_spawn_fn,
                dry_run=False,
                max_spawn=max_spawn,
                board=slug,
            )
        out = _dispatch_to_dict(result, dry_run=False)
        out["board"] = slug
        return out


def _board_counts(conn) -> dict[str, int]:
    rows = conn.execute("SELECT status, COUNT(*) AS n FROM tasks GROUP BY status").fetchall()
    return {r["status"]: int(r["n"]) for r in rows}


def _json_result(payload: dict[str, Any]) -> str:
    return json.dumps(payload, indent=2, ensure_ascii=False)


def create_mcp_server() -> "FastMCP":
    """Create a stdio MCP server exposing the bounded Kanban facade."""
    if not _MCP_SERVER_AVAILABLE:
        raise ImportError(
            "Kanban MCP server requires the 'mcp' package. "
            f"Install with: {sys.executable} -m pip install 'mcp'"
        )

    facade = KanbanMCPFacade()
    mcp = FastMCP(
        "hermes-kanban",
        instructions=(
            "Bounded Hermes Kanban bridge. Every task operation requires an explicit board slug. "
            "Safe writes are allowed unless HERMES_KANBAN_MCP_WRITE_MODE=readonly; dangerous "
            "operations require HERMES_KANBAN_MCP_WRITE_MODE=operator."
        ),
    )

    @mcp.tool()
    def boards_list(include_archived: bool = False) -> str:
        """List Hermes Kanban boards with per-status counts."""
        return _json_result(facade.boards_list(include_archived=include_archived))

    @mcp.tool()
    def tasks_list(
        board: str,
        status: Optional[str] = None,
        assignee: Optional[str] = None,
        tenant: Optional[str] = None,
        include_archived: bool = False,
        limit: int = 100,
    ) -> str:
        """List tasks on one explicit board, optionally filtered by status/assignee/tenant."""
        return _json_result(
            facade.tasks_list(
                board=board,
                status=status,
                assignee=assignee,
                tenant=tenant,
                include_archived=include_archived,
                limit=limit,
            )
        )

    @mcp.tool()
    def task_show(board: str, task_id: str) -> str:
        """Show one task including comments, events, parents, children, and runs."""
        return _json_result(facade.task_show(board=board, task_id=task_id))

    @mcp.tool()
    def task_create(
        board: str,
        title: str,
        body: Optional[str] = None,
        assignee: Optional[str] = None,
        created_by: str = "kanban-mcp",
        parents: Optional[list[str]] = None,
        tenant: Optional[str] = None,
        priority: int = 0,
        workspace_kind: str = "scratch",
        workspace_path: Optional[str] = None,
        triage: bool = False,
        idempotency_key: Optional[str] = None,
        max_runtime_seconds: Optional[int] = None,
        skills: Optional[list[str]] = None,
    ) -> str:
        """Create a task on an explicit board through the Kanban DB API."""
        return _json_result(
            facade.task_create(
                board=board,
                title=title,
                body=body,
                assignee=assignee,
                created_by=created_by,
                parents=parents,
                tenant=tenant,
                priority=priority,
                workspace_kind=workspace_kind,
                workspace_path=workspace_path,
                triage=triage,
                idempotency_key=idempotency_key,
                max_runtime_seconds=max_runtime_seconds,
                skills=skills,
            )
        )

    @mcp.tool()
    def task_comment(board: str, task_id: str, body: str, author: str = "kanban-mcp") -> str:
        """Append an audited comment to a task."""
        return _json_result(facade.task_comment(board=board, task_id=task_id, body=body, author=author))

    @mcp.tool()
    def task_assign(board: str, task_id: str, assignee: Optional[str]) -> str:
        """Assign or unassign a task. Pass null/empty assignee to unassign."""
        return _json_result(facade.task_assign(board=board, task_id=task_id, assignee=assignee))

    @mcp.tool()
    def task_link(board: str, parent_id: str, child_id: str) -> str:
        """Add a parent -> child task dependency on one board."""
        return _json_result(facade.task_link(board=board, parent_id=parent_id, child_id=child_id))

    @mcp.tool()
    def task_unlink(board: str, parent_id: str, child_id: str) -> str:
        """Remove a parent -> child task dependency on one board."""
        return _json_result(facade.task_unlink(board=board, parent_id=parent_id, child_id=child_id))

    @mcp.tool()
    def stats(board: str) -> str:
        """Return board stats for one explicit board."""
        return _json_result(facade.stats(board=board))

    @mcp.tool()
    def dispatch_dry_run(board: str, max_spawn: Optional[int] = None) -> str:
        """Run a non-spawning dispatcher preview for one board."""
        return _json_result(facade.dispatch_dry_run(board=board, max_spawn=max_spawn))

    @mcp.tool()
    def task_complete(
        board: str,
        task_id: str,
        result: Optional[str] = None,
        summary: Optional[str] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> str:
        """Dangerous: mark a task done. Requires HERMES_KANBAN_MCP_WRITE_MODE=operator."""
        return _json_result(
            facade.task_complete(board=board, task_id=task_id, result=result, summary=summary, metadata=metadata)
        )

    @mcp.tool()
    def task_block(board: str, task_id: str, reason: str) -> str:
        """Dangerous: block a task. Requires HERMES_KANBAN_MCP_WRITE_MODE=operator."""
        return _json_result(facade.task_block(board=board, task_id=task_id, reason=reason))

    @mcp.tool()
    def task_unblock(board: str, task_id: str) -> str:
        """Dangerous: unblock a task. Requires HERMES_KANBAN_MCP_WRITE_MODE=operator."""
        return _json_result(facade.task_unblock(board=board, task_id=task_id))

    @mcp.tool()
    def task_archive(board: str, task_id: str) -> str:
        """Dangerous: archive a task. Requires HERMES_KANBAN_MCP_WRITE_MODE=operator."""
        return _json_result(facade.task_archive(board=board, task_id=task_id))

    @mcp.tool()
    def dispatch(board: str, max_spawn: Optional[int] = None) -> str:
        """Dangerous: actually dispatch workers. Requires HERMES_KANBAN_MCP_WRITE_MODE=operator."""
        return _json_result(facade.dispatch(board=board, max_spawn=max_spawn))

    return mcp


def run_mcp_server(verbose: bool = False) -> None:
    """Start the bounded Kanban MCP server on stdio."""
    if not _MCP_SERVER_AVAILABLE:
        print(
            "Error: Kanban MCP server requires the 'mcp' package.\n"
            f"Install with: {sys.executable} -m pip install 'mcp'",
            file=sys.stderr,
        )
        sys.exit(1)
    logging.basicConfig(level=logging.DEBUG if verbose else logging.WARNING, stream=sys.stderr)
    server = create_mcp_server()
    import asyncio

    try:
        asyncio.run(server.run_stdio_async())
    except KeyboardInterrupt:
        return


def main() -> None:
    verbose = "--verbose" in sys.argv or "-v" in sys.argv
    run_mcp_server(verbose=verbose)


if __name__ == "__main__":
    main()
