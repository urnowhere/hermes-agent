from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from gateway.config import GatewayConfig, Platform, PlatformConfig
from gateway.platforms.base import MessageEvent, MessageType, SendResult
from gateway.run import GatewayRunner
from gateway.session import SessionEntry, SessionSource


class _Adapter:
    def __init__(self):
        self.send = AsyncMock(return_value=SendResult(success=True, message_id="m1"))


def _build_runner():
    runner = object.__new__(GatewayRunner)
    runner.config = GatewayConfig(
        platforms={Platform.TELEGRAM: PlatformConfig(enabled=True, token="fake-token")}
    )
    runner.adapters = {Platform.TELEGRAM: _Adapter()}
    runner._voice_mode = {}
    runner.hooks = SimpleNamespace(emit=AsyncMock(), loaded_hooks=False)
    runner.session_store = MagicMock()
    runner.session_store.get_or_create_session.return_value = SessionEntry(
        session_key="agent:main:telegram:dm:1",
        session_id="sess-1",
        created_at=datetime.now(),
        updated_at=datetime.now(),
        platform=Platform.TELEGRAM,
        chat_type="dm",
    )
    runner.session_store.load_transcript.return_value = []
    runner.session_store.has_any_sessions.return_value = True
    runner.session_store.append_to_transcript = MagicMock()
    runner.session_store.update_session = MagicMock()
    runner.session_store._save = MagicMock()
    runner._running_agents = {}
    runner._pending_messages = {}
    runner._pending_approvals = {}
    runner._session_db = None
    runner._voice_reply_config = {}
    runner._smart_model_routing = {}
    runner._tool_progress_cfg = {}
    runner._response_semaphores = {}
    runner._response_semaphore_default = 1
    runner._is_user_authorized = lambda _source: True
    runner._set_session_env = lambda _context: None
    runner._format_session_info = lambda: ""
    runner._get_guild_id = lambda _event: None
    runner._has_setup_skill = lambda: False
    runner._run_agent = AsyncMock(
        return_value={
            "final_response": "ok",
            "messages": [],
            "tools": [],
            "history_offset": 0,
            "last_prompt_tokens": 0,
        }
    )
    runner.delivery_router = MagicMock()
    runner._model = "test-model"
    runner._base_url = ""
    return runner


def _source():
    return SessionSource(platform=Platform.TELEGRAM, chat_id="1", chat_type="dm", user_id="u1")


@pytest.mark.asyncio
async def test_gateway_photo_uses_native_multimodal_when_model_supports_vision(tmp_path, monkeypatch):
    runner = _build_runner()
    image_path = tmp_path / "img.png"
    image_path.write_bytes(b"\x89PNG\r\n\x1a\nfake")

    monkeypatch.setattr("gateway.run._resolve_runtime_agent_kwargs", lambda: {"api_key": "fake"})
    monkeypatch.setattr("gateway.run._resolve_gateway_model", lambda *_args, **_kwargs: "test-model")
    monkeypatch.setattr("agent.model_metadata.get_model_capabilities", lambda *_args, **_kwargs: {"supports_vision": True})

    enrich_mock = AsyncMock(return_value="should-not-be-used")
    runner._enrich_message_with_vision = enrich_mock

    event = MessageEvent(
        text="describe",
        message_type=MessageType.PHOTO,
        source=_source(),
        media_urls=[str(image_path)],
        media_types=["image/png"],
        message_id="1",
    )

    result = await runner._handle_message_with_agent(event, event.source, "q1")

    assert result == "ok"
    enrich_mock.assert_not_awaited()
    sent_message = runner._run_agent.call_args.kwargs["message"]
    assert isinstance(sent_message, list)
    assert sent_message[0] == {"type": "text", "text": "describe"}
    assert sent_message[1]["type"] == "image_url"
    assert sent_message[1]["image_url"]["url"].startswith("data:image/png;base64,")


