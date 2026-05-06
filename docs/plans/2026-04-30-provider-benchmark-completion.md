# Provider Benchmark Completion Implementation Plan

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.

**Goal:** Make provider benchmarks resumable and chunk-safe so every provider can eventually produce complete all-suite result JSONs despite container OOMs, rate limits, or service interruptions.

**Architecture:** Add category-level checkpointing to the benchmark runner. After each category finishes, serialize the partial `RunResult` to an atomic per-backend/per-seed checkpoint file under the output directory. On `--resume`, load completed categories from checkpoint files and skip them, then aggregate completed category results into the normal schema-v2 result JSON. This keeps the existing result schema intact while adding a recovery trail.

**Tech Stack:** Python stdlib JSON/pathlib/tempfile, existing `BenchmarkConfig`, `RunResult`, `CategoryResult`, and `aggregate_results`.

---

### Task 1: Add checkpoint serialization/deserialization tests

**Objective:** Prove category results can be checkpointed and loaded without losing score/token/metric fields.

**Files:**
- Modify: `tests/benchmarks/test_result_serialization.py`
- Modify later: `benchmarks/runner.py`

**Steps:**
1. Add a test using `_sample_run()` that calls `save_run_checkpoint(tmp_path, config, run, completed=True)`.
2. Load it with `load_run_checkpoint(tmp_path, config, seed=42)`.
3. Assert category score, correct/total, token usage, retrieval metrics, and completed flag round-trip.
4. Run the new test and verify it fails because helpers do not exist yet.

### Task 2: Implement checkpoint helpers

**Objective:** Add atomic checkpoint save/load functions and dict-to-dataclass conversion.

**Files:**
- Modify: `benchmarks/runner.py:246-370`

**Steps:**
1. Add `deserialize_category_result(data)`.
2. Add `deserialize_run(data)`.
3. Add `checkpoint_path(checkpoint_dir, backend_name, seed)`.
4. Add `save_run_checkpoint(checkpoint_dir, config, run, completed=False)` that writes a `*.tmp` then replaces target.
5. Add `load_run_checkpoint(checkpoint_dir, config, seed)` returning `(RunResult | None, metadata)`.
6. Run the test from Task 1 and verify pass.

### Task 3: Add resume skip behavior tests

**Objective:** Prove a resumed run skips completed categories and executes only missing categories.

**Files:**
- Modify: `tests/benchmarks/test_result_serialization.py`
- Modify later: `benchmarks/runner.py:2980-3108`

**Steps:**
1. Monkeypatch `load_fixtures` to return two categories.
2. Monkeypatch `CATEGORY_RUNNERS` with two tiny runners.
3. Create a checkpoint containing the first category.
4. Run `run_single(config, seed=42)` with `parameters={"checkpoint_dir": tmp_path, "resume": True}`.
5. Assert only the second runner was called and the final run contains both categories.
6. Run the test and verify it fails because `run_single` ignores checkpoints.

### Task 4: Wire checkpoint/resume into `run_single`

**Objective:** Save after every category and support resume skips.

**Files:**
- Modify: `benchmarks/runner.py:2980-3108`

**Steps:**
1. At run start, read `checkpoint_dir` and `resume` from `config.parameters`.
2. If resume is enabled and checkpoint exists, initialize `results_by_cat` from it.
3. Before running a category, skip it if already present in checkpoint.
4. After each category completes, recompute run summary fields and save a partial checkpoint.
5. At run end, save a completed checkpoint.
6. Run resume behavior test and verify pass.

### Task 5: Add CLI flags and default checkpoint directory

**Objective:** Make checkpointing available from normal benchmark commands.

**Files:**
- Modify: `benchmarks/runner.py:3206-3260`

**Steps:**
1. Add `--resume` flag.
2. Add `--no-checkpoint` flag.
3. Add `--checkpoint-dir` optional path.
4. Default checkpoint dir to `<output-dir>/checkpoints` unless `--no-checkpoint` is set.
5. Store `checkpoint_dir`, `checkpoint_enabled`, and `resume` in `config.parameters`.
6. Keep normal result output unchanged.

### Task 6: Verify full benchmark suite and smoke checkpoint output

**Objective:** Prove no regressions and that checkpoint files appear during a real small run.

**Commands:**
```bash
PYTHONPATH=/workspace/Projects/hermes-agent-benchmark-fairness:/workspace/Projects/mnemoria \
  /workspace/Projects/hermes-agent/.venv/bin/python -m pytest tests/benchmarks -q

rm -rf /tmp/mnemoria-checkpoint-smoke
PYTHONPATH=/workspace/Projects/hermes-agent-benchmark-fairness:/workspace/Projects/mnemoria \
  /workspace/Projects/hermes-agent/.venv/bin/python -B -m benchmarks \
  --backend baseline-flat --suite p --runs 1 --seeds 42 --embedding tfidf \
  --output-dir /tmp/mnemoria-checkpoint-smoke

python - <<'PY'
from pathlib import Path
print(sorted(str(p) for p in Path('/tmp/mnemoria-checkpoint-smoke/checkpoints').glob('*.json')))
PY
```

Expected:
- `54+` benchmark tests pass.
- Smoke run writes normal result JSON and checkpoint JSON.

### Task 7: Commit concern-split change

**Objective:** Preserve the harness upgrade as a local commit.

**Commands:**
```bash
git diff --check
git status --short
git add benchmarks/runner.py tests/benchmarks/test_result_serialization.py docs/plans/2026-04-30-provider-benchmark-completion.md
git commit -m "bench: checkpoint provider benchmark runs"
```

Expected:
- Worktree clean after commit.
