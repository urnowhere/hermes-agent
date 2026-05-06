"""Tests for the QQ Bot platform adapter."""

import asyncio
import json
import os
import sys
from unittest import mock

import pytest

from gateway.config import Platform, PlatformConfig


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_config(**extra):
    """Build a PlatformConfig(enabled=True, extra=extra) for testing."""
    return PlatformConfig(enabled=True, extra=extra)


# ---------------------------------------------------------------------------
# check_qq_requirements
# ---------------------------------------------------------------------------

class TestQQRequirements:
    def test_returns_bool(self):
        from gateway.platforms.qqbot import check_qq_requirements
        result = check_qq_requirements()
        assert isinstance(result, bool)


# ---------------------------------------------------------------------------
# QQAdapter.__init__
# ---------------------------------------------------------------------------

class TestQQAdapterInit:
    def _make(self, **extra):
        from gateway.platforms.qqbot import QQAdapter
        return QQAdapter(_make_config(**extra))

    def test_basic_attributes(self):
        adapter = self._make(app_id="123", client_secret="sec")
        assert adapter._app_id == "123"
        assert adapter._client_secret == "sec"

    def test_env_fallback(self):
        with mock.patch.dict(os.environ, {"QQ_APP_ID": "env_id", "QQ_CLIENT_SECRET": "env_sec"}, clear=False):
            adapter = self._make()
            assert adapter._app_id == "env_id"
            assert adapter._client_secret == "env_sec"

    def test_env_fallback_extra_wins(self):
        with mock.patch.dict(os.environ, {"QQ_APP_ID": "env_id"}, clear=False):
            adapter = self._make(app_id="extra_id", client_secret="sec")
            assert adapter._app_id == "extra_id"

    def test_dm_policy_default(self):
        adapter = self._make(app_id="a", client_secret="b")
        assert adapter._dm_policy == "open"

    def test_dm_policy_explicit(self):
        adapter = self._make(app_id="a", client_secret="b", dm_policy="allowlist")
        assert adapter._dm_policy == "allowlist"

    def test_group_policy_default(self):
        adapter = self._make(app_id="a", client_secret="b")
        assert adapter._group_policy == "open"

    def test_allow_from_parsing_string(self):
        adapter = self._make(app_id="a", client_secret="b", allow_from="x, y , z")
        assert adapter._allow_from == ["x", "y", "z"]

    def test_allow_from_parsing_list(self):
        adapter = self._make(app_id="a", client_secret="b", allow_from=["a", "b"])
        assert adapter._allow_from == ["a", "b"]

    def test_allow_from_default_empty(self):
        adapter = self._make(app_id="a", client_secret="b")
        assert adapter._allow_from == []

    def test_group_allow_from(self):
        adapter = self._make(app_id="a", client_secret="b", group_allow_from="g1,g2")
        assert adapter._group_allow_from == ["g1", "g2"]

    def test_markdown_support_default(self):
        adapter = self._make(app_id="a", client_secret="b")
        assert adapter._markdown_support is True

    def test_markdown_support_false(self):
        adapter = self._make(app_id="a", client_secret="b", markdown_support=False)
        assert adapter._markdown_support is False

    def test_name_property(self):
        adapter = self._make(app_id="a", client_secret="b")
        assert adapter.name == "QQBot"


# ---------------------------------------------------------------------------
# _coerce_list
# ---------------------------------------------------------------------------

class TestCoerceList:
    def _fn(self, value):
        from gateway.platforms.qqbot import _coerce_list
        return _coerce_list(value)

    def test_none(self):
        assert self._fn(None) == []

    def test_string(self):
        assert self._fn("a, b ,c") == ["a", "b", "c"]

    def test_list(self):
        assert self._fn(["x", "y"]) == ["x", "y"]

    def test_empty_string(self):
        assert self._fn("") == []

    def test_tuple(self):
        assert self._fn(("a", "b")) == ["a", "b"]

    def test_single_item_string(self):
        assert self._fn("hello") == ["hello"]


# ---------------------------------------------------------------------------
# _is_voice_content_type
# ---------------------------------------------------------------------------

