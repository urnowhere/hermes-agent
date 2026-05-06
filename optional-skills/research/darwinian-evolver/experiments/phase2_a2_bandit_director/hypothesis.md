# Phase 2 — A2 Self-modifying bandit director

## Claim

Enabling the bandit director on a long-running experiment
(generations ≥ 40) produces better end-of-run fitness than the fixed
operator library because the director replaces under-performing
arms with task-specific operators.

## Expected effect size

* End-of-run best fitness: **+0.04** mean improvement at gen 40
  over the fixed bandit, n = 10 seeds.
* Operator diversity: the final arm set includes ≥ 2 LLM-generated
  operators per task, each with Exp3 weight ≥ 0.1.

Confidence: medium-low — there's no prior art for mid-run operator
library growth, so the effect may be a wash on tasks whose operator
needs are well-served by the handcrafted set.

## Ablation protocol

Conditions × tasks × seeds:

1. **director-off** — fixed arm set.
2. **director-periodic** — `--bandit-director periodic` with
   `trigger_every_r = 4` (audit every 4 generations).

Tasks: same three as Phase 1. Seeds 42 … 51. Generations = 40.

Metrics:
* Best fitness trajectory + end-of-run best.
* Arm history: timestamps of add / retire / merge; Exp3 weight
  trajectories.
* Cost-per-win-fitness-delta per operator (human vs generated).

## Risks

* Generated operators that produce garbage drift fitness down
  before the bandit retires them. Mitigated by low initial weight
  + consecutive-floor retire rule.
