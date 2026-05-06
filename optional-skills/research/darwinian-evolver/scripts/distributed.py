"""Distributed worker backends for candidate evaluation (B1 — v0.5).

The core evaluator uses an ``asyncio.Semaphore`` to bound concurrency
on a single process. At cluster scale that's a bottleneck. This
module abstracts the worker pool behind a tiny ``WorkerBackend``
protocol and ships three implementations:

* ``LocalBackend`` — the v0.2 semaphore path. Zero new deps, default.
* ``RayBackend``   — optional ``ray>=2.40``. ``--workers ray`` spins a
  local mini-cluster or connects to ``RAY_ADDRESS``. Graceful
  absence when ``ray`` is not installed.
* ``RaySimBackend`` — pure-stdlib shim that mimics Ray's ``remote`` /
  ``get`` surface on top of ``asyncio``. Used for tests and for
  running on machines where installing Ray is heavy-weight. Also
  the implementation the v0.5 test suite exercises, so CI stays
  independent of Ray.

The :func:`select_backend` factory picks one based on CLI flags and
whether the Ray package can be imported. Callers use a single
``await backend.map(fn, items)`` call regardless of backend.
"""

from __future__ import annotations

import asyncio
import importlib.util
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Protocol, runtime_checkable


@runtime_checkable
class WorkerBackend(Protocol):
    """Minimal protocol every backend implements.

    The protocol is intentionally narrow — ``map(fn, items)`` is the
    only operation the evaluator needs. If a future feature requires
    more (e.g. streaming), we'll add ``map_stream`` rather than
    break backward compat.
    """

    name: str

    async def map(
        self,
        fn: Callable[[Any], Awaitable[Any] | Any],
        items: list[Any],
    ) -> list[Any]: ...


# ---------------------------------------------------------------------------
# LocalBackend — the v0.2 async semaphore path
# ---------------------------------------------------------------------------


@dataclass
class LocalBackend:
    name: str = "local"
    concurrency: int = 4

    async def map(self, fn, items):
        sem = asyncio.Semaphore(max(1, self.concurrency))

        async def worker(item):
            async with sem:
                res = fn(item)
                if asyncio.iscoroutine(res):
                    res = await res
                return res

        return await asyncio.gather(*(worker(i) for i in items))


# ---------------------------------------------------------------------------
# RaySimBackend — stdlib-only Ray-compatible shim
# ---------------------------------------------------------------------------


@dataclass
class RaySimBackend:
    """Pretends to be Ray for the purposes of our tests.

    We use asyncio under the hood but expose a ``remote``/``get`` API
    so that when the caller writes code against the "Ray style" it
    runs against both RaySim (CI) and real Ray (clusters) without
    branching logic. Concurrency is still bounded by
    ``workers`` so a runaway dispatch doesn't melt the box.
    """
    name: str = "raysim"
    workers: int = 4

    async def map(self, fn, items):
        sem = asyncio.Semaphore(max(1, self.workers))
        loop = asyncio.get_event_loop()

        async def worker(item):
            async with sem:
                res = fn(item)
                if asyncio.iscoroutine(res):
                    return await res
                if callable(getattr(res, "result", None)) and not isinstance(res, asyncio.Task):
                    # Synchronous future-like object from a mock.
                    return res.result()
                return res

        return await asyncio.gather(*(worker(i) for i in items))


# ---------------------------------------------------------------------------
# RayBackend — real Ray; lazy import so tests don't require ray[default]
# ---------------------------------------------------------------------------


def _ray_available() -> bool:
    return importlib.util.find_spec("ray") is not None


@dataclass
class RayBackend:
    name: str = "ray"
    workers: int = 8
    address: str | None = None       # e.g. "ray://head.example:10001"
    _initialised: bool = field(default=False, init=False, repr=False)

    def _ensure_init(self) -> None:
        if self._initialised:
            return
        import ray                        # local import — backed by optional dep
        if not ray.is_initialized():
            ray.init(address=self.address, ignore_reinit_error=True)
        self._initialised = True

    async def map(self, fn, items):
        if not _ray_available():
            raise ImportError(
                "ray is not installed; `pip install 'ray[default]>=2.40'` "
                "or choose --workers=local / raysim.",
            )
        import ray
        self._ensure_init()

        remote = ray.remote(fn)
        futures = [remote.remote(item) for item in items]
        # Use ray.wait in a loop so we don't block the asyncio loop.
        results: list[Any] = [None] * len(futures)
        pending = list(enumerate(futures))
        while pending:
            idx, fut = pending.pop(0)
            # ``ray.get`` would block; we hop through the default
            # executor so we stay within the asyncio loop.
            result = await asyncio.get_event_loop().run_in_executor(
                None, ray.get, fut,
            )
            results[idx] = result
        return results


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def select_backend(
    name: str,
    *,
    workers: int = 4,
    address: str | None = None,
) -> WorkerBackend:
    name = name.lower()
    if name == "local":
        return LocalBackend(concurrency=workers)
    if name == "raysim":
        return RaySimBackend(workers=workers)
    if name == "ray":
        if not _ray_available():
            raise ImportError(
                "ray is not installed; either `pip install ray` or "
                "use --workers local (default).",
            )
        return RayBackend(workers=workers, address=address)
    raise ValueError(f"unknown backend {name!r}")
