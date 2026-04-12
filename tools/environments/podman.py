"""Podman execution environment for sandboxed command execution.

Security hardened (cap-drop ALL, no-new-privileges, PID limits),
optionally rootless and user-namespace-remapped
configurable resource limits (CPU, memory, disk), and optional filesystem
persistence via bind mounts.
"""

import logging
import os
import re
import shutil
import subprocess
import sys
import uuid
from typing import Optional

from tools.environments.base import _popen_bash
# for now, we'll use the exact same default security args as the Docker backend
from tools.environments.docker import DockerEnvironment, _SECURITY_ARGS
from tools.environments.utils import \
    normalize_forward_env_names, \
    normalize_env_dict, \
    load_hermes_env_vars, \
    find_container_cli_binary
from tools.environments.local import _HERMES_PROVIDER_ENV_BLOCKLIST

logger = logging.getLogger(__name__)


# Common Podman install paths checked when 'podman' is not in PATH.
_PODMAN_SEARCH_PATHS = [
    "/usr/bin/podman"
    "/usr/local/bin/podman",
    "/opt/homebrew/bin/podman",
    "/opt/podman/bin/podman",
    "/home/linuxbrew/.linuxbrew/bin/podman",
]

_podman_executable: Optional[str] = None  # resolved once, cached


def find_podman() -> Optional[str]:
    """Locate the podman CLI binary"""
    global _podman_executable
    if _podman_executable is not None:
        return _podman_executable

    found = find_container_cli_binary("podman", _PODMAN_SEARCH_PATHS)
    if found:
        _podman_executable = found
        return found

    return None


