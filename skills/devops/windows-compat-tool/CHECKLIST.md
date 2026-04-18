# Windows Compatibility Checklist

This is the definitive reference for all Windows compatibility fixes applied to Hermes Agent. Use this after pulling latest upstream.

## Critical Files to Review After Each Pull

These files change frequently in upstream and contain the most Unix-only code:

1. `tools/environments/local.py` — bash finder, temp dir, process killing
2. `tools/code_execution_tool.py` — sandbox RPC (UDS vs TCP)
3. `tools/process_registry.py` — process lifecycle management
4. `tools/browser_tool.py` — daemon process management
5. `gateway/run.py` — service restart, signal handlers
6. `gateway/status.py` — PID checks, `/proc/` access
7. `hermes_cli/gateway.py` — service install/start/stop/restart
8. `hermes_cli/main.py` — update command kills gateways
9. `hermes_cli/profiles.py` — gateway running checks

## Fix Patterns

### Pattern 1: os.kill(pid, 0) — Process Existence Check

**Before (breaks on Windows):**
```python
try:
    os.kill(pid, 0)
except (ProcessLookupError, PermissionError):
    ...
```

**After:**
```python
try:
    if _IS_WINDOWS:  # or sys.platform == "win32"
        import psutil
        if not psutil.pid_exists(pid):
            raise ProcessLookupError(pid)
    else:
        os.kill(pid, 0)
except (ProcessLookupError, PermissionError, OSError):
    ...
```

### Pattern 2: os.kill(pid, signal.SIGTERM)

**Before:**
```python
try:
    os.kill(pid, signal.SIGTERM)
except (ProcessLookupError, PermissionError):
    pass
```

**After:**
```python
try:
    if sys.platform == "win32":
        import psutil
        psutil.Process(pid).terminate()
    else:
        os.kill(pid, signal.SIGTERM)
except (ProcessLookupError, PermissionError, OSError):
    pass
```

### Pattern 3: os.killpg(os.getpgid(pid), signal.SIGTERM)

**Before:**
```python
try:
    os.killpg(os.getpgid(pid), signal.SIGTERM)
except (OSError, ProcessLookupError, PermissionError):
    os.kill(pid, signal.SIGTERM)
```

**After:**
```python
if _IS_WINDOWS:
    import psutil
    try:
        psutil.Process(pid).terminate()
    except psutil.NoSuchProcess:
        pass
    return

try:
    os.killpg(os.getpgid(pid), signal.SIGTERM)
except (OSError, ProcessLookupError, PermissionError):
    os.kill(pid, signal.SIGTERM)
```

### Pattern 4: preexec_fn=os.setsid

**Before:**
```python
subprocess.Popen(
    args,
    preexec_fn=os.setsid,
)
```

**After:**
```python
subprocess.Popen(
    args,
    preexec_fn=None if _IS_WINDOWS else os.setsid,
)
```

### Pattern 5: /proc/{pid}/stat or /proc/{pid}/cmdline

**Before:**
```python
stat_path = Path(f"/proc/{pid}/stat")
try:
    stat = stat_path.read_text()
    start_time = int(stat.split()[21])
except (OSError, ValueError):
    start_time = None
```

**After:**
```python
# Prefer psutil (cross-platform)
try:
    import psutil
    proc = psutil.Process(pid)
    start_time = int(proc.create_time())
except Exception:
    start_time = None

# Fallback for Linux without psutil
if start_time is None and sys.platform != "win32":
    stat_path = Path(f"/proc/{pid}/stat")
    try:
        stat = stat_path.read_text()
        start_time = int(stat.split()[21])
    except (OSError, ValueError):
        start_time = None
```

### Pattern 6: socket.AF_UNIX

**Before:**
```python
server_sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
server_sock.bind(sock_path)
```

**After:**
```python
if _IS_WINDOWS:
    server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_sock.bind(("127.0.0.1", 0))
    rpc_port = server_sock.getsockname()[1]
    # Pass HERMES_RPC_PORT to child instead of HERMES_RPC_SOCKET
else:
    server_sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server_sock.bind(sock_path)
```

### Pattern 7: import pwd / import grp

**Before:**
```python
import pwd
username = pwd.getpwuid(os.getuid()).pw_name
```

**After:**
```python
if sys.platform == "win32":
    import getpass
    username = getpass.getuser()
else:
    import pwd
    username = pwd.getpwuid(os.getuid()).pw_name
```

### Pattern 8: os.getuid()

**Before:**
```python
uid = os.getuid()
```

**After:**
```python
if sys.platform == "win32":
    return  # or handle differently
uid = os.getuid()
```

### Pattern 9: schtasks encoding on Chinese Windows

**Before:**
```python
result = subprocess.run(
    ["schtasks", "/Query", "/TN", task_name],
    capture_output=True, text=True, timeout=10,
)
```

**After:**
```python
result = subprocess.run(
    ["schtasks", "/Query", "/TN", task_name],
    capture_output=True, text=True,
    encoding="gbk", errors="replace", timeout=10,
)
```

### Pattern 10: shlex.split() with Windows paths

**Before:**
```python
args = shlex.split(command_line)
```

**After:**
```python
args = shlex.split(command_line, posix=(sys.platform != "win32"))
```

### Pattern 11: Unicode symbols in CLI output

**Before:**
```python
print(f"✓ Success")
print(f"✗ Failed")
print(f"⚠ Warning")
```

**After:**
```python
print(f"[OK] Success")
print(f"[ERR] Failed")
print(f"[WARN] Warning")
```

## Files That Should NOT Be Auto-fixed

These require architectural decisions:

- `hermes_cli/gateway.py` — System service installation (systemd/launchd/Task Scheduler)
- `gateway/run.py` — Signal handlers, detached process restart
- `hermes_cli/setup.py` — Terminal setup (bash-specific)
- Any file using Docker/Podman APIs directly

## Testing Steps After Pull

1. Run the scanner:
   ```bash
   python skills/devops/windows-compat-tool/scripts/fix_windows_compat.py --scan-only
   ```

2. Apply safe fixes:
   ```bash
   python skills/devops/windows-compat-tool/scripts/fix_windows_compat.py --fix
   ```

3. Test CLI commands:
   ```bash
   python skills/devops/windows-compat-tool/scripts/test_cli_help.py
   ```

4. Manual review of reported issues

5. Run unit tests on modified files

## Common Pitfalls

1. **Don't use `_IS_WINDOWS` before it's defined** — some files define it inline; check before referencing
2. **psutil is already a dependency** (added to pyproject.toml), safe to import anywhere
3. **Don't change existing Windows guards** — only fix unguarded Unix-only calls
4. **Be careful with generated code strings** — some code is string-literal Python executed elsewhere
5. **Test subprocess encoding** — Chinese Windows uses GBK for system commands
