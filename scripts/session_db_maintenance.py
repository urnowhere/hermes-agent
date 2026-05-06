#!/usr/bin/env python3
"""Archive-before-maintenance workflow for Hermes session DB/state files.

Default mode is dry-run. With --apply it creates a timestamped archive containing
state.db, WAL/SHM sidecars, session logs, and a manifest with SHA-256 evidence.
It never prunes rows by default; --optimize only runs SQLite maintenance after a
verified archive exists.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sqlite3
import sys
import tarfile
from datetime import datetime, timezone
from pathlib import Path


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def collect_targets(home: Path) -> list[Path]:
    candidates = [home / "state.db", home / "state.db-wal", home / "state.db-shm"]
    for sub in ("sessions", "logs", "checkpoints"):
        root = home / sub
        if root.exists():
            candidates.extend(p for p in root.rglob("*") if p.is_file())
    return sorted({p for p in candidates if p.exists()})


def build_manifest(home: Path, targets: list[Path]) -> dict:
    files = []
    for p in targets:
        files.append({
            "path": str(p.relative_to(home)),
            "bytes": p.stat().st_size,
            "sha256": sha256_file(p),
        })
    return {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "hermes_home": str(home),
        "file_count": len(files),
        "files": files,
    }


def create_archive(home: Path, out_dir: Path) -> tuple[Path, Path, dict]:
    targets = collect_targets(home)
    manifest = build_manifest(home, targets)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = out_dir / f"session-db-archive-{stamp}.manifest.json"
    archive_path = out_dir / f"session-db-archive-{stamp}.tar.gz"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    with tarfile.open(archive_path, "w:gz") as tar:
        for p in targets:
            tar.add(p, arcname=str(p.relative_to(home)))
        tar.add(manifest_path, arcname=manifest_path.name)
    return archive_path, manifest_path, manifest


def verify_archive(archive_path: Path, manifest: dict) -> list[str]:
    errors: list[str] = []
    expected = {f["path"]: f for f in manifest.get("files", [])}
    with tarfile.open(archive_path, "r:gz") as tar:
        members = {m.name: m for m in tar.getmembers() if m.isfile()}
        for rel, meta in expected.items():
            member = members.get(rel)
            if member is None:
                errors.append(f"missing:{rel}")
                continue
            extracted = tar.extractfile(member)
            if extracted is None:
                errors.append(f"unreadable:{rel}")
                continue
            h = hashlib.sha256(extracted.read()).hexdigest()
            if h != meta["sha256"]:
                errors.append(f"sha256-mismatch:{rel}")
    return errors


def sqlite_optimize(db_path: Path) -> dict:
    result = {"db": str(db_path), "operations": []}
    with sqlite3.connect(str(db_path)) as conn:
        for sql in ("PRAGMA optimize", "VACUUM"):
            conn.execute(sql)
            result["operations"].append(sql)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--home", default="~/.hermes")
    parser.add_argument("--out-dir", default="~/.hermes/archives/session-db")
    parser.add_argument("--apply", action="store_true", help="create archive; default is dry-run")
    parser.add_argument("--optimize", action="store_true", help="after verified archive, run PRAGMA optimize + VACUUM")
    args = parser.parse_args(argv)

    home = Path(args.home).expanduser().resolve(strict=False)
    out_dir = Path(args.out_dir).expanduser().resolve(strict=False)
    targets = collect_targets(home)
    plan = {
        "mode": "apply" if args.apply else "dry-run",
        "home": str(home),
        "out_dir": str(out_dir),
        "target_count": len(targets),
        "total_bytes": sum(p.stat().st_size for p in targets),
        "optimize_requested": bool(args.optimize),
    }
    if not args.apply:
        print(json.dumps(plan, indent=2, sort_keys=True))
        return 0

    archive_path, manifest_path, manifest = create_archive(home, out_dir)
    errors = verify_archive(archive_path, manifest)
    result = {
        **plan,
        "archive": str(archive_path),
        "manifest": str(manifest_path),
        "archive_sha256": sha256_file(archive_path),
        "verify_errors": errors,
    }
    if errors:
        print(json.dumps(result, indent=2, sort_keys=True))
        return 2
    if args.optimize:
        result["sqlite_optimize"] = sqlite_optimize(home / "state.db")
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
