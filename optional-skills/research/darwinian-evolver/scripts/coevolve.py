"""Adversarial co-evolution of solvers and test inputs (A3 — v0.4).

Claim: a solver population and an adversary population of test inputs
— each trying to defeat the other — converge to more robust winners
than single-population evolution can reach.

Design
------
We model co-evolution as two coupled evolution loops sharing a clock
and a single SQLite file:

* **Solver archive** optimises the user's ``fitness(candidate, ctx)``
  function. This is the same contract as non-adversarial runs — the
  context dict now carries an ``input`` string drawn from the
  adversary archive.
* **Adversary archive** holds synthetic *test inputs* and maximises
  ``adversary_fitness = 1 - mean(solver_fitness(solver, input))``
  — inputs that defeat lots of solvers are valued.

Each generation we step ONE side at a time; solver and adversary
alternate. That keeps the Red-Queen dynamic bounded: both sides have
time to adapt to the other's improvements.

Safety
------
* Adversary mutation uses the same LLM operators as solver mutation;
  no new code paths, no new Python evaluation.
* Adversary archive has a hard ``max_generations`` cap — the loop
  terminates even if fitness keeps improving, preventing drift.
* Every adversarial input is written to ``red_team_inputs`` for
  audit.

Author: v0.4 roadmap feature A3.
"""

from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import Awaitable, Callable, Optional, Sequence

import algorithms
import operators as _operators
import storage


# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------


SolverFitness = Callable[[str, dict], float]
"""User-supplied scalar fitness: ``fitness(candidate, context) -> float``."""


@dataclass
class CoevolveRun:
    """Complete state of one co-evolutionary run.

    Kept as a plain dataclass so tests can construct instances from
    handcrafted populations without touching the LLM.
    """
    solvers:    list[algorithms.Individual]
    adversaries: list[algorithms.Individual]
    history:    list[dict] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Fitness helpers
# ---------------------------------------------------------------------------


def evaluate_solver_vs_adversaries(
    solver: algorithms.Individual,
    adversaries: Sequence[algorithms.Individual],
    fitness_fn: SolverFitness,
    *,
    context_seed: int = 0,
) -> float:
    """Return the solver's mean fitness across every adversarial input.

    Pure, synchronous, stdlib-only — this is the kernel the async
    runner dispatches over.
    """
    if not adversaries:
        return 0.0
    scores = []
    for adv in adversaries:
        ctx = {
            "seed":     context_seed,
            "input":    adv.genome,
            "held_out": False,
            "fidelity": 1.0,
        }
        try:
            scores.append(float(fitness_fn(solver.genome, ctx)))
        except Exception:
            # A solver that raises on an adversarial input gets the
            # worst possible score — it "lost" that match.
            scores.append(0.0)
    return sum(scores) / len(scores)


def adversary_fitness(
    adversary: algorithms.Individual,
    solvers: Sequence[algorithms.Individual],
    fitness_fn: SolverFitness,
    *,
    context_seed: int = 0,
) -> float:
    """Fraction of solvers that the adversary *successfully defeats*.

    "Defeats" means: ``solver_fitness(solver, adversary) < 0.5``.
    Threshold is a hard-coded 0.5 here — the contract is "fitness is
    in [0,1], half is the failure boundary". The caller can rewrap
    their metric to honour that before invoking this module.
    """
    if not solvers:
        return 0.0
    losses = 0
    for s in solvers:
        ctx = {"seed": context_seed, "input": adversary.genome,
               "held_out": False, "fidelity": 1.0}
        try:
            score = float(fitness_fn(s.genome, ctx))
        except Exception:
            score = 0.0
        if score < 0.5:
            losses += 1
    return losses / len(solvers)


# ---------------------------------------------------------------------------
# Mutation wiring
# ---------------------------------------------------------------------------


async def mutate_population(
    llm,                                          # llm.LLMClient
    individuals: list[algorithms.Individual],
    op_name: str = "paraphrase",
    *,
    rng: random.Random,
) -> list[algorithms.Individual]:
    """Apply a handcrafted operator to every individual; return offspring."""
    fn = _operators.MUTATION_OPERATORS.get(op_name, _operators.paraphrase)
    tasks = [
        fn(llm, ind.genome, seed=rng.randint(0, 2**31 - 1))
        for ind in individuals
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    offspring: list[algorithms.Individual] = []
    for parent, res in zip(individuals, results):
        if isinstance(res, Exception):
            continue
        child = algorithms.Individual(
            cid=storage.hash_genome(res.child),
            genome=res.child,
            parents=[parent.cid],
            operator=op_name,
        )
        offspring.append(child)
    return offspring


# ---------------------------------------------------------------------------
# One co-evolutionary step
# ---------------------------------------------------------------------------


async def step(
    run: CoevolveRun,
    *,
    llm,
    fitness_fn: SolverFitness,
    conn,
    generation: int,
    side: str,                      # 'solver' | 'adversary'
    rng: random.Random,
    solver_op: str = "paraphrase",
    adversary_op: str = "novelty",
) -> None:
    """Evolve *side* one generation against the other's current state."""
    if side == "solver":
        offspring = await mutate_population(llm, run.solvers, solver_op, rng=rng)
        for child in offspring:
            child.fitness = evaluate_solver_vs_adversaries(
                child, run.adversaries, fitness_fn,
                context_seed=generation,
            )
        survivors = algorithms.mu_plus_lambda(
            run.solvers, offspring, mu=len(run.solvers),
        )
        run.solvers = survivors
    elif side == "adversary":
        offspring = await mutate_population(llm, run.adversaries, adversary_op, rng=rng)
        for child in offspring:
            child.fitness = adversary_fitness(
                child, run.solvers, fitness_fn,
                context_seed=generation,
            )
            storage.record_red_team_input(
                conn, child.cid, child.genome, child.fitness, generation,
            )
        survivors = algorithms.mu_plus_lambda(
            run.adversaries, offspring, mu=len(run.adversaries),
        )
        run.adversaries = survivors
    else:
        raise ValueError(f"unknown side {side!r}")
    run.history.append({
        "generation":    generation,
        "side":          side,
        "solvers_best":  _best(run.solvers),
        "advers_best":   _best(run.adversaries),
    })


def _best(pop: Sequence[algorithms.Individual]) -> Optional[float]:
    scores = [
        float(ind.fitness) for ind in pop
        if isinstance(ind.fitness, (int, float))
    ]
    return max(scores) if scores else None


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


async def coevolve(
    run: CoevolveRun,
    *,
    llm,
    fitness_fn: SolverFitness,
    conn,
    generations: int,
    seed: int = 0,
    max_adversary_generations: int = 0,
) -> CoevolveRun:
    """Alternate solver / adversary evolution for *generations* rounds.

    The adversary side is halted once it reaches
    ``max_adversary_generations`` (0 = unbounded) so a runaway Red-
    Queen dynamic cannot eat all the budget.
    """
    rng = random.Random(seed)
    advers_steps = 0
    for gen in range(1, generations + 1):
        side = "solver" if gen % 2 == 1 else "adversary"
        if side == "adversary" and max_adversary_generations and advers_steps >= max_adversary_generations:
            side = "solver"
        await step(run, llm=llm, fitness_fn=fitness_fn, conn=conn,
                   generation=gen, side=side, rng=rng)
        if side == "adversary":
            advers_steps += 1
    return run
