#!/usr/bin/env bash
#
# Phase 1 — A1 descriptor ablation driver.
#
# Produces results.jsonl with one row per (condition, task, seed)
# combination. Idempotent: reruns skip rows already present.

set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
OUT="$HERE/results.jsonl"
SCRIPTS="$(cd "$HERE/../../scripts" && pwd)"

: "${MODEL:=local:qwen3.5-32b-instruct}"
: "${TASKS:=prompt/summarize_10_words regex/email-f1 code/fizzbuzz}"
: "${SEEDS:=42 43 44 45 46 47 48 49 50 51}"
: "${CONDITIONS:=off periodic continuous}"

mkdir -p "$HERE/logs"
touch "$OUT"

for task in $TASKS; do
  for cond in $CONDITIONS; do
    for seed in $SEEDS; do
      tag="${task//\//-}__${cond}__${seed}"
      if grep -q "\"tag\":\"${tag}\"" "$OUT"; then
        echo "skip $tag (already in results)"
        continue
      fi
      exp_dir="$HERE/runs/$tag"
      mkdir -p "$exp_dir/seed"
      # Each task has a canonical seed file under ../fixtures/<task>/
      cp "$HERE/fixtures/${task}/initial.txt" "$exp_dir/seed/initial.txt" 2>/dev/null || true
      cp "$HERE/fixtures/${task}/fitness.py"  "$exp_dir/fitness.py"        2>/dev/null || true

      python "$SCRIPTS/evolver.py" run "$exp_dir" \
        --algorithm map-elites \
        --generations 30 --pop 16 --budget 1.00 \
        --descriptor-controller "$cond" --descriptor-every-k 5 \
        --seed "$seed" \
        > "$HERE/logs/$tag.log" 2>&1 || true

      python - <<PY >> "$OUT"
import json, sys, sqlite3
db = "$exp_dir/lineage.db"
conn = sqlite3.connect(db); conn.row_factory = sqlite3.Row
best = conn.execute(
    "SELECT MAX(f.value) AS best FROM fitness f WHERE f.held_out = 0"
).fetchone()["best"] or 0.0
print(json.dumps({
    "tag":        "$tag",
    "task":       "$task",
    "condition":  "$cond",
    "seed":       int("$seed"),
    "best":       float(best),
}))
PY
    done
  done
done

echo "$(wc -l < "$OUT") rows in $OUT"
