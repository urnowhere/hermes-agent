"""CLI commands for the zoom_meeting plugin."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from plugins.zoom_meeting.client import ZoomClient, ZoomCredentials
from plugins.zoom_meeting.server import AIOHTTP_AVAILABLE, ZoomWebhookServer
from plugins.zoom_meeting.store import ZoomMeetingStore
from plugins.zoom_meeting.tools import (
    handle_zoom_meeting_action_items,
    handle_zoom_meeting_artifacts,
    handle_zoom_meeting_events,
    handle_zoom_meeting_status,
    handle_zoom_meeting_summary,
    handle_zoom_meeting_transcript,
    handle_zoom_meeting_watch,
)


def register_cli(subparser: argparse.ArgumentParser) -> None:
    subs = subparser.add_subparsers(dest="zoom_command")

    auth_p = subs.add_parser("auth-check", help="Check whether Zoom OAuth env vars are configured and fetch a token")
    auth_p.set_defaults(func=zoom_command)

    watch_p = subs.add_parser("watch", help="Initialize local state for a Zoom meeting from REST metadata")
    watch_p.add_argument("meeting_id")
    watch_p.add_argument("--fetch-recordings", action="store_true")

    status_p = subs.add_parser("status", help="Print local state for a watched Zoom meeting")
    status_p.add_argument("meeting_id")

    tr_p = subs.add_parser("transcript", help="Print the locally captured transcript for a meeting")
    tr_p.add_argument("meeting_id")
    tr_p.add_argument("--last", type=int, default=None)

    ev_p = subs.add_parser("events", help="Print normalized events for a meeting")
    ev_p.add_argument("meeting_id")
    ev_p.add_argument("--last", type=int, default=None)

    sum_p = subs.add_parser("summary", help="Render a markdown summary for a meeting")
    sum_p.add_argument("meeting_id")

    ai_p = subs.add_parser("action-items", help="Extract action items, decisions, and open questions")
    ai_p.add_argument("meeting_id")

    exp_p = subs.add_parser("export", help="Export artifact bundle for a meeting")
    exp_p.add_argument("meeting_id")
    exp_p.add_argument("--format", choices=("markdown", "json"), default="markdown")
    exp_p.add_argument("--output", default=None)

    ing_p = subs.add_parser("ingest", help="Ingest a webhook payload from a local JSON file")
    ing_p.add_argument("payload_file")

    serve_p = subs.add_parser("serve", help="Run an aiohttp Zoom webhook receiver")
    serve_p.add_argument("--host", default="0.0.0.0")
    serve_p.add_argument("--port", type=int, default=8754)
    serve_p.add_argument("--path", default="/zoom/webhook")

    subparser.set_defaults(func=zoom_command)


def _print_json(result: str) -> int:
    parsed = json.loads(result)
    print(json.dumps(parsed, indent=2, ensure_ascii=False))
    return 0 if parsed.get("success", parsed.get("ok", False)) else 1


def _client_from_env() -> ZoomClient | None:
    account_id = os.getenv("ZOOM_ACCOUNT_ID", "").strip()
    client_id = os.getenv("ZOOM_CLIENT_ID", "").strip()
    client_secret = os.getenv("ZOOM_CLIENT_SECRET", "").strip()
    if not (account_id and client_id and client_secret):
        return None
    return ZoomClient(ZoomCredentials(account_id=account_id, client_id=client_id, client_secret=client_secret))


def zoom_command(args: argparse.Namespace) -> int:
    sub = getattr(args, "zoom_command", None)
    if not sub:
        print("usage: hermes zoom {auth-check,watch,status,transcript,events,summary,action-items,export,ingest,serve}")
        return 2
    if sub == "auth-check":
        client = _client_from_env()
        if client is None:
            print("Zoom OAuth env vars missing. Set ZOOM_ACCOUNT_ID, ZOOM_CLIENT_ID, and ZOOM_CLIENT_SECRET.")
            return 1
        token = client.get_access_token()
        print(json.dumps({"ok": True, "token_prefix": token[:8], "token_length": len(token)}, indent=2))
        return 0
    if sub == "watch":
        return _print_json(handle_zoom_meeting_watch({"meeting_id": args.meeting_id, "fetch_recordings": bool(args.fetch_recordings)}))
    if sub == "status":
        return _print_json(handle_zoom_meeting_status({"meeting_id": args.meeting_id}))
    if sub == "transcript":
        payload = {"meeting_id": args.meeting_id}
        if args.last:
            payload["last"] = args.last
        return _print_json(handle_zoom_meeting_transcript(payload))
    if sub == "events":
        payload = {"meeting_id": args.meeting_id}
        if args.last:
            payload["last"] = args.last
        return _print_json(handle_zoom_meeting_events(payload))
    if sub == "summary":
        return _print_json(handle_zoom_meeting_summary({"meeting_id": args.meeting_id}))
    if sub == "action-items":
        return _print_json(handle_zoom_meeting_action_items({"meeting_id": args.meeting_id}))
    if sub == "export":
        payload = {"meeting_id": args.meeting_id, "format": args.format}
        if args.output:
            payload["output_path"] = args.output
        return _print_json(handle_zoom_meeting_artifacts(payload))
    if sub == "ingest":
        payload = json.loads(Path(args.payload_file).expanduser().read_text(encoding="utf-8"))
        normalized = ZoomMeetingStore().ingest_event(payload)
        print(json.dumps(normalized, indent=2, ensure_ascii=False))
        return 0
    if sub == "serve":
        if not AIOHTTP_AVAILABLE:
            print("aiohttp not installed. Run: pip install aiohttp")
            return 1
        secret = os.getenv("ZOOM_WEBHOOK_SECRET_TOKEN", "").strip() or os.getenv("ZOOM_WEBHOOK_SECRET", "").strip()
        server = ZoomWebhookServer(
            ZoomMeetingStore(),
            secret_token=secret,
            host=args.host,
            port=args.port,
            path=args.path,
        )
        print(
            json.dumps(
                {
                    "ok": True,
                    "host": args.host,
                    "port": args.port,
                    "path": args.path,
                    "secret_configured": bool(secret),
                },
                indent=2,
            )
        )
        server.serve_forever()
        return 0
    print(f"unknown subcommand: {sub}")
    return 2
