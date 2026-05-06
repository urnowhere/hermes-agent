"""v0.3 feature A1 — LLM-conditioned MAP-Elites descriptor controller.

Covers three layers:

* ``descriptor_dsl`` parser: whitelist enforcement, kwarg parsing,
  nested-paren splitting, canonical round-trip.
* ``DescriptorController.propose``: JSON extraction, bad-proposal
  soft-fail to "keep", controller trigger gating.
* ``remap_archive``: archive re-bins under a new grid without losing
  fitness history and with collision-by-fitness resolution.
"""

from __future__ import annotations

import asyncio
import json
import random
import sys
from pathlib import Path

import httpx
import pytest

SCRIPTS_DIR = (
    Path(__file__).resolve().parents[2]
    / "optional-skills" / "research" / "darwinian-evolver" / "scripts"
)
sys.path.insert(0, str(SCRIPTS_DIR))

import algorithms            # noqa: E402
import descriptor_controller # noqa: E402
import descriptor_dsl        # noqa: E402
import llm                   # noqa: E402


# ---------------------------------------------------------------------------
# descriptor_dsl parser
# ---------------------------------------------------------------------------


class TestDescriptorDSL:
    def test_parses_default_grid(self):
        fn = descriptor_dsl.parse_descriptor("grid(length(bins=8), cot_presence())")
        assert fn.bin_counts == (8, 2)
        assert fn("Let's think step by step.") == (fn.extractors[0].call("Let's think step by step."),
                                                    1)
        # canonical reprints the same expression (kwargs are re-emitted
        # for length because the factory takes `bins` and `max`).
        assert fn.canonical() == "grid(length(bins=8, max=2000), cot_presence())"

    def test_rejects_unknown_extractor(self):
        with pytest.raises(descriptor_dsl.DescriptorParseError):
            descriptor_dsl.parse_descriptor("grid(sentiment(bins=4))")

    def test_rejects_top_level_nongrid(self):
        with pytest.raises(descriptor_dsl.DescriptorParseError):
            descriptor_dsl.parse_descriptor("length(bins=8)")

    def test_rejects_non_numeric_kwargs(self):
        """No arbitrary-value kwargs — keeps the DSL hermetic."""
        with pytest.raises(descriptor_dsl.DescriptorParseError):
            descriptor_dsl.parse_descriptor("grid(length(bins=os.system))")

    def test_handles_nested_parens_in_split(self):
        """Argument splitter must respect nested parens in extractors."""
        fn = descriptor_dsl.parse_descriptor(
            "grid(length(bins=4, max=500), token_entropy(bins=4, max=8.0))"
        )
        assert fn.bin_counts == (4, 4)
        coords = fn("hello world hello")
        assert len(coords) == 2
        assert all(0 <= c < 4 for c in coords)

    def test_reading_grade_and_punctuation(self):
        fn = descriptor_dsl.parse_descriptor(
            "grid(reading_grade(bins=4), punctuation_density(bins=4))"
        )
        text = "This is a short, simple test. Punctuation!"
        coords = fn(text)
        assert len(coords) == 2
        assert all(0 <= c < 4 for c in coords)


# ---------------------------------------------------------------------------
# remap_archive
# ---------------------------------------------------------------------------


class TestRemapArchive:
    def _populated(self):
        old = algorithms.MapElitesArchive(
            bin_counts=(8, 2), lows=(0, 0), highs=(8, 2),
        )
        for i in range(4):
            genome = "x" * (10 ** (i + 1))     # increasing length
            ind = algorithms.Individual(
                cid=f"c{i}", genome=genome, fitness=0.1 * i,
                descriptor=(i * 2, 0),
            )
            old.place(ind)
        return old

    def test_remap_preserves_fitness(self):
        """Every fitness in the remapped archive must come from the old
        one (no corruption), and the best fitness must be preserved
        exactly. Collapses due to a coarser grid are allowed — the
        contract is "no data invented, best retained"."""
        old = self._populated()
        new_fn = descriptor_dsl.parse_descriptor(
            "grid(length(bins=4), cot_presence())"
        )
        fresh = descriptor_controller.remap_archive(old, new_fn)
        original_fits = {ind.fitness for ind in old.cells.values()}
        new_fits = {ind.fitness for ind in fresh.cells.values()}
        # No alien fitness values introduced.
        assert new_fits.issubset(original_fits)
        # Best fitness retained (collapses keep the strongest occupant).
        assert max(new_fits) == max(original_fits)
        assert fresh.bin_counts == (4, 2)

    def test_remap_resolves_collisions_by_fitness(self):
        """Two candidates that map to the same bin → higher fitness wins."""
        old = algorithms.MapElitesArchive(bin_counts=(4,), lows=(0,), highs=(4,))
        a = algorithms.Individual(cid="a", genome="short", fitness=0.1, descriptor=(0,))
        b = algorithms.Individual(cid="b", genome="short!", fitness=0.9, descriptor=(1,))
        old.place(a); old.place(b)
        # Collapse into a single 1-bin grid so both collide.
        fn = descriptor_dsl.parse_descriptor("grid(cot_presence())")
        fresh = descriptor_controller.remap_archive(old, fn)
        assert len(fresh.cells) == 1
        winner = next(iter(fresh.cells.values()))
        assert winner.cid == "b"


