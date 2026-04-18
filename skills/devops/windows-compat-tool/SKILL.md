---
name: windows-compat-tool
description: Automatically scan and fix Unix-only code in Hermes Agent for Windows compatibility. Run after pulling latest upstream code to re-apply Windows patches.
version: 1.0.0
metadata:
  hermes:
    tags: [windows, compatibility, cross-platform, porting, automation]
---

# Windows Compatibility Tool

Auto-scan and fix Unix-only code patterns in the Hermes Agent codebase for Windows compatibility.

## When to Use

- After `git pull` or rebasing on latest upstream (NousResearch/hermes-agent)
- When new files/features are added and you need to verify Windows safety
- As a pre-commit hook to catch Unix-only code before it reaches production

## Quick Start

```bash
# Scan only (report problems, don't fix)
python skills/devops/windows-compat-tool/scripts/fix_windows_compat.py --scan-only

# Auto-fix all safe replacements
python skills/devops/windows-compat-tool/scripts/fix_windows_compat.py --fix

# Fix specific file or directory
python skills/devops/windows-compat-tool/scripts/fix_windows_compat.py --fix --path hermes_cli/gateway.py
```

## What It Fixes

### 1. Process Management

| Unix Call | Windows Replacement | Auto-fix? |
|-----------|---------------------|-----------|
| `os.kill(pid, 0)` | `psutil.pid_exists(pid)` | Yes |
| `os.kill(pid, signal.SIGTERM)` | `psutil.Process(pid).terminate()` | Yes |
| `os.kill(pid, signal.SIGKILL)` | `psutil.Process(pid).kill()` | Yes |
| `os.killpg(os.getpgid(pid), signal.SIGTERM)` | `psutil.Process(pid).terminate()` | Yes |
| `os.setsid` in `preexec_fn` | `None if _IS_WINDOWS else os.setsid` | Yes |
| `os.getuid()` | Guard with `sys.platform != "win32"` | Yes |

### 2. File System

| Unix Call | Windows Replacement | Auto-fix? |
|-----------|---------------------|-----------|
| `os.symlink()` | `try/except OSError` fallback | Report only |
| `os.chmod(path, 0o600)` | Skip on Windows (no-op, safe) | Report only |
| `fcntl.flock()` | `msvcrt.locking()` or skip | Report only |

### 3. Network

| Unix Call | Windows Replacement | Auto-fix? |
|-----------|---------------------|-----------|
| `socket.AF_UNIX` | `socket.AF_INET` (localhost TCP) | Yes (if `_IS_WINDOWS` guard exists) |
| Unix Domain Socket paths | `127.0.0.1:0` + `HERMES_RPC_PORT` env var | Yes |

### 4. System Info

| Unix Call | Windows Replacement | Auto-fix? |
|-----------|---------------------|-----------|
| `/proc/{pid}/stat` | `psutil.Process(pid).create_time()` | Yes |
| `/proc/{pid}/cmdline` | `psutil.Process(pid).cmdline()` | Yes |
| `/proc/1/cgroup` | Skip on Windows | Yes |
| `import pwd` / `import grp` | Guard with `sys.platform != "win32"` | Yes |
| `os.uname()` | `platform.uname()` or skip | Yes |

### 5. Shell & Terminal

| Unix Call | Windows Replacement | Auto-fix? |
|-----------|---------------------|-----------|
| `shlex.split()` with Windows paths | `shlex.split(..., posix=False)` | Yes |
| `bash -lic "cmd"` with Windows python path | Convert `C:\` to `/drive/c/` style | Yes |
| `subprocess.CREATE_NEW_PROCESS_GROUP` | Already Windows-only, safe | N/A |

### 6. Unicode / Encoding

| Issue | Fix | Auto-fix? |
|-------|-----|-----------|
| `✓ ✗ ⚠ ◆ → ←` in CLI output | Replace with ASCII equivalents `[OK] [ERR] [WARN]` | Yes |
| `schtasks` output on Chinese Windows | Add `encoding="gbk", errors="replace"` | Yes |

## Manual Fixes (Not Auto-fixable)

These require architectural decisions:

1. **systemd/launchd services** → Windows Task Scheduler (`schtasks.exe`)
2. **Unix signals (SIGUSR1, SIGHUP)** → Skip on Windows or use alternative IPC
3. **Docker integration** → Check Docker Desktop availability
4. **PTY support** → `pywinpty` (already in optional dependencies)

## Testing on Windows

After running the tool:

```bash
# Test all CLI commands parse without errors
python skills/devops/windows-compat-tool/scripts/test_cli_help.py

# Run unit tests for fixed modules
python -m pytest tests/tools/test_process_registry.py -q
python -m pytest tests/hermes_cli/test_profiles.py -q
python -m pytest tests/hermes_cli/test_doctor.py -q
python -m pytest tests/gateway/test_status_command.py -q
```

## Known Upstream Incompatibilities

These files in upstream NousResearch/hermes-agent are Unix-only and need manual review after each pull:

- `tools/environments/local.py` — UDS sockets, `os.setsid`, `os.killpg`
- `tools/code_execution_tool.py` — UDS sockets for sandbox RPC
- `tools/process_registry.py` — `os.killpg`, `os.setsid`
- `gateway/run.py` — systemd/launchd service restart logic
- `gateway/status.py` — `/proc/` filesystem access
- `hermes_cli/gateway.py` — systemd/launchd service management
- `hermes_cli/profiles.py` — `os.kill()` process checks

## Contributing

If you find a new Unix-only pattern not covered by this tool, add it to:
- `scripts/fix_windows_compat.py` — the fix dictionary
- This `SKILL.md` — the reference table
