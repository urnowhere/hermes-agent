"""Tests for the /plan CLI slash command."""

import pytest
from unittest.mock import MagicMock, patch

from agent.skill_commands import scan_skill_commands
from cli import HermesCLI


@pytest.mark.skip(reason="Windows: NoConsoleScreenBufferError in pytest parallel mode")
class TestCLIPlanCommand:
    def _make_cli(self):
        cli_obj = HermesCLI.__new__(HermesCLI)
        cli_obj.config = {}
        cli_obj.console = MagicMock()
        cli_obj.agent = None
        cli_obj.conversation_history = []
        cli_obj.session_id = "sess-123"
        cli_obj._pending_input = MagicMock()
        return cli_obj

    def _make_plan_skill(self, skills_dir):
        skill_dir = skills_dir / "plan"
        skill_dir.mkdir(parents=True, exist_ok=True)
        (skill_dir / "SKILL.md").write_text(
            """---
name: plan
description: Plan mode skill.
---

# Plan

Use the current conversation context when no explicit instruction is provided.
Save plans under the active workspace's .hermes/plans directory.
"""
        )

    def test_plan_without_args_uses_skill_context_guidance(self, tmp_path, monkeypatch):
        cli_obj = self._make_cli()

        with patch("tools.skills_tool.SKILLS_DIR", tmp_path):
            self._make_plan_skill(tmp_path)
            scan_skill_commands()
            cli_obj.process_command("/plan")

        queued = cli_obj._pending_input.put.call_args[0][0]
        assert "current conversation context" in queued
        assert ".hermes/plans/" in queued
        assert "conversation-plan.md" in queued
