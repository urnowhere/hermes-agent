"""v0.4 — A2 bandit director, A3 co-evolution, A4 fitness synthesis."""

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

import algorithms          # noqa: E402
import bandit_director     # noqa: E402
import coevolve            # noqa: E402
import fitness_synth       # noqa: E402
import llm                 # noqa: E402
import storage             # noqa: E402


def _patch_post(response_text):
    original = httpx.AsyncClient.post
    async def fake_post(self, url, json=None, **kw):
        class _R:
            status_code = 200
            headers: dict = {}
            def raise_for_status(self_): pass
            def json(self_inner):
                return {
                    "choices": [{"message": {"content": response_text}}],
                    "usage": {"prompt_tokens": 15, "completion_tokens": 5},
                }
        return _R()
    httpx.AsyncClient.post = fake_post  # type: ignore[assignment]
    return lambda: setattr(httpx.AsyncClient, "post", original)


# ---------------------------------------------------------------------------
# A2 — bandit director
# ---------------------------------------------------------------------------


class TestBanditDirector:
    def test_should_trigger(self):
        d = bandit_director.BanditDirector(client=type("S", (), {"model":"m"})(), trigger_every_r=3)
        assert not d.should_trigger(0)
        assert d.should_trigger(3)
        assert not d.should_trigger(4)
        assert d.should_trigger(6)

    def test_parse_add_action(self):
        raw = json.dumps({"actions": [
            {"type": "add", "name": "tightenOp", "template": "tighten the prompt",
             "temperature": 0.5},
        ]})
        verdict = bandit_director.BanditDirector._parse(raw)
        assert len(verdict.actions) == 1
        a = verdict.actions[0]
        assert a.type == "add"
        # Sanitised name: lowercase + snake.
        assert a.payload["name"] == "tightenop"
        assert a.payload["temperature"] == 0.5

    def test_parse_retire_and_merge(self):
        raw = json.dumps({"actions": [
            {"type": "retire", "name": "novelty"},
            {"type": "merge",  "keep": "paraphrase", "drop": "paraphrase2"},
            {"type": "merge",  "keep": "x", "drop": "x"},      # invalid self-merge
            {"type": "bogus"},                                   # unknown type
        ]})
        verdict = bandit_director.BanditDirector._parse(raw)
        types = [a.type for a in verdict.actions]
        assert types == ["retire", "merge"]

    def test_parse_malformed_json_soft_fails(self):
        verdict = bandit_director.BanditDirector._parse("not json")
        assert verdict.actions == []

    def test_apply_add_bounded_by_max_arms(self):
        b = algorithms.Exp3Bandit(arms=["a", "b"])
        reg = {"a": lambda: None, "b": lambda: None}
        d = bandit_director.BanditDirector(client=type("S",(),{"model":"m"})(), max_arms=2)
        verdict = bandit_director.DirectorVerdict(actions=[
            bandit_director.DirectorAction(
                type="add",
                payload={"name": "new_op", "template": "…", "temperature": 0.7},
            ),
        ])
        applied = bandit_director.apply_verdict(verdict, b, reg, d)
        assert applied == []                # at cap
        assert "new_op" not in reg
        assert b.arms == ["a", "b"]

    def test_retire_requires_sustained_starvation(self):
        b = algorithms.Exp3Bandit(arms=["strong", "weak"])
        b.weights = [1.0, 0.001]
        reg = {"strong": lambda: None, "weak": lambda: None}
        d = bandit_director.BanditDirector(client=type("S",(),{"model":"m"})(),
                                           retire_consecutive_f=2)
        # Only one "under-floor" observation so far → retire rejected.
        d._below_floor_counts = {"strong": 0, "weak": 1}
        verdict = bandit_director.DirectorVerdict(actions=[
            bandit_director.DirectorAction(type="retire", payload={"name": "weak"}),
        ])
        assert bandit_director.apply_verdict(verdict, b, reg, d) == []
        assert "weak" in reg
        # Two observations now → retire accepted.
        d._below_floor_counts["weak"] = 2
        applied = bandit_director.apply_verdict(verdict, b, reg, d)
        assert len(applied) == 1
        assert "weak" not in reg

    def test_apply_merge_averages_weights(self):
        b = algorithms.Exp3Bandit(arms=["p1", "p2"])
        b.weights = [0.6, 0.4]
        reg = {"p1": lambda: None, "p2": lambda: None}
        d = bandit_director.BanditDirector(client=type("S",(),{"model":"m"})())
        verdict = bandit_director.DirectorVerdict(actions=[
            bandit_director.DirectorAction(
                type="merge", payload={"keep": "p1", "drop": "p2"},
            ),
        ])
        bandit_director.apply_verdict(verdict, b, reg, d)
        assert b.arms == ["p1"]
        assert abs(b.weights[0] - 0.5) < 1e-9
        assert "p2" not in reg

    def test_refresh_below_floor_resets_on_recovery(self):
        d = bandit_director.BanditDirector(client=type("S",(),{"model":"m"})(),
                                           retire_floor=0.05)
        d._refresh_below_floor([
            bandit_director.OperatorStats("a", 0.01, 10, -0.01, 0),
        ])
        assert d._below_floor_counts["a"] == 1
        d._refresh_below_floor([
            bandit_director.OperatorStats("a", 0.2, 10, 0.05, 1),
        ])
        assert d._below_floor_counts["a"] == 0

    def test_apply_verdict_stops_on_missing_arm(self):
        b = algorithms.Exp3Bandit(arms=["only"])
        reg = {"only": lambda: None}
        d = bandit_director.BanditDirector(client=type("S",(),{"model":"m"})())
        verdict = bandit_director.DirectorVerdict(actions=[
            bandit_director.DirectorAction(type="retire", payload={"name": "nonexistent"}),
            bandit_director.DirectorAction(
                type="merge", payload={"keep": "only", "drop": "missing"}),
        ])
        assert bandit_director.apply_verdict(verdict, b, reg, d) == []

    def test_dynamic_operator_prompt_hash_stable(self):
        op = bandit_director.DynamicOperator(
            name="test", template="rewrite", temperature=0.5,
        )
        # Same template → same hash regardless of instance.
        op2 = bandit_director.DynamicOperator(
            name="other", template="rewrite", temperature=0.9,
        )
        assert op.prompt_hash == op2.prompt_hash

    def test_audit_uses_llm(self):
        restore = _patch_post(json.dumps({"actions": []}))
        try:
            async def _run():
                async with llm.LLMClient(model="m", base_url="http://x", api_key="") as c:
                    d = bandit_director.BanditDirector(client=c)
                    return await d.audit(
                        algorithms.Exp3Bandit(arms=["a"]),
                        [bandit_director.OperatorStats("a", 0.5, 3, 0.1, 1)],
                        recent_best=[0.1, 0.2, 0.3],
                    )
            verdict = asyncio.run(_run())
            assert verdict.actions == []
        finally:
            restore()


