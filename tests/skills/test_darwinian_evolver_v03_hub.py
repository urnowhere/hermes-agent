"""v0.3 feature B3 — persistent archive hub."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = (
    Path(__file__).resolve().parents[2]
    / "optional-skills" / "research" / "darwinian-evolver" / "scripts"
)
sys.path.insert(0, str(SCRIPTS_DIR))

import hub      # noqa: E402
import storage  # noqa: E402


def _populated_experiment(dir_: Path) -> Path:
    dir_.mkdir(parents=True, exist_ok=True)
    (dir_ / "seed").mkdir(exist_ok=True)
    (dir_ / "logs").mkdir(exist_ok=True)
    (dir_ / "fitness.py").write_text(
        "def fitness(c, ctx): return float(len(c))\n", encoding="utf-8"
    )
    conn = storage.open_db(dir_ / "lineage.db")
    a = storage.insert_candidate(conn, "short",   0)
    b = storage.insert_candidate(conn, "medium",  1, parents=[(a, "paraphrase", "h")])
    c = storage.insert_candidate(conn, "longest", 2, parents=[(b, "structural_edit", "h")])
    for cid, val in ((a, 0.1), (b, 0.5), (c, 0.9)):
        storage.record_fitness(conn, cid, "fitness", val)
    conn.close()
    return dir_


class TestHubPushPull:
    def test_push_creates_snapshot_and_index(self, tmp_path):
        exp = _populated_experiment(tmp_path / "exp1")
        snap = hub.push(exp, tag="prompt-v1", hub_root=tmp_path / "hub")
        assert (snap.path / "lineage.db.tar.gz").exists()
        assert (snap.path / "manifest.json").exists()
        assert (snap.path / "fitness.py").exists()
        assert (tmp_path / "hub" / "index.json").exists()
        assert snap.manifest["tag"] == "prompt-v1"
        assert snap.manifest["generations"] == 2

    def test_push_is_content_addressed_and_idempotent(self, tmp_path):
        exp = _populated_experiment(tmp_path / "exp1")
        s1 = hub.push(exp, tag="t1", hub_root=tmp_path / "hub")
        s2 = hub.push(exp, tag="t1", hub_root=tmp_path / "hub")
        assert s1.hash == s2.hash
        # Same directory was reused (not recreated).
        assert s1.path == s2.path

    def test_list_snapshots(self, tmp_path):
        e1 = _populated_experiment(tmp_path / "e1")
        e2 = _populated_experiment(tmp_path / "e2")
        # Mutate e2 so it hashes differently.
        conn = storage.open_db(e2 / "lineage.db")
        storage.insert_candidate(conn, "novel-candidate", 3)
        conn.close()
        hub.push(e1, tag="t1", hub_root=tmp_path / "hub")
        hub.push(e2, tag="t2", hub_root=tmp_path / "hub")
        listing = hub.list_snapshots(hub_root=tmp_path / "hub")
        tags = {s.tag for s in listing}
        assert {"t1", "t2"}.issubset(tags)

    def test_resolve_by_tag_picks_newest(self, tmp_path):
        e1 = _populated_experiment(tmp_path / "e1")
        e2 = _populated_experiment(tmp_path / "e2")
        conn = storage.open_db(e2 / "lineage.db")
        storage.insert_candidate(conn, "distinct-new-genome", 5)
        conn.close()
        s1 = hub.push(e1, tag="shared", hub_root=tmp_path / "hub")
        s2 = hub.push(e2, tag="shared", hub_root=tmp_path / "hub")
        newest = hub.resolve("shared", hub_root=tmp_path / "hub")
        assert newest is not None
        assert newest.hash == s2.hash
        assert newest.hash != s1.hash

    def test_pull_extracts_db_and_manifest(self, tmp_path):
        exp = _populated_experiment(tmp_path / "exp1")
        hub.push(exp, tag="pulltag", hub_root=tmp_path / "hub")
        dest = tmp_path / "restored"
        hub.pull("pulltag", dest, hub_root=tmp_path / "hub")
        assert (dest / "lineage.db").exists()
        # Restored DB still answers to storage queries.
        conn = storage.open_db(dest / "lineage.db")
        best = storage.get_best(conn, "fitness", k=1)
        conn.close()
        assert best and best[0]["value"] == 0.9

    def test_warm_start_seeds_returns_top_genomes(self, tmp_path):
        exp = _populated_experiment(tmp_path / "exp1")
        hub.push(exp, tag="seeds", hub_root=tmp_path / "hub", top_k=3)
        genomes = hub.warm_start_seeds("seeds", top_k=2, hub_root=tmp_path / "hub")
        assert genomes == ["longest", "medium"]  # ordered by descending fitness

    def test_pull_unknown_raises(self, tmp_path):
        with pytest.raises(KeyError):
            hub.pull("nonexistent", tmp_path / "dest", hub_root=tmp_path / "hub")

    def test_missing_lineage_db_raises(self, tmp_path):
        empty = tmp_path / "empty"
        empty.mkdir()
        with pytest.raises(FileNotFoundError):
            hub.push(empty, tag="empty", hub_root=tmp_path / "hub")