class TestIsVoiceContentType:
    def _fn(self, content_type, filename):
        from gateway.platforms.qqbot import QQAdapter
        return QQAdapter._is_voice_content_type(content_type, filename)

    def test_voice_content_type(self):
        assert self._fn("voice", "msg.silk") is True

    def test_audio_content_type(self):
        assert self._fn("audio/mp3", "file.mp3") is True

    def test_voice_extension(self):
        assert self._fn("", "file.silk") is True

    def test_non_voice(self):
        assert self._fn("image/jpeg", "photo.jpg") is False

    def test_audio_extension_amr(self):
        assert self._fn("", "recording.amr") is True


# ---------------------------------------------------------------------------
# Voice attachment SSRF protection
# ---------------------------------------------------------------------------

class TestVoiceAttachmentSSRFProtection:
    def _make_adapter(self, **extra):
        from gateway.platforms.qqbot import QQAdapter
        return QQAdapter(_make_config(**extra))

    def test_stt_blocks_unsafe_download_url(self):
        adapter = self._make_adapter(app_id="a", client_secret="b")
        adapter._http_client = mock.AsyncMock()

        with mock.patch("tools.url_safety.is_safe_url", return_value=False):
            transcript = asyncio.run(
                adapter._stt_voice_attachment(
                    "http://127.0.0.1/voice.silk",
                    "audio/silk",
                    "voice.silk",
                )
            )

        assert transcript is None
        adapter._http_client.get.assert_not_called()

    def test_connect_uses_redirect_guard_hook(self):
        from gateway.platforms.qqbot import QQAdapter, _ssrf_redirect_guard

        client = mock.AsyncMock()
        with mock.patch("gateway.platforms.qqbot.adapter.httpx.AsyncClient", return_value=client) as async_client_cls:
            adapter = QQAdapter(_make_config(app_id="a", client_secret="b"))
            adapter._ensure_token = mock.AsyncMock(side_effect=RuntimeError("stop after client creation"))

            connected = asyncio.run(adapter.connect())

        assert connected is False
        assert async_client_cls.call_count == 1
        kwargs = async_client_cls.call_args.kwargs
        assert kwargs.get("follow_redirects") is True
        assert kwargs.get("event_hooks", {}).get("response") == [_ssrf_redirect_guard]


# ---------------------------------------------------------------------------
# WebSocket proxy handling
# ---------------------------------------------------------------------------

class TestQQWebSocketProxy:
    @pytest.mark.asyncio
    async def test_open_ws_honors_proxy_env(self, monkeypatch):
        from gateway.platforms.qqbot import QQAdapter

        for key in (
            "WSS_PROXY",
            "wss_proxy",
            "HTTPS_PROXY",
            "https_proxy",
            "ALL_PROXY",
            "all_proxy",
        ):
            monkeypatch.delenv(key, raising=False)
        monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:7897")

        adapter = QQAdapter(_make_config(app_id="a", client_secret="b"))

        seen_session_kwargs = {}
        seen_ws_kwargs = {}

        class FakeSession:
            def __init__(self, **kwargs):
                seen_session_kwargs.update(kwargs)
                self.closed = False

            async def close(self):
                self.closed = True

            async def ws_connect(self, *args, **kwargs):
                seen_ws_kwargs.update(kwargs)
                return mock.AsyncMock(closed=False)

        with mock.patch("gateway.platforms.qqbot.adapter.aiohttp.ClientSession", side_effect=FakeSession):
            await adapter._open_ws("wss://api.sgroup.qq.com/websocket")

        assert seen_session_kwargs.get("trust_env") is True
        assert seen_ws_kwargs.get("proxy") == "http://127.0.0.1:7897"

# ---------------------------------------------------------------------------
# _strip_at_mention
# ---------------------------------------------------------------------------

class TestStripAtMention:
    def _fn(self, content):
        from gateway.platforms.qqbot import QQAdapter
        return QQAdapter._strip_at_mention(content)

    def test_removes_mention(self):
        result = self._fn("@BotUser hello there")
        assert result == "hello there"

    def test_no_mention(self):
        result = self._fn("just text")
        assert result == "just text"

    def test_empty_string(self):
        assert self._fn("") == ""

    def test_only_mention(self):
        assert self._fn("@Someone  ") == ""