def _ensure_podman_available() -> None:
    """Best-effort check that the podman CLI is available before use.

    Reuses ``find_podman()`` so this preflight stays consistent with the rest of
    the Podman backend, including known non-PATH Podman Desktop locations.
    """
    podman_exe = find_podman()
    if not podman_exe:
        logger.error(
            "Podman backend selected but no podman executable was found in PATH "
            "or known install locations. Install Podman Desktop and ensure the "
            "CLI is available."
        )
        raise RuntimeError(
            "Podman executable not found in PATH or known install locations. "
            "Install Podman and ensure the 'podman' command is available."
        )

    try:
        result = subprocess.run(
            [podman_exe, "version"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except FileNotFoundError:
        logger.error(
            "Podman backend selected but the resolved podman executable '%s' could "
            "not be executed.",
            podman_exe,
            exc_info=True,
        )
        raise RuntimeError(
            "Podman executable could not be executed. Check your Podman installation."
        )
    except subprocess.TimeoutExpired:
        logger.error(
            "Podman backend selected but '%s version' timed out.",
            podman_exe,
            exc_info=True,
        )
        raise RuntimeError(
            "`podman version` is not responding."
        )
    except Exception:
        logger.error(
            "Unexpected error while checking Podman availability.",
            exc_info=True,
        )
        raise
    else:
        if result.returncode != 0:
            logger.error(
                "Podman backend selected but '%s version' failed "
                "(exit code %d, stderr=%s)",
                podman_exe,
                result.returncode,
                result.stderr.strip(),
            )
            raise RuntimeError(
                "Podman command is available but 'podman version' failed. "
                "Check your Podman installation."
            )


class PodmanEnvironment(DockerEnvironment):
    """Hardened Podman container execution with resource limits and persistence.

    Security: all capabilities dropped, no privilege escalation, PID limits,
    size-limited tmpfs for scratch dirs. The container itself is the security
    boundary — the filesystem inside is writable so agents can install packages
    (pip, npm, apt) as needed. Writable workspace via tmpfs or bind mounts.

    Persistence: when enabled, bind mounts preserve /workspace and /root
    across container restarts.

    This class is derived from DockerEnvironment so we can reuse the
    _build_init_env_args method. Meanwhile, we support some options that are
    first-class only in Podman, in particular:
    1) rootless vs rootful
    2) privileged mode
    """

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
        if cwd == "~":
            cwd = "/root"
        super().__init__(cwd=cwd, timeout=timeout)
        self._persistent = persistent_filesystem
        self._task_id = task_id
        self._forward_env = normalize_forward_env_names(forward_env)
        self._env = normalize_env_dict(env)
        self._container_id: Optional[str] = None
        # Podman-specific options
        self._privileged = privileged
        self._userns = userns
        self._user = user
        self._extra_capabilities = extra_capabilities or []
        self._extra_args = extra_args or []
        self._rootful = rootful
        logger.info(f"PodmanEnvironment volumes: {volumes}")
        # Ensure volumes is a list (config.yaml could be malformed)
        if volumes is not None and not isinstance(volumes, list):
            logger.warning(f"docker_volumes config is not a list: {volumes!r}")
            volumes = []

        # Fail fast if Podman is not available.
        _ensure_podman_available()

        # Build resource limit args
        resource_args = []
        if cpu > 0:
            resource_args.extend(["--cpus", str(cpu)])
        if memory > 0:
            resource_args.extend(["--memory", f"{memory}m"])
        if disk > 0:
            logger.warning(
                "Podman storage driver does not support per-container disk limits. "
                "Container will run without disk quota."
            )
        if not network:
            resource_args.append("--network=none")

        # Persistent workspace via bind mounts from a configurable host directory
        # (TERMINAL_SANDBOX_DIR, default ~/.hermes/sandboxes/). Non-persistent
        # mode uses tmpfs (ephemeral, fast, gone on cleanup).
        from tools.environments.base import get_sandbox_dir

        # User-configured volume mounts (from config.yaml docker_volumes)
        volume_args = []
        workspace_explicitly_mounted = False
        for vol in (volumes or []):
            if not isinstance(vol, str):
                logger.warning(f"Podman volume entry is not a string: {vol!r}")
                continue
            vol = vol.strip()
            if not vol:
                continue
            if ":" in vol:
                volume_args.extend(["-v", vol])
                if ":/workspace" in vol:
                    workspace_explicitly_mounted = True
            else:
                logger.warning(f"Podman volume '{vol}' missing colon, skipping")

        host_cwd_abs = os.path.abspath(os.path.expanduser(host_cwd)) if host_cwd else ""
        bind_host_cwd = (
            auto_mount_cwd
            and bool(host_cwd_abs)
            and os.path.isdir(host_cwd_abs)
            and not workspace_explicitly_mounted
        )
        if auto_mount_cwd and host_cwd and not os.path.isdir(host_cwd_abs):
            logger.debug(f"Skipping podman cwd mount: host_cwd is not a valid directory: {host_cwd}")

        self._workspace_dir: Optional[str] = None
        self._home_dir: Optional[str] = None
        writable_args = []
        if self._persistent:
            sandbox = get_sandbox_dir() / "podman" / task_id
            self._home_dir = str(sandbox / "home")
            os.makedirs(self._home_dir, exist_ok=True)
            writable_args.extend([
                "-v", f"{self._home_dir}:/root",
            ])
            if not bind_host_cwd and not workspace_explicitly_mounted:
                self._workspace_dir = str(sandbox / "workspace")
                os.makedirs(self._workspace_dir, exist_ok=True)
                writable_args.extend([
                    "-v", f"{self._workspace_dir}:/workspace",
                ])
        else:
            if not bind_host_cwd and not workspace_explicitly_mounted:
                writable_args.extend([
                    "--tmpfs", "/workspace:rw,exec,size=10g",
                ])
            writable_args.extend([
                "--tmpfs", "/home:rw,exec,size=1g",
                "--tmpfs", "/root:rw,exec,size=1g",
            ])

        if bind_host_cwd:
            logger.info(f"Mounting configured host cwd to /workspace: {host_cwd_abs}")
            volume_args = ["-v", f"{host_cwd_abs}:/workspace", *volume_args]
        elif workspace_explicitly_mounted:
            logger.debug("Skipping podman cwd mount: /workspace already mounted by user config")

        # Mount credential files (OAuth tokens, etc.) declared by skills.
        # Read-only so the container can authenticate but not modify host creds.
        try:
            from tools.credential_files import (
                get_credential_file_mounts,
                get_skills_directory_mount,
                get_cache_directory_mounts,
            )

            for mount_entry in get_credential_file_mounts():
                volume_args.extend([
                    "-v",
                    f"{mount_entry['host_path']}:{mount_entry['container_path']}:ro",
                ])
                logger.info(
                    "Podman: mounting credential %s -> %s",
                    mount_entry["host_path"],
                    mount_entry["container_path"],
                )

            # Mount skill directories (local + external) so skill
            # scripts/templates are available inside the container.
            for skills_mount in get_skills_directory_mount():
                volume_args.extend([
                    "-v",
                    f"{skills_mount['host_path']}:{skills_mount['container_path']}:ro",
                ])
                logger.info(
                    "Podman: mounting skills dir %s -> %s",
                    skills_mount["host_path"],
                    skills_mount["container_path"],
                )

            # Mount host-side cache directories (documents, images, audio,
            # screenshots) so the agent can access uploaded files and other
            # cached media from inside the container.  Read-only — the
            # container reads these but the host gateway manages writes.
            for cache_mount in get_cache_directory_mounts():
                volume_args.extend([
                    "-v",
                    f"{cache_mount['host_path']}:{cache_mount['container_path']}:ro",
                ])
                logger.info(
                    "Podman: mounting cache dir %s -> %s",
                    cache_mount["host_path"],
                    cache_mount["container_path"],
                )
        except Exception as e:
            logger.debug("Podman: could not load credential file mounts: %s", e)

        # Apply privileged flag
        if self._privileged:
            writable_args.append("--privileged")

        # Apply user namespace
        if self._userns:
            writable_args.extend(["--userns", self._userns])

        # Apply user
        if self._user:
            writable_args.extend(["--user", self._user])

        # Apply extra capabilities (additive to defaults)
        if self._extra_capabilities:
            for cap in self._extra_capabilities:
                writable_args.extend(["--cap-add", cap])

        # Apply extra args (no validation)
        if self._extra_args:
            writable_args.extend(self._extra_args)

        # Explicit environment variables (docker_env config) — set at container
        # creation so they're available to all processes (including entrypoint).
        env_args = []
        for key in sorted(self._env):
            env_args.extend(["-e", f"{key}={self._env[key]}"])

        logger.info(f"Podman volume_args: {volume_args}")
        all_run_args = list(_SECURITY_ARGS) + writable_args + resource_args + volume_args + env_args
        logger.info(f"Podman run_args: {all_run_args}")

        # Resolve the podman executable once so it works even when
        # /usr/local/bin is not in PATH (common on macOS gateway/service).
        self._podman_exe = find_podman() or "podman"

        # Start the container directly via `podman run -d`.
        container_name = f"hermes-{uuid.uuid4().hex[:8]}"
        run_cmd = [
            self._podman_exe, "run", "-d",
            "--name", container_name,
            "-w", cwd,
            *all_run_args,
            image,
            "sleep", "2h",
        ]
        if self._rootful:
            run_cmd = ["sudo"] + run_cmd
        logger.debug(f"Starting container: {' '.join(run_cmd)}")
        result = subprocess.run(
            run_cmd,
            capture_output=True,
            text=True,
            timeout=120,  # image pull may take a while
            check=True,
        )
        self._container_id = result.stdout.strip()
        logger.info(f"Started container {container_name} ({self._container_id[:12]})")

        # Build init-time env forwarding args (used only by init_session
        # to inject host env vars into the snapshot; subsequent commands get
        # them from the snapshot file).
        self._init_env_args = self._build_init_env_args()

        # Initialize session snapshot inside container
        self.init_session()

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

    def cleanup(self):
        """Stop and remove the container. Bind-mount dirs persist if persistent=True.
        
        Our implementation needs to support rootful mode.
        """
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
