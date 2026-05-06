# Phase 1 — A1 LLM-conditioned MAP-Elites descriptor ablation

## Claim

Allowing an LLM to rewrite MAP-Elites' behavioural descriptor mid-run
improves final-generation coverage and fitness relative to a fixed
hand-picked descriptor, with no additional LLM budget spent inside
the main evolutionary loop.

## Expected effect size (pre-registered)

* Coverage at generation 30: **+15 % absolute** (fixed baseline
  ≈ 55 %, LLM-conditioned ≈ 70 %).
* Best fitness at generation 30: **+0.05** mean improvement
  (±0.03 standard error, n = 10 seeds).

Confidence: medium. Effect sizes are from pilot runs on
`summarize_10_words`; real ablation may be smaller on tasks with
narrower behavioural support.

## Ablation protocol

Three conditions × three tasks × ten seeds = 90 runs.

Conditions:
1. **fixed**        — `--descriptor-controller off` (v0.2 default).
2. **periodic**     — `--descriptor-controller periodic --descriptor-every-k 5`.
3. **continuous**   — `--descriptor-controller continuous`.

Tasks:
* `prompt/summarize_10_words` — packaged demo.
* `regex/email-f1`            — templates/regex_fitness.py corpus.
* `code/fizzbuzz`              — templates/code_fitness.py sandbox.

Settings:
* `--generations 30 --pop 16 --budget 1.00 --algorithm map-elites`
* Seeds: 42 … 51 (fixed for reproducibility).
* Model: Qwen3.5-32B-Instruct via local vLLM.

Metrics:
* Coverage per generation (archive cells / capacity).
* Best fitness per generation.
* Descriptor-controller trigger count + action histogram (periodic /
  continuous only).

Analysis:
* Per-generation trajectory plot + 95 % CI ribbon.
* Two-sample Welch's t-test at gen 30 between conditions.
* Effect size reported as Cohen's d.

## Risks + mitigations

* R1 — DSL too narrow to express useful axes: mitigated by
  logging every proposal; if acceptance rate < 10 %, widen the
  extractor registry in a follow-up PR.
* R2 — overfitting to tasks where length matters: run on `regex`
  task where length is not a primary signal; if fixed wins there,
  report as a negative result.

## Compute budget

* 90 runs × 30 gens × 16 pop × 6 LLM calls ≈ 260K LLM calls.
* At $0.003/1K tokens blended rate ≈ $30 total API.
* GPU time: ~4 A100-hours for vLLM serving.
