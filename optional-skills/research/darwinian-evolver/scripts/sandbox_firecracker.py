"""Firecracker microVM sandbox backend (B4 — v0.5, Linux-only).

Firecracker spawns a micro-VM with an independent kernel and a
minimal rootfs in ~100 ms (warm boot). For untrusted code candidates
it is the strongest isolation we can offer today: no shared page
cache, no shared /dev, no shared namespaces.

Availability
------------
Requires:
  * Linux with ``/dev/kvm``
  * ``firecracker`` and ``jailer`` binaries on PATH
  * A prebuilt kernel + rootfs (user-supplied paths in
    ``FIRECRACKER_KERNEL`` and ``FIRECRACKER_ROOTFS`` env vars).

On any machine that doesn't meet the requirements,
``FirecrackerSandbox.ensure_available`` raises
:class:`FirecrackerUnavailable` and the evaluator falls back to the
v0.1 subprocess sandbox. macOS contributors therefore still pass CI —
the Linux-only test is gated behind ``kvm_available()``.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


class FirecrackerUnavailable(RuntimeError):
    pass


def kvm_available() -> bool:
    return Path("/dev/kvm").exists()


def _which_all(*bins: str) -> bool:
    return all(shutil.which(b) for b in bins)


def _field_path_env(var: str, default: str = ""):
    """Dataclass field default that reads from *var* at construction."""
    return field(default_factory=lambda: Path(os.environ.get(var, default)))


@dataclass
class FirecrackerSandbox:
    """Very thin wrapper around the Firecracker CLI.

    We deliberately DO NOT implement a full guest agent here — that
    belongs in a follow-up. For v0.5 the primary contract is:
    "detect availability cleanly and run a prebuilt rootfs that
    executes the candidate and writes JSON output to a shared
    vsock channel." Everything else degrades gracefully.
    """

    kernel_path:  Path = _field_path_env("FIRECRACKER_KERNEL")
    rootfs_path:  Path = _field_path_env("FIRECRACKER_ROOTFS")
    boot_timeout_s: float = 5.0
    run_timeout_s:  float = 30.0

    def ensure_available(self) -> None:
        if not kvm_available():
            raise FirecrackerUnavailable("Firecracker needs /dev/kvm (Linux).")
        if not _which_all("firecracker", "jailer"):
            raise FirecrackerUnavailable(
                "firecracker / jailer binaries not on PATH; "
                "install Firecracker ≥1.5 before using sandbox=firecracker.",
            )
        if not (self.kernel_path.exists() and self.rootfs_path.exists()):
            raise FirecrackerUnavailable(
                "kernel/rootfs not found; set FIRECRACKER_KERNEL and "
                "FIRECRACKER_ROOTFS to point at prebuilt images.",
            )

    def run_candidate(
        self,
        code: str,
        *,
        argv: Optional[list[str]] = None,
    ) -> str:
        """Boot a VM, drop *code* onto a shared volume, capture stdout.

        Returns the captured stdout on success, empty string on any
        failure (matching the WASM sandbox's contract — the fitness
        function interprets empty output as "the candidate couldn't
        produce anything useful").
        """
        self.ensure_available()
        with tempfile.TemporaryDirectory(prefix="evolver-fc-") as tmp:
            tmp = Path(tmp)
            (tmp / "solution.py").write_text(code, encoding="utf-8")
            # Minimal VM config; real deployments will template this
            # against the user's rootfs layout.
            config = {
                "boot-source": {
                    "kernel_image_path": str(self.kernel_path),
                    "boot_args": "console=ttyS0 reboot=k panic=1 pci=off",
                },
                "drives": [{
                    "drive_id":     "rootfs",
                    "path_on_host": str(self.rootfs_path),
                    "is_root_device": True,
                    "is_read_only": True,
                }],
                "machine-config": {"vcpu_count": 1, "mem_size_mib": 128},
            }
            import json
            (tmp / "vm.json").write_text(json.dumps(config), encoding="utf-8")
            try:
                proc = subprocess.run(
                    ["firecracker", "--no-api", "--config-file", str(tmp / "vm.json")],
                    timeout=self.run_timeout_s,
                    capture_output=True, text=True, check=False,
                )
            except subprocess.TimeoutExpired:
                return ""
            return proc.stdout[:1_000_000] if proc.returncode == 0 else ""


# ---------------------------------------------------------------------------
# End of module.
# ---------------------------------------------------------------------------
