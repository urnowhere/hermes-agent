"""SQLite lineage + fitness + budget storage for darwinian-evolver.

Content-addressed candidate IDs (blake2b of the genome) collapse duplicate
mutations and let crossover reuse identical parents without re-evaluation.
All writes are inside a single transaction per call; the database is opened
with WAL mode so concurrent readers (e.g. `evolver status`) see committed
state without blocking the runner.

Schema summary
--------------
candidates     (id, genome, generation, descriptor JSON, created_at)
fitness        (candidate_id, objective, value, held_out, eval_seed, evaluated_at)
lineage        (child_id, parent_id, operator, prompt_hash)
budget_ledger  (ts, input_tokens, output_tokens, usd, operator)

All timestamps are unix seconds (int).
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from pathlib import Path
from typing import Any, Iterable, Optional


SCHEMA = """
CREATE TABLE IF NOT EXISTS candidates (
    id         TEXT PRIMARY KEY,
    genome     TEXT NOT NULL,
    generation INTEGER NOT NULL,
    descriptor TEXT NOT NULL DEFAULT '{}',
    created_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS fitness (
    candidate_id TEXT NOT NULL REFERENCES candidates(id),
    objective    TEXT NOT NULL,
    value        REAL NOT NULL,
    held_out     INTEGER NOT NULL DEFAULT 0,
    eval_seed    INTEGER NOT NULL DEFAULT 0,
    evaluated_at INTEGER NOT NULL,
    PRIMARY KEY (candidate_id, objective, held_out, eval_seed)
);

CREATE TABLE IF NOT EXISTS lineage (
    child_id    TEXT NOT NULL REFERENCES candidates(id),
    parent_id   TEXT NOT NULL REFERENCES candidates(id),
    operator    TEXT NOT NULL,
    prompt_hash TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (child_id, parent_id, operator)
);

CREATE TABLE IF NOT EXISTS budget_ledger (
    ts            INTEGER NOT NULL,
    input_tokens  INTEGER NOT NULL,
    output_tokens INTEGER NOT NULL,
    usd           REAL NOT NULL,
    operator      TEXT NOT NULL
);

-- v0.2: LLM response cache (content-addressed per request body)
CREATE TABLE IF NOT EXISTS llm_cache (
    key               TEXT PRIMARY KEY,
    response          TEXT NOT NULL,
    prompt_tokens     INTEGER NOT NULL DEFAULT 0,
    completion_tokens INTEGER NOT NULL DEFAULT 0,
    model             TEXT NOT NULL DEFAULT '',
    created_at        INTEGER NOT NULL
);

-- v0.2: Pairwise judge votes (Bradley-Terry aggregation source)
CREATE TABLE IF NOT EXISTS pairwise_votes (
    generation INTEGER NOT NULL,
    left_id    TEXT    NOT NULL REFERENCES candidates(id),
    right_id   TEXT    NOT NULL REFERENCES candidates(id),
    winner     TEXT    NOT NULL,
    eval_seed  INTEGER NOT NULL,
    PRIMARY KEY (generation, left_id, right_id, eval_seed)
);

-- v0.2: Bradley-Terry MLE scores per (candidate, generation)
CREATE TABLE IF NOT EXISTS bt_scores (
    candidate_id TEXT    NOT NULL REFERENCES candidates(id),
    generation   INTEGER NOT NULL,
    log_odds     REAL    NOT NULL,
    iters        INTEGER NOT NULL,
    PRIMARY KEY (candidate_id, generation)
);

-- v0.2: Constitutional-critic post-hoc reviews
CREATE TABLE IF NOT EXISTS critic_evaluations (
    candidate_id TEXT    NOT NULL REFERENCES candidates(id),
    generation   INTEGER NOT NULL,
    risk         REAL    NOT NULL,
    evidence     TEXT    NOT NULL DEFAULT '',
    signal_tags  TEXT    NOT NULL DEFAULT '[]',
    model        TEXT    NOT NULL DEFAULT '',
    evaluated_at INTEGER NOT NULL,
    PRIMARY KEY (candidate_id, generation, model)
);

-- v0.3 (A1): controller-driven descriptor mutations
CREATE TABLE IF NOT EXISTS descriptor_history (
    generation INTEGER PRIMARY KEY,
    grid_expr  TEXT    NOT NULL,
    action     TEXT    NOT NULL,          -- 'keep' | 'replace'
    reason     TEXT    NOT NULL DEFAULT '',
    applied_at INTEGER NOT NULL
);

-- v0.3 (B3): warm-starts pulled from hub snapshots
CREATE TABLE IF NOT EXISTS hub_imports (
    candidate_id TEXT    NOT NULL REFERENCES candidates(id),
    hub_hash     TEXT    NOT NULL,
    hub_tag      TEXT    NOT NULL DEFAULT '',
    imported_at  INTEGER NOT NULL,
    PRIMARY KEY (candidate_id, hub_hash)
);

-- v0.4 (A2): LLM-proposed mutation operators added mid-run
CREATE TABLE IF NOT EXISTS generated_operators (
    name         TEXT PRIMARY KEY,
    template     TEXT NOT NULL,
    temperature  REAL NOT NULL DEFAULT 0.8,
    created_at   INTEGER NOT NULL,
    retired_at   INTEGER NOT NULL DEFAULT 0
);

-- v0.4 (A3): adversarial test inputs produced during co-evolution
CREATE TABLE IF NOT EXISTS red_team_inputs (
    id          TEXT PRIMARY KEY,
    genome      TEXT NOT NULL,
    fitness     REAL NOT NULL,
    generation  INTEGER NOT NULL,
    created_at  INTEGER NOT NULL
);

-- v0.4 (A4): auto-synthesised fitness functions
CREATE TABLE IF NOT EXISTS fitness_syntheses (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    archetype   TEXT NOT NULL,           -- 'exact' | 'soft' | 'judge'
    examples_n  INTEGER NOT NULL,
    fitness_src TEXT NOT NULL,
    created_at  INTEGER NOT NULL
);

-- v0.6 (A5): task features learned for cross-task transfer
CREATE TABLE IF NOT EXISTS task_features (
    experiment  TEXT PRIMARY KEY,
    features    TEXT NOT NULL,          -- JSON blob
    policy_hash TEXT NOT NULL DEFAULT '',
    recorded_at INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_fitness_candidate ON fitness(candidate_id);
CREATE INDEX IF NOT EXISTS idx_lineage_child     ON lineage(child_id);
CREATE INDEX IF NOT EXISTS idx_lineage_parent    ON lineage(parent_id);
CREATE INDEX IF NOT EXISTS idx_candidates_gen    ON candidates(generation);
CREATE INDEX IF NOT EXISTS idx_bt_gen            ON bt_scores(generation);
CREATE INDEX IF NOT EXISTS idx_critic_gen        ON critic_evaluations(generation);
"""


def hash_genome(genome: str) -> str:
    """Return the content-addressed ID for a genome.

    The first 16 hex chars of blake2b give 64 bits of identifier space —
    collisions are astronomically unlikely for the populations we run
    (≤ 10^5 candidates per experiment) and the short form keeps the DAG
    diagrams readable.
    """
    return hashlib.blake2b(genome.encode("utf-8"), digest_size=8).hexdigest()


def open_db(path: Path) -> sqlite3.Connection:
    """Open (creating if missing) the lineage database at *path*.

    Uses WAL journaling so a background writer does not block readers
    invoked by ``evolver status`` or ``evolver best``. Foreign keys are
    enabled; the caller owns the connection lifecycle.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), isolation_level=None, timeout=10.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    return conn


# ---------------------------------------------------------------------------
# Writes
# ---------------------------------------------------------------------------


def insert_candidate(
    conn: sqlite3.Connection,
    genome: str,
    generation: int,
    descriptor: Optional[dict] = None,
    parents: Iterable[tuple[str, str, str]] = (),
) -> str:
    """Insert a candidate (idempotent on genome) and its lineage edges.

    Parameters
    ----------
    genome : str
        The textual artifact being evolved.
    generation : int
        Generation in which this candidate first appeared. If the candidate
        is rediscovered in a later generation, we keep the original row.
    descriptor : dict, optional
        Behavioral descriptor payload (JSON-serialized on disk).
    parents : iterable of (parent_id, operator, prompt_hash)
        Zero or more parent edges. Use for both mutation (1 parent) and
        crossover (2+ parents). Edges are inserted *after* the candidate
        row so the foreign keys resolve.

    Returns
    -------
    str
        The content-addressed candidate ID.
    """
    cid = hash_genome(genome)
    descriptor_json = json.dumps(descriptor or {}, sort_keys=True, ensure_ascii=False)
    now = int(time.time())
    with conn:
        conn.execute(
            "INSERT OR IGNORE INTO candidates (id, genome, generation, descriptor, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (cid, genome, generation, descriptor_json, now),
        )
        for parent_id, operator, prompt_hash in parents:
            conn.execute(
                "INSERT OR IGNORE INTO lineage (child_id, parent_id, operator, prompt_hash) "
                "VALUES (?, ?, ?, ?)",
                (cid, parent_id, operator, prompt_hash),
            )
    return cid


def record_fitness(
    conn: sqlite3.Connection,
    candidate_id: str,
    objective: str,
    value: float,
    held_out: bool = False,
    eval_seed: int = 0,
) -> None:
    """Record one (objective, value) observation for a candidate.

    The primary key is ``(candidate_id, objective, held_out, eval_seed)``:
    calling this twice with the same tuple is a no-op (INSERT OR REPLACE),
    which keeps replay deterministic even if the runner double-scores.
    """
    with conn:
        conn.execute(
            "INSERT OR REPLACE INTO fitness "
            "(candidate_id, objective, value, held_out, eval_seed, evaluated_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (candidate_id, objective, float(value), 1 if held_out else 0, eval_seed, int(time.time())),
        )


def record_budget(
    conn: sqlite3.Connection,
    input_tokens: int,
    output_tokens: int,
    usd: float,
    operator: str,
) -> None:
    """Append one line to the per-LLM-call budget ledger."""
    with conn:
        conn.execute(
            "INSERT INTO budget_ledger (ts, input_tokens, output_tokens, usd, operator) "
            "VALUES (?, ?, ?, ?, ?)",
            (int(time.time()), int(input_tokens), int(output_tokens), float(usd), operator),
        )


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------


def get_candidate(conn: sqlite3.Connection, cid: str) -> Optional[dict]:
    row = conn.execute("SELECT * FROM candidates WHERE id = ?", (cid,)).fetchone()
    if row is None:
        return None
    out = dict(row)
    out["descriptor"] = json.loads(out["descriptor"] or "{}")
    return out


def get_best(
    conn: sqlite3.Connection,
    objective: str,
    k: int = 5,
    held_out: Optional[bool] = None,
) -> list[dict]:
    """Return the top-K candidates by *objective*, highest value first.

    If *held_out* is None, we prefer held-out scores where available and
    fall back to the training-split score. This matches the user's
    expectation that ``evolver best`` reports the generalization-robust
    winner rather than the leaderboard overfit.
    """
    sql = """
        SELECT c.id, c.genome, c.generation, f.value, f.held_out, f.eval_seed
        FROM candidates c
        JOIN fitness f ON f.candidate_id = c.id
        WHERE f.objective = ?
    """
    params: list[Any] = [objective]
    if held_out is not None:
        sql += " AND f.held_out = ?"
        params.append(1 if held_out else 0)
    sql += " ORDER BY f.held_out DESC, f.value DESC LIMIT ?"
    params.append(int(k))
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


def get_ancestry(conn: sqlite3.Connection, cid: str, max_depth: int = 20) -> list[dict]:
    """Return all ancestor edges of *cid* up to *max_depth*.

    The result is a flat list of edges ``{child, parent, operator,
    prompt_hash, depth}`` suitable for rendering a Mermaid/Graphviz DAG.
    A BFS guards against cycles which cannot occur in an append-only
    lineage but could appear if the database is corrupted.
    """
    seen: set[str] = {cid}
    frontier: list[tuple[str, int]] = [(cid, 0)]
    edges: list[dict] = []
    while frontier:
        node, depth = frontier.pop(0)
        if depth >= max_depth:
            continue
        rows = conn.execute(
            "SELECT child_id, parent_id, operator, prompt_hash FROM lineage WHERE child_id = ?",
            (node,),
        ).fetchall()
        for r in rows:
            edges.append({**dict(r), "depth": depth + 1})
            if r["parent_id"] not in seen:
                seen.add(r["parent_id"])
                frontier.append((r["parent_id"], depth + 1))
    return edges


def get_budget_used(conn: sqlite3.Connection) -> dict:
    """Return cumulative budget usage."""
    row = conn.execute(
        "SELECT COALESCE(SUM(input_tokens), 0) AS in_toks, "
        "       COALESCE(SUM(output_tokens), 0) AS out_toks, "
        "       COALESCE(SUM(usd), 0.0)         AS usd, "
        "       COUNT(*)                        AS calls "
        "FROM budget_ledger"
    ).fetchone()
    return dict(row)


def count_generations(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT MAX(generation) AS g FROM candidates").fetchone()
    return int(row["g"] or 0)


def all_candidates_in_generation(conn: sqlite3.Connection, generation: int) -> list[dict]:
    rows = conn.execute(
        "SELECT id, genome, descriptor FROM candidates WHERE generation = ?",
        (generation,),
    ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["descriptor"] = json.loads(d["descriptor"] or "{}")
        out.append(d)
    return out


# ---------------------------------------------------------------------------
# Replay / determinism
# ---------------------------------------------------------------------------


def lineage_hash(conn: sqlite3.Connection) -> str:
    """Return a stable hash of the full lineage state.

    ``evolver replay --seed N`` asserts this hash equals the previous
    run's recorded hash; a mismatch means determinism broke (non-seeded
    LLM output, unstable fitness function, etc.).
    """
    h = hashlib.blake2b(digest_size=16)
    for table, cols in (
        ("candidates", "id, generation, descriptor"),
        ("fitness",    "candidate_id, objective, value, held_out, eval_seed"),
        ("lineage",    "child_id, parent_id, operator, prompt_hash"),
    ):
        for row in conn.execute(f"SELECT {cols} FROM {table} ORDER BY 1, 2, 3"):
            h.update(str(tuple(row)).encode("utf-8"))
        h.update(b"|")
    return h.hexdigest()


# ---------------------------------------------------------------------------
# v0.2: Pairwise votes + Bradley-Terry scores (feature 3)
# ---------------------------------------------------------------------------


def record_pairwise_vote(
    conn: sqlite3.Connection,
    generation: int,
    left_id: str,
    right_id: str,
    winner: str,
    eval_seed: int,
) -> None:
    """Record one pairwise judge verdict.

    ``winner`` is one of ``"left" | "right" | "tie"``. Primary key is
    ``(generation, left_id, right_id, eval_seed)`` so rerunning the
    same pair with the same seed overwrites rather than duplicates.
    """
    with conn:
        conn.execute(
            "INSERT OR REPLACE INTO pairwise_votes "
            "(generation, left_id, right_id, winner, eval_seed) "
            "VALUES (?, ?, ?, ?, ?)",
            (int(generation), left_id, right_id, winner, int(eval_seed)),
        )


def record_bt_score(
    conn: sqlite3.Connection,
    generation: int,
    candidate_id: str,
    log_odds: float,
    iters: int,
) -> None:
    with conn:
        conn.execute(
            "INSERT OR REPLACE INTO bt_scores "
            "(candidate_id, generation, log_odds, iters) VALUES (?, ?, ?, ?)",
            (candidate_id, int(generation), float(log_odds), int(iters)),
        )


def get_pairwise_votes(conn: sqlite3.Connection, generation: int) -> list[dict]:
    rows = conn.execute(
        "SELECT left_id, right_id, winner, eval_seed FROM pairwise_votes "
        "WHERE generation = ?",
        (int(generation),),
    ).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# v0.2: Constitutional critic evaluations (feature 4)
# ---------------------------------------------------------------------------


def record_critic_evaluation(
    conn: sqlite3.Connection,
    candidate_id: str,
    generation: int,
    risk: float,
    evidence: str,
    signal_tags: list[str],
    model: str,
) -> None:
    """Persist one critic review. Primary key is
    ``(candidate_id, generation, model)`` so re-running the same model
    on the same candidate overwrites rather than duplicates.
    """
    with conn:
        conn.execute(
            "INSERT OR REPLACE INTO critic_evaluations "
            "(candidate_id, generation, risk, evidence, signal_tags, model, evaluated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (candidate_id, int(generation), float(risk),
             str(evidence), json.dumps(signal_tags, ensure_ascii=False),
             str(model), int(time.time())),
        )


def get_critic_evaluation(
    conn: sqlite3.Connection,
    candidate_id: str,
    generation: int,
) -> Optional[dict]:
    row = conn.execute(
        "SELECT risk, evidence, signal_tags, model FROM critic_evaluations "
        "WHERE candidate_id = ? AND generation = ? ORDER BY evaluated_at DESC LIMIT 1",
        (candidate_id, int(generation)),
    ).fetchone()
    if row is None:
        return None
    out = dict(row)
    out["signal_tags"] = json.loads(out["signal_tags"] or "[]")
    return out


# ---------------------------------------------------------------------------
# v0.3 (A1): descriptor history — one row per controller verdict
# ---------------------------------------------------------------------------


def record_descriptor(
    conn: sqlite3.Connection,
    generation: int,
    grid_expr: str,
    action: str,
    reason: str = "",
) -> None:
    with conn:
        conn.execute(
            "INSERT OR REPLACE INTO descriptor_history "
            "(generation, grid_expr, action, reason, applied_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (int(generation), str(grid_expr), str(action), str(reason), int(time.time())),
        )


def list_descriptor_history(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        "SELECT generation, grid_expr, action, reason FROM descriptor_history "
        "ORDER BY generation"
    ).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# v0.3 (B3): hub imports — lineage-aware warm-start provenance
# ---------------------------------------------------------------------------


def record_hub_import(
    conn: sqlite3.Connection,
    candidate_id: str,
    hub_hash: str,
    hub_tag: str = "",
) -> None:
    with conn:
        conn.execute(
            "INSERT OR IGNORE INTO hub_imports "
            "(candidate_id, hub_hash, hub_tag, imported_at) VALUES (?, ?, ?, ?)",
            (candidate_id, str(hub_hash), str(hub_tag), int(time.time())),
        )


# ---------------------------------------------------------------------------
# v0.4 (A2): LLM-generated operators
# ---------------------------------------------------------------------------


def record_generated_operator(
    conn: sqlite3.Connection,
    name: str,
    template: str,
    temperature: float,
) -> None:
    with conn:
        conn.execute(
            "INSERT OR REPLACE INTO generated_operators "
            "(name, template, temperature, created_at) VALUES (?, ?, ?, ?)",
            (str(name), str(template), float(temperature), int(time.time())),
        )


def retire_generated_operator(conn: sqlite3.Connection, name: str) -> None:
    with conn:
        conn.execute(
            "UPDATE generated_operators SET retired_at = ? WHERE name = ?",
            (int(time.time()), str(name)),
        )


def list_generated_operators(
    conn: sqlite3.Connection, include_retired: bool = False,
) -> list[dict]:
    sql = "SELECT name, template, temperature, created_at, retired_at FROM generated_operators"
    if not include_retired:
        sql += " WHERE retired_at = 0"
    rows = conn.execute(sql).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# v0.4 (A3): adversarial red-team inputs
# ---------------------------------------------------------------------------


def record_red_team_input(
    conn: sqlite3.Connection,
    adversary_id: str,
    genome: str,
    fitness: float,
    generation: int,
) -> None:
    with conn:
        conn.execute(
            "INSERT OR REPLACE INTO red_team_inputs "
            "(id, genome, fitness, generation, created_at) VALUES (?, ?, ?, ?, ?)",
            (str(adversary_id), str(genome), float(fitness), int(generation), int(time.time())),
        )


def list_red_team_inputs(
    conn: sqlite3.Connection, min_generation: int = 0,
) -> list[dict]:
    rows = conn.execute(
        "SELECT id, genome, fitness, generation FROM red_team_inputs "
        "WHERE generation >= ? ORDER BY fitness DESC",
        (int(min_generation),),
    ).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# v0.4 (A4): persisted fitness syntheses
# ---------------------------------------------------------------------------


def record_fitness_synthesis(
    conn: sqlite3.Connection,
    archetype: str,
    examples_n: int,
    fitness_src: str,
) -> int:
    with conn:
        cur = conn.execute(
            "INSERT INTO fitness_syntheses (archetype, examples_n, fitness_src, created_at) "
            "VALUES (?, ?, ?, ?)",
            (str(archetype), int(examples_n), str(fitness_src), int(time.time())),
        )
        return int(cur.lastrowid or 0)


# ---------------------------------------------------------------------------
# v0.6 (A5): task features for cross-task transfer
# ---------------------------------------------------------------------------


def record_task_features(
    conn: sqlite3.Connection,
    experiment: str,
    features: dict,
    policy_hash: str = "",
) -> None:
    with conn:
        conn.execute(
            "INSERT OR REPLACE INTO task_features "
            "(experiment, features, policy_hash, recorded_at) VALUES (?, ?, ?, ?)",
            (str(experiment),
             json.dumps(features, ensure_ascii=False, sort_keys=True),
             str(policy_hash), int(time.time())),
        )


def get_task_features(
    conn: sqlite3.Connection, experiment: str,
) -> Optional[dict]:
    row = conn.execute(
        "SELECT features, policy_hash FROM task_features WHERE experiment = ?",
        (str(experiment),),
    ).fetchone()
    if row is None:
        return None
    return {
        "features":    json.loads(row["features"] or "{}"),
        "policy_hash": row["policy_hash"],
    }
