"""P2 routing controls: kill switches and guarded Feishu auto-dispatch."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from gateway.background_wakeups import clear_background_wake_manifest_cache
from gateway.config import GatewayConfig, Platform, load_gateway_config
from gateway.platforms.base import MessageEvent, MessageType
from gateway.route_decision import (
    build_feishu_route_decision_shadow_hint,
    resolve_route_decision,
    should_auto_dispatch_feishu,
)
from gateway.session import SessionSource


ROUTE_AUDIT_PROMPT = "请体系化审查和制定route机制提升计划，阅读开源社区先进案例、Hermes本身机制"
EXTERNAL_WRITE_PROMPT = "帮我把这份报告发布到外部群，并公开分享链接"


@pytest.fixture(autouse=True)
def isolated_hermes_home(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    clear_background_wake_manifest_cache()
    yield tmp_path
    clear_background_wake_manifest_cache()


def _make_runner():
    from gateway.run import GatewayRunner

    runner = object.__new__(GatewayRunner)
    runner.adapters = {}
    runner._running_agents = {}
    runner._background_tasks = set()
    runner._session_key_for_source = lambda source: f"agent:main:{source.platform.value}:dm:{source.chat_id}"
    runner.config = GatewayConfig()
    return runner


def _source(platform=Platform.FEISHU):
    return SessionSource(
        platform=platform,
        user_id="u-test",
        chat_id="c-test",
        user_name="testuser",
        chat_type="dm",
    )


def test_gateway_config_defaults_and_kill_switch_overrides():
    defaults = GatewayConfig()
    assert defaults.feishu_auto_dispatch_enabled is False
    assert defaults.feishu_route_shadow_hints_enabled is True

    enabled = GatewayConfig.from_dict(
        {
            "feishu_auto_dispatch_enabled": True,
            "feishu_route_shadow_hints_enabled": False,
        }
    )
    assert enabled.feishu_auto_dispatch_enabled is True
    assert enabled.feishu_route_shadow_hints_enabled is False

    serialized = enabled.to_dict()
    assert serialized["feishu_auto_dispatch_enabled"] is True
    assert serialized["feishu_route_shadow_hints_enabled"] is False


def test_load_gateway_config_reads_routing_block_kill_switches(isolated_hermes_home):
    (isolated_hermes_home / "config.yaml").write_text(
        """
routing:
  feishu_auto_dispatch_enabled: false
  feishu_route_shadow_hints_enabled: false
