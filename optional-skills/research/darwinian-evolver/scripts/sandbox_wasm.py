"""WebAssembly sandbox backend (B4 — v0.5, cross-platform).

Linux's ``firecracker`` microVM offers the tightest isolation but
needs ``/dev/kvm`` — which rules out macOS contributors. WASI (via
``wasmtime-py``) gives us: zero filesystem/network by default, pure
userspace, works on every desktop OS. That matches the skill's
"works on my M-series Mac" invariant.

Usage
-----
Set ``@fitness_spec(sandbox="wasm")`` in your fitness function. The
evaluator routes code candidates through :func:`run_wasm_module` on
a compiled ``.wasm`` payload. We do NOT compile Python-to-WASM
here; callers supply a prebuilt ``.wasm`` module or declare the
backend unused for their task.

When ``wasmtime`` isn't installed, :class:`WasmSandbox` raises
:class:`WasmSandboxUnavailable` with an install hint and the
evaluator falls back to the v0.1 subprocess path.
"""

from __future__ import annotations

import importlib.util
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


class WasmSandboxUnavailable(RuntimeError):
    """Raised when wasmtime-py isn't on PATH / PYTHONPATH."""


def _wasmtime_available() -> bool:
    return importlib.util.find_spec("wasmtime") is not None


@dataclass
class WasmSandbox:
    """Wasmtime-backed WASI sandbox.

    Default WASI config permits no preopens — the guest has no file
    system. Stdout is captured; stderr is dropped to avoid leaking
    host paths from wasmtime's error messages into fitness logs.
    """

    fuel:       int  = 10_000_000     # approx 100ms on commodity HW
    memory_mb:  int  = 128

    def ensure_available(self) -> None:
        if not _wasmtime_available():
            raise WasmSandboxUnavailable(
                "wasmtime is required for sandbox=wasm — "
                "`pip install wasmtime` or switch to sandbox=subprocess.",
            )

    def run_module(
        self,
        wasm_path: Path,
        *,
        stdin: str = "",
    ) -> str:
        """Run a compiled ``.wasm`` module; return stdout."""
        self.ensure_available()
        import io
        import wasmtime as wt

        engine = wt.Engine(wt.Config().consume_fuel(True))
        module = wt.Module.from_file(engine, str(wasm_path))
        linker = wt.Linker(engine)
        linker.define_wasi()
        wasi_config = wt.WasiConfig()
        wasi_config.argv = []                # no host argv exposed
        # captured via pipe; stderr sinkholed to /dev/null
        stdout_path = Path("/tmp") / f"evolver-wasm-{wasm_path.name}.out"
        wasi_config.stdout_file = str(stdout_path)
        wasi_config.stderr_file = "/dev/null"

        store = wt.Store(engine)
        store.set_wasi(wasi_config)
        store.add_fuel(self.fuel)

        inst = linker.instantiate(store, module)
        start = inst.exports(store).get("_start")
        if start is None:
            return ""
        try:
            start(store)
        except wt.Trap:
            return ""
        try:
            return stdout_path.read_text(encoding="utf-8")
        except OSError:
            return ""
        finally:
            try:
                stdout_path.unlink()
            except OSError:
                pass
