"""CLI entry point for the Hermes ↔ Bindu A2A adapter.

Loads ``~/.hermes/.env`` (same path the main CLI uses), reads Bindu-specific
config from ``HERMES_BINDU_*`` env vars, and starts the Bindu A2A server
with Hermes' ``AIAgent`` as the handler.

Usage::

    hermes-bindu
    # or
    python -m bindu_adapter

The server listens on ``http://localhost:3773`` by default. The first-line
banner prints the agent's DID and the local endpoint. All artifacts returned
to callers are DID-signed on the way out.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from hermes_cli.env_loader import load_hermes_dotenv
from hermes_constants import get_hermes_home

from bindu_adapter.adapter import handler


def _build_config() -> dict:
    """Translate ``HERMES_BINDU_*`` env vars into a Bindu config dict.

    Everything has a sensible default so a first-time user can run
    ``hermes-bindu`` with only an ``OPENROUTER_API_KEY`` (or equivalent)
    set. The agent card, DID method, and deployment URL all flow from
    these fields.
    """
    return {
        "author": os.getenv("HERMES_BINDU_AUTHOR", "you@example.com"),
        "name": os.getenv("HERMES_BINDU_NAME", "hermes"),
        "description": os.getenv(
            "HERMES_BINDU_DESCRIPTION",
            "Hermes Agent (tool-using) exposed as a Bindu A2A microservice",
        ),
        "deployment": {
            "url": os.getenv("HERMES_BINDU_URL", "http://localhost:3773"),
            "expose": os.getenv("HERMES_BINDU_EXPOSE", "false").lower() == "true",
        },
        "skills": [],
    }


def main() -> None:
    """Start Hermes as a Bindu A2A microservice."""
    # Load env from ~/.hermes/.env first (user config), then project-local
    # .env as a dev fallback. Mirrors how run_agent.py loads env so users
    # don't need to re-source for the adapter.
    load_hermes_dotenv(
        hermes_home=get_hermes_home(),
        project_env=Path(__file__).resolve().parent.parent / ".env",
    )

    try:
        from bindu.penguin.bindufy import bindufy
    except ImportError:
        print(
            "Bindu is not installed. Install the adapter extras with:\n"
            "    pip install -e '.[bindu]'\n"
            "or, standalone:\n"
            "    pip install bindu",
            file=sys.stderr,
        )
        sys.exit(1)

    bindufy(_build_config(), handler)


if __name__ == "__main__":
    main()
