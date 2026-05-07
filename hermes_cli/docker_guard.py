"""
Docker-root execution guard for the Hermes CLI.

Background (issue #16480):
    The Docker entrypoint normally starts as root, fixes ownership of
    ``$HERMES_HOME``, and then drops privileges to the ``hermes`` user
    (UID/GID controlled by ``HERMES_UID`` / ``HERMES_GID``) before running
    the gateway.

    However, when a user enters a running container with
    ``docker exec -it ... bash`` and runs ``hermes`` interactively, the
    privilege drop in the entrypoint is bypassed. The CLI then runs as
    ``root`` and any files it creates or rewrites under ``$HERMES_HOME``
    (``config.yaml``, logs, cache, state DBs, …) become root-owned. The
    long-running gateway, which still runs as the ``hermes`` user, can
    then no longer read or chown them.

    This module guards against that case. When the CLI is invoked
    inside a container as root **and** the container declares a non-root
    runtime user via ``HERMES_UID`` / ``HERMES_GID``, we either:

    1.  Re-execute the same command as that user via ``gosu`` / ``su-exec``
        (preferred — transparent for the user), or
    2.  Refuse to continue and print an instruction to re-launch as the
        ``hermes`` user.

The guard is deliberately conservative:
    * It only triggers inside a container (``/.dockerenv`` present, or
      ``/proc/1/cgroup`` mentions docker/containerd, or the standard
      Hermes Docker env vars are present).
    * It is a no-op on non-Linux hosts and for non-root users.
    * It can be disabled with ``HERMES_DISABLE_DOCKER_ROOT_GUARD=1`` for
      maintenance / debugging.
"""

from __future__ import annotations

import logging
import os
import shutil
import sys
from typing import Iterable, Optional

logger = logging.getLogger(__name__)

# Subcommands that legitimately need to keep running as the original
# (possibly root) user. These typically *want* root to fix ownership
# from inside the container during recovery.
_BYPASS_SUBCOMMANDS = frozenset({"version", "--version", "-V", "help", "--help", "-h"})

_DISABLE_ENV = "HERMES_DISABLE_DOCKER_ROOT_GUARD"


def _is_linux() -> bool:
    return sys.platform.startswith("linux")


def _running_as_root() -> bool:
    geteuid = getattr(os, "geteuid", None)
    if geteuid is None:  # pragma: no cover - non-POSIX
        return False
    try:
        return geteuid() == 0
    except OSError:  # pragma: no cover - extremely unusual
        return False


def _in_container() -> bool:
    """Best-effort container detection.

    We treat any of the following as "inside a container":
      * ``/.dockerenv`` exists (classic Docker marker).
      * ``/run/.containerenv`` exists (Podman marker).
      * ``/proc/1/cgroup`` mentions ``docker``, ``containerd``, ``kubepods``,
        ``podman``, or ``crio``.
      * ``HERMES_DOCKER`` env var is truthy (set by our official image).
    """
    if os.path.exists("/.dockerenv") or os.path.exists("/run/.containerenv"):
        return True
    if os.environ.get("HERMES_DOCKER", "").lower() in {"1", "true", "yes"}:
        return True
    try:
        with open("/proc/1/cgroup", "r", encoding="utf-8", errors="replace") as fh:
            content = fh.read()
    except OSError:
        return False
    needles = ("docker", "containerd", "kubepods", "podman", "crio")
    return any(n in content for n in needles)


def _resolve_target_uid_gid() -> Optional[tuple[int, int]]:
    """Return the (uid, gid) the CLI *should* run as, or ``None`` if
    the container did not declare a non-root runtime identity.
    """
    raw_uid = os.environ.get("HERMES_UID")
    if not raw_uid:
        return None
    try:
        uid = int(raw_uid)
    except ValueError:
        logger.warning("Ignoring non-integer HERMES_UID=%r", raw_uid)
        return None
    if uid == 0:
        # Operator explicitly opted into running as root; honour it.
        return None
    raw_gid = os.environ.get("HERMES_GID") or str(uid)
    try:
        gid = int(raw_gid)
    except ValueError:
        logger.warning("Ignoring non-integer HERMES_GID=%r", raw_gid)
        gid = uid
    return uid, gid


def _find_dropper() -> Optional[list[str]]:
    """Locate ``gosu`` or ``su-exec`` for transparent privilege drop."""
    for name in ("gosu", "su-exec"):
        path = shutil.which(name)
        if path:
            return [path]
    return None


def _argv_is_bypass(argv: Iterable[str]) -> bool:
    args = list(argv)[1:]
    if not args:
        return False
    return args[0] in _BYPASS_SUBCOMMANDS


def enforce_docker_non_root(argv: Optional[list[str]] = None) -> None:
    """Refuse-or-reexec when running as root inside a container.

    Should be called as early as possible in :func:`hermes_cli.main.main`,
    *before* any code path that might create or touch files under
    ``$HERMES_HOME``.
    """
    if argv is None:
        argv = sys.argv

    if os.environ.get(_DISABLE_ENV, "").lower() in {"1", "true", "yes"}:
        return
    if not _is_linux():
        return
    if not _running_as_root():
        return
    if not _in_container():
        return
    if _argv_is_bypass(argv):
        # ``hermes version`` / ``hermes --help`` don't write state.
        return

    target = _resolve_target_uid_gid()
    if target is None:
        return  # container did not declare a non-root identity
    uid, gid = target

    # Mark the re-exec so we don't loop forever if the dropped process
    # somehow still appears to satisfy the trigger conditions.
    if os.environ.get("HERMES_DOCKER_GUARD_REEXEC") == "1":
        return

    dropper = _find_dropper()
    env = os.environ.copy()
    env["HERMES_DOCKER_GUARD_REEXEC"] = "1"
    # Ensure the dropped process has a sane HOME so config.yaml / .env
    # land where the gateway expects them.
    hermes_home = env.get("HERMES_HOME")
    if hermes_home and not env.get("HOME"):
        env["HOME"] = hermes_home

    if dropper:
        spec = f"{uid}:{gid}"
        cmd = [*dropper, spec, *argv]
        sys.stderr.write(
            f"[hermes] Dropping privileges to uid={uid} gid={gid} via "
            f"{os.path.basename(dropper[0])} (issue #16480 guard).\n"
        )
        sys.stderr.flush()
        try:
            os.execvpe(cmd[0], cmd, env)
        except OSError as exc:  # pragma: no cover - exec failure is rare
            logger.error("Privilege drop via %s failed: %s", dropper[0], exc)
            # fall through to refuse path

    # No dropper available (or exec failed) — refuse rather than poison
    # ``$HERMES_HOME`` with root-owned files.
    sys.stderr.write(
        "ERROR: hermes refuses to run as root inside this container.\n"
        f"       Files written under $HERMES_HOME would become root-owned and the\n"
        f"       gateway running as uid={uid} would lose access to them.\n\n"
        "       Re-launch as the hermes user, e.g.:\n"
        f"         gosu {uid}:{gid} hermes {' '.join(argv[1:]) or 'chat'}\n"
        "       or enter the container with:\n"
        f"         docker exec -it -u {uid}:{gid} <container> bash\n\n"
        f"       To override (NOT recommended), set {_DISABLE_ENV}=1.\n"
    )
    sys.stderr.flush()
    sys.exit(1)


__all__ = ["enforce_docker_non_root"]
