"""Tests for the enzyme memory provider plugin.

Tests the MemoryProvider implementation without requiring the enzyme binary —
all subprocess calls are mocked.
"""

import json
import os
import pytest
from unittest.mock import patch, MagicMock

from agent.memory_provider import MemoryProvider
from agent.memory_manager import MemoryManager
from agent.builtin_memory_provider import BuiltinMemoryProvider


# ---------------------------------------------------------------------------
# Import the provider (relative to repo root)
# ---------------------------------------------------------------------------

import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from plugins.memory.enzyme import EnzymeMemoryProvider


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

SAMPLE_PETRI_OUTPUT = """\
entities:
  - name: "design-systems"
    type: tag
    frequency: 42
    activity_trend: rising
    catalysts:
      - text: "What makes a design system actually get adopted?"
        context: "recurring theme across 12 notes"
  - name: "agent-architecture"
    type: tag
    frequency: 28
    activity_trend: stable
    catalysts:
      - text: "How do agents decide what to remember vs forget?"
        context: "3 recent notes exploring memory trade-offs"
"""

SAMPLE_CATALYZE_OUTPUT = """\
results:
  - file_path: "2026-03-15 design tokens.md"
    content: "Decided to use CSS custom properties over Tailwind tokens..."
    similarity: 0.87
  - file_path: "2026-02-28 component API.md"
    content: "The composable pattern works better than render props here..."
    similarity: 0.82
"""


def _fake_run(cmd, capture_output=True, text=True, timeout=30):
    """Mock subprocess.run that returns canned enzyme output."""
    args = cmd if isinstance(cmd, list) else cmd.split()
    result = MagicMock()
    result.returncode = 0
    result.stderr = ""

    if "petri" in args:
        result.stdout = SAMPLE_PETRI_OUTPUT
    elif "catalyze" in args:
        result.stdout = SAMPLE_CATALYZE_OUTPUT
    elif "refresh" in args:
        result.stdout = ""
    elif "init" in args:
        result.stdout = SAMPLE_PETRI_OUTPUT
    elif "status" in args:
        result.stdout = "docs: 347, entities: 42, catalysts: 156, coverage: 94%"
    elif "--version" in args:
        result.stdout = "enzyme 0.4.4"
    else:
        result.stdout = ""

    return result


# ---------------------------------------------------------------------------
# ABC compliance
# ---------------------------------------------------------------------------


class TestEnzymeABCCompliance:
    def test_is_memory_provider(self):
        p = EnzymeMemoryProvider()
        assert isinstance(p, MemoryProvider)

    def test_name(self):
        p = EnzymeMemoryProvider()
        assert p.name == "enzyme"

    def test_has_required_methods(self):
        p = EnzymeMemoryProvider()
        assert callable(p.is_available)
        assert callable(p.initialize)
        assert callable(p.system_prompt_block)
        assert callable(p.prefetch)
        assert callable(p.get_tool_schemas)
        assert callable(p.handle_tool_call)
        assert callable(p.shutdown)


# ---------------------------------------------------------------------------
# Availability
# ---------------------------------------------------------------------------


class TestEnzymeAvailability:
    @patch("plugins.memory.enzyme.subprocess.run", side_effect=FileNotFoundError)
    def test_unavailable_when_binary_missing(self, mock_run):
        p = EnzymeMemoryProvider()
        assert not p.is_available()

    @patch("plugins.memory.enzyme.subprocess.run", side_effect=_fake_run)
    def test_available_when_binary_exists(self, mock_run):
        p = EnzymeMemoryProvider()
        assert p.is_available()


# ---------------------------------------------------------------------------
# Initialization
# ---------------------------------------------------------------------------


class TestEnzymeInitialize:
    @patch("plugins.memory.enzyme.subprocess.run", side_effect=_fake_run)
    @patch("plugins.memory.enzyme._vault_is_initialized", return_value=False)
    @patch("plugins.memory.enzyme._is_enzyme_available", return_value=True)
    def test_init_when_no_index(self, mock_avail, mock_vault, mock_run):
        p = EnzymeMemoryProvider()
        p.initialize(session_id="test-1")
        assert p._initialized

    @patch("plugins.memory.enzyme.subprocess.run", side_effect=_fake_run)
    @patch("plugins.memory.enzyme._vault_is_initialized", return_value=True)
    @patch("plugins.memory.enzyme._is_enzyme_available", return_value=True)
    def test_refresh_when_already_initialized(self, mock_avail, mock_vault, mock_run):
        p = EnzymeMemoryProvider()
        p.initialize(session_id="test-2")
        assert p._initialized

    @patch("plugins.memory.enzyme._find_enzyme_bin", return_value="")
    def test_skip_when_binary_missing(self, mock_find):
        p = EnzymeMemoryProvider()
        p.initialize(session_id="test-3")
        assert not p._initialized


