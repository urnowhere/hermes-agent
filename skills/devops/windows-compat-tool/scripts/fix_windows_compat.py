#!/usr/bin/env python3
"""Auto-scan and fix Unix-only code for Windows compatibility.

Usage:
    python fix_windows_compat.py --scan-only          # Report only
    python fix_windows_compat.py --fix                # Auto-fix safe changes
    python fix_windows_compat.py --fix --path PATH    # Fix specific file/dir
"""

import argparse
import ast
import re
import sys
from pathlib import Path

# Patterns that can be safely auto-replaced
AUTO_REPLACEMENTS = [
    # --- os.kill(pid, 0) existence checks ---
    {
        "name": "os.kill existence check in try/except",
        "pattern": re.compile(
            r'([ \t]+try:\n)'
            r'([ \t]+os\.kill\([^)]+,\s*0\)\n)'
            r'([ \t]+except\s+\(ProcessLookupError,\s*PermissionError\):)',
            re.MULTILINE,
        ),
        "replace": r'''\1            if sys.platform == "win32":
\1                import psutil
\1                if not psutil.pid_exists(\2.split("(")[1].split(",")[0].strip()):
\1                    raise ProcessLookupError(\2.split("(")[1].split(",")[0].strip())
\1            else:
\1\2\3''',
        "note": "Replaced os.kill(pid,0) with psutil.pid_exists() for Windows",
    },
]

# Simpler text-based replacements (order matters — more specific first)
TEXT_REPLACEMENTS = [
    # 1. os.kill(pid, signal.SIGTERM) in Windows context
    (
        r'if _IS_WINDOWS:\s*\n\s*os\.kill\(([^,]+),\s*signal\.SIGTERM\)',
        r'if _IS_WINDOWS:\n            import psutil\n            try:\n                psutil.Process(\1).terminate()\n            except psutil.NoSuchProcess:\n                pass',
    ),
    # 2. os.kill(pid, 0) in simple context
    (
        r'([ \t]*)os\.kill\(([^,]+),\s*0\)\s*# existence check',
        r'''\1if sys.platform == "win32":\n\1    import psutil\n\1    if not psutil.pid_exists(\2):\n\1        raise ProcessLookupError(\2)\n\1else:\n\1    os.kill(\2, 0)  # existence check''',
    ),
]


PATTERNS_TO_REPORT = [
    (r"os\.fork\(", "os.fork() — use multiprocessing on Windows"),
    (r"import\s+pty\b", "pty module — use pywinpty on Windows"),
    (r"import\s+termios\b", "termios module — not available on Windows"),
    (r"import\s+tty\b", "tty module — not available on Windows"),
    (r"os\.mkfifo\(", "os.mkfifo() — not available on Windows"),
    (r"signal\.SIGUSR1\b", "SIGUSR1 — not available on Windows, guard with hasattr"),
    (r"signal\.SIGHUP\b", "SIGHUP — not available on Windows, guard with hasattr"),
    (r"signal\.SIGALRM\b", "SIGALRM — not available on Windows"),
    (r"os\.getuid\(", "os.getuid() — not available on Windows"),
    (r"os\.getgid\(", "os.getgid() — not available on Windows"),
    (r"import\s+grp\b", "grp module — not available on Windows"),
    (r"import\s+pwd\b", "pwd module — not available on Windows"),
    (r"import\s+resource\b", "resource module — not available on Windows"),
    (r"os\.set_blocking\(", "os.set_blocking() — not available on Windows"),
    (r"select\.poll\(", "select.poll() — limited on Windows"),
    (r"os\.getpgrp\(", "os.getpgrp() — not available on Windows"),
    (r"os\.setpgrp\(", "os.setpgrp() — not available on Windows"),
    (r"os\.setpgid\(", "os.setpgid() — not available on Windows"),
]


def scan_file(path: Path) -> list[dict]:
    """Scan a single file for Unix-only patterns."""
    issues = []
    try:
        source = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return issues

    lines = source.splitlines()
    for lineno, line in enumerate(lines, 1):
        for pattern, message in PATTERNS_TO_REPORT:
            if re.search(pattern, line):
                # Skip lines that are already guarded
                if _is_guarded(lines, lineno - 1):
                    continue
                issues.append({
                    "path": path,
                    "line": lineno,
                    "col": line.index(re.search(pattern, line).group(0)),
                    "code": line.strip(),
                    "message": message,
                })
    return issues


def _is_guarded(lines: list[str], idx: int) -> bool:
    """Check if a line is already behind a Windows platform guard."""
    # Look back up to 5 lines for a guard
    for i in range(max(0, idx - 5), idx):
        line = lines[i]
        if any(g in line for g in (
            "_IS_WINDOWS",
            'sys.platform == "win32"',
            "sys.platform != \"win32\"",
            "hasattr(signal,",
            "try:",
            "except ImportError",
            "except (ImportError",
            "# noqa",
        )):
            return True
    return False


