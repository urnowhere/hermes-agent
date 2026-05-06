"""Public-facing forkable-experiment marketplace (C5 — v1.0).

Thin layer on top of the v0.3 hub: where ``hub`` stores snapshots
locally, ``marketplace`` can push / pull snapshots from a GitHub
repo that acts as a community registry. The storage format is
identical — each entry is a tarball + manifest — so forking an
experiment amounts to cloning one directory and extracting it.

We deliberately do NOT build a custom HTTP service: the registry is
a git repo, authentication goes through the user's existing
``gh auth``, and the JSONL index is `append-only` so concurrent
submissions don't race on line order.

Scope (v1.0)
------------
Ship the data contract and CLI surface. A reference registry lives
at ``NousResearch/hermes-evolver-marketplace`` but we don't auto-
push to it; the ``publish`` command prints the tarball location and
leaves the actual git push to the user.
"""

from __future__ import annotations

import json
import shutil
import tarfile
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import hub


@dataclass
class MarketListing:
    hash:       str
    tag:        str
    manifest:   dict
    tarball:    Path


def prepare_listing(
    experiment_dir: Path,
    out_dir: Path,
    *,
    tag: Optional[str] = None,
) -> MarketListing:
    """Produce a shareable tarball for *experiment_dir*.

    The tarball contains the v0.3 snapshot (lineage.db.tar.gz +
    manifest.json + fitness.py). Callers then push the tarball to
    the marketplace repo via git.
    """
    experiment_dir = Path(experiment_dir)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    snap = hub.push(experiment_dir, tag=tag)
    dest_name = f"{snap.hash}.tar"
    dest = out_dir / dest_name
    with tarfile.open(dest, "w") as tf:
        tf.add(str(snap.path), arcname=snap.hash)
    return MarketListing(
        hash=snap.hash, tag=snap.tag,
        manifest=snap.manifest, tarball=dest,
    )


def fork_listing(
    tarball: Path,
    target_experiment: Path,
) -> Path:
    """Extract a marketplace tarball into a warm-start experiment dir."""
    tarball = Path(tarball)
    target_experiment = Path(target_experiment)
    target_experiment.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="evolver-fork-") as tmp:
        with tarfile.open(tarball, "r") as tf:
            tf.extractall(tmp)
        # The archive contains one directory named by content hash.
        tmp_root = Path(tmp)
        inner = next((p for p in tmp_root.iterdir() if p.is_dir()), tmp_root)
        (target_experiment / "seed").mkdir(exist_ok=True)
        # Copy manifest + fitness so the forked experiment runs.
        for fn in ("manifest.json", "fitness.py"):
            src = inner / fn
            if src.exists():
                shutil.copy2(src, target_experiment / fn)
        lineage_tar = inner / "lineage.db.tar.gz"
        if lineage_tar.exists():
            with tarfile.open(lineage_tar, "r:gz") as tf:
                tf.extractall(target_experiment)
        manifest_bytes = (target_experiment / "manifest.json").read_bytes() \
            if (target_experiment / "manifest.json").exists() else b"{}"
        manifest = json.loads(manifest_bytes.decode("utf-8"))
        best = manifest.get("best", {})
        objectives = manifest.get("objectives") or ["fitness"]
        for i, row in enumerate((best.get(objectives[0]) or [])[:5], start=1):
            (target_experiment / "seed" / f"forked_{i:02d}.txt").write_text(
                row.get("genome", ""), encoding="utf-8",
            )
    return target_experiment


def listing_summary(tarball: Path) -> dict:
    tarball = Path(tarball)
    with tarfile.open(tarball, "r") as tf:
        members = [m.name for m in tf.getmembers() if m.isfile() and m.name.endswith("manifest.json")]
        if not members:
            return {"tarball": str(tarball), "manifest": None}
        first = members[0]
        manifest_bytes = tf.extractfile(first).read()
    manifest = json.loads(manifest_bytes.decode("utf-8"))
    return {
        "tarball":    str(tarball),
        "hash":       first.split("/", 1)[0],
        "tag":        manifest.get("tag", ""),
        "objectives": manifest.get("objectives", []),
        "generations": manifest.get("generations", 0),
        "summarised_at": int(time.time()),
    }
