"""Persistent slash-command worker -- one HermesCLI per TUI session.

Protocol: reads JSON lines from stdin {id, command}, writes {id, ok, output|error} to stdout.

The slash_worker only needs CLI command processing, not MCP tool bootstrapping.
The TUI server already handles MCP discovery and spawns hermes mcp serve
children; without the HERMES_MCP_DISCOVERY guard both code paths independently
spawn duplicate MCP serve processes per session (#15275).  The cli module is
imported lazily inside main() so the env var is set before model_tools runs.
"""

import argparse
import contextlib
import io
import json
import os
import sys

from rich.console import Console

# cli module reference -- populated lazily in main() after setting the
# HERMES_MCP_DISCOVERY env var to prevent duplicate MCP serve children.
_cli_mod = None


def _run(cli, command: str) -> str:
    cmd = (command or "").strip()
    if not cmd:
        return ""
    if not cmd.startswith("/"):
        cmd = f"/{cmd}"

    buf = io.StringIO()

    # Rich Console captures its file handle at construction time, so
    # contextlib.redirect_stdout won't affect it. Swap the console's
    # underlying file to our buffer so self.console.print() is captured.
    cli.console = Console(file=buf, force_terminal=True, width=120)

    old = getattr(_cli_mod, "_cprint", None)
    if old is not None:
        _cli_mod._cprint = lambda text: print(text)

    try:
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            cli.process_command(cmd)
    finally:
        if old is not None:
            _cli_mod._cprint = old

    return buf.getvalue().rstrip()


def main():
    global _cli_mod

    p = argparse.ArgumentParser(add_help=False)
    p.add_argument("--session-key", required=True)
    p.add_argument("--model", default="")
    args = p.parse_args()

    os.environ["HERMES_SESSION_KEY"] = args.session_key
    os.environ["HERMES_INTERACTIVE"] = "1"

    # Suppress MCP discovery -- the TUI server already handles MCP servers;
    # importing cli triggers model_tools which calls discover_mcp_tools() at
    # module scope.  This env var tells model_tools to skip that step.
    os.environ["HERMES_MCP_DISCOVERY"] = "0"

    import cli as cli_mod
    from cli import HermesCLI

    _cli_mod = cli_mod

    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        cli = HermesCLI(model=args.model or None, compact=True, resume=args.session_key, verbose=False)

    for raw in sys.stdin:
        line = raw.strip()
        if not line:
            continue

        rid = None
        try:
            req = json.loads(line)
            rid = req.get("id")
            out = _run(cli, req.get("command", ""))
            sys.stdout.write(json.dumps({"id": rid, "ok": True, "output": out}) + "\n")
            sys.stdout.flush()
        except Exception as e:
            sys.stdout.write(json.dumps({"id": rid, "ok": False, "error": str(e)}) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
