# PodmanEnvironment BaseEnvironment Migration Plan

## Overview

Migrate `PodmanEnvironment` to use the unified `BaseEnvironment.execute()` pattern, matching the implementation in `DockerEnvironment`. This ensures consistent session management, CWD tracking, and command execution flow across all container backends.

## Background

After a rebase against upstream, `BaseEnvironment` now has a unified `execute()` method that provides:

- Session snapshot sourcing (env vars, functions, aliases persist across commands)
- CWD tracking via stdout markers
- Login shell fallback when snapshot fails
- Unified interrupt/timeout handling
- Stdin heredoc embedding for SDK backends

## Current State Analysis

### BaseEnvironment.execute() (tools/environments/base.py:489-528)

Provides unified execution flow:
1. Calls `_before_execute()` hook
2. Prepares command with `_prepare_command()` (sudo transformation)
3. Merges sudo stdin with caller stdin
4. Embeds stdin as heredoc for backends that need it
5. Wraps command with `_wrap_command()` (sources snapshot, cd's, runs command, re-dumps env, emits CWD markers)
6. Uses login shell if snapshot failed
7. Calls abstract `_run_bash()` method
8. Waits for process with `_wait_for_process()` (handles interrupts, timeouts, stdout draining)
9. Updates CWD with `_update_cwd()`

### PodmanEnvironment.execute() (tools/environments/podman.py:398-501) - TO BE REMOVED

Current implementation has:
- ✓ Calls `_before_execute()` hook
- ✓ Prepares command with `_prepare_command()`
- ✓ Merges sudo stdin with caller stdin
- ✗ **Special handling for `~` expansion** - prepends `cd` to command and sets cwd to `/`
- ✗ **Per-exec environment variable injection** - builds env args for every execute call
- ✗ **Custom process spawning** - directly builds `podman exec` command
- ✗ **Custom drain thread and wait loop** - duplicates `_wait_for_process()` logic
- ✗ **No session snapshot sourcing** - doesn't use `_wrap_command()`
- ✗ **No CWD marker emission/parsing** - doesn't use CWD markers

### DockerEnvironment Pattern (Reference)

`DockerEnvironment` correctly uses base class pattern:
- Implements `_run_bash()` abstract method (tools/environments/docker.py:396-417)
- Implements `_build_init_env_args()` for init-time env injection (tools/environments/docker.py:364-394)
- Calls `self.init_session()` in `__init__` (line 362)
- **Does NOT override `execute()`** - uses base class implementation
- Uses `_popen_bash()` helper from base

## Key Differences

| Feature | BaseEnvironment | PodmanEnvironment (current) |
|---------|-----------------|-----------------------------|
| Session snapshot sourcing | Yes (via `_wrap_command`) | No |
| CWD tracking via markers | Yes (via `_wrap_command`) | No |
| Per-exec env injection | No (only at init via snapshot) | Yes (every execute call) |
| Process waiting | `_wait_for_process()` | Custom implementation |
| Stdin handling | Supports pipe/heredoc modes | Pipe only |
| Login shell fallback | Yes (if snapshot failed) | No |
| Uses base `execute()` | N/A (base class) | No (overrides) |

## Migration Plan

### Step 1: Remove custom execute() method

**Remove lines 398-501** from `tools/environments/podman.py`

The entire `execute()` method will be deleted and replaced by base class implementation.

### Step 2: Implement _run_bash() method

**Add after line 397** (after `__init__` method, before `cleanup()`):

```python
def _run_bash(self, cmd_string: str, *, login: bool = False,
              timeout: int = 120,
              stdin_data: str | None = None) -> subprocess.Popen:
    """Spawn a bash process inside Podman container."""
    assert self._container_id, "Container not started"
    cmd = [self._podman_exe, "exec"]

    # Rootful support: prefix with sudo if needed
    if self._rootful:
        cmd = ["sudo"] + cmd

    if stdin_data is not None:
        cmd.append("-i")

    # Only inject -e env args during init_session (login=True).
    # Subsequent commands get env vars from the snapshot file.
    if login:
        cmd.extend(self._init_env_args)

    cmd.extend([self._container_id])

    if login:
        cmd.extend(["bash", "-l", "-c", cmd_string])
    else:
        cmd.extend(["bash", "-c", cmd_string])

    return _popen_bash(cmd, stdin_data)
```

**Key points:**
- Uses `_popen_bash()` helper from base class (need to import it)
- Handles rootful mode by prefixing `sudo` to the command
- Only injects env args during `login=True` (init_session)
- Uses `-i` flag when stdin_data is provided
- Uses `bash -l` for login shell (init_session), `bash -c` for regular commands

### Step 3: Implement _build_init_env_args() method

**Add after `_run_bash()` method**:

```python
def _build_init_env_args(self) -> list[str]:
    """Build -e KEY=VALUE args for injecting host env vars into init_session.

    These are used once during init_session() so that export -p captures
    them into the snapshot.  Subsequent execute() calls don't need -e flags.
    """
    from tools.environments.utils import _load_hermes_env_vars
    from tools.environments.local import _HERMES_PROVIDER_ENV_BLOCKLIST

    exec_env: dict[str, str] = dict(self._env)

    forward_keys = set(self._forward_env)
    passthrough_keys: set[str] = set()
    try:
        from tools.env_passthrough import get_all_passthrough
        passthrough_keys = set(get_all_passthrough())
    except Exception:
        pass

    # Explicit docker_forward_env entries are an intentional opt-in and must
    # win over the generic Hermes secret blocklist. Only implicit passthrough
    # keys are filtered.
    forward_keys = forward_keys | (passthrough_keys - _HERMES_PROVIDER_ENV_BLOCKLIST)
    hermes_env = _load_hermes_env_vars() if forward_keys else {}
    for key in sorted(forward_keys):
        value = os.getenv(key)
        if value is None:
            value = hermes_env.get(key)
        if value is not None:
            exec_env[key] = value

    args = []
    for key in sorted(exec_env):
        args.extend(["-e", f"{key}={exec_env[key]}"])
    return args
```

**Key points:**
- Mirrors `DockerEnvironment._build_init_env_args()` implementation
- Combines explicit forward env with passthrough env
- Filters out Hermes provider env blocklist for implicit passthrough keys
- Returns list of `-e KEY=VALUE` arguments

### Step 4: Add init_session() call to __init__

**Add after line 396** (after container starts, after `logger.info` line):

```python
# Build init-time env forwarding args (used only by init_session
# to inject host env vars into the snapshot; subsequent commands get
# them from the snapshot file).
self._init_env_args = self._build_init_env_args()

# Initialize session snapshot inside container
self.init_session()
```

### Step 5: Add import for _popen_bash

**Modify line 21** (imports section):

```python
from tools.environments.base import BaseEnvironment, _popen_bash
```

### Step 6: Update cleanup() for rootful support

The `cleanup()` method already handles rootful support (lines 508-527), but ensure it's consistent:

```python
def cleanup(self):
    """Stop and remove the container. Bind-mount dirs persist if persistent=True."""
    if self._container_id:
        try:
            # Stop in background so cleanup doesn't block
            sudo_prefix = "sudo " if self._rootful else ""
            stop_cmd = (
                f"(timeout 60 {sudo_prefix}{self._podman_exe} stop {self._container_id} || "
                f"{sudo_prefix}{self._podman_exe} rm -f {self._container_id}) >/dev/null 2>&1 &"
            )
            subprocess.Popen(stop_cmd, shell=True)
        except Exception as e:
            logger.warning("Failed to stop container %s: %s", self._container_id, e)

        if not self._persistent:
            # Also schedule removal (stop only leaves it as stopped)
            sudo_prefix = "sudo " if self._rootful else ""
            try:
                subprocess.Popen(
                    f"sleep 3 && {sudo_prefix}{self._podman_exe} rm -f {self._container_id} >/dev/null 2>&1 &",
                    shell=True,
                )
            except Exception:
                pass
        self._container_id = None

    if not self._persistent:
        for d in (self._workspace_dir, self._home_dir):
            if d:
                shutil.rmtree(d, ignore_errors=True)
```

This is already correct - no changes needed.

## Special Considerations

### ~ Expansion Handling

The current podman code has special handling for `~` in cwd (lines 417-422):

```python
if effective_cwd == "~":
    exec_command = f"cd ~ && {exec_command}"
    effective_cwd = "/"
elif effective_cwd.startswith("~/"):
    exec_command = f"cd ~/{shlex.quote(effective_cwd[2:])} && {exec_command}"
    effective_cwd = "/"
```

**This logic should be preserved in `_run_bash()`** OR verified that `_wrap_command()` in base class handles it correctly.

Looking at `BaseEnvironment._wrap_command()` (tools/environments/base.py:317-353), it uses:

```python
quoted_cwd = (
    shlex.quote(cwd) if cwd != "~" and not cwd.startswith("~/") else cwd
)
parts.append(f"cd {quoted_cwd} || exit 126")
```

This should handle `~` correctly since it's passed unquoted to bash, which will expand it natively. However, the podman-specific logic may need to stay in `_run_bash()` if there are podman-specific issues with `-w` flag not expanding `~`.

**Recommendation:** Test whether the base class `_wrap_command()` handles `~` correctly. If not, add the special handling to `_run_bash()` before passing to `_popen_bash()`.

### Environment Injection Behavior Change

**Important:** The current podman code injects env vars on EVERY execute call. The base class pattern injects them ONCE during init_session and then relies on the snapshot.

This is the correct behavior - env vars should be captured in the snapshot and re-sourced for subsequent commands. This matches how Docker works and provides better session state persistence.

## Files to Modify

| File | Changes |
|-------|----------|
| `tools/environments/podman.py` | 1. Import `_popen_bash` from base<br>2. Remove `execute()` method (lines 398-501)<br>3. Add `_run_bash()` method<br>4. Add `_build_init_env_args()` method<br>5. Add `self.init_session()` call in `__init__`<br>6. Add `self._init_env_args` initialization |

## Benefits of Migration

1. **Consistent behavior** across all container backends (Docker, Podman, Singularity)
2. **Session state persistence** - env vars, functions, aliases survive across commands
3. **CWD tracking** - `cd` commands properly persist via markers
4. **Less code duplication** - Reuse base class logic (~100 lines removed)
5. **Better error handling** - Login shell fallback when snapshot fails
6. **Maintainability** - Single source of truth for execution logic

## Testing Checklist

After implementation, test:

1. **Basic command execution** - Verify simple commands work
2. **Session state persistence** - Set env var, verify it persists across commands
3. **CWD tracking** - `cd` to directory, verify subsequent commands run in that directory
4. **`~` expansion** - Test commands with `~` and `~/path` as cwd
5. **Rootful mode** - Test with `podman_rootful: true`
6. **Snapshot failure fallback** - Simulate snapshot failure, verify login shell fallback works
7. **Interrupt handling** - Verify Ctrl+C properly interrupts running commands
8. **Timeout handling** - Verify long-running commands timeout correctly
9. **Stdin handling** - Test commands that read from stdin
10. **Environment forwarding** - Verify `docker_forward_env` and env passthrough work correctly

## Summary

The migration will make `PodmanEnvironment` behave identically to `DockerEnvironment` in terms of session management and command execution flow. The rootful podman support will be handled within the `_run_bash()` method, similar to how it's currently handled in `cleanup()` for container removal.

**Total lines removed:** ~100 (the entire `execute()` method)
**Total lines added:** ~80 (`_run_bash()` + `_build_init_env_args()` + init call)
**Net reduction:** ~20 lines of code
