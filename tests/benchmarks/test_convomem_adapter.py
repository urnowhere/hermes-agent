"""Smoke tests for the ConvoMem benchmark adapter."""
from __future__ import annotations

from pathlib import Path

# Only ConvoMemQuestion is imported at module level — loaders and run_convomem
# are imported lazily so this file works before Tasks 4 and 5 are done.
from benchmarks.convomem.adapter import ConvoMemQuestion

FIXTURE = Path(__file__).parent / "fixtures" / "convomem_tiny.json"


def test_question_from_dict_parses_minimal_row():
    raw = {
        "question": "What color does the user use for hot leads?",
        "answer": "Green",
        "evidence_type": "user_evidence",
        "n_evidence": 1,
        "persona": "sales_rep",
        "messages": [
            {"speaker": "User", "text": "I use green for hot leads."},
        ],
    }
    q = ConvoMemQuestion.from_dict(raw)
    assert q.question == "What color does the user use for hot leads?"
    assert q.answer == "Green"
    assert q.evidence_type == "user_evidence"
    assert q.n_evidence == 1
    assert len(q.messages) == 1
    assert q.messages[0]["speaker"] == "user"  # normalized to lowercase
    assert q.question_id  # non-empty synthesized id


def test_load_convomem_local_reads_fixture():
    from benchmarks.convomem.adapter import load_convomem_local
    questions = load_convomem_local(FIXTURE)
    assert len(questions) == 3
    assert all(isinstance(q, ConvoMemQuestion) for q in questions)


def test_load_convomem_local_filters_by_n_evidence():
    from benchmarks.convomem.adapter import load_convomem_local
    # All fixture rows have n_evidence=1
    questions = load_convomem_local(FIXTURE, n_evidence=1)
    assert len(questions) == 3
    questions = load_convomem_local(FIXTURE, n_evidence=999)
    assert questions == []


def test_run_convomem_against_flat_store_smoke():
    from benchmarks.convomem.adapter import load_convomem_local, run_convomem
    from benchmarks.baseline.flat_store import FlatMemoryStore
    from benchmarks.judge import HeuristicJudge

    questions = load_convomem_local(FIXTURE)
    judge = HeuristicJudge(model="heuristic")
    summary = run_convomem(
        questions=questions,
        judge=judge,
        backend_cls=FlatMemoryStore,
        backend_kwargs={},
        top_k=5,
    )
    assert summary.total == 3
    assert 0.0 <= summary.score <= 1.0
    assert len(summary.results) == 3
