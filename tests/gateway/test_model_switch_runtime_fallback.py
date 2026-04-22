import threading
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from gateway.config import Platform
from gateway.platforms.base import MessageEvent, MessageType
from gateway.run import GatewayRunner
from gateway.session import SessionSource
from hermes_cli.model_switch import ModelSwitchResult


def _make_runner():
    runner = object.__new__(GatewayRunner)
    runner.adapters = {}
    runner._voice_mode = {}
    runner._session_model_overrides = {}
    runner._pending_model_notes = {}
    runner._agent_cache_lock = threading.Lock()
    runner._agent_cache = {}
    runner._fallback_model = [{"provider": "zai", "model": "glm-5"}]
    return runner


def _make_event(text="/model gpt-5.4"):
    return MessageEvent(
        text=text,
        message_type=MessageType.TEXT,
        source=SessionSource(platform=Platform.TELEGRAM, chat_id="12345", chat_type="dm"),
    )


def _result() -> ModelSwitchResult:
    model_info = SimpleNamespace(
        context_window=200_000,
        max_output=8_000,
        has_cost_data=lambda: False,
        format_capabilities=lambda: "chat",
    )
    return ModelSwitchResult(
        success=True,
        new_model="gpt-5.4",
        target_provider="openai",
        api_key="new-key",
        base_url="https://api.openai.com/v1",
        api_mode="chat_completions",
        provider_label="OpenAI",
        model_info=model_info,
    )


@pytest.mark.asyncio
async def test_handle_model_command_passes_runtime_fallback_chain_to_cached_agent(tmp_path, monkeypatch):
    runner = _make_runner()
    event = _make_event()
    session_key = runner._session_key_for_source(event.source)
    cached_agent = MagicMock()
    runner._agent_cache[session_key] = (cached_agent, None)

    hermes_home = tmp_path / ".hermes"
    hermes_home.mkdir()
    (hermes_home / "config.yaml").write_text("model: {}\n", encoding="utf-8")

    import gateway.run as gateway_run

    monkeypatch.setattr(gateway_run, "_hermes_home", hermes_home)

    with (
        patch("hermes_cli.model_switch.parse_model_flags", return_value=("gpt-5.4", None, False)),
        patch("hermes_cli.model_switch.switch_model", return_value=_result()),
    ):
        await runner._handle_model_command(event)

    cached_agent.switch_model.assert_called_once_with(
        new_model="gpt-5.4",
        new_provider="openai",
        api_key="new-key",
        base_url="https://api.openai.com/v1",
        api_mode="chat_completions",
        fallback_model=runner._fallback_model,
    )


@pytest.mark.asyncio
async def test_model_picker_callback_passes_runtime_fallback_chain_to_cached_agent(tmp_path, monkeypatch):
    runner = _make_runner()

    class _Adapter:
        async def send_model_picker(self, **kwargs):
            await kwargs["on_model_selected"](kwargs["chat_id"], "gpt-5.4", "openai")
            return SimpleNamespace(success=True)

    runner.adapters = {Platform.TELEGRAM: _Adapter()}
    event = _make_event("/model")
    session_key = runner._session_key_for_source(event.source)
    cached_agent = MagicMock()
    runner._agent_cache[session_key] = (cached_agent, None)

    hermes_home = tmp_path / ".hermes"
    hermes_home.mkdir()
    (hermes_home / "config.yaml").write_text("model: {}\n", encoding="utf-8")

    import gateway.run as gateway_run

    monkeypatch.setattr(gateway_run, "_hermes_home", hermes_home)

    with (
        patch(
            "hermes_cli.model_switch.list_authenticated_providers",
            return_value=[
                {
                    "name": "OpenAI",
                    "slug": "openai",
                    "models": ["gpt-5.4"],
                    "total_models": 1,
                    "is_current": True,
                }
            ],
        ),
        patch("hermes_cli.model_switch.parse_model_flags", return_value=("", None, False)),
        patch("hermes_cli.model_switch.switch_model", return_value=_result()),
    ):
        reply = await runner._handle_model_command(event)

    assert reply is None
    cached_agent.switch_model.assert_called_once_with(
        new_model="gpt-5.4",
        new_provider="openai",
        api_key="new-key",
        base_url="https://api.openai.com/v1",
        api_mode="chat_completions",
        fallback_model=runner._fallback_model,
    )