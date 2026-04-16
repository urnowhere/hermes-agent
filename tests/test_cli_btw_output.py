from unittest.mock import MagicMock, patch

import cli as cli_module
from cli import HermesCLI


def _make_cli_stub():
    cli = HermesCLI.__new__(HermesCLI)
    cli._ensure_runtime_credentials = MagicMock(return_value=True)
    cli._resolve_turn_agent_config = MagicMock(return_value={
        "model": "anthropic/claude-opus-4.6",
        "runtime": {
            "api_key": "test-key-1234567890",
            "base_url": "https://openrouter.ai/api/v1",
            "provider": "auto",
            "api_mode": None,
            "command": None,
            "args": [],
        },
        "request_overrides": None,
    })
    cli.conversation_history = []
    cli.reasoning_config = None
    cli.service_tier = None
    cli._providers_only = None
    cli._providers_ignore = None
    cli._providers_order = None
    cli._provider_sort = None
    cli._provider_require_params = None
    cli._provider_data_collection = None
    cli._fallback_model = None
    cli._app = None
    cli.bell_on_complete = False
    cli._invalidate = MagicMock()
    return cli


class TestCliBtwOutput:
    def test_btw_agent_routes_status_output_through_cprint(self):
        cli = _make_cli_stub()
        created_agents = []

        class _FakeAgent:
            def __init__(self, **kwargs):
                self.kwargs = kwargs
                self._print_fn = None
                self._line_printer = None
                created_agents.append(self)

            def run_conversation(self, **kwargs):
                return {}

        class _ImmediateThread:
            def __init__(self, target=None, daemon=None, name=None):
                self._target = target

            def start(self):
                self._target()

        with (
            patch.object(cli_module, "AIAgent", _FakeAgent),
            patch.object(cli_module.threading, "Thread", _ImmediateThread),
            patch.object(cli_module, "_cprint") as mock_cprint,
        ):
            cli._handle_btw_command("/btw explain this bug")

        assert len(created_agents) == 1
        assert created_agents[0]._print_fn is mock_cprint
        assert created_agents[0]._line_printer is mock_cprint
