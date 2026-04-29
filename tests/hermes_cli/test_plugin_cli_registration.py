"""Tests for plugin CLI registration system.

Covers:
  - PluginContext.register_cli_command()
  - PluginManager._cli_commands storage
  - get_plugin_cli_commands() convenience function
  - Memory plugin CLI discovery (discover_plugin_cli_commands)
  - Honcho register_cli() builds correct argparse tree
"""

import argparse
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from hermes_cli.plugins import (
    PluginContext,
    PluginManager,
    PluginManifest,
)


# ── PluginContext.register_cli_command ─────────────────────────────────────


class TestRegisterCliCommand:
    def _make_ctx(self):
        mgr = PluginManager()
        manifest = PluginManifest(name="test-plugin")
        return PluginContext(manifest, mgr), mgr

    def test_registers_command(self):
        ctx, mgr = self._make_ctx()
        setup = MagicMock()
        handler = MagicMock()
        ctx.register_cli_command(
            name="mycmd",
            help="Do something",
            setup_fn=setup,
            handler_fn=handler,
            description="Full description",
        )
        assert "mycmd" in mgr._cli_commands
        entry = mgr._cli_commands["mycmd"]
        assert entry["name"] == "mycmd"
        assert entry["help"] == "Do something"
        assert entry["setup_fn"] is setup
        assert entry["handler_fn"] is handler
        assert entry["plugin"] == "test-plugin"

    def test_normalizes_name(self):
        ctx, mgr = self._make_ctx()
        ctx.register_cli_command(" /My Cmd ", "Do something", MagicMock())
        assert "my-cmd" in mgr._cli_commands
        assert "/My Cmd " not in mgr._cli_commands

    def test_empty_name_rejected(self, caplog):
        ctx, mgr = self._make_ctx()
        with caplog.at_level("WARNING", logger="hermes_cli.plugins"):
            ctx.register_cli_command(" / ", "Do something", MagicMock())
        assert mgr._cli_commands == {}
        assert "empty name" in caplog.text

    def test_duplicate_rejected_preserves_first(self, caplog):
        ctx, mgr = self._make_ctx()
        first = MagicMock()
        second = MagicMock()
        ctx.register_cli_command("x", "first", first)
        with caplog.at_level("WARNING", logger="hermes_cli.plugins"):
            ctx.register_cli_command("x", "second", second)
        assert mgr._cli_commands["x"]["help"] == "first"
        assert mgr._cli_commands["x"]["setup_fn"] is first
        assert "already registered" in caplog.text.lower()

    def test_handler_optional(self):
        ctx, mgr = self._make_ctx()
        ctx.register_cli_command("nocb", "test", MagicMock())
        assert mgr._cli_commands["nocb"]["handler_fn"] is None


class TestGeneralPluginCliDiscovery:
    def test_get_plugin_cli_commands_discovers_plugins_lazily(self):
        import hermes_cli.plugins as plugins_mod

        mgr = PluginManager()
        manifest = PluginManifest(name="test-plugin", source="user")
        ctx = PluginContext(manifest, mgr)
        setup = MagicMock()
        handler = MagicMock()
        ctx.register_cli_command(
            name="mycmd",
            help="Do something",
            setup_fn=setup,
            handler_fn=handler,
            description="Full description",
        )

        original = getattr(plugins_mod, "_plugin_manager", None)
        plugins_mod._plugin_manager = mgr
        try:
            cmds = plugins_mod.get_plugin_cli_commands()
        finally:
            plugins_mod._plugin_manager = original

        assert "mycmd" in cmds
        entry = cmds["mycmd"]
        assert entry["setup_fn"] is setup
        assert entry["handler_fn"] is handler
        assert entry["plugin"] == "test-plugin"

    def test_main_registers_general_plugin_cli_commands(self, monkeypatch):
        import hermes_cli.main as main_mod

        received = {}

        def setup_fn(subparser):
            subparser.add_argument("--value", required=True)

        def handler_fn(args):
            received["value"] = args.value

        monkeypatch.setattr(
            "hermes_cli.plugins.get_plugin_cli_commands",
            lambda: {
                "plugcmd": {
                    "name": "plugcmd",
                    "help": "Plugin command",
                    "description": "Plugin command description",
                    "setup_fn": setup_fn,
                    "handler_fn": handler_fn,
                    "plugin": "test-plugin",
                }
            },
            raising=False,
        )
        monkeypatch.setattr("plugins.memory.discover_plugin_cli_commands", lambda: [], raising=False)
        monkeypatch.setattr(sys, "argv", ["hermes", "plugcmd", "--value", "ok"])

        main_mod.main()

        assert received == {"value": "ok"}

    def test_main_skips_builtin_name_collision(self, monkeypatch):
        import hermes_cli.main as main_mod

        handler = MagicMock()

        def setup_fn(subparser):
            subparser.add_argument("--value")

        monkeypatch.setattr(
            "hermes_cli.plugins.get_plugin_cli_commands",
            lambda: {
                "version": {
                    "name": "version",
                    "help": "Plugin command",
                    "description": "Plugin command description",
                    "setup_fn": setup_fn,
                    "handler_fn": handler,
                    "plugin": "test-plugin",
                }
            },
            raising=False,
        )
        monkeypatch.setattr("plugins.memory.discover_plugin_cli_commands", lambda: [], raising=False)
        monkeypatch.setattr(sys, "argv", ["hermes", "version"])

        main_mod.main()

        handler.assert_not_called()