# ---------------------------------------------------------------------------
# A3 — co-evolution
# ---------------------------------------------------------------------------


class TestCoevolution:
    def test_solver_eval_over_adversaries(self):
        adv1 = algorithms.Individual(cid="a1", genome="adv_input_1")
        adv2 = algorithms.Individual(cid="a2", genome="adv_input_2")
        solver = algorithms.Individual(cid="s1", genome="solver_prompt")

        def fit(candidate, ctx):
            # solver beats adv1 but fails adv2.
            return 1.0 if ctx["input"] == "adv_input_1" else 0.0

        score = coevolve.evaluate_solver_vs_adversaries(
            solver, [adv1, adv2], fit,
        )
        assert score == 0.5

    def test_adversary_fitness_counts_failures(self):
        solvers = [
            algorithms.Individual(cid="s1", genome="p1"),
            algorithms.Individual(cid="s2", genome="p2"),
            algorithms.Individual(cid="s3", genome="p3"),
        ]
        adv = algorithms.Individual(cid="a1", genome="bad_input")

        # All solvers score < 0.5 → adversary wins 3/3.
        def fit(_c, _ctx): return 0.1

        assert coevolve.adversary_fitness(adv, solvers, fit) == 1.0

        # No solver fails → adversary wins 0/3.
        def fit_ok(_c, _ctx): return 0.99

        assert coevolve.adversary_fitness(adv, solvers, fit_ok) == 0.0

    def test_evaluate_handles_exceptions(self):
        """A fitness fn that raises should be treated as a loss."""
        solver = algorithms.Individual(cid="s", genome="p")

        def bad(_c, _ctx):
            raise RuntimeError("oops")

        score = coevolve.evaluate_solver_vs_adversaries(
            solver, [algorithms.Individual(cid="a", genome="x")], bad,
        )
        assert score == 0.0

    def test_empty_adversaries_yields_zero(self):
        assert coevolve.evaluate_solver_vs_adversaries(
            algorithms.Individual(cid="s", genome="p"), [], lambda c, ctx: 1.0,
        ) == 0.0

    def test_empty_solvers_yields_zero_adversary_fitness(self):
        assert coevolve.adversary_fitness(
            algorithms.Individual(cid="a", genome="x"), [], lambda c, ctx: 0.0,
        ) == 0.0


