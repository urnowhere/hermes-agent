# ConvoMem Benchmark Adapter

Integrates the [ConvoMem](https://huggingface.co/datasets/Salesforce/ConvoMem)
benchmark from Salesforce AI Research (arXiv:2511.10523) with the Hermes
memory benchmark suite.

## Dataset

- **Source**: `Salesforce/ConvoMem` on HuggingFace
- **Reference**: Salesforce AI Research (2025). "ConvoMem Benchmark: Why Your
  First 150 Conversations Don't Need RAG." arXiv:2511.10523
- **GitHub**: https://github.com/SalesforceAIResearch/ConvoMem
- **Evidence types** (`evidence_type` field):
  - `abstention_evidence` — questions the assistant should refuse to answer
  - `assistant_facts_evidence` — things the assistant said earlier
  - `changing_evidence` — facts that have changed over time
  - `implicit_connection_evidence` — multi-hop reasoning across messages
  - `preference_evidence` — user preferences
  - `user_evidence` — facts the user told the assistant
- **n_evidence levels**: 1–6 (number of evidence conversations included)

## How it works

1. For each question, instantiate a fresh backend store and reset it.
2. Ingest the conversation `messages` as factual memories (user-speaker
   messages get importance 0.6, assistant-speaker messages get 0.4).
3. Recall against the question text.
4. HeuristicJudge scores correctness; `compute_metric_suite` computes
   Recall@k / MRR / token F1.
5. Aggregate by `evidence_type` and by `n_evidence`.

## Usage

```bash
cd /path/to/hermes-agent

# Quick smoke test (5 questions, baseline-flat backend)
python -m benchmarks.convomem.runner --sample 5

# Run against a specific backend (all n_evidence levels)
python -m benchmarks.convomem.runner --backend hindsight

# Single evidence level
python -m benchmarks.convomem.runner --backend honcho --n-evidence 3

# Filter to one evidence type
python -m benchmarks.convomem.runner --backend mem0 --category abstention_evidence

# All academic benchmarks including ConvoMem
python -m benchmarks --academic
```

## Limitations

- **Single-message ingestion**: each `text` field is stored as an isolated
  factual memory. Conversational structure between turns is flattened.
- **Abstention scoring**: questions in `abstention_evidence` are scored on
  recall correctness. A backend that returns "I don't know" will be marked
  incorrect even if that is the right behaviour — treat this category's
  scores with caution.
- **ConvoMem thesis alignment**: the benchmark's central finding is that
  flat baselines are competitive with RAG for up to ~150 conversations.
  `baseline-flat` is expected to score well, especially at lower n_evidence
  levels. Score differences are most meaningful at n_evidence 4–6.
