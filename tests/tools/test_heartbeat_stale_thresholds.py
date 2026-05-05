"""Tests for delegate heartbeat stale threshold configuration."""

import pytest


class TestHeartbeatStaleThresholds:
    """Verify the heartbeat stale threshold constants are correct."""

    def test_idle_cycles_value(self):
        """IDLE stale cycles should be 5 (5 * 30s = 150s)."""
        from tools.delegate_tool import _HEARTBEAT_STALE_CYCLES_IDLE
        assert _HEARTBEAT_STALE_CYCLES_IDLE == 5

    def test_in_tool_cycles_value(self):
        """IN_TOOL stale cycles should be 40 (40 * 30s = 1200s)."""
        from tools.delegate_tool import _HEARTBEAT_STALE_CYCLES_IN_TOOL
        assert _HEARTBEAT_STALE_CYCLES_IN_TOOL == 40

    def test_idle_timeout_seconds(self):
        """Effective idle stale timeout: 5 * 30 = 150s for wedged children outside tools."""
        from tools.delegate_tool import _HEARTBEAT_STALE_CYCLES_IDLE, _HEARTBEAT_INTERVAL
        effective = _HEARTBEAT_STALE_CYCLES_IDLE * _HEARTBEAT_INTERVAL
        assert effective == 150
        assert effective < 300  # Idle wedged children should stop masking gateway inactivity

    def test_in_tool_timeout_seconds(self):
        """Effective in-tool stale timeout: 40 * 30 = 1200s (= 20 minutes)."""
        from tools.delegate_tool import _HEARTBEAT_STALE_CYCLES_IN_TOOL, _HEARTBEAT_INTERVAL
        effective = _HEARTBEAT_STALE_CYCLES_IN_TOOL * _HEARTBEAT_INTERVAL
        assert effective == 1200

    def test_interval_unchanged(self):
        """Heartbeat interval should remain 30s."""
        from tools.delegate_tool import _HEARTBEAT_INTERVAL
        assert _HEARTBEAT_INTERVAL == 30
