# Podman Backend Configuration Implementation Plan

## Overview

Add support for user namespace remapping, rootless mode, and other Podman-specific options as configuration options in Hermes Agent. This includes integrating Podman as a selectable terminal backend.

## Configuration Schema

Add these options to `terminal:` section in `config.yaml`:

```yaml
terminal:
  # Podman-specific options
  podman_userns: ""              # --userns flag (e.g., "keep-id", "auto")
  podman_user: ""                # --user flag (e.g., "1000:1000", "nonroot")
  podman_privileged: false        # --privileged flag
  podman_extra_capabilities: []     # Additional --cap-add values (additive to defaults)
  podman_extra_args: []          # Arbitrary additional podman run flags
  podman_rootful: false        # Run podman commands with sudo (i.e. rootful mode)
```

### Environment Variables

Each config option can also be set via environment variable:

| Config Key | Environment Variable | Type | Default |
|-------------|---------------------|--------|----------|
| `podman_userns` | `TERMINAL_PODMAN_USERNS` | string | `""` |
| `podman_user` | `TERMINAL_PODMAN_USER` | string | `""` |
| `podman_privileged` | `TERMINAL_PODMAN_PRIVILEGED` | boolean | `false` |
| `podman_extra_capabilities` | `TERMINAL_PODMAN_EXTRA_CAPABILITIES` | JSON array | `[]` |
| `podman_extra_args` | `TERMINAL_PODMAN_EXTRA_ARGS` | JSON array | `[]` |
| `podman_rootful` | `TERMINAL_PODMAN_ROOTFUL` | boolean | `false` |

## Implementation Details

### 1. Configuration Options in `hermes_cli/config.py`

#### Add to `DEFAULT_CONFIG["terminal"]`:

```python
"podman_userns": "",              # --userns flag
"podman_user": "",                # --user flag
"podman_privileged": False,        # --privileged flag
"podman_extra_capabilities": [],     # Additional --cap-add values
"podman_extra_args": [],          # Arbitrary podman run flags
"podman_rootful": False,        # Run podman with sudo
```

#### Add to `OPTIONAL_ENV_VARS`:

```python
"TERMINAL_PODMAN_USERNS": {
    "description": "Podman user namespace mode (--userns flag)",
    "prompt": "Podman User Namespace",
    "url": "https://docs.podman.io/en/latest/markdown/podman-run.1.html#userns-mode",
    "password": False,
    "category": "tool",
},
"TERMINAL_PODMAN_USER": {
    "description": "Podman user to run as inside container (--user flag)",
    "prompt": "Podman User",
    "url": "https://docs.podman.io/en/latest/markdown/podman-run.1.html#user-user",
    "password": False,
    "category": "tool",
},
"TERMINAL_PODMAN_PRIVILEGED": {
    "description": "Run Podman containers in privileged mode (--privileged flag)",
    "prompt": "Podman Privileged Mode",
    "url": "https://docs.podman.io/en/latest/markdown/podman-run.1.html#privileged",
    "password": False,
    "category": "tool",
},
"TERMINAL_PODMAN_EXTRA_CAPABILITIES": {
    "description": "Additional Linux capabilities to add to Podman containers (--cap-add)",
    "prompt": "Podman Extra Capabilities",
    "url": "https://docs.podman.io/en/latest/markdown/podman-run.1.html#cap-add-capability",
    "password": False,
    "category": "tool",
},
"TERMINAL_PODMAN_EXTRA_ARGS": {
    "description": "Additional arbitrary arguments for podman run (JSON array)",
    "prompt": "Podman Extra Arguments",
    "url": "https://docs.podman.io/en/latest/markdown/podman-run.1.html",
    "password": False,
    "category": "tool",
},
"TERMINAL_PODMAN_ROOTFUL": {
    "description": "Run Podman commands with sudo",
    "prompt": "Podman Use Sudo",
    "url": "",
    "password": False,
    "category": "tool",
},
```

### 2. Update `PodmanEnvironment` in `tools/environments/podman.py`

#### Modify `__init__` signature (line 155):

```python
def __init__(
    self,
    image: str,
    cwd: str = "/root",
    timeout: int = 60,
    cpu: float = 0,
    memory: int = 0,
    disk: int = 0,
    persistent_filesystem: bool = False,
    task_id: str = "default",
    volumes: list = None,
    forward_env: list[str] | None = None,
    env: dict | None = None,
    network: bool = True,
    host_cwd: str = None,
    auto_mount_cwd: bool = False,
    # New Podman-specific options
    userns: str = "",
    user: str = "",
    privileged: bool = False,
    extra_capabilities: list = None,
    extra_args: list = None,
    rootful: bool = False,
):
```

#### Store new instance variables (after line 180):

```python
self._privileged = privileged
self._userns = userns
self._user = user
self._extra_capabilities = extra_capabilities or []
self._extra_args = extra_args or []
self._rootful = rootful
```

#### Apply options when building `run_cmd` (before line 334):

```python
# Apply privileged flag
if self._privileged:
    all_run_args.append("--privileged")

# Apply user namespace
if self._userns:
    all_run_args.extend(["--userns", self._userns])

# Apply user
if self._user:
    all_run_args.extend(["--user", self._user])

# Apply extra capabilities (additive to defaults)
if self._extra_capabilities:
    for cap in self._extra_capabilities:
        all_run_args.extend(["--cap-add", cap])

# Apply extra args (no validation)
if self._extra_args:
    all_run_args.extend(self._extra_args)
```