# ---------------------------------------------------------------------------
# _is_dm_allowed
# ---------------------------------------------------------------------------

class TestDmAllowed:
    def _make_adapter(self, **extra):
        from gateway.platforms.qqbot import QQAdapter
        return QQAdapter(_make_config(**extra))

    def test_open_policy(self):
        adapter = self._make_adapter(app_id="a", client_secret="b", dm_policy="open")
        assert adapter._is_dm_allowed("any_user") is True

    def test_disabled_policy(self):
        adapter = self._make_adapter(app_id="a", client_secret="b", dm_policy="disabled")
        assert adapter._is_dm_allowed("any_user") is False

    def test_allowlist_match(self):
        adapter = self._make_adapter(app_id="a", client_secret="b", dm_policy="allowlist", allow_from="user1,user2")
        assert adapter._is_dm_allowed("user1") is True

    def test_allowlist_no_match(self):
        adapter = self._make_adapter(app_id="a", client_secret="b", dm_policy="allowlist", allow_from="user1,user2")
        assert adapter._is_dm_allowed("user3") is False

    def test_allowlist_wildcard(self):
        adapter = self._make_adapter(app_id="a", client_secret="b", dm_policy="allowlist", allow_from="*")
        assert adapter._is_dm_allowed("anyone") is True


# ---------------------------------------------------------------------------
# _is_group_allowed
# ---------------------------------------------------------------------------

class TestGroupAllowed:
    def _make_adapter(self, **extra):
        from gateway.platforms.qqbot import QQAdapter
        return QQAdapter(_make_config(**extra))

    def test_open_policy(self):
        adapter = self._make_adapter(app_id="a", client_secret="b", group_policy="open")
        assert adapter._is_group_allowed("grp1", "user1") is True

    def test_allowlist_match(self):
        adapter = self._make_adapter(app_id="a", client_secret="b", group_policy="allowlist", group_allow_from="grp1")
        assert adapter._is_group_allowed("grp1", "user1") is True

    def test_allowlist_no_match(self):
        adapter = self._make_adapter(app_id="a", client_secret="b", group_policy="allowlist", group_allow_from="grp1")
        assert adapter._is_group_allowed("grp2", "user1") is False


# ---------------------------------------------------------------------------
# _resolve_stt_config
# ---------------------------------------------------------------------------

class TestResolveSTTConfig:
    def _make_adapter(self, **extra):
        from gateway.platforms.qqbot import QQAdapter
        return QQAdapter(_make_config(**extra))

    def test_no_config(self):
        adapter = self._make_adapter(app_id="a", client_secret="b")
        with mock.patch.dict(os.environ, {}, clear=True):
            assert adapter._resolve_stt_config() is None

    def test_env_config(self):
        adapter = self._make_adapter(app_id="a", client_secret="b")
        with mock.patch.dict(os.environ, {
            "QQ_STT_API_KEY": "key123",
            "QQ_STT_BASE_URL": "https://example.com/v1",
            "QQ_STT_MODEL": "my-model",
        }, clear=True):
            cfg = adapter._resolve_stt_config()
            assert cfg is not None
            assert cfg["api_key"] == "key123"
            assert cfg["base_url"] == "https://example.com/v1"
            assert cfg["model"] == "my-model"

    def test_extra_config(self):
        stt_cfg = {
            "baseUrl": "https://custom.api/v4",
            "apiKey": "sk_extra",
            "model": "glm-asr",
        }
        adapter = self._make_adapter(app_id="a", client_secret="b", stt=stt_cfg)
        with mock.patch.dict(os.environ, {}, clear=True):
            cfg = adapter._resolve_stt_config()
            assert cfg is not None
            assert cfg["base_url"] == "https://custom.api/v4"
            assert cfg["api_key"] == "sk_extra"
            assert cfg["model"] == "glm-asr"


# ---------------------------------------------------------------------------
# _detect_message_type
# ---------------------------------------------------------------------------