def fix_file(path: Path, dry_run: bool = False) -> list[str]:
    """Apply safe auto-fixes to a file. Returns list of changes made."""
    changes = []
    try:
        source = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return changes

    original = source

    # --- Fix 1: Simple os.kill(pid, 0) in try/except blocks ---
    # Pattern: try: os.kill(pid, 0) except (ProcessLookupError, PermissionError):
    source = re.sub(
        r'([ \t]+try:\n)'
        r'([ \t]+os\.kill\(([^,]+),\s*0\)\n)'
        r'([ \t]+except\s+\(ProcessLookupError,\s*PermissionError\):)',
        lambda m: (
            f'{m.group(1)}            if sys.platform == "win32":\n'
            f'{m.group(1)}                import psutil\n'
            f'{m.group(1)}                if not psutil.pid_exists({m.group(3)}):\n'
            f'{m.group(1)}                    raise ProcessLookupError({m.group(3)})\n'
            f'{m.group(1)}            else:\n'
            f'{m.group(1)}    {m.group(2).lstrip()}'
            f'{m.group(4)}'
        ),
        source,
    )

    # --- Fix 2: os.kill(pid, signal.SIGTERM) in _IS_WINDOWS block ---
    def _fix_sigterm(m):
        indent = m.group(1)
        pid = m.group(2)
        return (
            f'{indent}if _IS_WINDOWS:\n'
            f'{indent}    import psutil\n'
            f'{indent}    try:\n'
            f'{indent}        psutil.Process({pid}).terminate()\n'
            f'{indent}    except psutil.NoSuchProcess:\n'
            f'{indent}        pass\n'
            f'{indent}else:\n'
            f'{indent}    os.kill({pid}, signal.SIGTERM)'
        )

    source = re.sub(
        r'([ \t]*)if _IS_WINDOWS:\s*\n\s*os\.kill\(([^,]+),\s*signal\.SIGTERM\)',
        _fix_sigterm,
        source,
    )

    # --- Fix 3: os.kill(pid, signal.SIGKILL) in _IS_WINDOWS block ---
    def _fix_sigkill(m):
        indent = m.group(1)
        pid = m.group(2)
        return (
            f'{indent}if _IS_WINDOWS:\n'
            f'{indent}    import psutil\n'
            f'{indent}    try:\n'
            f'{indent}        psutil.Process({pid}).kill()\n'
            f'{indent}    except psutil.NoSuchProcess:\n'
            f'{indent}        pass\n'
            f'{indent}else:\n'
            f'{indent}    os.kill({pid}, getattr(signal, "SIGKILL", signal.SIGTERM))'
        )

    source = re.sub(
        r'([ \t]*)if _IS_WINDOWS:\s*\n\s*os\.kill\(([^,]+),\s*getattr\(signal,\s*"SIGKILL"',
        _fix_sigkill,
        source,
    )

    if source != original:
        changes.append(f"Applied os.kill() fixes to {path}")
        if not dry_run:
            path.write_text(source, encoding="utf-8")

    return changes


def main():
    parser = argparse.ArgumentParser(description="Windows compatibility scanner/fixer")
    parser.add_argument("--scan-only", action="store_true", help="Report issues without fixing")
    parser.add_argument("--fix", action="store_true", help="Apply safe auto-fixes")
    parser.add_argument("--path", type=Path, default=Path("."), help="File or directory to scan")
    args = parser.parse_args()

    if not args.scan_only and not args.fix:
        parser.print_help()
        sys.exit(1)

    targets = []
    if args.path.is_file():
        targets.append(args.path)
    elif args.path.is_dir():
        targets = list(args.path.rglob("*.py"))
    else:
        print(f"Path not found: {args.path}")
        sys.exit(1)

    all_issues = []
    all_changes = []

    for target in targets:
        # Skip test files and generated code
        if "/test" in str(target) or "__pycache__" in str(target):
            continue
        if args.fix:
            changes = fix_file(target, dry_run=False)
            all_changes.extend(changes)
        issues = scan_file(target)
        all_issues.extend(issues)

    if all_changes:
        print(f"\n[FIXED] {len(all_changes)} file(s) modified:")
        for c in all_changes:
            print(f"  {c}")

    if all_issues:
        print(f"\n[REPORT] {len(all_issues)} issue(s) found (manual review required):")
        by_file = {}
        for issue in all_issues:
            by_file.setdefault(issue["path"], []).append(issue)

        for path, issues in sorted(by_file.items()):
            print(f"\n  {path}")
            for issue in issues:
                print(f"    Line {issue['line']}: {issue['message']}")
                print(f"      -> {issue['code'][:80]}")
    else:
        print("\n[OK] No unguarded Unix-only patterns found.")

    if all_issues:
        sys.exit(2 if args.scan_only else 0)


if __name__ == "__main__":
    main()
