"""Tests for the /caveman toggle command (CLI and gateway)."""

import os
import asyncio
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import gateway.run as gateway_run
from gateway.config import Platform
from gateway.platforms.base import MessageEvent
from gateway.session import SessionEntry, SessionSource
from hermes_cli.commands import (
    CAVEMAN_INTENSITY_RULES,
    CAVEMAN_SYSTEM_INSTRUCTION,
    resolve_command,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_source(chat_id="test:chat1", user_id="user1"):
    return SessionSource(platform=Platform.TELEGRAM, chat_id=chat_id, user_id=user_id)


def _make_event(text="/caveman", chat_id="test:chat1", user_id="user1"):
    """Build a minimal MessageEvent for testing."""
    source = _make_source(chat_id=chat_id, user_id=user_id)
    return MessageEvent(text=text, source=source)


def _make_session_entry(session_key="test:chat1", session_id="sess_001"):
    """Build a minimal SessionEntry."""
    now = datetime(2025, 1, 1, 12, 0, 0)
    return SessionEntry(
        session_key=session_key,
        session_id=session_id,
        created_at=now,
        updated_at=now,
    )


def _make_runner(session_entry=None, transcript=None):
    """Create a bare GatewayRunner with a stubbed session_store."""
    runner = object.__new__(gateway_run.GatewayRunner)
    runner.adapters = {}
    runner._ephemeral_system_prompt = ""
    runner._prefill_messages = []
    runner._reasoning_config = None
    runner._show_reasoning = False
    runner._provider_routing = {}
    runner._fallback_model = None
    runner._running_agents = {}
    runner.hooks = MagicMock()
    runner.hooks.emit = AsyncMock()
    runner.hooks.loaded_hooks = []
    runner._session_db = None
    runner._get_or_create_gateway_honcho = lambda session_key: (None, None)

    entry = session_entry or _make_session_entry()
    history = transcript if transcript is not None else []

    store = MagicMock()
    store.get_or_create_session.return_value = entry
    store.load_transcript.return_value = list(history)
    store.rewrite_transcript = MagicMock()
    runner.session_store = store

    return runner, entry


# ---------------------------------------------------------------------------
# CAVEMAN_INTENSITY_RULES
# ---------------------------------------------------------------------------

class TestCavemanIntensityRules:
    def test_keys_match_valid_intensities(self):
        assert set(CAVEMAN_INTENSITY_RULES) == {"lite", "full", "ultra"}

    def test_values_are_nonempty_strings(self):
        for key, val in CAVEMAN_INTENSITY_RULES.items():
            assert isinstance(val, str) and val, f"Empty rule for {key!r}"


# ---------------------------------------------------------------------------
# Command registry
# ---------------------------------------------------------------------------

class TestCavemanCommandDef:
    def test_registered(self):
        cmd = resolve_command("caveman")
        assert cmd is not None
        assert cmd.name == "caveman"

    def test_alias_resolves_to_canonical(self):
        cmd = resolve_command("cav")
        assert cmd is not None
        assert cmd.name == "caveman"

    def test_description_has_no_percentage_claim(self):
        cmd = resolve_command("caveman")
        assert "75%" not in cmd.description

    def test_valid_intensities_in_args_hint(self):
        cmd = resolve_command("caveman")
        for word in ("lite", "full", "ultra"):
            assert word in cmd.args_hint


# ---------------------------------------------------------------------------
# Gateway handler
# ---------------------------------------------------------------------------

class TestGatewayCavemanCommand:
    """Tests for GatewayRunner._handle_caveman_command."""

    @pytest.mark.asyncio
    async def test_toggle_on_default_intensity(self):
        runner, entry = _make_runner()
        result = await runner._handle_caveman_command(_make_event("/caveman"))

        assert entry.caveman_mode == "full"
        assert "ON" in result
        assert "full" in result
        assert "75%" not in result

    @pytest.mark.asyncio
    async def test_toggle_on_lite_intensity(self):
        runner, entry = _make_runner()
        result = await runner._handle_caveman_command(_make_event("/caveman lite"))

        assert entry.caveman_mode == "lite"
        assert "lite" in result

    @pytest.mark.asyncio
    async def test_toggle_on_injects_system_message_into_transcript(self):
        runner, entry = _make_runner(transcript=[])
        await runner._handle_caveman_command(_make_event("/caveman"))

        runner.session_store.rewrite_transcript.assert_called_once()
        rewritten = runner.session_store.rewrite_transcript.call_args[0][1]
        assert len(rewritten) == 1
        assert rewritten[0]["role"] == "user"
        assert rewritten[0]["content"].startswith("[SYSTEM: CAVEMAN MODE ON")

    @pytest.mark.asyncio
    async def test_toggle_off_clears_caveman_mode(self):
        runner, entry = _make_runner()
        entry.caveman_mode = "full"
        result = await runner._handle_caveman_command(_make_event("/caveman"))

        assert entry.caveman_mode is None
        assert "OFF" in result
        assert "Normal speech restored" in result

    @pytest.mark.asyncio
    async def test_toggle_off_removes_caveman_entry_from_transcript(self):
        sentinel_msg = {"role": "user", "content": "[SYSTEM: CAVEMAN MODE ON — intensity: full] ..."}
        other_msg = {"role": "user", "content": "hello"}
        runner, entry = _make_runner(transcript=[sentinel_msg, other_msg])
        entry.caveman_mode = "full"

        await runner._handle_caveman_command(_make_event("/caveman"))

        runner.session_store.rewrite_transcript.assert_called_once()
        rewritten = runner.session_store.rewrite_transcript.call_args[0][1]
        assert rewritten == [other_msg]

    @pytest.mark.asyncio
    async def test_event_text_none_does_not_crash(self):
        """event.text = None must not raise an AttributeError."""
        runner, entry = _make_runner()
        event = _make_event()
        event.text = None
        # Should not raise
        result = await runner._handle_caveman_command(event)
        assert isinstance(result, str)

    @pytest.mark.asyncio
    async def test_invalid_intensity_warns_and_does_not_change_state(self):
        runner, entry = _make_runner()
        result = await runner._handle_caveman_command(_make_event("/caveman gibberish"))

        assert entry.caveman_mode is None
        assert "gibberish" in result
        assert "unchanged" in result

    @pytest.mark.asyncio
    async def test_toggle_twice_does_not_stack_history(self):
        """Toggling on twice must not append two caveman entries."""
        runner, entry = _make_runner()

        await runner._handle_caveman_command(_make_event("/caveman"))
        # Simulate what rewrite_transcript recorded
        written_first = runner.session_store.rewrite_transcript.call_args[0][1]
        # Feed that back as current transcript for the second call
        runner.session_store.load_transcript.return_value = list(written_first)

        runner.session_store.rewrite_transcript.reset_mock()
        await runner._handle_caveman_command(_make_event("/caveman full"))

        rewritten = runner.session_store.rewrite_transcript.call_args[0][1]
        caveman_entries = [
            m for m in rewritten
            if isinstance(m.get("content"), str)
            and m["content"].startswith("[SYSTEM: CAVEMAN MODE ON")
        ]
        assert len(caveman_entries) == 1, "Only one caveman entry should be in history after two toggles ON"

    @pytest.mark.asyncio
    async def test_session_isolation(self):
        """Caveman mode for one session must not affect another."""
        runner1, entry1 = _make_runner()
        runner2, entry2 = _make_runner()

        await runner1._handle_caveman_command(_make_event("/caveman", chat_id="chat:A"))
        # Simulate the second runner having a separate session entry
        assert entry1.caveman_mode == "full"
        assert entry2.caveman_mode is None

    @pytest.mark.asyncio
    async def test_injected_message_uses_shared_template(self):
        """Gateway transcript text must come from CAVEMAN_SYSTEM_INSTRUCTION so CLI
        and gateway cannot drift to competing wordings (see llm-instruction-compliance
        skill, pitfall 6: 'Dual injection fights itself')."""
        runner, _ = _make_runner()
        await runner._handle_caveman_command(_make_event("/caveman ultra"))

        rewritten = runner.session_store.rewrite_transcript.call_args[0][1]
        injected_content = rewritten[0]["content"]
        expected = CAVEMAN_SYSTEM_INSTRUCTION.format(
            intensity_upper="ULTRA",
            rule=CAVEMAN_INTENSITY_RULES["ultra"],
        )
        assert injected_content == expected

    @pytest.mark.asyncio
    async def test_cli_and_gateway_templates_match(self):
        """CLI and gateway must emit byte-identical instruction text for the same
        intensity. Wording drift between the two paths is how caveman started
        slipping in the first place."""
        import cli as cli_module

        runner, _ = _make_runner()
        await runner._handle_caveman_command(_make_event("/caveman full"))
        gateway_msg = runner.session_store.rewrite_transcript.call_args[0][1][0]["content"]

        c = object.__new__(cli_module.HermesCLI)
        c.conversation_history = []
        c.console = MagicMock()
        c._inject_caveman_instruction("on", "full")
        cli_msg = c.conversation_history[0]["content"]

        assert gateway_msg == cli_msg


# ---------------------------------------------------------------------------
# CLI toggle (unit — no agent needed)
# ---------------------------------------------------------------------------

class TestCliCavemanToggle:
    """Tests for HermesCLI._toggle_caveman and _inject_caveman_instruction."""

    def _make_cli(self):
        """Build a minimal HermesCLI instance with stubs."""
        import cli as cli_module

        obj = object.__new__(cli_module.HermesCLI)
        obj.conversation_history = []
        obj.console = MagicMock()
        return obj

    def test_toggle_on_sets_env_var(self, monkeypatch):
        monkeypatch.delenv("HERMES_CAVEMAN_MODE", raising=False)
        c = self._make_cli()
        c._toggle_caveman("/caveman")
        assert os.environ.get("HERMES_CAVEMAN_MODE") == "full"

    def test_toggle_on_lite_sets_env_var(self, monkeypatch):
        monkeypatch.delenv("HERMES_CAVEMAN_MODE", raising=False)
        c = self._make_cli()
        c._toggle_caveman("/caveman lite")
        assert os.environ.get("HERMES_CAVEMAN_MODE") == "lite"

    def test_toggle_off_clears_env_var(self, monkeypatch):
        monkeypatch.setenv("HERMES_CAVEMAN_MODE", "full")
        c = self._make_cli()
        c._toggle_caveman("/caveman")
        assert "HERMES_CAVEMAN_MODE" not in os.environ

    def test_invalid_intensity_warns_and_no_env_change(self, monkeypatch):
        monkeypatch.delenv("HERMES_CAVEMAN_MODE", raising=False)
        c = self._make_cli()
        c._toggle_caveman("/caveman gibberish")
        # Env var must remain unset
        assert "HERMES_CAVEMAN_MODE" not in os.environ
        # Warning must have been printed
        c.console.print.assert_called_once()
        warn_text = c.console.print.call_args[0][0]
        assert "gibberish" in warn_text

    def test_toggle_on_injects_system_message(self, monkeypatch):
        monkeypatch.delenv("HERMES_CAVEMAN_MODE", raising=False)
        c = self._make_cli()
        c._toggle_caveman("/caveman")
        assert len(c.conversation_history) == 1
        msg = c.conversation_history[0]
        assert msg["role"] == "user"
        assert msg["content"].startswith("[SYSTEM: CAVEMAN MODE ON")

    def test_toggle_off_removes_system_message(self, monkeypatch):
        monkeypatch.setenv("HERMES_CAVEMAN_MODE", "full")
        c = self._make_cli()
        c.conversation_history = [
            {"role": "user", "content": "[SYSTEM: CAVEMAN MODE ON — intensity: full] ..."},
            {"role": "user", "content": "hello"},
        ]
        c._toggle_caveman("/caveman")
        # The caveman entry must be gone; the real message must remain
        contents = [m["content"] for m in c.conversation_history]
        assert all(not s.startswith("[SYSTEM: CAVEMAN MODE") for s in contents)
        assert "hello" in contents

    def test_toggling_twice_does_not_stack_history(self, monkeypatch):
        monkeypatch.delenv("HERMES_CAVEMAN_MODE", raising=False)
        c = self._make_cli()
        c._inject_caveman_instruction("on", "full")
        c._inject_caveman_instruction("on", "ultra")
        caveman_entries = [
            m for m in c.conversation_history
            if m.get("content", "").startswith("[SYSTEM: CAVEMAN MODE ON")
        ]
        assert len(caveman_entries) == 1
        assert "ULTRA" in caveman_entries[0]["content"]
