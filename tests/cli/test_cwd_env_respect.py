"""Tests for load_cli_config() cwd resolution.

When the gateway resolves TERMINAL_CWD at startup and cli.py is later
imported lazily (via delegate_tool → CLI_CONFIG), load_cli_config() must
not overwrite the already-resolved value with os.getcwd().

Interactive CLI/TUI startup should use the shell launch directory for local
terminal sessions, even if config.yaml or .env has a stale terminal cwd.

Gateway config.yaml terminal.cwd remains the canonical source of truth.
.env TERMINAL_CWD and MESSAGING_CWD are deprecated.
See issue #10817.
"""

import os


# The sentinel values that mean "resolve at runtime"
_CWD_PLACEHOLDERS = (".", "auto", "cwd")


def _resolve_terminal_cwd(
    terminal_config: dict,
    defaults: dict,
    env: dict,
    *,
    gateway_runtime: bool = False,
):
    """Simulate the CWD resolution logic from load_cli_config().

    This mirrors the code in cli.py that forces interactive local sessions
    to os.getcwd(), while preserving pre-resolved gateway TERMINAL_CWD.
    """
    effective_backend = terminal_config.get("env_type", "local")
    force_interactive_cwd = not gateway_runtime and effective_backend == "local"
    if force_interactive_cwd:
        terminal_config["cwd"] = "/fake/getcwd"  # stand-in for os.getcwd()
        defaults["terminal"]["cwd"] = terminal_config["cwd"]
    elif terminal_config.get("cwd") in _CWD_PLACEHOLDERS:
        _existing_cwd = env.get("TERMINAL_CWD", "")
        if _existing_cwd and _existing_cwd not in _CWD_PLACEHOLDERS and os.path.isabs(_existing_cwd):
            terminal_config["cwd"] = _existing_cwd
            defaults["terminal"]["cwd"] = _existing_cwd
        else:
            if effective_backend == "local":
                terminal_config["cwd"] = "/fake/getcwd"  # stand-in for os.getcwd()
                defaults["terminal"]["cwd"] = terminal_config["cwd"]
            else:
                terminal_config.pop("cwd", None)

    # Simulate the bridging loop: write terminal_config["cwd"] to env
    _file_has_terminal = defaults.get("_file_has_terminal", False)
    if "cwd" in terminal_config:
        if force_interactive_cwd or _file_has_terminal or "TERMINAL_CWD" not in env:
            env["TERMINAL_CWD"] = str(terminal_config["cwd"])

    return env.get("TERMINAL_CWD", "")


class TestLazyImportGuard:
    """TERMINAL_CWD resolved by gateway must survive a lazy cli.py import."""

    def test_gateway_resolved_cwd_survives(self):
        """Gateway set TERMINAL_CWD → lazy cli import must not clobber."""
        env = {"TERMINAL_CWD": "/home/user/workspace"}
        terminal_config = {"cwd": ".", "env_type": "local"}
        defaults = {"terminal": {"cwd": "."}, "_file_has_terminal": False}

        result = _resolve_terminal_cwd(
            terminal_config, defaults, env, gateway_runtime=True
        )
        assert result == "/home/user/workspace"

    def test_gateway_resolved_cwd_survives_with_file_terminal(self):
        """Even when config.yaml has a terminal: section, resolved CWD survives."""
        env = {"TERMINAL_CWD": "/home/user/workspace"}
        terminal_config = {"cwd": ".", "env_type": "local"}
        defaults = {"terminal": {"cwd": "."}, "_file_has_terminal": True}

        result = _resolve_terminal_cwd(
            terminal_config, defaults, env, gateway_runtime=True
        )
        assert result == "/home/user/workspace"


class TestInteractiveCwdResolution:
    """Interactive CLI/TUI local sessions use the launch cwd."""

    def test_explicit_config_cwd_does_not_pin_interactive_cli(self):
        """terminal.cwd must not override the shell cwd for local CLI/TUI."""
        env = {}
        terminal_config = {"cwd": "/explicit/path", "env_type": "local"}
        defaults = {"terminal": {"cwd": "/explicit/path"}, "_file_has_terminal": True}

        result = _resolve_terminal_cwd(terminal_config, defaults, env)
        assert result == "/fake/getcwd"

    def test_env_terminal_cwd_does_not_pin_interactive_cli(self):
        """A stale .env TERMINAL_CWD must not override local launch cwd."""
        env = {"TERMINAL_CWD": "/old/gateway/value"}
        terminal_config = {"cwd": ".", "env_type": "local"}
        defaults = {"terminal": {"cwd": "."}, "_file_has_terminal": False}

        result = _resolve_terminal_cwd(terminal_config, defaults, env)
        assert result == "/fake/getcwd"

    def test_dot_cwd_resolves_to_getcwd_when_no_prior(self):
        """With no pre-set TERMINAL_CWD, "." resolves to os.getcwd()."""
        env = {}
        terminal_config = {"cwd": "."}
        defaults = {"terminal": {"cwd": "."}, "_file_has_terminal": False}

        result = _resolve_terminal_cwd(terminal_config, defaults, env)
        assert result == "/fake/getcwd"


class TestGatewayConfigCwdResolution:
    """Gateway config.yaml terminal.cwd is the canonical source of truth."""

    def test_explicit_config_cwd_wins_for_gateway_runtime(self):
        """terminal.cwd: /explicit/path still anchors gateway sessions."""
        env = {"TERMINAL_CWD": "/old/gateway/value"}
        terminal_config = {"cwd": "/explicit/path"}
        defaults = {"terminal": {"cwd": "/explicit/path"}, "_file_has_terminal": True}

        result = _resolve_terminal_cwd(
            terminal_config, defaults, env, gateway_runtime=True
        )
        assert result == "/explicit/path"

    def test_remote_backend_pops_cwd(self):
        """Remote backend + placeholder cwd → popped for backend default."""
        env = {}
        terminal_config = {"cwd": ".", "env_type": "docker"}
        defaults = {"terminal": {"cwd": "."}, "_file_has_terminal": False}

        result = _resolve_terminal_cwd(terminal_config, defaults, env)
        assert result == ""  # cwd popped, no env var set

    def test_remote_backend_with_prior_cwd_preserves(self):
        """Remote backend + pre-resolved TERMINAL_CWD → adopted."""
        env = {"TERMINAL_CWD": "/project"}
        terminal_config = {"cwd": ".", "env_type": "docker"}
        defaults = {"terminal": {"cwd": "."}, "_file_has_terminal": False}

        result = _resolve_terminal_cwd(terminal_config, defaults, env)
        assert result == "/project"