@pytest.mark.asyncio
async def test_gateway_photo_falls_back_to_vision_tool_when_model_lacks_vision(tmp_path, monkeypatch):
    runner = _build_runner()
    image_path = tmp_path / "img.png"
    image_path.write_bytes(b"\x89PNG\r\n\x1a\nfake")

    monkeypatch.setattr("gateway.run._resolve_runtime_agent_kwargs", lambda: {"api_key": "fake"})
    monkeypatch.setattr("gateway.run._resolve_gateway_model", lambda *_args, **_kwargs: "test-model")
    monkeypatch.setattr("agent.model_metadata.get_model_capabilities", lambda *_args, **_kwargs: {"supports_vision": False})

    enrich_mock = AsyncMock(return_value="[vision fallback]")
    runner._enrich_message_with_vision = enrich_mock

    event = MessageEvent(
        text="describe",
        message_type=MessageType.PHOTO,
        source=_source(),
        media_urls=[str(image_path)],
        media_types=["image/png"],
        message_id="1",
    )

    result = await runner._handle_message_with_agent(event, event.source, "q1")

    assert result == "ok"
    enrich_mock.assert_awaited_once()
    assert runner._run_agent.call_args.kwargs["message"] == "[vision fallback]"


@pytest.mark.asyncio
async def test_gateway_photo_uses_resolved_turn_model_for_native_multimodal(tmp_path, monkeypatch):
    runner = _build_runner()
    runner._model = "non-vision-shell-model"
    runner._resolve_turn_agent_config = lambda *_args, **_kwargs: {
        "model": "vision-turn-model",
        "runtime": {"api_key": "fake"},
    }
    image_path = tmp_path / "img.png"
    image_path.write_bytes(b"\x89PNG\r\n\x1a\nfake")

    monkeypatch.setattr("gateway.run._resolve_runtime_agent_kwargs", lambda: {"api_key": "fake"})
    monkeypatch.setattr("gateway.run._resolve_gateway_model", lambda *_args, **_kwargs: "base-model")
    monkeypatch.setattr(
        "agent.model_metadata.get_model_capabilities",
        lambda model, *_args, **_kwargs: {"supports_vision": model == "vision-turn-model"},
    )

    enrich_mock = AsyncMock(return_value="should-not-be-used")
    runner._enrich_message_with_vision = enrich_mock

    event = MessageEvent(
        text="describe",
        message_type=MessageType.PHOTO,
        source=_source(),
        media_urls=[str(image_path)],
        media_types=["image/png"],
        message_id="1",
    )

    result = await runner._handle_message_with_agent(event, event.source, "q1")

    assert result == "ok"
    enrich_mock.assert_not_awaited()
    sent_message = runner._run_agent.call_args.kwargs["message"]
    assert isinstance(sent_message, list)
    assert sent_message[1]["type"] == "image_url"


@pytest.mark.asyncio
async def test_gateway_photo_falls_back_when_resolved_turn_model_lacks_vision(tmp_path, monkeypatch):
    runner = _build_runner()
    runner._model = "vision-shell-model"
    runner._resolve_turn_agent_config = lambda *_args, **_kwargs: {
        "model": "text-only-turn-model",
        "runtime": {"api_key": "fake"},
    }
    image_path = tmp_path / "img.png"
    image_path.write_bytes(b"\x89PNG\r\n\x1a\nfake")

    monkeypatch.setattr("gateway.run._resolve_runtime_agent_kwargs", lambda: {"api_key": "fake"})
    monkeypatch.setattr("gateway.run._resolve_gateway_model", lambda *_args, **_kwargs: "base-model")
    monkeypatch.setattr(
        "agent.model_metadata.get_model_capabilities",
        lambda model, *_args, **_kwargs: {"supports_vision": model == "vision-shell-model"},
    )

    enrich_mock = AsyncMock(return_value="[vision fallback]")
    runner._enrich_message_with_vision = enrich_mock

    event = MessageEvent(
        text="describe",
        message_type=MessageType.PHOTO,
        source=_source(),
        media_urls=[str(image_path)],
        media_types=["image/png"],
        message_id="1",
    )

    result = await runner._handle_message_with_agent(event, event.source, "q1")

    assert result == "ok"
    enrich_mock.assert_awaited_once()
    assert runner._run_agent.call_args.kwargs["message"] == "[vision fallback]"