# ---------------------------------------------------------------------------
# System prompt block
# ---------------------------------------------------------------------------


class TestEnzymeSystemPromptBlock:
    @patch("plugins.memory.enzyme.subprocess.run", side_effect=_fake_run)
    @patch("plugins.memory.enzyme._vault_is_initialized", return_value=True)
    @patch("plugins.memory.enzyme._is_enzyme_available", return_value=True)
    def test_returns_petri_context(self, mock_avail, mock_vault, mock_run):
        p = EnzymeMemoryProvider()
        p.initialize(session_id="test")
        block = p.system_prompt_block()
        assert "Enzyme" in block
        assert "design-systems" in block
        assert "catalysts" in block or "catalyst" in block.lower()

    def test_empty_when_not_initialized(self):
        p = EnzymeMemoryProvider()
        assert p.system_prompt_block() == ""


# ---------------------------------------------------------------------------
# Prefetch
# ---------------------------------------------------------------------------


class TestEnzymePrefetch:
    @patch("plugins.memory.enzyme.subprocess.run", side_effect=_fake_run)
    @patch("plugins.memory.enzyme._vault_is_initialized", return_value=True)
    @patch("plugins.memory.enzyme._is_enzyme_available", return_value=True)
    def test_prefetch_returns_context(self, mock_avail, mock_vault, mock_run):
        p = EnzymeMemoryProvider()
        p.initialize(session_id="test")
        result = p.prefetch("design tokens")
        assert "Vault context" in result

    def test_prefetch_empty_when_not_initialized(self):
        p = EnzymeMemoryProvider()
        assert p.prefetch("anything") == ""

    @patch("plugins.memory.enzyme.subprocess.run", side_effect=_fake_run)
    @patch("plugins.memory.enzyme._vault_is_initialized", return_value=True)
    @patch("plugins.memory.enzyme._is_enzyme_available", return_value=True)
    def test_prefetch_empty_for_empty_query(self, mock_avail, mock_vault, mock_run):
        p = EnzymeMemoryProvider()
        p.initialize(session_id="test")
        assert p.prefetch("") == ""


# ---------------------------------------------------------------------------
# Tool schemas
# ---------------------------------------------------------------------------


class TestEnzymeToolSchemas:
    def test_returns_five_tools(self):
        p = EnzymeMemoryProvider()
        schemas = p.get_tool_schemas()
        assert len(schemas) == 5
        names = {s["name"] for s in schemas}
        assert names == {
            "enzyme_petri", "enzyme_catalyze", "enzyme_refresh",
            "enzyme_status", "enzyme_init",
        }

    def test_schemas_have_required_fields(self):
        p = EnzymeMemoryProvider()
        for schema in p.get_tool_schemas():
            assert "name" in schema
            assert "description" in schema
            assert "parameters" in schema
            assert schema["description"]  # not empty


# ---------------------------------------------------------------------------
# Tool handling
# ---------------------------------------------------------------------------


class TestEnzymeToolHandling:
    @patch("plugins.memory.enzyme.subprocess.run", side_effect=_fake_run)
    @patch("plugins.memory.enzyme._vault_is_initialized", return_value=True)
    @patch("plugins.memory.enzyme._is_enzyme_available", return_value=True)
    def test_handle_petri(self, mock_avail, mock_vault, mock_run):
        p = EnzymeMemoryProvider()
        p.initialize(session_id="test")
        result = json.loads(p.handle_tool_call("enzyme_petri", {"top": 5}))
        assert "output" in result
        assert "design-systems" in result["output"]

    @patch("plugins.memory.enzyme.subprocess.run", side_effect=_fake_run)
    @patch("plugins.memory.enzyme._vault_is_initialized", return_value=True)
    @patch("plugins.memory.enzyme._is_enzyme_available", return_value=True)
    def test_handle_catalyze(self, mock_avail, mock_vault, mock_run):
        p = EnzymeMemoryProvider()
        p.initialize(session_id="test")
        result = json.loads(p.handle_tool_call(
            "enzyme_catalyze", {"query": "design tokens", "limit": 5}
        ))
        assert "output" in result

    @patch("plugins.memory.enzyme.subprocess.run", side_effect=_fake_run)
    @patch("plugins.memory.enzyme._vault_is_initialized", return_value=True)
    @patch("plugins.memory.enzyme._is_enzyme_available", return_value=True)
    def test_handle_refresh(self, mock_avail, mock_vault, mock_run):
        p = EnzymeMemoryProvider()
        p.initialize(session_id="test")
        result = json.loads(p.handle_tool_call("enzyme_refresh", {"full": True}))
        assert result["ok"]

    @patch("plugins.memory.enzyme.subprocess.run", side_effect=_fake_run)
    @patch("plugins.memory.enzyme._vault_is_initialized", return_value=True)
    @patch("plugins.memory.enzyme._is_enzyme_available", return_value=True)
    def test_handle_status(self, mock_avail, mock_vault, mock_run):
        p = EnzymeMemoryProvider()
        p.initialize(session_id="test")
        result = json.loads(p.handle_tool_call("enzyme_status", {}))
        assert "output" in result

    @patch("plugins.memory.enzyme.subprocess.run", side_effect=_fake_run)
    @patch("plugins.memory.enzyme._vault_is_initialized", return_value=True)
    @patch("plugins.memory.enzyme._is_enzyme_available", return_value=True)
    def test_handle_init(self, mock_avail, mock_vault, mock_run):
        p = EnzymeMemoryProvider()
        p.initialize(session_id="test")
        result = json.loads(p.handle_tool_call(
            "enzyme_init", {"guide": "design\nagent", "quiet": True}
        ))
        assert result["ok"]

    def test_unknown_tool_returns_error(self):
        p = EnzymeMemoryProvider()
        result = json.loads(p.handle_tool_call("nonexistent_tool", {}))
        assert "error" in result


