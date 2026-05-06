"""Persistent archive hub for cross-experiment warm-starts (B3).

Every finished experiment can be snapshotted into a content-addressed
hub directory. Future ``evolver init --warm-start <tag>`` invocations
pull matching snapshots and use their best-K candidates as starter
seeds — cold-starts turn into warm-starts, cross-experiment knowledge
accumulates, and the user can publish snapshots to other machines
without shipping full SQLite files around.

Snapshot layout
---------------
```
<hub_root>/
├── <content-hash>/
│   ├── manifest.json        metadata: tag, objectives, best-K, timestamp
│   ├── lineage.db.tar.gz    full SQLite DB, compressed
│   └── fitness.py           user fitness function (so snapshots are
│                            self-contained for replay)
└── index.json               flat index: [{hash, tag, created_at, size}]
```

Compression: gzip (stdlib only). zstandard would be faster but adds a
runtime dep we don't need at this scale — compressed `lineage.db`
files for our target population sizes are < 50 MB.

Hash scheme: blake2b-16 of the concatenated bytes of ``lineage.db``
and the normalised manifest (objectives + task tag). Re-pushing the
identical experiment is a no-op.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import tarfile
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import storage


_ENV_VAR = "HERMES_HOME"


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------


def default_hub_root() -> Path:
    val = os.environ.get(_ENV_VAR, "").strip()
    root = Path(val) if val else Path.home() / ".hermes"
    return root / "skills" / "research" / "darwinian-evolver" / "hub"


# ---------------------------------------------------------------------------
# Snapshot metadata
# ---------------------------------------------------------------------------


@dataclass
class Snapshot:
    hash:       str
    tag:        str
    created_at: int
    size:       int
    manifest:   dict
    path:       Path

    @property
    def lineage_tar(self) -> Path:
        return self.path / "lineage.db.tar.gz"

    @property
    def fitness_path(self) -> Path:
        return self.path / "fitness.py"


def _hash_payload(db_bytes: bytes, manifest_body: bytes) -> str:
    """Content hash of (raw db bytes + normalised manifest bytes).

    We explicitly skip the tar wrapper because gzip headers include a
    timestamp and tar headers include per-invocation mtimes, either of
    which would break idempotent push. Hashing the raw SQLite file
    bytes gives bit-identical output for an unchanged experiment.
    """
    h = hashlib.blake2b(digest_size=16)
    h.update(db_bytes)
    h.update(b"\x1e")
    h.update(manifest_body)
    return h.hexdigest()


def _build_manifest(experiment_dir: Path, tag: str, top_k: int) -> dict:
    """Read the experiment DB and build a manifest with best-K summaries.

    Forces a WAL checkpoint before returning so the raw DB file bytes
    we subsequently hash reflect all committed state — otherwise a
    pending WAL would mask new candidates and make push non-idempotent.
    """
    db_path = experiment_dir / "lineage.db"
    conn = storage.open_db(db_path)
    try:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        objectives = [
            r["objective"]
            for r in conn.execute(
                "SELECT DISTINCT objective FROM fitness"
            ).fetchall()
        ] or ["fitness"]
        best_per_obj: dict[str, list[dict]] = {}
        for obj in objectives:
            rows = storage.get_best(conn, obj, k=top_k)
            best_per_obj[obj] = [
                {"id": r["id"], "value": r["value"], "genome": r["genome"]}
                for r in rows
            ]
        gen_row = conn.execute(
            "SELECT COALESCE(MAX(generation), 0) AS g FROM candidates"
        ).fetchone()
        op_rows = conn.execute(
            "SELECT operator, COUNT(*) AS n FROM lineage "
            "GROUP BY operator ORDER BY n DESC"
        ).fetchall()
        return {
            "tag":         tag,
            "created_at":  int(time.time()),
            "experiment":  experiment_dir.name,
            "objectives":  objectives,
            "generations": int(gen_row["g"]),
            "best":        best_per_obj,
            "operators":   [{"name": r["operator"], "count": r["n"]} for r in op_rows],
            "schema":      "darwinian-evolver-hub/v1",
        }
    finally:
        conn.close()


def _tar_lineage(db_path: Path) -> bytes:
    """Return the gzipped tar bytes for *db_path* (single-file archive)."""
    buf = tempfile.NamedTemporaryFile(delete=False, suffix=".tar.gz")
    buf.close()
    try:
        with tarfile.open(buf.name, "w:gz") as tf:
            tf.add(str(db_path), arcname="lineage.db")
        return Path(buf.name).read_bytes()
    finally:
        os.unlink(buf.name)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def push(
    experiment_dir: Path,
    *,
    tag: Optional[str] = None,
    top_k: int = 10,
    hub_root: Optional[Path] = None,
) -> Snapshot:
    """Snapshot *experiment_dir* into the hub.

    Deterministic: the same experiment pushed twice lands at the same
    content hash and the second push is a no-op.
    """
    experiment_dir = Path(experiment_dir)
    db_path = experiment_dir / "lineage.db"
    if not db_path.exists():
        raise FileNotFoundError(f"no lineage.db at {db_path}")
    root = Path(hub_root or default_hub_root())
    root.mkdir(parents=True, exist_ok=True)

    tag = tag or experiment_dir.name
    manifest = _build_manifest(experiment_dir, tag, top_k)
    # Drop the volatile ``created_at`` field from the hash input so
    # re-pushing after a clock tick hashes identically; the stored
    # manifest still carries the wall-clock stamp for UX.
    hashable = {k: v for k, v in manifest.items() if k != "created_at"}
    manifest_bytes = json.dumps(hashable, sort_keys=True, ensure_ascii=False).encode("utf-8")
    # Content hash uses a row-level digest rather than raw SQLite
    # bytes. This sidesteps WAL / journal quirks that can make byte-
    # level hashes differ across runs even when the logical data is
    # identical, while still catching any genuine schema / data drift.
    conn = storage.open_db(db_path)
    try:
        db_digest = storage.lineage_hash(conn).encode("ascii")
    finally:
        conn.close()
    tar_bytes = _tar_lineage(db_path)
    content_hash = _hash_payload(db_digest, manifest_bytes)

    snap_dir = root / content_hash
    if snap_dir.exists():
        # Idempotent push — update index timestamp only.
        _bump_index(root, content_hash, tag)
        return _read_snapshot(snap_dir, content_hash)

    snap_dir.mkdir()
    (snap_dir / "lineage.db.tar.gz").write_bytes(tar_bytes)
    (snap_dir / "manifest.json").write_bytes(manifest_bytes)
    # Copy fitness.py so the snapshot is self-contained.
    fit = experiment_dir / "fitness.py"
    if fit.exists():
        shutil.copy2(fit, snap_dir / "fitness.py")
    _bump_index(root, content_hash, tag)
    return _read_snapshot(snap_dir, content_hash)


def pull(
    tag_or_hash: str,
    dest: Path,
    *,
    hub_root: Optional[Path] = None,
) -> Snapshot:
    """Extract the matching snapshot into *dest* and return its record.

    Matches by content hash first; falls back to the most recent
    snapshot whose tag equals *tag_or_hash*.
    """
    snapshot = resolve(tag_or_hash, hub_root=hub_root)
    if snapshot is None:
        raise KeyError(f"no hub snapshot matching {tag_or_hash!r}")
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    with tarfile.open(snapshot.lineage_tar, "r:gz") as tf:
        tf.extractall(dest)
    (dest / "manifest.json").write_bytes(snapshot.lineage_tar.with_name("manifest.json").read_bytes())
    if snapshot.fitness_path.exists():
        shutil.copy2(snapshot.fitness_path, dest / "fitness.py")
    return snapshot


def resolve(
    tag_or_hash: str,
    *,
    hub_root: Optional[Path] = None,
) -> Optional[Snapshot]:
    """Find a snapshot by content hash or (newest wins) tag."""
    root = Path(hub_root or default_hub_root())
    if not root.exists():
        return None
    direct = root / tag_or_hash
    if direct.is_dir() and (direct / "manifest.json").exists():
        return _read_snapshot(direct, tag_or_hash)
    index_path = root / "index.json"
    if not index_path.exists():
        return None
    idx = json.loads(index_path.read_text("utf-8") or "[]")
    matches = [e for e in idx if e.get("tag") == tag_or_hash]
    if not matches:
        return None
    # Prefer nanosecond precision when available; fall back to
    # seconds for entries written by older versions.
    matches.sort(
        key=lambda e: (e.get("created_at_ns", 0), e.get("created_at", 0)),
        reverse=True,
    )
    chosen = matches[0]["hash"]
    return _read_snapshot(root / chosen, chosen)


def list_snapshots(*, hub_root: Optional[Path] = None) -> list[Snapshot]:
    root = Path(hub_root or default_hub_root())
    if not root.exists():
        return []
    index_path = root / "index.json"
    if not index_path.exists():
        return []
    idx = json.loads(index_path.read_text("utf-8") or "[]")
    out: list[Snapshot] = []
    for entry in idx:
        path = root / entry["hash"]
        if path.is_dir():
            out.append(_read_snapshot(path, entry["hash"]))
    return out


def warm_start_seeds(
    tag_or_hash: str,
    *,
    top_k: int = 5,
    objective: Optional[str] = None,
    hub_root: Optional[Path] = None,
) -> list[str]:
    """Return the top-K genomes from a snapshot's manifest.

    Read directly from ``manifest.json`` so the caller never unpacks
    the full SQLite — warm-starting a new experiment is cheap.
    """
    snap = resolve(tag_or_hash, hub_root=hub_root)
    if snap is None:
        raise KeyError(f"no snapshot for {tag_or_hash!r}")
    best = snap.manifest.get("best", {})
    if not best:
        return []
    obj = objective or (snap.manifest.get("objectives") or ["fitness"])[0]
    rows = best.get(obj, [])
    return [r["genome"] for r in rows[:top_k]]


# ---------------------------------------------------------------------------
# Index bookkeeping
# ---------------------------------------------------------------------------


def _read_snapshot(path: Path, content_hash: str) -> Snapshot:
    manifest = json.loads((path / "manifest.json").read_text("utf-8"))
    size = (path / "lineage.db.tar.gz").stat().st_size if (path / "lineage.db.tar.gz").exists() else 0
    return Snapshot(
        hash=content_hash,
        tag=str(manifest.get("tag", "")),
        created_at=int(manifest.get("created_at", 0)),
        size=size,
        manifest=manifest,
        path=path,
    )


def _bump_index(root: Path, content_hash: str, tag: str) -> None:
    """Write/merge the flat index file used by :func:`list_snapshots`."""
    index_path = root / "index.json"
    idx: list[dict] = []
    if index_path.exists():
        try:
            idx = json.loads(index_path.read_text("utf-8") or "[]")
        except json.JSONDecodeError:
            idx = []
    # Remove any stale entry for this hash, then re-append with fresh stamp.
    idx = [e for e in idx if e.get("hash") != content_hash]
    size = 0
    tar = root / content_hash / "lineage.db.tar.gz"
    if tar.exists():
        size = tar.stat().st_size
    # Nanosecond precision so rapid-fire pushes are strictly ordered —
    # second-precision ``int(time.time())`` can tie and make
    # ``resolve()`` return the older snapshot for a given tag.
    idx.append({
        "hash":        content_hash,
        "tag":         tag,
        "created_at":  int(time.time()),
        "created_at_ns": time.time_ns(),
        "size":        size,
    })
    # Atomic write (tmpfile + rename) so concurrent readers don't tear.
    tmp = index_path.with_suffix(".tmp")
    tmp.write_text(json.dumps(idx, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(index_path)
