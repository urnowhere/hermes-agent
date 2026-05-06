# Phase 4 — A5 Cross-task transfer leave-one-out

## Claim

A transfer policy trained on 9 tasks predicts operator weights and
seed corpora that warm-start the 10th task; the warm-started run
reaches the same final fitness as a cold-start at ≥ 40 % lower
LLM cost.

## Expected effect size

* Cost to reach 95 % of cold-start's final fitness: **-40 %**
  median across LOO folds (so if cold-start spends $1.00, warm-
  start spends ≤ $0.60 on average).

## Protocol

Task bank (10 tasks):

1. summarize_10_words
2. email-regex
3. sql-select-easy
4. fizzbuzz (code)
5. date-parse-regex
6. markdown-to-json
7. number-words-prompt
8. title-case
9. redact-pii
10. generate-exam-question

For each task i:
* Train a policy on tasks 1..10 excluding i.
* Cold-start i with the default settings.
* Warm-start i with the policy's predicted operator weights and
  top-5 seed genomes.
* Compare cost-to-target curves.

## Risks

* Meta-learning noise. Mitigated by k = 3 nearest-neighbours in the
  transfer policy (robust to outliers) and by reporting median
  cost reduction rather than mean.
