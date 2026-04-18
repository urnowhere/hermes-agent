"""Command registry API routes for the Hermes Web Console backend."""

from __future__ import annotations

from aiohttp import web

from hermes_cli.commands import COMMAND_REGISTRY


def _command_entry(command) -> dict[str, object]:
    aliases = list(command.aliases)
    names = [command.name, *aliases]
    return {
        "name": command.name,
        "description": command.description,
        "category": command.category,
        "aliases": aliases,
        "names": names,
        "args_hint": command.args_hint,
        "subcommands": list(command.subcommands),
        "cli_only": command.cli_only,
        "gateway_only": command.gateway_only,
        "gateway_config_gate": command.gateway_config_gate,
    }


async def handle_list_commands(request: web.Request) -> web.Response:
    """GET /api/gui/commands — expose the shared Hermes slash-command registry."""
    commands = [_command_entry(command) for command in COMMAND_REGISTRY]
    return web.json_response({"ok": True, "commands": commands})


def register_commands_api_routes(app: web.Application) -> None:
    app.router.add_get("/api/gui/commands", handle_list_commands)