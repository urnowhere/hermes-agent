import importlib
import sys
import types
from contextlib import nullcontext
from types import SimpleNamespace

import pytest

from hermes_cli.auth import AuthError
from hermes_cli import main as hermes_main


@pytest.mark.skip(reason="Windows: NoConsoleScreenBufferError in pytest parallel mode")
class TestCLIProviderResolution:
    # ---------------------------------------------------------------------------
    # Module isolation: _import_cli() wipes tools.* / cli / run_agent from
    # sys.modules so it can re-import cli fresh.  Without cleanup the wiped
    # modules leak into subsequent tests on the same xdist worker, breaking
    # mock patches that target "tools.file_tools._get_file_ops" etc.
    # ---------------------------------------------------------------------------

    def _reset_modules(self, prefixes: tuple[str, ...]):
        for name in list(sys.modules):
            if any(name == p or name.startswith(p + ".") for p in prefixes):
                sys.modules.pop(name, None)

    @pytest.fixture(autouse=True)
    def _restore_cli_and_tool_modules(self):
        """Save and restore tools/cli/run_agent modules around every test."""
        prefixes = ("tools", "cli", "run_agent")
        original_modules = {
            name: module
            for name, module in sys.modules.items()
            if any(name == p or name.startswith(p + ".") for p in prefixes)
        }
        try:
            yield
        finally:
            self._reset_modules(prefixes)
            sys.modules.update(original_modules)

    def _install_prompt_toolkit_stubs(self):
        class _Dummy:
            def __init__(self, *args, **kwargs):
                pass

        class _Condition:
            def __init__(self, func):
                self.func = func

            def __bool__(self):
                return bool(self.func())

        class _ANSI(str):
            pass

        root = types.ModuleType("prompt_toolkit")
        history = types.ModuleType("prompt_toolkit.history")
        styles = types.ModuleType("prompt_toolkit.styles")
        patch_stdout = types.ModuleType("prompt_toolkit.patch_stdout")
        application = types.ModuleType("prompt_toolkit.application")
        layout = types.ModuleType("prompt_toolkit.layout")
        processors = types.ModuleType("prompt_toolkit.layout.processors")
        filters = types.ModuleType("prompt_toolkit.filters")
        dimension = types.ModuleType("prompt_toolkit.layout.dimension")
        menus = types.ModuleType("prompt_toolkit.layout.menus")
        widgets = types.ModuleType("prompt_toolkit.widgets")
        key_binding = types.ModuleType("prompt_toolkit.key_binding")
        completion = types.ModuleType("prompt_toolkit.completion")
        formatted_text = types.ModuleType("prompt_toolkit.formatted_text")

        history.FileHistory = _Dummy
        styles.Style = _Dummy
        patch_stdout.patch_stdout = lambda *args, **kwargs: nullcontext()
        application.Application = _Dummy
        layout.Layout = _Dummy
        layout.HSplit = _Dummy
        layout.Window = _Dummy
        layout.FormattedTextControl = _Dummy
        layout.ConditionalContainer = _Dummy
        processors.Processor = _Dummy
        processors.Transformation = _Dummy
        processors.PasswordProcessor = _Dummy
        processors.ConditionalProcessor = _Dummy
        filters.Condition = _Condition
        dimension.Dimension = _Dummy
        menus.CompletionsMenu = _Dummy
        widgets.TextArea = _Dummy
        key_binding.KeyBindings = _Dummy
        completion.Completer = _Dummy
        completion.Completion = _Dummy
        formatted_text.ANSI = _ANSI
        root.print_formatted_text = lambda *args, **kwargs: None

        sys.modules.setdefault("prompt_toolkit", root)
        sys.modules.setdefault("prompt_toolkit.history", history)
        sys.modules.setdefault("prompt_toolkit.styles", styles)
        sys.modules.setdefault("prompt_toolkit.patch_stdout", patch_stdout)
        sys.modules.setdefault("prompt_toolkit.application", application)
        sys.modules.setdefault("prompt_toolkit.layout", layout)
        sys.modules.setdefault("prompt_toolkit.layout.processors", processors)
        sys.modules.setdefault("prompt_toolkit.filters", filters)
        sys.modules.setdefault("prompt_toolkit.layout.dimension", dimension)
        sys.modules.setdefault("prompt_toolkit.layout.menus", menus)
        sys.modules.setdefault("prompt_toolkit.widgets", widgets)
        sys.modules.setdefault("prompt_toolkit.key_binding", key_binding)
        sys.modules.setdefault("prompt_toolkit.completion", completion)
        sys.modules.setdefault("prompt_toolkit.formatted_text", formatted_text)

    def _import_cli(self):
        for name in list(sys.modules):
            if (
                name == "cli"
                or name == "run_agent"
                or name == "tools"
                or name.startswith("tools.")
            ):
                sys.modules.pop(name, None)

        if "firecrawl" not in sys.modules:
            sys.modules["firecrawl"] = types.SimpleNamespace(Firecrawl=object)

        try:
            importlib.import_module("prompt_toolkit")
        except ModuleNotFoundError:
            self._install_prompt_toolkit_stubs()
        return importlib.import_module("cli")

    def test_runtime_resolution_failure_is_not_sticky(self, monkeypatch):
        cli = self._import_cli()
        calls = {"count": 0}

        def _runtime_resolve(**kwargs):
            calls["count"] += 1
            if calls["count"] == 1:
                raise RuntimeError("temporary auth failure")
            return {
                "provider": "openrouter",
                "api_mode": "chat_completions",
                "base_url": "https://openrouter.ai/api/v1",
                "api_key": "test-key",
                "source": "env/config",
            }

        class _DummyAgent:
            def __init__(self, *args, **kwargs):
                self.kwargs = kwargs

        monkeypatch.setattr(
            "hermes_cli.runtime_provider.resolve_runtime_provider", _runtime_resolve
        )
        monkeypatch.setattr(
            "hermes_cli.runtime_provider.format_runtime_provider_error",
            lambda exc: str(exc),
        )
        monkeypatch.setattr(cli, "AIAgent", _DummyAgent)

        shell = cli.HermesCLI(model="gpt-5", compact=True, max_turns=1)

        assert shell._init_agent() is False
        assert shell._init_agent() is True
        assert calls["count"] == 2
        assert shell.agent is not None