class TestDetectMessageType:
    def _fn(self, media_urls, media_types):
        from gateway.platforms.qqbot import QQAdapter
        return QQAdapter._detect_message_type(media_urls, media_types)

    def test_no_media(self):
        from gateway.platforms.base import MessageType
        assert self._fn([], []) == MessageType.TEXT

    def test_image(self):
        from gateway.platforms.base import MessageType
        assert self._fn(["file.jpg"], ["image/jpeg"]) == MessageType.PHOTO

    def test_voice(self):
        from gateway.platforms.base import MessageType
        assert self._fn(["voice.silk"], ["audio/silk"]) == MessageType.VOICE

    def test_video(self):
        from gateway.platforms.base import MessageType
        assert self._fn(["vid.mp4"], ["video/mp4"]) == MessageType.VIDEO


# ---------------------------------------------------------------------------
# QQCloseError
# ---------------------------------------------------------------------------

class TestQQCloseError:
    def test_attributes(self):
        from gateway.platforms.qqbot import QQCloseError
        err = QQCloseError(4004, "bad token")
        assert err.code == 4004
        assert err.reason == "bad token"

    def test_code_none(self):
        from gateway.platforms.qqbot import QQCloseError
        err = QQCloseError(None, "")
        assert err.code is None

    def test_string_to_int(self):
        from gateway.platforms.qqbot import QQCloseError
        err = QQCloseError("4914", "banned")
        assert err.code == 4914
        assert err.reason == "banned"

    def test_message_format(self):
        from gateway.platforms.qqbot import QQCloseError
        err = QQCloseError(4008, "rate limit")
        assert "4008" in str(err)
        assert "rate limit" in str(err)


# ---------------------------------------------------------------------------
# _dispatch_payload
# ---------------------------------------------------------------------------

class TestDispatchPayload:
    def _make_adapter(self, **extra):
        from gateway.platforms.qqbot import QQAdapter
        adapter = QQAdapter(_make_config(**extra))
        return adapter

    def test_unknown_op(self):
        adapter = self._make_adapter(app_id="a", client_secret="b")
        # Should not raise
        adapter._dispatch_payload({"op": 99, "d": {}})
        # last_seq should remain None
        assert adapter._last_seq is None

    def test_op10_updates_heartbeat_interval(self):
        adapter = self._make_adapter(app_id="a", client_secret="b")
        adapter._dispatch_payload({"op": 10, "d": {"heartbeat_interval": 50000}})
        # Should be 50000 / 1000 * 0.8 = 40.0
        assert adapter._heartbeat_interval == 40.0

    def test_op11_heartbeat_ack(self):
        adapter = self._make_adapter(app_id="a", client_secret="b")
        # Should not raise
        adapter._dispatch_payload({"op": 11, "t": "HEARTBEAT_ACK", "s": 42})

    def test_seq_tracking(self):
        adapter = self._make_adapter(app_id="a", client_secret="b")
        adapter._dispatch_payload({"op": 0, "t": "READY", "s": 100, "d": {}})
        assert adapter._last_seq == 100

    def test_seq_increments(self):
        adapter = self._make_adapter(app_id="a", client_secret="b")
        adapter._dispatch_payload({"op": 0, "t": "READY", "s": 5, "d": {}})
        adapter._dispatch_payload({"op": 0, "t": "SOME_EVENT", "s": 10, "d": {}})
        assert adapter._last_seq == 10


# ---------------------------------------------------------------------------
# READY / RESUMED handling
# ---------------------------------------------------------------------------

class TestReadyHandling:
    def _make_adapter(self, **extra):
        from gateway.platforms.qqbot import QQAdapter
        return QQAdapter(_make_config(**extra))

    def test_ready_stores_session(self):
        adapter = self._make_adapter(app_id="a", client_secret="b")
        adapter._dispatch_payload({
            "op": 0, "t": "READY",
            "s": 1,
            "d": {"session_id": "sess_abc123"},
        })
        assert adapter._session_id == "sess_abc123"

    def test_resumed_preserves_session(self):
        adapter = self._make_adapter(app_id="a", client_secret="b")
        adapter._session_id = "old_sess"
        adapter._last_seq = 50
        adapter._dispatch_payload({
            "op": 0, "t": "RESUMED", "s": 60, "d": {},
        })
        # Session should remain unchanged on RESUMED
        assert adapter._session_id == "old_sess"
        assert adapter._last_seq == 60