# ---------------------------------------------------------------------------
# Manager integration
# ---------------------------------------------------------------------------


class TestEnzymeManagerIntegration:
    @patch("plugins.memory.enzyme.subprocess.run", side_effect=_fake_run)
    @patch("plugins.memory.enzyme._vault_is_initialized", return_value=True)
    @patch("plugins.memory.enzyme._is_enzyme_available", return_value=True)
    def test_full_lifecycle(self, mock_avail, mock_vault, mock_run):
        """Exercise the full manager lifecycle with enzyme."""
        mgr = MemoryManager()
        builtin = BuiltinMemoryProvider()
        enzyme = EnzymeMemoryProvider()

        mgr.add_provider(builtin)
        mgr.add_provider(enzyme)
        assert mgr.provider_names == ["builtin", "enzyme"]

        # Initialize
        mgr.initialize_all(session_id="test-session", platform="cli")
        assert enzyme._initialized

        # System prompt includes petri
        prompt = mgr.build_system_prompt()
        assert "Enzyme" in prompt

        # Tool schemas registered
        schemas = mgr.get_all_tool_schemas()
        names = {s["name"] for s in schemas}
        assert "enzyme_petri" in names
        assert "enzyme_catalyze" in names

        # Tool routing
        assert mgr.has_tool("enzyme_petri")
        assert mgr.has_tool("enzyme_catalyze")
        result = json.loads(mgr.handle_tool_call("enzyme_petri", {"top": 3}))
        assert "output" in result

        # Prefetch
        prefetched = mgr.prefetch_all("design systems")
        assert "Vault context" in prefetched

        # Shutdown
        mgr.shutdown_all()
        assert not enzyme._initialized

    @patch("plugins.memory.enzyme.subprocess.run", side_effect=_fake_run)
    @patch("plugins.memory.enzyme._vault_is_initialized", return_value=True)
    @patch("plugins.memory.enzyme._is_enzyme_available", return_value=True)
    def test_sync_turn_is_noop(self, mock_avail, mock_vault, mock_run):
        """Enzyme doesn't auto-ingest turns — verify no crash."""
        mgr = MemoryManager()
        mgr.add_provider(BuiltinMemoryProvider())
        mgr.add_provider(EnzymeMemoryProvider())
        mgr.initialize_all(session_id="test")
        mgr.sync_all("user message", "assistant response")  # should not raise


# ---------------------------------------------------------------------------
# Plugin discovery
# ---------------------------------------------------------------------------


class TestEnzymeDiscovery:
    def test_discover_finds_enzyme(self):
        from plugins.memory import discover_memory_providers
        providers = discover_memory_providers()
        names = [name for name, _, _ in providers]
        assert "enzyme" in names

    def test_load_enzyme_provider(self):
        from plugins.memory import load_memory_provider
        p = load_memory_provider("enzyme")
        assert p is not None
        assert p.name == "enzyme"

    def test_register_function_exists(self):
        from plugins.memory.enzyme import register
        assert callable(register)


# ---------------------------------------------------------------------------
# Session end
# ---------------------------------------------------------------------------


class TestEnzymeSessionEnd:
    @patch("plugins.memory.enzyme.subprocess.run", side_effect=_fake_run)
    @patch("plugins.memory.enzyme._vault_is_initialized", return_value=True)
    @patch("plugins.memory.enzyme._is_enzyme_available", return_value=True)
    def test_session_end_refreshes(self, mock_avail, mock_vault, mock_run):
        p = EnzymeMemoryProvider()
        p.initialize(session_id="test")
        p.on_session_end([{"role": "user", "content": "hi"}])
        # Should not raise — refresh runs silently

    def test_session_end_noop_when_not_initialized(self):
        p = EnzymeMemoryProvider()
        p.on_session_end([])  # should not raise
