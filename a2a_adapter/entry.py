#!/usr/bin/env python3
"""Entry point for the Hermes A2A server.

Starts Hermes as an A2A-discoverable agent on HTTP, serving:
  - /.well-known/agent.json  — Agent Card (discovery)
  - /                        — JSON-RPC endpoint (task execution)

Usage:
    hermes-a2a                              # default port 9990
    hermes-a2a --port 8080                  # custom port
    hermes-a2a --host 127.0.0.1 --port 8080  # bind to localhost only
    hermes-a2a --name "My Agent"            # custom agent name
"""

import argparse
import logging
import os
import sys


def _setup_logging():
    """Configure logging to stderr so stdout stays clean for HTTP."""
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(
        logging.Formatter("%(asctime)s [%(name)s] %(levelname)s: %(message)s")
    )
    root = logging.getLogger()
    root.addHandler(handler)
    root.setLevel(logging.INFO)


def _load_env():
    """Load environment from ~/.hermes/.env if available."""
    try:
        from hermes_cli.env_loader import load_dotenv
        load_dotenv()
    except ImportError:
        pass


def main():
    """Start the Hermes A2A server."""
    parser = argparse.ArgumentParser(
        description="Run Hermes as an A2A-discoverable agent server"
    )
    parser.add_argument(
        "--host", default="127.0.0.1",
        help="Host to bind to (default: 127.0.0.1). Use 0.0.0.0 for remote access (requires --bearer-token)."
    )
    parser.add_argument(
        "--port", type=int, default=9990,
        help="Port to listen on (default: 9990)"
    )
    parser.add_argument(
        "--name", default="Hermes Agent",
        help="Agent name in the Agent Card (default: 'Hermes Agent')"
    )
    parser.add_argument(
        "--bearer-token",
        default=os.environ.get("HERMES_A2A_BEARER_TOKEN"),
        help="Require this bearer token for all requests (env: HERMES_A2A_BEARER_TOKEN). "
             "Strongly recommended when binding to 0.0.0.0.",
    )
    parser.add_argument(
        "--toolset", default="hermes-cli",
        help="Toolset for agent sessions (default: hermes-cli). Use 'hermes-acp' for restricted access.",
    )
    args = parser.parse_args()

    _setup_logging()
    _load_env()

    logger = logging.getLogger(__name__)

    # Security: warn loudly if binding to all interfaces without auth
    if args.host == "0.0.0.0" and not args.bearer_token:
        logger.warning(
            "WARNING: Binding to 0.0.0.0 WITHOUT --bearer-token. "
            "This exposes full agent access (terminal, files, code execution) "
            "to anyone who can reach this port. Set --bearer-token or use "
            "HERMES_A2A_BEARER_TOKEN env var to require authentication."
        )

    try:
        from a2a_adapter.server import build_application, is_server_available
    except ImportError:
        logger.error(
            "A2A server dependencies not installed. "
            "Install with: pip install 'hermes-agent[a2a]' or pip install 'a2a-sdk[http-server]'"
        )
        sys.exit(1)

    if not is_server_available():
        logger.error(
            "A2A SDK server components not available. "
            "Install with: pip install 'a2a-sdk[http-server]'"
        )
        sys.exit(1)

    try:
        import uvicorn
    except ImportError:
        logger.error(
            "uvicorn not installed. Install with: pip install uvicorn[standard]"
        )
        sys.exit(1)

    logger.info("Starting Hermes A2A server on %s:%d", args.host, args.port)
    if args.bearer_token:
        logger.info("Bearer token authentication ENABLED")
    logger.info("Agent Card: http://%s:%d/.well-known/agent.json", args.host, args.port)

    app = build_application(
        host=args.host, port=args.port, name=args.name,
        bearer_token=args.bearer_token, toolset=args.toolset,
    )
    uvicorn.run(app.build(), host=args.host, port=args.port)


if __name__ == "__main__":
    main()
