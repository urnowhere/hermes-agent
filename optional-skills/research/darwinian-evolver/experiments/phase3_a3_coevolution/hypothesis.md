# Phase 3 — A3 Co-evolutionary adversarial population

## Claim

Alternating solver / adversary steps produces solver prompts whose
held-out robustness (measured on an unseen adversarial test suite)
is higher than single-population evolution can reach.

## Expected effect size

* Held-out robustness score: **+10 %** absolute (single-pop ≈ 0.60,
  co-evolved ≈ 0.70) on the email-regex task where the adversary
  generates hostile email-like strings.

## Protocol

Conditions: single-population vs co-evolution (alternating steps).
Tasks: email-regex, sql-select-easy.
Adversary seed count: 4.
Max adversary generations: half of total generations.

## Risks

* Red-Queen divergence. Mitigated by `max_adversary_gens` cap +
  explicit niching in the adversary archive (future work).
