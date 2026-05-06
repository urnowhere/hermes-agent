"""v0.5 — B1 distributed, B4 sandbox backends, B2 repo sweep."""

from __future__ import annotations

import asyncio
import importlib.util
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = (
    Path(__file__).resolve().parents[2]
    / "optional-skills" / "research" / "darwinian-evolver" / "scripts"
)
sys.path.insert(0, str(SCRIPTS_DIR))

import distributed               # noqa: E402
import repo_sweep                # noqa: E402
import sandbox_firecracker as fc # noqa: E402
import sandbox_wasm as wasm      # noqa: E402


# ---------------------------------------------------------------------------
# B1 — distributed
# ---------------------------------------------------------------------------


class TestDistributed:
    def test_local_backend_preserves_order(self):
        backend = distributed.LocalBackend(concurrency=2)
        result = asyncio.run(backend.map(lambda x: x * 2, [1, 2, 3, 4, 5]))
        assert result == [2, 4, 6, 8, 10]

    def test_raysim_backend_supports_async_fn(self):
        backend = distributed.RaySimBackend(workers=2)

        async def double(x): return x * 2

        result = asyncio.run(backend.map(double, [1, 2, 3]))
        assert result == [2, 4, 6]

    def test_select_local_default(self):
        backend = distributed.select_backend("local", workers=4)
        assert isinstance(backend, distributed.LocalBackend)

    def test_select_raysim(self):
        backend = distributed.select_backend("raysim", workers=4)
        assert isinstance(backend, distributed.RaySimBackend)

    def test_select_ray_without_dep_raises(self):
        # Skip if ray is somehow installed in this env.
        if distributed._ray_available():
            pytest.skip("ray installed; can't test missing-dep path")
        with pytest.raises(ImportError):
            distributed.select_backend("ray")

    def test_select_unknown_raises(self):
        with pytest.raises(ValueError):
            distributed.select_backend("bogus")


# ---------------------------------------------------------------------------
# B4 — sandbox backends
# ---------------------------------------------------------------------------


class TestWasmSandbox:
    def test_unavailable_when_wasmtime_missing(self):
        if importlib.util.find_spec("wasmtime") is not None:
            pytest.skip("wasmtime installed; can't test missing-dep path")
        s = wasm.WasmSandbox()
        with pytest.raises(wasm.WasmSandboxUnavailable):
            s.ensure_available()


class TestFirecrackerSandbox:
    def test_kvm_missing_raises(self):
        if fc.kvm_available():
            pytest.skip("/dev/kvm exists; can't test missing-KVM path")
        s = fc.FirecrackerSandbox()
        with pytest.raises(fc.FirecrackerUnavailable):
            s.ensure_available()


# ---------------------------------------------------------------------------
# B2 — repo sweep
# ---------------------------------------------------------------------------


class TestRepoSweep:
    def _mk_repo(self, tmp_path):
        repo = tmp_path / "repo"
        (repo / "skills" / "research" / "demo").mkdir(parents=True)
        skill_md = (repo / "skills" / "research" / "demo" / "SKILL.md")
        skill_md.write_text(
            "# Demo skill\n\n## Overview\n\nA demo.\n\n## When to use\n\n"
            "When demoing.\n\n## Examples\n\nExample here.\n\n" + ("lorem " * 50),
            encoding="utf-8",
        )
        return repo

    def test_discover_skills(self, tmp_path):
        repo = self._mk_repo(tmp_path)
        found = repo_sweep.discover_skills(repo)
        assert len(found) == 1
        assert found[0].name == "SKILL.md"

    def test_heuristic_score_bounds(self):
        # Empty → 0.
        assert repo_sweep._heuristic_score("") == 0.0
        # Well-structured → close to the cap.
        full = (
            "## Overview\n## When to use\n## Prerequisites\n## Examples\n"
            "## Pitfalls\n## Verification\n" + ("x" * 600)
        )
        assert repo_sweep._heuristic_score(full) >= 0.8

    def test_sweep_dry_run_does_not_evolve(self, tmp_path):
        repo = self._mk_repo(tmp_path)
        cfg = repo_sweep.SweepConfig(
            repo_root=repo,
            output_dir=tmp_path / "out",
            dry_run=True,
            cooldown_dir=tmp_path / "cooldown",
        )
        results = repo_sweep.sweep(cfg)
        assert len(results) == 1
        assert results[0].evolved_text is None
        assert not results[0].improved

    def test_cooldown_respected(self, tmp_path):
        repo = self._mk_repo(tmp_path)
        cfg = repo_sweep.SweepConfig(
            repo_root=repo,
            output_dir=tmp_path / "out",
            dry_run=True,
            cooldown_dir=tmp_path / "cooldown",
        )
        # Touch the cooldown marker to simulate a recent sweep.
        skill_path = next(iter(repo_sweep.discover_skills(repo)))
        cfg.cooldown_dir.mkdir(parents=True, exist_ok=True)
        repo_sweep._touch_cooldown(cfg, skill_path)
        results = repo_sweep.sweep(cfg)
        assert results[0].cooldown_applied