# ---------------------------------------------------------------------------
# _parse_json
# ---------------------------------------------------------------------------

class TestParseJson:
    def _fn(self, raw):
        from gateway.platforms.qqbot import QQAdapter
        return QQAdapter._parse_json(raw)

    def test_valid_json(self):
        result = self._fn('{"op": 10, "d": {}}')
        assert result == {"op": 10, "d": {}}

    def test_invalid_json(self):
        result = self._fn("not json")
        assert result is None

    def test_none_input(self):
        result = self._fn(None)
        assert result is None

    def test_non_dict_json(self):
        result = self._fn('"just a string"')
        assert result is None

    def test_empty_dict(self):
        result = self._fn('{}')
        assert result == {}


# ---------------------------------------------------------------------------
# _build_text_body
# ---------------------------------------------------------------------------

class TestBuildTextBody:
    def _make_adapter(self, **extra):
        from gateway.platforms.qqbot import QQAdapter
        return QQAdapter(_make_config(**extra))

    def test_plain_text(self):
        adapter = self._make_adapter(app_id="a", client_secret="b", markdown_support=False)
        body = adapter._build_text_body("hello world")
        assert body["msg_type"] == 0  # MSG_TYPE_TEXT
        assert body["content"] == "hello world"

    def test_markdown_text(self):
        adapter = self._make_adapter(app_id="a", client_secret="b", markdown_support=True)
        body = adapter._build_text_body("**bold** text")
        assert body["msg_type"] == 2  # MSG_TYPE_MARKDOWN
        assert body["markdown"]["content"] == "**bold** text"

    def test_truncation(self):
        adapter = self._make_adapter(app_id="a", client_secret="b", markdown_support=False)
        long_text = "x" * 10000
        body = adapter._build_text_body(long_text)
        assert len(body["content"]) == adapter.MAX_MESSAGE_LENGTH

    def test_empty_string(self):
        adapter = self._make_adapter(app_id="a", client_secret="b", markdown_support=False)
        body = adapter._build_text_body("")
        assert body["content"] == ""

    def test_reply_to(self):
        adapter = self._make_adapter(app_id="a", client_secret="b", markdown_support=False)
        body = adapter._build_text_body("reply text", reply_to="msg_123")
        assert body.get("message_reference", {}).get("message_id") == "msg_123"


# ---------------------------------------------------------------------------
# _wait_for_reconnection / send reconnection wait
# ---------------------------------------------------------------------------

class TestWaitForReconnection:
    """Test that send() waits for reconnection instead of silently dropping."""

    def _make_adapter(self, **extra):
        from gateway.platforms.qqbot import QQAdapter
        return QQAdapter(_make_config(**extra))

    @pytest.mark.asyncio
    async def test_send_waits_and_succeeds_on_reconnect(self):
        """send() should wait for reconnection and then deliver the message."""
        adapter = self._make_adapter(app_id="a", client_secret="b")
        # Initially disconnected
        adapter._running = False
        adapter._http_client = mock.MagicMock()

        # Simulate reconnection after 0.3s (faster than real interval)
        async def fake_api_request(*args, **kwargs):
            return {"id": "msg_123"}

        adapter._api_request = fake_api_request
        adapter._ensure_token = mock.AsyncMock()
        adapter._RECONNECT_POLL_INTERVAL = 0.1
        adapter._RECONNECT_WAIT_SECONDS = 5.0

        # Schedule reconnection after a short delay
        async def reconnect_after_delay():
            await asyncio.sleep(0.3)
            adapter._running = True

        asyncio.get_event_loop().create_task(reconnect_after_delay())

        result = await adapter.send("test_openid", "Hello, world!")
        assert result.success
        assert result.message_id == "msg_123"

    @pytest.mark.asyncio
    async def test_send_returns_retryable_after_timeout(self):
        """send() should return retryable=True if reconnection takes too long."""
        adapter = self._make_adapter(app_id="a", client_secret="b")
        adapter._running = False
        adapter._RECONNECT_POLL_INTERVAL = 0.05
        adapter._RECONNECT_WAIT_SECONDS = 0.2

        result = await adapter.send("test_openid", "Hello, world!")
        assert not result.success
        assert result.retryable is True
        assert "Not connected" in result.error

    @pytest.mark.asyncio
    async def test_send_succeeds_immediately_when_connected(self):
        """send() should not wait when already connected."""
        adapter = self._make_adapter(app_id="a", client_secret="b")
        adapter._running = True
        adapter._http_client = mock.MagicMock()

        async def fake_api_request(*args, **kwargs):
            return {"id": "msg_immediate"}

        adapter._api_request = fake_api_request

        result = await adapter.send("test_openid", "Hello!")
        assert result.success
        assert result.message_id == "msg_immediate"

    @pytest.mark.asyncio
    async def test_send_media_waits_for_reconnect(self):
        """_send_media should also wait for reconnection."""
        adapter = self._make_adapter(app_id="a", client_secret="b")
        adapter._running = False
        adapter._RECONNECT_POLL_INTERVAL = 0.05
        adapter._RECONNECT_WAIT_SECONDS = 0.2

        result = await adapter._send_media("test_openid", "http://example.com/img.jpg", 1, "image")
        assert not result.success
        assert result.retryable is True
        assert "Not connected" in result.error


