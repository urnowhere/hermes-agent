#!/usr/bin/env python3
"""Test runner for Windows-native development.

Automatically excludes Linux-specific tests and integration tests.
"""

import os
import subprocess
import sys
from pathlib import Path

# Linux-specific tests to skip on Windows
IGNORED_TESTS = [
    # Unix syscalls (os.getuid, pwd, grp, fcntl)
    "tests/gateway/test_gateway_service.py",
    "tests/gateway/test_gateway.py",
    "tests/gateway/test_gateway_linger.py",
    "tests/tools/test_memory_tool.py",
    # Unix tools (bash, which)
    "tests/hermes_cli/test_setup_hermes_script.py",
    "tests/tools/test_search_hidden_dirs.py",
    # Curses module
    "tests/hermes_cli/test_session_browse.py",
    # macOS specific
    "tests/hermes_cli/test_update_gateway_restart.py",
    # Signal handling (SIGKILL)
    "tests/tools/test_mcp_stability.py",
    # File permissions (Unix 0o600/0o700)
    "tests/tools/test_file_permissions.py",
    "tests/gateway/test_pairing.py",
    # Symlinks (require admin on Windows)
    "tests/tools/test_worktree_security.py",
    "tests/tools/test_worktree.py",
    "tests/cli/test_worktree_security.py",
    "tests/cli/test_worktree.py",
    "tests/gateway/test_credential_files.py",
    # Missing dependencies (acp, aiohttp, mcp)
    "tests/acp/test_entry.py",
    "tests/acp/test_events.py",
    "tests/acp/test_mcp_e2e.py",
    "tests/acp/test_permissions.py",
    "tests/acp/test_server.py",
    "tests/acp/test_tools.py",
    "tests/gateway/test_api_server.py",
    "tests/gateway/test_api_server_jobs.py",
    "tests/gateway/test_webhook_adapter.py",
    "tests/gateway/test_webhook_integration.py",
    "tests/integration/test_ha_integration.py",
    "tests/integration/test_web_tools.py",
    "tests/tools/test_mcp_tool.py",
    # File drag/drop (UI feature, not core functionality)
    "tests/cli/test_cli_file_drop.py",
    "tests/cli/test_cli_image_command.py",
    # Windows-native overrides (use windows-native/tests/ versions instead)
    "tests/agent/test_auxiliary_config_bridge.py",
    "tests/agent/test_context_references.py",
    "tests/agent/test_subdirectory_hints.py",
    "tests/cli/test_branch_command.py",
    "tests/cli/test_cli_secret_capture.py",
    "tests/cli/test_cli_tools_command.py",
    "tests/cli/test_quick_commands.py",
    "tests/cli/test_cli_plan_command.py",
    "tests/cli/test_cli_provider_resolution.py",
    "tests/cron/test_codex_execution_paths.py",
    "tests/cron/test_file_permissions.py",
    "tests/gateway/test_email.py",
    "tests/gateway/test_feishu.py",
    "tests/gateway/test_telegram_conflict.py",
]


def run_tests(test_path=None, extra_args=None):
    """Run pytest with appropriate exclusions.

    Args:
        test_path: Specific test file or directory to run. If None, runs all tests.
        extra_args: Additional pytest arguments.
    """
    # Set PYTHONPATH to include windows-native directory
    env = os.environ.copy()
    wp = str(Path(__file__).parent)
    if wp not in env.get("PYTHONPATH", ""):
        env["PYTHONPATH"] = wp + os.pathsep + env.get("PYTHONPATH", "")

    # Build common pytest arguments
    common_args = [
        sys.executable,
        "-m",
        "pytest",
        "-m",
        "not integration",  # Skip external service tests
        "-n",
        "0",  # Disable parallel execution (overrides pyproject.toml addopts)
    ]

    # Add --ignore flags for Linux-specific tests
    ignore_args = []
    for test_file in IGNORED_TESTS:
        ignore_args.extend(["--ignore", test_file])

    # Add extra arguments if provided
    final_extra_args = extra_args or []

    # Determine which tests to run
    windows_native_tests_dir = Path(__file__).parent / "tests"

    if test_path:
        # Run specific test path
        test_dirs = [test_path]
    else:
        # Run all tests: both windows-native and original
        test_dirs = [str(windows_native_tests_dir), "tests/"]

    # Run tests in separate subprocesses to avoid capture conflicts
    returncodes = []

    for test_dir in test_dirs:
        # Check if this is the windows-native tests directory
        if str(test_dir) == str(windows_native_tests_dir):
            # Run from windows-native/tests directory to avoid capture issues
            cmd = common_args + ignore_args + final_extra_args
            print(f"Running Windows-native tests: {' '.join(cmd)}")
            result = subprocess.run(cmd, env=env, cwd=str(windows_native_tests_dir))
            returncodes.append(result.returncode)
        else:
            # Run from project root with -s flag
            cmd = common_args + ["-s"] + ignore_args + final_extra_args + [test_dir]
            print(f"Running: {' '.join(cmd)}")
            result = subprocess.run(cmd, env=env)
            returncodes.append(result.returncode)

    # Exit with non-zero if any test failed
    sys.exit(1 if any(rc != 0 for rc in returncodes) else 0)


if __name__ == "__main__":
    # Parse command line arguments
    test_path = None
    extra_args = []

    # Parse arguments
    i = 1
    while i < len(sys.argv):
        arg = sys.argv[i]
        if arg == "--slice" and i + 1 < len(sys.argv):
            slice_arg = sys.argv[i + 1]
            if "/" in slice_arg:
                part, total = map(int, slice_arg.split("/", 1))
                if 1 <= part <= total:
                    # Handle slicing logic - will be expanded in next version
                    pass
            i += 2
        else:
            if test_path is None:
                test_path = arg
            else:
                extra_args.append(arg)
            i += 1

    if test_path:
        run_tests(test_path=test_path, extra_args=extra_args)
    else:
        run_tests(extra_args=extra_args)
