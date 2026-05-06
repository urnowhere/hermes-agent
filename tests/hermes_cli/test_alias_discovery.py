"""Tests for model alias discoverability — list_all_aliases and /aliases command."""

from __future__ import annotations

import pytest

import hermes_cli.model_switch as ms
from hermes_cli.model_switch import (
    MODEL_ALIASES,
    DirectAlias,
    _resolve_builtin_alias_target,
    list_all_aliases,
)
from hermes_cli.commands import COMMAND_REGISTRY, resolve_command


# ---------------------------------------------------------------------------
# list_all_aliases unit tests
# ---------------------------------------------------------------------------

class TestListAllAliases:
    """Test list_all_aliases() output structure and correctness."""

    def test_returns_list_of_tuples(self, monkeypatch):
        """list_all_aliases returns list of (name, resolved_model, source) tuples."""
        monkeypatch.setattr(ms, "DIRECT_ALIASES", {})
        result = list_all_aliases()
        assert isinstance(result, list)
        for entry in result:
            assert isinstance(entry, tuple)
            assert len(entry) == 3
            name, resolved, source = entry
            assert isinstance(name, str)
            assert isinstance(resolved, str)
            assert source in ("builtin", "user")

    def test_builtin_aliases_present(self, monkeypatch):
        """Built-in aliases like 'sonnet', 'opus', 'gpt5' are included."""
        monkeypatch.setattr(ms, "DIRECT_ALIASES", {})
        result = list_all_aliases()
        names = {name for name, _, _ in result}
        # All MODEL_ALIASES keys should be present (with no DIRECT_ALIASES to shadow)
        for expected in ("sonnet", "opus", "haiku", "gpt5", "gemini", "grok"):
            assert expected in names, f"Missing built-in alias: {expected}"

    def test_user_aliases_override_builtins(self, monkeypatch):
        """User-defined aliases shadow built-in aliases with the same name."""
        monkeypatch.setattr(ms, "DIRECT_ALIASES", {
            "sonnet": DirectAlias(model="custom-sonnet-v1", provider="custom", base_url=""),
        })
        result = list_all_aliases()
        sonnet_entries = [(n, r, s) for n, r, s in result if n == "sonnet"]
        assert len(sonnet_entries) == 1
        name, resolved, source = sonnet_entries[0]
        assert source == "user"
        assert resolved == "custom-sonnet-v1"

    def test_user_aliases_listed_as_user_source(self, monkeypatch):
        """User-defined direct aliases appear with source='user'."""
        monkeypatch.setattr(ms, "DIRECT_ALIASES", {
            "my-model": DirectAlias(model="custom/v1", provider="custom", base_url=""),
        })
        result = list_all_aliases()
        user_entries = [(n, r, s) for n, r, s in result if s == "user"]
        user_names = {n for n, _, _ in user_entries}
        assert "my-model" in user_names

    def test_no_duplicates_when_user_shadows_builtin(self, monkeypatch):
        """A user alias that shadows a builtin should not produce duplicate entries."""
        monkeypatch.setattr(ms, "DIRECT_ALIASES", {
            "grok": DirectAlias(model="my-grok", provider="custom", base_url=""),
        })
        result = list_all_aliases()
        grok_entries = [n for n, _, _ in result if n == "grok"]
        assert len(grok_entries) == 1

    def test_empty_direct_aliases_gives_only_builtins(self, monkeypatch):
        """With no user aliases, only builtins are returned."""
        monkeypatch.setattr(ms, "DIRECT_ALIASES", {})
        result = list_all_aliases()
        sources = {s for _, _, s in result}
        assert sources == {"builtin"}

    def test_sorted_within_each_source(self, monkeypatch):
        """User and builtin aliases are each sorted by name."""
        monkeypatch.setattr(ms, "DIRECT_ALIASES", {
            "zzz-custom": DirectAlias(model="custom-z", provider="custom", base_url=""),
            "aaa-custom": DirectAlias(model="custom-a", provider="custom", base_url=""),
        })
        result = list_all_aliases()
        user_names = [n for n, _, s in result if s == "user"]
        builtin_names = [n for n, _, s in result if s == "builtin"]
        assert user_names == sorted(user_names)
        assert builtin_names == sorted(builtin_names)


# ---------------------------------------------------------------------------
# _resolve_builtin_alias_target unit tests
# ---------------------------------------------------------------------------

class TestResolveBuiltinAliasTarget:
    """Test _resolve_builtin_alias_target produces a reasonable model string."""

    def test_returns_string(self, monkeypatch):
        """_resolve_builtin_alias_target always returns a string."""
        monkeypatch.setattr(ms, "DIRECT_ALIASES", {})
        for name, identity in list(MODEL_ALIASES.items())[:5]:
            result = _resolve_builtin_alias_target(name, identity)
            assert isinstance(result, str)
            assert len(result) > 0

    def test_fallback_format(self, monkeypatch):
        """When resolve_alias and catalog fail, returns vendor/family fallback."""
        monkeypatch.setattr(ms, "resolve_alias", lambda *a, **kw: None)
        monkeypatch.setattr(ms, "list_provider_models", lambda *a, **kw: [])
        identity = ms.ModelIdentity("test-vendor", "test-family")
        result = _resolve_builtin_alias_target("test", identity)
        assert result == "test-vendor/test-family"


# ---------------------------------------------------------------------------
# /aliases command registration
# ---------------------------------------------------------------------------

class TestAliasesCommandRegistration:
    """Verify /aliases is registered in the command registry."""

    def test_aliases_command_exists(self):
        cmd = resolve_command("aliases")
        assert cmd is not None
        assert cmd.name == "aliases"
        assert cmd.category == "Info"

    def test_aliases_command_has_als_alias(self):
        cmd = resolve_command("als")
        assert cmd is not None
        assert cmd.name == "aliases"

    def test_aliases_in_registry(self):
        names = [cmd.name for cmd in COMMAND_REGISTRY]
        assert "aliases" in names

    def test_aliases_not_cli_only_not_gateway_only(self):
        """aliases command should be available in both CLI and gateway."""
        cmd = resolve_command("aliases")
        assert cmd.cli_only is False
        assert cmd.gateway_only is False
