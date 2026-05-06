from __future__ import annotations

from pathlib import Path
from typing import Dict, List


def discover_flat_txt(corpus_dir: Path) -> List[Path]:
    root = Path(corpus_dir)
    if not root.exists():
        return []
    return sorted(path for path in root.glob("*.txt") if path.is_file())


def verify_corpus_shape(corpus_dir: Path) -> Dict[str, object]:
    root = Path(corpus_dir)
    flat_txt = discover_flat_txt(root)
    nested_txt = sorted(path for path in root.rglob("*.txt") if path.is_file() and path.parent != root) if root.exists() else []
    parts = sorted(path for path in root.glob("*.part") if path.is_file()) if root.exists() else []
    return {
        "ok": not nested_txt and not parts,
        "flat_txt_count": len(flat_txt),
        "nested_txt_count": len(nested_txt),
        "corpus_part_count": len(parts),
        "flat_txt": [str(p) for p in flat_txt],
        "nested_txt": [str(p) for p in nested_txt],
        "corpus_part": [str(p) for p in parts],
    }