# ── Memory plugin CLI discovery ───────────────────────────────────────────


class TestMemoryPluginCliDiscovery:
    def test_discovers_active_plugin_with_register_cli(self, tmp_path, monkeypatch):
        """Only the active memory provider's CLI commands are discovered."""
        plugin_dir = tmp_path / "testplugin"
        plugin_dir.mkdir()
        (plugin_dir / "__init__.py").write_text("pass\n")
        (plugin_dir / "cli.py").write_text(
            "def register_cli(subparser):\n"
            "    subparser.add_argument('--test')\n"
            "\n"
            "def testplugin_command(args):\n"
            "    pass\n"
        )
        (plugin_dir / "plugin.yaml").write_text(
            "name: testplugin\ndescription: A test plugin\n"
        )

        # Also create a second plugin that should NOT be discovered
        other_dir = tmp_path / "otherplugin"
        other_dir.mkdir()
        (other_dir / "__init__.py").write_text("pass\n")
        (other_dir / "cli.py").write_text(
            "def register_cli(subparser):\n"
            "    subparser.add_argument('--other')\n"
        )

        import plugins.memory as pm
        original_dir = pm._MEMORY_PLUGINS_DIR
        mod_key = "plugins.memory.testplugin.cli"
        sys.modules.pop(mod_key, None)

        monkeypatch.setattr(pm, "_MEMORY_PLUGINS_DIR", tmp_path)
        # Set testplugin as the active provider
        monkeypatch.setattr(pm, "_get_active_memory_provider", lambda: "testplugin")
        try:
            cmds = pm.discover_plugin_cli_commands()
        finally:
            monkeypatch.setattr(pm, "_MEMORY_PLUGINS_DIR", original_dir)
            sys.modules.pop(mod_key, None)

        # Only testplugin should be discovered, not otherplugin
        assert len(cmds) == 1
        assert cmds[0]["name"] == "testplugin"
        assert cmds[0]["help"] == "A test plugin"
        assert callable(cmds[0]["setup_fn"])
        assert cmds[0]["handler_fn"].__name__ == "testplugin_command"

    def test_returns_nothing_when_no_active_provider(self, tmp_path, monkeypatch):
        """No commands when memory.provider is not set in config."""
        plugin_dir = tmp_path / "testplugin"
        plugin_dir.mkdir()
        (plugin_dir / "__init__.py").write_text("pass\n")
        (plugin_dir / "cli.py").write_text(
            "def register_cli(subparser):\n    pass\n"
        )

        import plugins.memory as pm
        original_dir = pm._MEMORY_PLUGINS_DIR
        monkeypatch.setattr(pm, "_MEMORY_PLUGINS_DIR", tmp_path)
        monkeypatch.setattr(pm, "_get_active_memory_provider", lambda: None)
        try:
            cmds = pm.discover_plugin_cli_commands()
        finally:
            monkeypatch.setattr(pm, "_MEMORY_PLUGINS_DIR", original_dir)

        assert len(cmds) == 0

    def test_skips_plugin_without_register_cli(self, tmp_path, monkeypatch):
        """An active plugin with cli.py but no register_cli returns nothing."""
        plugin_dir = tmp_path / "noplugin"
        plugin_dir.mkdir()
        (plugin_dir / "__init__.py").write_text("pass\n")
        (plugin_dir / "cli.py").write_text("def some_other_fn():\n    pass\n")

        import plugins.memory as pm
        original_dir = pm._MEMORY_PLUGINS_DIR
        monkeypatch.setattr(pm, "_MEMORY_PLUGINS_DIR", tmp_path)
        monkeypatch.setattr(pm, "_get_active_memory_provider", lambda: "noplugin")
        try:
            cmds = pm.discover_plugin_cli_commands()
        finally:
            monkeypatch.setattr(pm, "_MEMORY_PLUGINS_DIR", original_dir)
            sys.modules.pop("plugins.memory.noplugin.cli", None)

        assert len(cmds) == 0

    def test_skips_plugin_without_cli_py(self, tmp_path, monkeypatch):
        """An active provider without cli.py returns nothing."""
        plugin_dir = tmp_path / "nocli"
        plugin_dir.mkdir()
        (plugin_dir / "__init__.py").write_text("pass\n")

        import plugins.memory as pm
        original_dir = pm._MEMORY_PLUGINS_DIR
        monkeypatch.setattr(pm, "_MEMORY_PLUGINS_DIR", tmp_path)
        monkeypatch.setattr(pm, "_get_active_memory_provider", lambda: "nocli")
        try:
            cmds = pm.discover_plugin_cli_commands()
        finally:
            monkeypatch.setattr(pm, "_MEMORY_PLUGINS_DIR", original_dir)

        assert len(cmds) == 0


# ── Honcho register_cli ──────────────────────────────────────────────────


# ── ProviderCollector no-op ──────────────────────────────────────────────


class TestProviderCollectorCliNoop:
    def test_register_cli_command_is_noop(self):
        """_ProviderCollector.register_cli_command is a no-op (doesn't crash)."""
        from plugins.memory import _ProviderCollector

        collector = _ProviderCollector()
        collector.register_cli_command(
            name="test", help="test", setup_fn=lambda s: None
        )
        # Should not store anything — CLI is discovered via file convention
        assert not hasattr(collector, "_cli_commands")