# ---------------------------------------------------------------------------
# A4 — fitness synthesis
# ---------------------------------------------------------------------------


class TestFitnessSynth:
    def test_parse_archetype(self):
        raw = json.dumps({"archetype": "exact", "rationale": "short outputs"})
        arch, why = fitness_synth._parse_archetype(raw)
        assert arch == "exact"
        assert "short" in why

    def test_parse_unknown_falls_back_to_soft(self):
        raw = json.dumps({"archetype": "something_weird"})
        arch, _ = fitness_synth._parse_archetype(raw)
        assert arch == "soft"

    def test_parse_malformed_falls_back(self):
        arch, why = fitness_synth._parse_archetype("not json at all")
        assert arch == "soft"
        assert "unparsable" in why

    def test_render_fitness_exact(self):
        src = fitness_synth.render_fitness("exact", [
            {"input": "a", "output": "b"},
        ])
        assert "EXAMPLES" in src
        assert "fitness_spec" in src

    def test_render_fitness_soft_contains_levenshtein(self):
        src = fitness_synth.render_fitness("soft", [
            {"input": "i", "output": "o"},
        ])
        assert "_levenshtein_ratio" in src

    def test_render_fitness_judge_includes_criterion(self):
        src = fitness_synth.render_fitness(
            "judge", [{"input": "i", "output": "o"}], criterion="concise-and-correct",
        )
        assert "concise-and-correct" in src

    def test_synthesise_requires_examples(self):
        async def _run():
            async with llm.LLMClient(model="m", base_url="http://x", api_key="") as c:
                await fitness_synth.synthesise(c, examples=[])
        with pytest.raises(ValueError):
            asyncio.run(_run())

    def test_synthesise_end_to_end(self):
        restore = _patch_post(json.dumps({"archetype": "soft", "rationale": "freeform"}))
        try:
            async def _run():
                async with llm.LLMClient(model="m", base_url="http://x", api_key="") as c:
                    return await fitness_synth.synthesise(
                        c, examples=[{"input": "a", "output": "b"}],
                    )
            out = asyncio.run(_run())
            assert out.archetype == "soft"
            assert "_levenshtein_ratio" in out.fitness_src
        finally:
            restore()


# ---------------------------------------------------------------------------
# Storage additions (v0.4)
# ---------------------------------------------------------------------------


class TestV04Storage:
    def test_generated_operator_crud(self, tmp_path):
        conn = storage.open_db(tmp_path / "lineage.db")
        storage.record_generated_operator(conn, "shorten", "template", 0.6)
        storage.record_generated_operator(conn, "lengthen", "template2", 0.7)
        rows = storage.list_generated_operators(conn)
        assert len(rows) == 2
        storage.retire_generated_operator(conn, "shorten")
        active = storage.list_generated_operators(conn, include_retired=False)
        assert len(active) == 1
        assert active[0]["name"] == "lengthen"

    def test_red_team_inputs(self, tmp_path):
        conn = storage.open_db(tmp_path / "lineage.db")
        storage.record_red_team_input(conn, "a1", "bad_input", 0.9, generation=3)
        storage.record_red_team_input(conn, "a2", "harder", 0.95, generation=5)
        rows = storage.list_red_team_inputs(conn)
        assert [r["id"] for r in rows] == ["a2", "a1"]  # desc by fitness

    def test_fitness_synthesis_record(self, tmp_path):
        conn = storage.open_db(tmp_path / "lineage.db")
        rid = storage.record_fitness_synthesis(conn, "soft", 10, "def fitness(c, ctx): ...")
        assert rid >= 1
