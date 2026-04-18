#!/usr/bin/env python3
import re
from pathlib import Path

STASH_WINS_FILES = {
    "gateway/run.py",
    "hermes_cli/gateway.py", 
    "hermes_cli/profiles.py",
    "run_agent.py",
}

UNICODE_REPLACEMENTS = [
    ("✓ ", "[OK] "),
    ("✗ ", "[ERR] "),
    ("⚠ ", "[WARN] "),
    ("◆ ", "[*] "),
    ("→ ", "-> "),
    ("← ", "<- "),
]

def resolve_file(path: Path) -> bool:
    content = path.read_text(encoding="utf-8")
    if "<<<<<<<" not in content:
        return False

    relpath = str(path).replace("\\", "/")
    stash_always_wins = relpath in STASH_WINS_FILES

    pattern = re.compile(
        r'<<<<<<< Updated upstream\n'
        r'(.*?)'
        r'=======\n'
        r'(.*?)'
        r'>>>>>>> Stashed changes',
        re.DOTALL
    )

    def replacer(m):
        upstream = m.group(1)
        stash = m.group(2)

        if stash_always_wins:
            return stash

        # Check if stash only differs by Unicode
        stash_clean = stash
        for old, new in UNICODE_REPLACEMENTS:
            stash_clean = stash_clean.replace(new, old)
        if stash_clean.strip() == upstream.strip():
            result = upstream
            for old, new in UNICODE_REPLACEMENTS:
                result = result.replace(old, new)
            return result

        # If stash has compat code, prefer stash
        if any(p in stash for p in ["psutil", "_IS_WINDOWS", 'sys.platform == "win32"', "CREATE_NEW_PROCESS_GROUP"]):
            return stash
        if any(p in upstream for p in ["psutil", "_IS_WINDOWS", 'sys.platform == "win32"', "CREATE_NEW_PROCESS_GROUP"]):
            return upstream

        # Default: upstream + Unicode
        result = upstream
        for old, new in UNICODE_REPLACEMENTS:
            result = result.replace(old, new)
        return result

    resolved = pattern.sub(replacer, content)
    if "<<<<<<<" in resolved:
        print(f"  [WARN] {relpath} still has conflicts")
        return False
    path.write_text(resolved, encoding="utf-8")
    print(f"  [OK] Resolved {relpath}")
    return True

files = [
    "agent/auxiliary_client.py",
    "gateway/platforms/qqbot/adapter.py", 
    "gateway/run.py",
    "hermes_cli/clipboard.py",
    "hermes_cli/debug.py",
    "hermes_cli/doctor.py",
    "hermes_cli/gateway.py",
    "hermes_cli/main.py",
    "hermes_cli/profiles.py",
    "hermes_cli/setup.py",
    "run_agent.py",
    "tests/agent/test_subagent_progress.py",
    "tests/hermes_cli/test_profiles.py",
]

for f in files:
    p = Path(f)
    if p.exists():
        resolve_file(p)
    else:
        print(f"  [SKIP] {f}")
