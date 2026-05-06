# Ablation experiment scaffolds

Each phase's research claim (A1-A5) has a dedicated directory here with:

* `hypothesis.md` — pre-registered claim, expected effect size, ablation
  protocol (written BEFORE the code landed).
* `run.sh` — one-shot cluster job that produces the raw results.
* `analysis.ipynb` — plots + statistics (created on first run).
* `results.jsonl` — raw per-seed outcomes (created on first run).

When a phase's experiments complete, a companion arXiv section goes
under `papers/2026_<topic>/`; no paper blocks a code release per the
v1.0 plan.

Cluster assumptions:
* A100 / H100, 4-16 GPUs per sweep
* Python 3.11 + skill's optional-deps installed
* OpenRouter / Anthropic API key in `OPENROUTER_API_KEY`

Experiment budgets listed per directory; total across phases 1-4 is
~200 GPU-hours + ~$1 800 API spend at the settings committed here.
