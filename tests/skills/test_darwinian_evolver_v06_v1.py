"""v0.6 + v1.0 — A5 transfer, B5 distill availability, C1-C5 ecosystem."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = (
    Path(__file__).resolve().parents[2]
    / "optional-skills" / "research" / "darwinian-evolver" / "scripts"
)
sys.path.insert(0, str(SCRIPTS_DIR))

import bench                  # noqa: E402
import distill                # noqa: E402
import marketplace            # noqa: E402
import storage                # noqa: E402
import task_features          # noqa: E402
import transfer               # noqa: E402
import validate as vldte      # noqa: E402 — name clash with stdlib


# ---------------------------------------------------------------------------
# A5 — task features + transfer policy
# ---------------------------------------------------------------------------


def _populated_experiment(p: Path, *, with_fitness: str) -> Path:
    p.mkdir(parents=True, exist_ok=True)
    (p / "seed").mkdir(exist_ok=True)
    (p / "seed" / "s.txt").write_text("seed text one", "utf-8")
    (p / "fitness.py").write_text(with_fitness, "utf-8")
    conn = storage.open_db(p / "lineage.db")
    a = storage.insert_candidate(conn, "cand_a", 0)
    b = storage.insert_candidate(conn, "cand_b", 1, parents=[(a, "paraphrase", "h")])
    storage.record_fitness(conn, a, "fitness", 0.3)
    storage.record_fitness(conn, b, "fitness", 0.7)
    conn.close()
    return p


class TestTaskFeatures:
    def test_feature_vector_shape(self, tmp_path):
        exp = _populated_experiment(
            tmp_path / "e1",
            with_fitness="from evolver_sdk import fitness_spec\n"
                         '@fitness_spec(judge="pairwise")\n'
                         "def fitness(c, ctx): return 0.0\n",
        )
        feats = task_features.featurise(exp)
        assert len(feats.vector) == 9
        # judge=pairwise → indices 4, 8 are truthy
        assert feats.vector[4] == 1.0
        assert feats.vector[8] == 1.0

    def test_entropy_is_nonnegative(self):
        assert task_features._char_ngram_entropy("") == 0.0
        assert task_features._char_ngram_entropy("aaaa") == 0.0
        assert task_features._char_ngram_entropy("abcabcxyz") > 0


class TestTransferPolicy:
    def test_empty_policy_returns_empty_prediction(self):
        policy = transfer.TransferPolicy()
        out = policy.predict([1.0, 0.0, 0.0])
        assert out["operator_weights"] == {}
        assert out["confidence"] == 0.0

    def test_knn_returns_nearest(self):
        policy = transfer.TransferPolicy(k=2)
        policy.add(transfer.TrainingPoint(
            features=[1.0, 0.0], operator_weights={"paraphrase": 1.0},
            best_seeds=["seed_close"],
        ))
        policy.add(transfer.TrainingPoint(
            features=[0.0, 1.0], operator_weights={"critique_then_edit": 1.0},
            best_seeds=["seed_far"],
        ))
        out = policy.predict([0.95, 0.05])
        assert "seed_close" in out["seeds"]
        assert out["operator_weights"]["paraphrase"] > out["operator_weights"].get(
            "critique_then_edit", 0,
        )

    def test_save_load_roundtrip(self, tmp_path):
        policy = transfer.TransferPolicy(k=1)
        policy.add(transfer.TrainingPoint(
            features=[1.0, 0.0], operator_weights={"paraphrase": 1.0},
            best_seeds=["s"],
        ))
        path = tmp_path / "policy.pkl"
        policy.save(path)
        loaded = transfer.TransferPolicy.load(path)
        assert loaded.k == 1
        assert len(loaded.points) == 1

    def test_collect_training_from_experiments(self, tmp_path):
        e1 = _populated_experiment(tmp_path / "e1", with_fitness="def fitness(c, ctx): return 0.0\n")
        e2 = _populated_experiment(tmp_path / "e2", with_fitness="def fitness(c, ctx): return 1.0\n")
        points = transfer.collect_training_points([e1, e2])
        assert len(points) == 2
        assert all(isinstance(p.features, list) for p in points)

    def test_policy_hash_changes_on_add(self):
        p = transfer.TransferPolicy()
        h0 = p.policy_hash
        p.add(transfer.TrainingPoint([1.0], {}, []))
        assert p.policy_hash != h0


# ---------------------------------------------------------------------------
# B5 — distill (availability check only; real training is GPU-gated)
# ---------------------------------------------------------------------------


class TestDistill:
    def test_distill_unavailable_without_transformers(self):
        if distill._deps_available():
            pytest.skip("transformers installed; can't test missing-deps path")
        with pytest.raises(distill.DistillUnavailable):
            distill.ensure_available()

    def test_build_dataset_uses_teacher_callback(self):
        calls = []

        def fake_teacher(x: str) -> str:
            calls.append(x)
            return "teacher output for: " + x[:20]

        ds = distill.build_dataset(
            prompts=["Prompt A", "Prompt B"],
            teacher_call=fake_teacher,
            inputs_per_prompt=2,
            seed_inputs=["inp1", "inp2"],
        )
        assert len(ds) == 4
        assert all(e.response.startswith("teacher output") for e in ds)
        assert len(calls) == 4


# ---------------------------------------------------------------------------
# C1 — benchmark hub
# ---------------------------------------------------------------------------


class TestBench:
    def test_registry_lists_known_benchmarks(self):
        ids = {b["id"] for b in bench.list_benchmarks()}
        assert "email-regex/v1" in ids
        assert "ten-word-summary/v1" in ids

    def test_unknown_benchmark_raises(self):
        with pytest.raises(KeyError):
            bench.get("nonexistent/v99")

    def test_email_regex_fitness_scores(self):
        bnch = bench.get("email-regex/v1")
        # Very permissive regex.
        assert 0 < bnch.fitness(r".+@.+\..+", {}) <= 1.0
        # Invalid regex → 0.
        assert bnch.fitness(r"(", {}) == 0.0

    def test_ten_word_fitness(self):
        bnch = bench.get("ten-word-summary/v1")
        assert bnch.fitness(" ".join(["x"] * 10), {}) == 1.0
        assert bnch.fitness("one two three", {}) < 1.0

    def test_score_archive_returns_structured(self, tmp_path):
        conn = storage.open_db(tmp_path / "l.db")
        storage.insert_candidate(conn, r".+@.+\..+", 0)
        storage.record_fitness(conn,
            storage.hash_genome(r".+@.+\..+"), "fitness", 0.5)
        out = bench.score_archive(conn, "email-regex/v1", top_k=1)
        assert out["benchmark"] == "email-regex/v1"
        assert 0 < out["best"] <= 1.0


# ---------------------------------------------------------------------------
# C2 — cross-model validation
# ---------------------------------------------------------------------------


class TestValidate:
    def test_spearman_perfect_correlation(self):
        assert abs(vldte._spearman([1, 2, 3, 4], [10, 20, 30, 40]) - 1.0) < 1e-9

    def test_spearman_reverse(self):
        assert abs(vldte._spearman([1, 2, 3, 4], [40, 30, 20, 10]) - (-1.0)) < 1e-9

    def test_spearman_len_mismatch(self):
        assert vldte._spearman([1, 2], [1, 2, 3]) == 0.0


# ---------------------------------------------------------------------------
# C5 — marketplace
# ---------------------------------------------------------------------------


class TestMarketplace:
    def test_prepare_and_fork_round_trip(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes-home"))
        exp = _populated_experiment(tmp_path / "src", with_fitness="def fitness(c, ctx): return 0.0\n")
        listing = marketplace.prepare_listing(exp, tmp_path / "listings", tag="demo")
        assert listing.tarball.exists()

        target = tmp_path / "fork"
        marketplace.fork_listing(listing.tarball, target)
        assert (target / "lineage.db").exists()
        # Seed dir is populated from manifest best-K.
        assert any((target / "seed").glob("forked_*.txt"))

    def test_listing_summary(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes-home"))
        exp = _populated_experiment(tmp_path / "src", with_fitness="def fitness(c, ctx): return 0.0\n")
        listing = marketplace.prepare_listing(exp, tmp_path / "listings", tag="summary-tag")
        summary = marketplace.listing_summary(listing.tarball)
        assert summary["tag"] == "summary-tag"
        assert "generations" in summary
