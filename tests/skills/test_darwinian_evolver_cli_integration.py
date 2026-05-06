"""CLI + HITL integration tests — fills the eksikler from the
v1.0 commits:

* `evolver synthesise-fitness` (A4) end-to-end with a mocked LLM.
* `evolver transfer train` + `transfer apply` round-trip on a
  synthetic 2-experiment corpus.
* `evolver coevolve` smoke — one solver/adversary pair, deterministic
  stub fitness, no LLM.
* Dashboard `POST /api/candidate/{cid}/edit` accepts a human-edited
  genome and links the new candidate via ``human_edit`` lineage.
* Argparse accepts the new ``--bandit-director`` and ``--workers``
  flags without breaking the existing --help surface.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import sys
from pathlib import Path

import httpx
import pytest

SCRIPTS_DIR = (
    Path(__file__).resolve().parents[2]
    / "optional-skills" / "research" / "darwinian-evolver" / "scripts"
)
sys.path.insert(0, str(SCRIPTS_DIR))

import storage   # noqa: E402
import evolver   # noqa: E402
import transfer  # noqa: E402


fastapi_missing = importlib.util.find_spec("fastapi") is None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _seeded_experiment(path: Path, *, fitness_src: str = "def fitness(c, ctx): return 0.0\n") -> Path:
    path.mkdir(parents=True, exist_ok=True)
    (path / "seed").mkdir(exist_ok=True)
    (path / "seed" / "initial.txt").write_text("seed text\n", "utf-8")
    (path / "fitness.py").write_text(fitness_src, "utf-8")
    (path / "evolver_sdk.py").write_text(
        "from evaluator import fitness_spec\n", encoding="utf-8",
    )
    conn = storage.open_db(path / "lineage.db")
    conn.close()
    return path


def _patch_post(text: str):
    original = httpx.AsyncClient.post
    async def fake(self, url, json=None, **kw):
        class _R:
            status_code = 200
            headers: dict = {}
            def raise_for_status(self_): pass
            def json(self_inner):
                return {
                    "choices": [{"message": {"content": text}}],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 3},
                }
        return _R()
    httpx.AsyncClient.post = fake  # type: ignore[assignment]
    return lambda: setattr(httpx.AsyncClient, "post", original)


# ---------------------------------------------------------------------------
# Argparse surface
# ---------------------------------------------------------------------------


class TestArgparseSurface:
    def test_run_accepts_new_flags(self):
        parser = evolver._build_parser()
        ns = parser.parse_args([
            "run", "demo",
            "--generations", "1", "--pop", "1",
            "--descriptor-controller", "periodic",
            "--bandit-director",      "periodic",
            "--workers", "raysim",
        ])
        assert ns.descriptor_controller == "periodic"
        assert ns.bandit_director == "periodic"
        assert ns.workers == "raysim"

    def test_new_subcommands_registered(self):
        parser = evolver._build_parser()
        # synthesise-fitness, transfer, coevolve
        for name in ("synthesise-fitness", "transfer", "coevolve"):
            ns = parser.parse_args([name, "--help"]) if False else None
            # parse_args with --help exits; inspect the subparsers dict instead.
        help_text = parser.format_help()
        assert "synthesise-fitness" in help_text
        assert "transfer" in help_text
        assert "coevolve" in help_text


# ---------------------------------------------------------------------------
# A4 — synthesise-fitness subcommand
# ---------------------------------------------------------------------------


class TestSynthesiseFitnessCLI:
    def test_writes_fitness_py(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes-home"))
        exp = _seeded_experiment(tmp_path / "exp")
        examples = tmp_path / "examples.jsonl"
        examples.write_text(
            json.dumps({"input": "i1", "output": "o1"}) + "\n"
            + json.dumps({"input": "i2", "output": "o2"}) + "\n",
            "utf-8",
        )
        restore = _patch_post(json.dumps({"archetype": "soft", "rationale": "freeform"}))
        try:
            args = argparse.Namespace(
                dir=str(exp), examples=str(examples),
                criterion="brevity", no_overwrite=False,
            )
            evolver.cmd_synthesise_fitness(args)
            payload = json.loads(capsys.readouterr().out)
        finally:
            restore()
        assert payload["ok"] is True
        assert payload["archetype"] == "soft"
        # fitness.py was overwritten; contains the levenshtein helper.
        fit = (exp / "fitness.py").read_text("utf-8")
        assert "_levenshtein_ratio" in fit

    def test_missing_examples_errors(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes-home"))
        exp = _seeded_experiment(tmp_path / "exp")
        args = argparse.Namespace(
            dir=str(exp), examples=str(tmp_path / "nonexistent.jsonl"),
            criterion="x", no_overwrite=False,
        )
        with pytest.raises(SystemExit):
            evolver.cmd_synthesise_fitness(args)


# ---------------------------------------------------------------------------
# A5 — transfer train / apply round-trip
# ---------------------------------------------------------------------------


class TestTransferCLI:
    def _populate(self, path: Path, fit_value: float) -> Path:
        p = _seeded_experiment(
            path,
            fitness_src=f"def fitness(c, ctx): return {fit_value}\n",
        )
        conn = storage.open_db(p / "lineage.db")
        a = storage.insert_candidate(conn, "cand_a", 0)
        storage.record_fitness(conn, a, "fitness", fit_value)
        b = storage.insert_candidate(conn, "cand_b", 1, parents=[(a, "paraphrase", "h")])
        storage.record_fitness(conn, b, "fitness", fit_value + 0.1)
        conn.close()
        return p

    def test_train_then_apply(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes-home"))
        # Train: two experiments.
        e1 = self._populate(tmp_path / "e1", 0.3)
        e2 = self._populate(tmp_path / "e2", 0.7)
        policy_path = tmp_path / "policy.pkl"

        evolver.cmd_transfer(argparse.Namespace(
            action="train",
            experiments=[str(e1), str(e2)],
            k=2,
            out=str(policy_path),
            policy=None, target=None,
        ))
        train_payload = json.loads(capsys.readouterr().out)
        assert train_payload["points"] == 2
        assert policy_path.exists()

        # Apply: on a fresh experiment.
        target = self._populate(tmp_path / "target", 0.0)
        # Reset seeds so apply can write in transfer seeds.
        for p in (target / "seed").glob("*"):
            p.unlink()
        evolver.cmd_transfer(argparse.Namespace(
            action="apply",
            experiments=[],
            k=2,
            out=None,
            policy=str(policy_path),
            target=str(target),
        ))
        apply_payload = json.loads(capsys.readouterr().out)
        assert apply_payload["ok"]
        # Seeds imported from policy.
        assert any((target / "seed").glob("transfer_*.txt"))


# ---------------------------------------------------------------------------
# A3 — coevolve CLI (no LLM — uses monkey-patched mutation)
# ---------------------------------------------------------------------------


class TestCoevolveCLI:
    def test_coevolve_smoke(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes-home"))
        exp = _seeded_experiment(
            tmp_path / "coevo",
            fitness_src=(
                "def fitness(candidate, context):\n"
                "    return 1.0 if 'good' in candidate else 0.2\n"
            ),
        )
        # Mock httpx.AsyncClient.post so mutation operators produce
        # deterministic output without a real LLM.
        restore = _patch_post("good prompt variant")
        try:
            args = argparse.Namespace(
                dir=str(exp),
                generations=2, adversaries=2,
                concurrency=1, budget=0.0, seed=0,
                max_adversary_gens=1,
            )
            evolver.cmd_coevolve(args)
            payload = json.loads(capsys.readouterr().out)
        finally:
            restore()
        assert payload["ok"]
        assert payload["generations"] == 2
        assert payload["solvers_final"] >= 1


# ---------------------------------------------------------------------------
# C3 — HITL dashboard edit endpoint
# ---------------------------------------------------------------------------


@pytest.mark.skipif(fastapi_missing, reason="fastapi not installed")
class TestDashboardHITL:
    def _populated(self, tmp_path):
        conn = storage.open_db(tmp_path / "lineage.db")
        a = storage.insert_candidate(conn, "original", 0)
        storage.record_fitness(conn, a, "fitness", 0.42)
        conn.close()
        return tmp_path, a

    def test_edit_endpoint_creates_child(self, tmp_path):
        import dashboard
        from fastapi.testclient import TestClient

        root, parent_cid = self._populated(tmp_path)
        client = TestClient(dashboard.build_app(root))
        resp = client.post(
            f"/api/candidate/{parent_cid}/edit",
            json={"genome": "human-edited variant with more context"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["ok"]
        assert body["parent"] == parent_cid
        # Lineage edge registered with human_edit operator.
        conn = storage.open_db(root / "lineage.db")
        rows = conn.execute(
            "SELECT operator FROM lineage WHERE child_id = ?",
            (body["id"],),
        ).fetchall()
        conn.close()
        assert any(r["operator"] == "human_edit" for r in rows)

    def test_edit_rejects_empty_genome(self, tmp_path):
        import dashboard
        from fastapi.testclient import TestClient

        root, parent_cid = self._populated(tmp_path)
        client = TestClient(dashboard.build_app(root))
        resp = client.post(
            f"/api/candidate/{parent_cid}/edit", json={"genome": "   "},
        )
        assert resp.status_code == 400

    def test_edit_unknown_parent_404(self, tmp_path):
        import dashboard
        from fastapi.testclient import TestClient

        root, _ = self._populated(tmp_path)
        client = TestClient(dashboard.build_app(root))
        resp = client.post(
            "/api/candidate/doesnotexist/edit", json={"genome": "x"},
        )
        assert resp.status_code == 404