""".strip()
        + "\n",
        encoding="utf-8",
    )

    config = load_gateway_config()

    assert config.feishu_auto_dispatch_enabled is False
    assert config.feishu_route_shadow_hints_enabled is False


def test_auto_dispatch_kill_switch_downgrades_safe_roi_decision():
    config = GatewayConfig.from_dict({"feishu_auto_dispatch_enabled": False})

    decision = resolve_route_decision(
        ROUTE_AUDIT_PROMPT,
        platform="feishu",
        active_toolsets=("terminal", "file", "skills"),
        feishu_auto_dispatch_enabled=config.feishu_auto_dispatch_enabled,
    )

    assert decision.decision_type == "suggest_wrapper"
    assert should_auto_dispatch_feishu(
        decision,
        feishu_auto_dispatch_enabled=config.feishu_auto_dispatch_enabled,
    ) is False
    assert "auto_dispatch_disabled" in decision.reasons


def test_shadow_hint_kill_switch_suppresses_route_decision_hint():
    decision = resolve_route_decision(
        ROUTE_AUDIT_PROMPT,
        platform="feishu",
        active_toolsets=("terminal", "file", "skills"),
    )

    assert build_feishu_route_decision_shadow_hint(decision, enabled=False) == ""


@pytest.mark.asyncio
async def test_auto_dispatch_gateway_respects_config_kill_switch():
    from gateway.run import GatewayRunner

    runner = _make_runner()
    runner.config = GatewayConfig.from_dict({"feishu_auto_dispatch_enabled": False})
    runner._handle_background_command = AsyncMock(return_value="should not run")
    event = MessageEvent(text=ROUTE_AUDIT_PROMPT, source=_source())

    result = await GatewayRunner._maybe_auto_dispatch_feishu_route(
        runner,
        event,
        event.source,
        active_toolsets=("terminal", "file", "skills"),
    )

    assert result is None
    runner._handle_background_command.assert_not_called()


@pytest.mark.asyncio
async def test_auto_dispatch_gateway_default_config_is_opt_in_disabled():
    from gateway.run import GatewayRunner

    runner = _make_runner()
    runner.config = GatewayConfig()
    runner._handle_background_command = AsyncMock(return_value="should not run")
    event = MessageEvent(text=ROUTE_AUDIT_PROMPT, source=_source())

    result = await GatewayRunner._maybe_auto_dispatch_feishu_route(
        runner,
        event,
        event.source,
        active_toolsets=("terminal", "file", "skills"),
    )

    assert result is None
    runner._handle_background_command.assert_not_called()


@pytest.mark.asyncio
async def test_auto_dispatch_gateway_routes_safe_internal_work_with_forced_routes():
    from gateway.run import GatewayRunner

    runner = _make_runner()
    runner.config = GatewayConfig.from_dict({"feishu_auto_dispatch_enabled": True})
    runner._handle_background_command = AsyncMock(return_value="background-started")
    event = MessageEvent(text=ROUTE_AUDIT_PROMPT, source=_source())

    result = await GatewayRunner._maybe_auto_dispatch_feishu_route(
        runner,
        event,
        event.source,
        active_toolsets=("terminal", "file", "skills"),
    )

    assert result is not None
    assert "Auto-dispatched" in result
    assert "research" in result
    assert "repo" in result
    runner._handle_background_command.assert_awaited_once_with(
        event,
        forced_routes=("research", "repo", "multi_agent"),
    )


@pytest.mark.asyncio
async def test_auto_dispatch_gateway_blocks_risky_or_nonplain_events():
    from gateway.run import GatewayRunner

    runner = _make_runner()
    runner.config = GatewayConfig.from_dict({"feishu_auto_dispatch_enabled": True})
    runner._handle_background_command = AsyncMock(return_value="should not run")

    cases = [
        MessageEvent(text=EXTERNAL_WRITE_PROMPT, source=_source()),
        MessageEvent(text="/status", source=_source()),
        MessageEvent(text=ROUTE_AUDIT_PROMPT, source=_source(Platform.TELEGRAM)),
        MessageEvent(text=ROUTE_AUDIT_PROMPT, source=_source(), internal=True),
        MessageEvent(text=ROUTE_AUDIT_PROMPT, source=_source(), media_urls=["/tmp/x.png"], message_type=MessageType.PHOTO),
    ]

    for event in cases:
        assert await GatewayRunner._maybe_auto_dispatch_feishu_route(
            runner,
            event,
            event.source,
            active_toolsets=("terminal", "file", "skills"),
        ) is None

    runner._handle_background_command.assert_not_called()


@pytest.mark.asyncio
async def test_handle_message_invokes_feishu_auto_dispatch_before_foreground_agent(monkeypatch):
    from gateway.run import GatewayRunner

    runner = _make_runner()
    runner.config = GatewayConfig.from_dict({"feishu_auto_dispatch_enabled": True})
    runner._is_user_authorized = lambda source: True
    runner._get_unauthorized_dm_behavior = lambda platform: "ignore"
    runner._is_telegram_topic_root_lobby = lambda source: False
    runner._draining = False
    runner.hooks = MagicMock()
    runner.hooks.emit_collect = AsyncMock(return_value=[])
    runner._maybe_auto_dispatch_feishu_route = AsyncMock(return_value="auto-dispatched")
    runner._handle_message_with_agent = AsyncMock(side_effect=AssertionError("foreground agent should not run"))

    event = MessageEvent(text=ROUTE_AUDIT_PROMPT, source=_source())

    assert await GatewayRunner._handle_message(runner, event) == "auto-dispatched"
    runner._maybe_auto_dispatch_feishu_route.assert_awaited_once()
