# Benchmark Suite — PR Description

## What this PR adds

A system-agnostic benchmark suite for evaluating AI agent memory systems, plus
four academic benchmark adapters that run the same backends against established
external datasets.

---

## Synthetic scenario suite (Suites A–O)

424 scenarios across 19 categories and 15 suites.  Measures retrieval quality,
contradiction handling, scope isolation, adversarial robustness, scale handling,
feedback-driven reranking, and multi-agent memory behaviours.

7 backend adapters implemented (`baseline-flat`, `holographic`, `mem0`,
`hindsight`, `honcho`, `retaindb`, `openviking`).  6 result bundles checked in.

See `benchmarks/README.md` for the full category and suite breakdown.

---

## Academic benchmark adapters

Four external benchmarks are now wired into `python -m benchmarks --academic`.

| Adapter | Dataset | Scale | Reference |
|---------|---------|-------|-----------|
| LongMemEval | `xiaowu0162/longmemeval-cleaned` | 500 questions | ICLR 2025 |
| LoCoMo | `snap-research/locomo` | ~2,000 QA pairs | arXiv:2402.17753 |
| HotpotQA | `hotpotqa/hotpot_qa` | 500-question sample | arXiv:1809.09600 |
| ConvoMem | `Salesforce/ConvoMem` | 75,336 QA pairs | arXiv:2511.10523 |

---

## ConvoMem results

ConvoMem (Salesforce AI Research, arXiv:2511.10523) benchmarks conversational
memory recall across 6 evidence types at n_evidence levels 1–6.

### Scores by backend

| Backend | Score | Questions | Notes |
|---------|------:|----------:|-------|
| baseline-flat | 66.9% | 6,693 | Full run, all n_evidence 1–6 |
| holographic | 61.9% | 6,693 | Full run |
| honcho | 74.7% | 300 | 6×50-question per-type sample |
| mem0 | 80.0% | 30 | 6×5-question per-type sample (cloud API ~79s/q) |
| hindsight | — | — | SKIPPED: Python 3.14 incompatibility |

**Notes on run scope:**
- `baseline-flat` and `holographic` are full-corpus runs (all n_evidence levels,
  all evidence types, 6,693 questions each).
- `honcho` and `mem0` scores are from smaller per-type samples due to API
  latency and cost.  These numbers are directionally useful but biased by sample
  size — treat with caution.
- `hindsight` was skipped in both this run and earlier runs due to a Python 3.14
  incompatibility in the hindsight dependency chain.

**Notable:** `abstention_evidence` scores 25.1% for `baseline-flat` (expected —
a flat store retrieves everything rather than abstaining, so questions that
require the backend to say "I don't know" will fail by design).  Scores on the
remaining five evidence types are meaningfully higher.

### Implementation note

The HuggingFace streaming API for `Salesforce/ConvoMem` returned a PyArrow
schema error during development.  The adapter works around this by fetching the
Parquet shards directly via `urllib.request` rather than using the `datasets`
library streaming interface.  See `benchmarks/convomem/adapter.py` docstring for
details.

---

## Synthetic suite results summary

Single-seed snapshot; see `benchmarks/results/COMPARISON_REPORT.md` for the
full category breakdown.

### Shared-suite mean (12 categories present for all 6 backends)

| Backend | Shared-Suite Mean |
|---------|------------------:|
| baseline-flat | 89.3% |
| retaindb | 86.3% |
| honcho | 82.1% |
| holographic | 78.3% |
| mem0 | 74.6% |
| hindsight | 72.9% |

Truth-in-advertising: stored result bundle is single-seed (`num_runs = 1`).
Re-run with 5 seeds before drawing publication-strength conclusions.

---

## Test status

```
pytest tests/benchmarks -q
# 22 tests passing
```

---

## Known Issues / TODO

> **Note on result files:** The `benchmarks/results/` JSON files are committed
> directly to the branch for reviewer convenience. These should eventually be
> moved to CI artifacts rather than tracked in git — they are large, change on
> every run, and don't belong in version control long-term. This applies to
> all result files in this PR, not just the ConvoMem ones.

---

## How to reproduce ConvoMem results

```bash
# Full baseline-flat run (matches the 66.9% / 6,693-question result above)
python -m benchmarks.convomem.runner --backend baseline-flat

# Full holographic run
python -m benchmarks.convomem.runner --backend holographic

# Honcho sample (50 questions per evidence type)
python -m benchmarks.convomem.runner --backend honcho --sample 50

# mem0 sample (5 questions per evidence type)
python -m benchmarks.convomem.runner --backend mem0 --sample 5

# All academic benchmarks in sequence
python -m benchmarks --academic
```
