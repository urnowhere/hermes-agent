"""Regression test for `hermes mcp add` argparse dest collision.

The `mcp add` subparser declares ``--command`` for stdio MCP servers.
Argparse derives the destination attribute from the flag name, so
without an explicit ``dest=`` it would target ``args.command`` —
the same attribute the top-level subparser uses to record which
top-level subcommand was selected (``dest="command"``).

When a user runs ``hermes mcp add NAME --url URL`` (no ``--command``),
argparse parses ``--command``'s default of ``None`` after the top-level
parser has already set ``args.command = "mcp"``, wiping it out. The
dispatcher in ``main()`` then sees ``args.command is None`` and falls
through to interactive chat instead of running ``mcp add``.

The fix gives the stdio ``--command`` flag an explicit
``dest="mcp_command"`` so it cannot shadow the top-level dest.

See: https://github.com/NousResearch/hermes-agent/issues/19785
"""

import argparse


def _build_minimal_parser():
    """Replicate the minimal parser shape that exhibits the bug.

    Mirrors ``hermes_cli/_parser.py`` (top-level ``dest="command"``)
    and ``hermes_cli/main.py`` (``mcp`` subparser with an ``add``
    sub-subparser that declares ``--url`` and ``--command``).
    """
    parser = argparse.ArgumentParser(prog="hermes")
    subparsers = parser.add_subparsers(dest="command")

    mcp_parser = subparsers.add_parser("mcp")
    mcp_sub = mcp_parser.add_subparsers(dest="mcp_action")

    mcp_add_p = mcp_sub.add_parser("add")
    mcp_add_p.add_argument("name")
    mcp_add_p.add_argument("--url")
    mcp_add_p.add_argument("--command", dest="mcp_command")
    return parser


class TestMcpAddDestCollision:
    def test_url_form_preserves_top_level_command(self):
        """`hermes mcp add foo --url ...` must keep args.command == 'mcp'."""
        parser = _build_minimal_parser()
        args = parser.parse_args(
            ["mcp", "add", "foo", "--url", "https://example.com/mcp"]
        )
        assert args.command == "mcp"
        assert args.mcp_action == "add"
        assert args.name == "foo"
        assert args.url == "https://example.com/mcp"
        assert args.mcp_command is None

    def test_stdio_form_routes_via_mcp_command(self):
        """`--command` populates args.mcp_command, not args.command."""
        parser = _build_minimal_parser()
        args = parser.parse_args(
            ["mcp", "add", "bar", "--command", "npx"]
        )
        assert args.command == "mcp"
        assert args.mcp_action == "add"
        assert args.mcp_command == "npx"
        assert args.url is None