#### Build command with sudo support (line 334):

```python
# Build the podman run command with sudo if needed
podman_exe = find_podman() or "podman"
if self._rootful:
    run_cmd = [
        "sudo", podman_exe, "run", "-d",
        "--name", container_name,
        "-w", cwd,
        *all_run_args,
        image,
        "sleep", "2h",
    ]
else:
    run_cmd = [
        podman_exe, "run", "-d",
        "--name", container_name,
        "-w", cwd,
        *all_run_args,
        image,
        "sleep", "2h",
    ]
```

#### Update `execute()` method for sudo support (line 378):

```python
assert self._container_id, "Container not started"
cmd = [self._podman_exe, "exec"]
if self._rootful:
    cmd = ["sudo"] + cmd
if effective_stdin is not None:
    cmd.append("-i")
cmd.extend(["-w", work_dir])
```

#### Update `cleanup()` for sudo support (line 459):

```python
if self._container_id:
    try:
        # Stop in background so cleanup doesn't block
        sudo_prefix = "sudo " if self._rootful else ""
        stop_cmd = (
            f"(timeout 60 {sudo_prefix}{self._podman_exe} stop {self._container_id} || "
            f"{sudo_prefix}{self._podman_exe} rm -f {self._container_id}) >/dev/null 2>&1 &"
        )
        subprocess.Popen(stop_cmd, shell=True)
```

### 3. Terminal Tool Integration in `tools/terminal_tool.py`

#### Import PodmanEnvironment (line ~408):

```python
from tools.environments.podman import PodmanEnvironment as _PodmanEnvironment
```

#### Add Podman config to `_get_env_config()` (after line 575):

```python
# Podman-specific config
"podman_userns": os.getenv("TERMINAL_PODMAN_USERNS", ""),
"podman_user": os.getenv("TERMINAL_PODMAN_USER", ""),
"podman_privileged": os.getenv("TERMINAL_PODMAN_PRIVILEGED", "false").lower() in ("true", "1", "yes"),
"podman_extra_capabilities": _parse_env_var("TERMINAL_PODMAN_EXTRA_CAPABILITIES", "[]", json.loads, "valid JSON"),
"podman_extra_args": _parse_env_var("TERMINAL_PODMAN_EXTRA_ARGS", "[]", json.loads, "valid JSON"),
"podman_rootful": os.getenv("TERMINAL_PODMAN_ROOTFUL", "false").lower() in ("true", "1", "yes"),
```

#### Add Podman case to `_create_environment()` (after line 632):

```python
elif env_type == "podman":
    return _PodmanEnvironment(
        image=image, cwd=cwd, timeout=timeout,
        cpu=cpu, memory=memory, disk=disk,
        persistent_filesystem=persistent, task_id=task_id,
        volumes=volumes,
        host_cwd=host_cwd,
        auto_mount_cwd=cc.get("docker_mount_cwd_to_workspace", False),
        forward_env=docker_forward_env,
        env=docker_env,
        network=cc.get("network", True),
        # Podman-specific options
        userns=cc.get("podman_userns", ""),
        user=cc.get("podman_user", ""),
        privileged=cc.get("podman_privileged", False),
        extra_capabilities=cc.get("podman_extra_capabilities", []),
        extra_args=cc.get("podman_extra_args", []),
        rootful=cc.get("podman_rootful", False),
    )
```

#### Update error message (line 716):

```python
raise ValueError(f"Unknown environment type: {env_type}. Use 'local', 'docker', 'podman', 'singularity', 'modal', 'daytona', or 'ssh'")
```

#### Update cwd handling for Podman (line 533):

```python
elif env_type in ("modal", "docker", "podman", "singularity", "daytona") and cwd:
```

## Files to Modify

| File | Path | Changes |
|-------|-------|----------|
| Config | `hermes_cli/config.py` | Add 6 config options to `DEFAULT_CONFIG["terminal"]` and 6 entries to `OPTIONAL_ENV_VARS` |
| Podman Environment | `tools/environments/podman.py` | Update `__init__` signature, add instance variables, apply options to run/exec/cleanup commands |
| Terminal Tool | `tools/terminal_tool.py` | Import PodmanEnvironment, add config reading, add podman case to _create_environment |

## Migration

No migration needed. These are new configuration options with safe defaults (empty strings, False, empty lists). Existing users will not be affected.

## Testing

After implementation, test:

1. **Basic Podman backend**: `TERMINAL_ENV=podman hermes`
2. **User namespace remapping**: Set `podman_userns: "keep-id"` in config
3. **Custom user**: Set `podman_user: "1000:1000"` in config
4. **Privileged mode**: Set `podman_privileged: true` in config
5. **Extra capabilities**: Set `podman_extra_capabilities: ["NET_ADMIN"]` in config
6. **Extra args**: Set `podman_extra_args: ["--rm", "--shm-size", "512m"]` in config
7. **Rootful support**: Set `podman_rootful: true` in config
8. **Environment variables**: Test each option via environment variable instead of config

## Notes

- `podman_extra_args` provides maximum flexibility for power users
- No validation or warnings for `podman_extra_args` - use at your own risk
- `podman_extra_capabilities` is additive to existing `DAC_OVERRIDE`, `CHOWN`, `FOWNER`
- Sudo is assumed to be in `$PATH` - if not found, exception will be caught and reported
- Rootless mode is implicit (determined by who runs Hermes), not a configuration option