# ---------------------------------------------------------------------------
# InlineKeyboard / approval / slash-confirm
# ---------------------------------------------------------------------------

class TestQQAdapterKeyboard:
    """Tests for InlineKeyboard button construction and interaction handling."""

    def _make_adapter(self, **extra):
        from gateway.platforms.qqbot import QQAdapter
        adapter = QQAdapter(_make_config(markdown_support=True, **extra))
        adapter._http_client = mock.MagicMock()
        return adapter

    # -- Keyboard button construction --

    def test_build_keyboard_button_structure(self):
        from gateway.platforms.qqbot.constants import KEYBOARD_ACTION_TYPE
        adapter = self._make_adapter(app_id="a", client_secret="b")
        btn = adapter._build_keyboard_button("Click me", "cb:data", 0)
        assert btn["render_data"]["label"] == "Click me"
        assert btn["render_data"]["style"] == 0
        assert btn["action"]["type"] == KEYBOARD_ACTION_TYPE
        assert btn["action"]["data"] == "cb:data"

    def test_build_keyboard_rows_structure(self):
        adapter = self._make_adapter(app_id="a", client_secret="b")
        row1 = [adapter._build_keyboard_button("A", "a")]
        row2 = [adapter._build_keyboard_button("B", "b"), adapter._build_keyboard_button("C", "c")]
        kb = adapter._build_keyboard_rows([row1, row2])
        assert len(kb["rows"]) == 2
        assert len(kb["rows"][0]["buttons"]) == 1
        assert len(kb["rows"][1]["buttons"]) == 2

    # -- send_exec_approval --

    @pytest.mark.asyncio
    async def test_send_exec_approval_returns_not_connected(self):
        """send_exec_approval returns error when not connected."""
        from gateway.platforms.qqbot import QQAdapter
        adapter = QQAdapter(_make_config(markdown_support=True, app_id="a", client_secret="b"))
        # _http_client is None (not connected)
        result = await adapter.send_exec_approval("chat1", "rm -rf /", "session_key")
        assert not result.success
        assert "Not connected" in result.error

    @pytest.mark.asyncio
    async def test_send_exec_approval_returns_not_supported_without_markdown(self):
        """send_exec_approval fails gracefully when markdown is disabled."""
        from gateway.platforms.qqbot import QQAdapter
        adapter = QQAdapter(_make_config(markdown_support=False, app_id="a", client_secret="b"))
        adapter._http_client = mock.MagicMock()
        result = await adapter.send_exec_approval("chat1", "rm -rf /", "session_key")
        assert not result.success
        assert "markdown_support" in result.error

    @pytest.mark.asyncio
    async def test_send_exec_approval_stores_approval_state(self):
        """send_exec_approval stores state for later callback resolution."""
        adapter = self._make_adapter(app_id="a", client_secret="b")

        async def fake_keyboard_send(*args, **kwargs):
            from gateway.platforms.base import SendResult
            return SendResult(success=True, message_id="msg_approval_1")

        adapter._send_keyboard_message = fake_keyboard_send
        adapter._guess_chat_type = lambda x: "c2c"

        result = await adapter.send_exec_approval("chat1", "rm -rf /", "sess_key_1")
        assert result.success
        # Verify approval state was stored
        assert len(adapter._approval_state) == 1
        state = list(adapter._approval_state.values())[0]
        assert state["session_key"] == "sess_key_1"
        assert state["chat_id"] == "chat1"

    # -- send_slash_confirm --

    @pytest.mark.asyncio
    async def test_send_slash_confirm_returns_not_connected(self):
        """send_slash_confirm returns error when not connected."""
        from gateway.platforms.qqbot import QQAdapter
        adapter = QQAdapter(_make_config(markdown_support=True, app_id="a", client_secret="b"))
        result = await adapter.send_slash_confirm("chat1", "Title", "msg", "sess", "cid1")
        assert not result.success
        assert "Not connected" in result.error

    @pytest.mark.asyncio
    async def test_send_slash_confirm_stores_state(self):
        """send_slash_confirm stores confirm_id → session_key mapping."""
        adapter = self._make_adapter(app_id="a", client_secret="b")

        async def fake_keyboard_send(*args, **kwargs):
            from gateway.platforms.base import SendResult
            return SendResult(success=True, message_id="msg_confirm_1")

        adapter._send_keyboard_message = fake_keyboard_send

        result = await adapter.send_slash_confirm(
            "chat1", "Confirm?", "Please confirm", "sess_key_2", "confirm_42",
        )
        assert result.success
        assert adapter._slash_confirm_state.get("confirm_42") == "sess_key_2"

    # -- Interaction callback parsing --

    @pytest.mark.asyncio
    async def test_on_interaction_parses_approval_callback(self):
        """_on_interaction routes approval button clicks correctly."""
        adapter = self._make_adapter(app_id="a", client_secret="b")
        approval_id = next(adapter._approval_counter)
        adapter._approval_state[approval_id] = {
            "session_key": "sess_1",
            "message_id": "msg_1",
            "chat_id": "chat_1",
        }

        resolved = []

        async def fake_resolve(session_key, choice):
            resolved.append((session_key, choice))
            return "✅ Approved"

        async def fake_send(chat_id, text, metadata=None):
            pass

        with mock.patch("tools.approval.resolve_gateway_approval", fake_resolve):
            adapter.send = fake_send
            await adapter._on_interaction({
                "id": "interaction_1",
                "data": {
                    "resolved": {
                        "button_data": f"hermes:approval:approve_once:{approval_id}",
                    },
                },
            })

        assert len(resolved) == 1
        assert resolved[0] == ("sess_1", "once")

    @pytest.mark.asyncio
    async def test_on_interaction_ignores_unknown_callback(self):
        """_on_interaction ignores callbacks with unrecognized format."""
        adapter = self._make_adapter(app_id="a", client_secret="b")
        # Should not raise
        await adapter._on_interaction({
            "id": "interaction_2",
            "data": {"resolved": {"button_data": "unknown:format"}},
        })

    @pytest.mark.asyncio
    async def test_on_interaction_ignores_empty_data(self):
        """_on_interaction ignores events without button_data."""
        adapter = self._make_adapter(app_id="a", client_secret="b")
        # Should not raise
        await adapter._on_interaction({
            "id": "interaction_3",
            "data": {},
        })
        await adapter._on_interaction("not a dict")

    @pytest.mark.asyncio
    async def test_on_interaction_parses_slash_confirm_callback(self):
        """_on_interaction routes slash_confirm button clicks correctly."""
        adapter = self._make_adapter(app_id="a", client_secret="b")
        adapter._slash_confirm_state["confirm_99"] = "sess_sc"

        resolved = []

        async def fake_sc_resolve(session_key, confirm_id, choice):
            resolved.append((session_key, confirm_id, choice))
            return None

        with mock.patch("tools.slash_confirm.resolve", fake_sc_resolve):
            await adapter._on_interaction({
                "id": "interaction_sc",
                "data": {
                    "resolved": {
                        "button_data": "hermes:slash_confirm:once:confirm_99",
                    },
                },
            })

        assert len(resolved) == 1
        assert resolved[0] == ("sess_sc", "confirm_99", "once")