# ---------------------------------------------------------------------------
# DescriptorController
# ---------------------------------------------------------------------------


def _install_controller_post(response_text):
    original = httpx.AsyncClient.post

    async def fake_post(self, url, json=None, **kw):
        class _R:
            status_code = 200
            headers: dict = {}
            def raise_for_status(self_): pass
            def json(self_inner):
                return {
                    "choices": [{"message": {"content": response_text}}],
                    "usage": {"prompt_tokens": 50, "completion_tokens": 20},
                }
        return _R()

    httpx.AsyncClient.post = fake_post  # type: ignore[assignment]
    return lambda: setattr(httpx.AsyncClient, "post", original)


class TestDescriptorController:
    def test_should_trigger_gating(self):
        ctl_off = descriptor_controller.DescriptorController(
            client=type("Stub", (), {"model": "m"})(), mode="off")
        ctl_every = descriptor_controller.DescriptorController(
            client=type("Stub", (), {"model": "m"})(), mode="periodic", trigger_every_k=3)
        ctl_cont = descriptor_controller.DescriptorController(
            client=type("Stub", (), {"model": "m"})(), mode="continuous")

        assert not any(ctl_off.should_trigger(g) for g in range(10))
        assert ctl_every.should_trigger(3)
        assert ctl_every.should_trigger(6)
        assert not ctl_every.should_trigger(4)
        assert not ctl_every.should_trigger(0)       # gen 0 skipped
        assert ctl_cont.should_trigger(1)
        assert not ctl_cont.should_trigger(0)

    def test_propose_accepts_valid_replace(self):
        restore = _install_controller_post(json.dumps({
            "action": "replace",
            "grid":   "grid(length(bins=4), token_entropy(bins=4))",
            "reason": "coverage is too low on current grid",
        }))
        try:
            current = descriptor_dsl.parse_descriptor("grid(length(bins=8), cot_presence())")
            archive = algorithms.MapElitesArchive(
                bin_counts=current.bin_counts, lows=current.lows, highs=current.highs,
            )

            async def _run():
                async with llm.LLMClient(model="m", base_url="http://x", api_key="") as c:
                    ctl = descriptor_controller.DescriptorController(client=c, mode="continuous")
                    return await ctl.propose(current, archive, fitness_deltas=[0.01, 0.005, 0.001])

            proposal = asyncio.run(_run())
            assert proposal.action == "replace"
            assert proposal.grid is not None
            assert proposal.grid.bin_counts == (4, 4)
        finally:
            restore()

    def test_propose_soft_fails_on_bad_grid(self):
        restore = _install_controller_post(json.dumps({
            "action": "replace",
            "grid":   "grid(fake_extractor(bins=8))",
            "reason": "we lied",
        }))
        try:
            current = descriptor_dsl.parse_descriptor("grid(length(bins=8), cot_presence())")
            archive = algorithms.MapElitesArchive(
                bin_counts=current.bin_counts, lows=current.lows, highs=current.highs,
            )

            async def _run():
                async with llm.LLMClient(model="m", base_url="http://x", api_key="") as c:
                    ctl = descriptor_controller.DescriptorController(client=c, mode="periodic")
                    return await ctl.propose(current, archive, fitness_deltas=[])

            proposal = asyncio.run(_run())
            assert proposal.action == "keep"
            assert "unknown extractor" in proposal.reason or "bad grid" in proposal.reason
        finally:
            restore()

    def test_propose_soft_fails_on_malformed_json(self):
        restore = _install_controller_post("not json at all")
        try:
            current = descriptor_dsl.parse_descriptor("grid(length(bins=8), cot_presence())")
            archive = algorithms.MapElitesArchive(
                bin_counts=current.bin_counts, lows=current.lows, highs=current.highs,
            )

            async def _run():
                async with llm.LLMClient(model="m", base_url="http://x", api_key="") as c:
                    ctl = descriptor_controller.DescriptorController(client=c, mode="periodic")
                    return await ctl.propose(current, archive, fitness_deltas=[])

            proposal = asyncio.run(_run())
            assert proposal.action == "keep"
        finally:
            restore()
