#!/usr/bin/env python3
"""Batch test all CLI commands to verify they parse without errors on Windows.

Usage:
    python test_cli_help.py              # Test all commands
    python test_cli_help.py --verbose    # Show each command result
"""

import subprocess
import sys

# Top-level commands and their subcommands
COMMANDS = {
    "chat": [],
    "model": [],
    "gateway": ["run", "start", "stop", "restart", "status", "install", "uninstall", "setup"],
    "setup": [],
    "whatsapp": [],
    "login": [],
    "logout": [],
    "auth": ["add", "list", "remove", "reset"],
    "status": [],
    "cron": ["list", "create", "edit", "pause", "resume", "run", "remove", "status", "tick"],
    "webhook": ["subscribe", "list", "remove", "test"],
    "doctor": [],
    "dump": [],
    "debug": [],
    "backup": [],
    "import": [],
    "config": ["show", "edit", "set", "path", "env-path", "check", "migrate"],
    "pairing": ["list", "approve", "revoke", "clear-pending"],
    "skills": ["browse", "search", "install", "inspect", "list", "check", "update", "audit", "uninstall", "publish", "snapshot", "tap", "config"],
    "plugins": ["install", "update", "remove", "list", "enable", "disable"],
    "memory": ["setup", "status", "off", "reset"],
    "tools": ["list", "disable", "enable"],
    "mcp": ["serve", "add", "remove", "list", "test", "configure", "login"],
    "sessions": ["list", "export", "delete", "prune", "stats", "rename", "browse"],
    "insights": [],
    "claw": ["migrate", "cleanup"],
    "version": [],
    "update": [],
    "uninstall": [],
    "acp": [],
    "profile": ["list", "use", "create", "delete", "show", "alias", "rename", "export", "import"],
    "completion": [],
    "dashboard": [],
    "logs": [],
}


def test_command(cmd: str, sub: str = "") -> tuple[bool, str]:
    args = ["hermes", cmd]
    if sub:
        args.append(sub)
    args.append("--help")
    result = subprocess.run(args, capture_output=True, text=True, timeout=30)
    if result.returncode != 0:
        return False, result.stderr.strip()[:120]
    return True, ""


def main():
    verbose = "--verbose" in sys.argv
    total = passed = failed = 0
    failures = []

    for cmd, subs in COMMANDS.items():
        ok, err = test_command(cmd)
        total += 1
        if ok:
            passed += 1
        else:
            failed += 1
            failures.append((cmd, err))
        if verbose:
            status = "OK" if ok else "FAIL"
            print(f"{cmd:25s} {status}")

        for sub in subs:
            ok, err = test_command(cmd, sub)
            total += 1
            if ok:
                passed += 1
            else:
                failed += 1
                failures.append((f"{cmd} {sub}", err))
            if verbose:
                status = "OK" if ok else "FAIL"
                print(f"  {sub:23s} {status}")

    print(f"\n{'='*60}")
    print(f"Total: {total}, Passed: {passed}, Failed: {failed}")
    if failures:
        print("\nFailed commands:")
        for cmd, err in failures:
            print(f"  {cmd}: {err}")
        sys.exit(1)
    else:
        print("\n[OK] All CLI commands parse successfully on Windows.")


if __name__ == "__main__":
    main()
