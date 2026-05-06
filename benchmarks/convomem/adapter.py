"""
ConvoMem adapter for Hermes memory backends.

Loads questions from the Salesforce/ConvoMem HuggingFace dataset (or a local
JSON file), ingests each question's conversation into a BenchmarkableStore,
then answers questions via recall.

Reference:
    Salesforce AI Research (2025). "ConvoMem Benchmark: Why Your First 150
    Conversations Don't Need RAG." arXiv:2511.10523
    https://huggingface.co/datasets/Salesforce/ConvoMem
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from benchmarks.metrics import compute_metric_suite

logger = logging.getLogger(__name__)

# ── Constants ──

DATASET_NAME = "Salesforce/ConvoMem"

N_EVIDENCE_LEVELS = [1, 2, 3, 4, 5, 6]
DEFAULT_N_EVIDENCE = 6  # use all evidence levels by default

EVIDENCE_TYPES = [
    "abstention_evidence",
    "assistant_facts_evidence",
    "changing_evidence",
    "implicit_connection_evidence",
    "preference_evidence",
    "user_evidence",
]


@dataclass
class ConvoMemQuestion:
    """A single ConvoMem question with its conversation context."""

    question_id: str
    question: str
    answer: str
    evidence_type: str
    n_evidence: int
    persona: str
    messages: list[dict[str, str]]  # list of {"speaker": "user"|"assistant", "text": "..."}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ConvoMemQuestion":
        # Normalize speaker values to lowercase
        raw_messages = d.get("messages") or []
        messages = [
            {"speaker": str(m.get("speaker", "user")).lower(), "text": str(m.get("text", ""))}
            for m in raw_messages
        ]
        question = str(d.get("question") or "")
        evidence_type = str(d.get("evidence_type") or "unknown")
        persona = str(d.get("persona") or "")
        # Synthesize a stable id — no native id field in the dataset
        # Use short MD5 hash of full question text to avoid collisions
        qhash = hashlib.md5(question.encode()).hexdigest()[:12]
        question_id = f"{evidence_type}::{persona}::{qhash}"
        return cls(
            question_id=question_id,
            question=question,
            answer=str(d.get("answer") or ""),
            evidence_type=evidence_type,
            n_evidence=int(d.get("n_evidence") or 0),
            persona=persona,
            messages=messages,
        )


@dataclass
class ConvoMemResult:
    """Result of evaluating one ConvoMem question."""

    question_id: str
    evidence_type: str
    n_evidence: int
    question: str
    gold_answer: str
    recalled: str
    context: str
    correct: bool
    recall_count: int
    metrics: dict[str, float] = field(default_factory=dict)


@dataclass
class ConvoMemSummary:
    """Aggregated results across all ConvoMem questions."""

    total: int
    correct: int
    score: float
    by_type: dict[str, dict[str, Any]] = field(default_factory=dict)
    by_n_evidence: dict[int, dict[str, Any]] = field(default_factory=dict)
    results: list[ConvoMemResult] = field(default_factory=list)
    mean_metrics: dict[str, float] = field(default_factory=dict)


# ── Dataset loading ──


def _fetch_json_file(
    repo_path: str,
    hf_token: str | None = None,
) -> list[dict]:
    """Fetch and parse a JSON file from the HuggingFace dataset repo."""
    import urllib.request

    url = (
        f"https://huggingface.co/datasets/{DATASET_NAME}/resolve/main/{repo_path}"
    )
    req = urllib.request.Request(url)
    if hf_token:
        req.add_header("Authorization", f"Bearer {hf_token}")
    with urllib.request.urlopen(req, timeout=120) as resp:
        data = json.loads(resp.read().decode())
    if isinstance(data, list):
        return data
    return [data]


def _parse_batched_file(
    records: list[dict],
    evidence_type: str,
    n_evidence: int,
) -> list[ConvoMemQuestion]:
    """
    Parse records from a batched JSON file into ConvoMemQuestion objects.

    Each record has:
      - evidenceItems: list of {question, answer, message_evidences, conversations, ...}
      - conversations: list of {messages, id, containsEvidence, ...}
      - contextSize: int (n_evidence level)
    """
    questions = []
    for record in records:
        ev_items = record.get("evidenceItems") or []
        conversations = record.get("conversations") or []

        # Flatten all conversation messages into one ordered list
        all_messages: list[dict[str, str]] = []
        for conv in conversations:
            for msg in conv.get("messages") or []:
                speaker = str(msg.get("speaker", "user")).lower()
                text = str(msg.get("text", "")).strip()
                if text:
                    all_messages.append({"speaker": speaker, "text": text})

        # One question per evidenceItem in this record
        for ev in ev_items:
            question_text = str(ev.get("question") or "")
            answer_text = str(ev.get("answer") or "")
            person_id = str(ev.get("personId") or "")
            if not question_text:
                continue

            qhash = hashlib.md5(question_text.encode()).hexdigest()[:12]
            question_id = f"{evidence_type}::{person_id[:8]}::{qhash}"

            questions.append(
                ConvoMemQuestion(
                    question_id=question_id,
                    question=question_text,
                    answer=answer_text,
                    evidence_type=evidence_type,
                    n_evidence=n_evidence,
                    persona=person_id,
                    messages=all_messages,
                )
            )
    return questions


def load_convomem_dataset(
    hf_cache: str | None = None,
    sample: int | None = None,
    n_evidence: int | None = DEFAULT_N_EVIDENCE,
    category_filter: str | None = None,
    max_files_per_level: int | None = 1,
) -> list[ConvoMemQuestion]:
    """
    Load ConvoMem questions from HuggingFace (Salesforce/ConvoMem).

    The dataset files use a nested JSON structure (evidenceItems + conversations)
    that differs from the simplified schema declared in the README. This loader
    fetches the raw JSON files directly via the HuggingFace file API and parses
    them without schema casting.

    Args:
        hf_cache: Unused (kept for API compatibility). Cache is not used when
            fetching via HTTP.
        sample: If set, return at most this many questions after filtering.
        n_evidence: If set, only return questions with this n_evidence value.
            Use None to return all levels. Default is DEFAULT_N_EVIDENCE (6).
        category_filter: If set, only return questions of this evidence_type.
        max_files_per_level: Maximum number of JSON shard files to fetch per
            (evidence_type, n_evidence) combination. Default is 1, which gives
            a representative sample of each category/level without downloading
            the entire dataset (each shard file contains ~500 questions and is
            ~8-50 MB). Set to None to fetch all shards (warning: very large).

    Returns:
        List of ConvoMemQuestion objects.
    """
    hf_token = os.environ.get("HF_TOKEN") or os.environ.get("HUGGINGFACE_TOKEN")

    # n_evidence levels 1-6 come from pre_mixed_testcases subdirectories
    # Directory structure:
    #   core_benchmark/pre_mixed_testcases/<evidence_type>/<N>_evidence/batched_*.json
    n_evidence_levels_to_load = [n_evidence] if n_evidence is not None else N_EVIDENCE_LEVELS
    evidence_types_to_load = [category_filter] if category_filter else EVIDENCE_TYPES

    logger.info(
        "Loading ConvoMem from HuggingFace (%s) via direct HTTP "
        "(n_evidence=%s, types=%s, max_files_per_level=%s)...",
        DATASET_NAME,
        n_evidence,
        evidence_types_to_load,
        max_files_per_level,
    )

    import urllib.request

    questions: list[ConvoMemQuestion] = []

    for ev_type in evidence_types_to_load:
        for n_ev in n_evidence_levels_to_load:
            if sample and len(questions) >= sample:
                break

            dir_path = (
                f"core_benchmark/pre_mixed_testcases/{ev_type}/{n_ev}_evidence"
            )
            logger.debug("Scanning %s", dir_path)

            # List JSON files in this directory
            url = f"https://huggingface.co/api/datasets/{DATASET_NAME}/tree/main/{dir_path}"
            req = urllib.request.Request(url)
            if hf_token:
                req.add_header("Authorization", f"Bearer {hf_token}")
            try:
                with urllib.request.urlopen(req, timeout=30) as resp:
                    entries = json.loads(resp.read().decode())
            except Exception as exc:
                logger.warning("Could not list %s: %s", dir_path, exc)
                continue

            json_files = [
                e["path"] for e in entries
                if e.get("type") == "file" and e.get("path", "").endswith(".json")
            ]

            if max_files_per_level is not None:
                json_files = json_files[:max_files_per_level]

            if not json_files:
                logger.warning(
                    "No JSON shards found at %s — dataset structure may have changed",
                    dir_path,
                )
                continue

            for file_path in json_files:
                if sample and len(questions) >= sample:
                    break
                try:
                    records = _fetch_json_file(file_path, hf_token=hf_token)
                    new_qs = _parse_batched_file(records, ev_type, n_ev)
                    for q in new_qs:
                        questions.append(q)
                        if sample and len(questions) >= sample:
                            break
                    logger.debug(
                        "  %s: parsed %d questions (total=%d)",
                        file_path,
                        len(new_qs),
                        len(questions),
                    )
                except Exception as exc:
                    logger.warning("Error reading %s: %s", file_path, exc)
                    continue

        if sample and len(questions) >= sample:
            break

    logger.info("Loaded %d questions from ConvoMem", len(questions))
    return questions


def load_convomem_local(
    path: str | Path,
    sample: int | None = None,
    n_evidence: int | None = None,
    category_filter: str | None = None,
) -> list[ConvoMemQuestion]:
    """
    Load ConvoMem questions from a local JSON file.

    Args:
        path: Path to a JSON file (list of question dicts).
        sample: If set, return at most this many questions after filtering.
        n_evidence: If set, only return questions with this n_evidence value.
        category_filter: If set, only return questions of this evidence_type.

    Returns:
        List of ConvoMemQuestion objects.
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"ConvoMem data file not found: {path}")

    with open(path, encoding="utf-8") as f:
        data = json.load(f)

    if isinstance(data, dict):
        data = list(data.values())

    questions: list[ConvoMemQuestion] = []
    for item in data:
        q = ConvoMemQuestion.from_dict(item)
        if n_evidence is not None and q.n_evidence != n_evidence:
            continue
        if category_filter and q.evidence_type != category_filter:
            continue
        questions.append(q)
        if sample and len(questions) >= sample:
            break

    logger.info("Loaded %d questions from %s", len(questions), path)
    return questions


# ── Ingestion ──


def ingest_conversation_into_store(
    store: Any,
    question: ConvoMemQuestion,
) -> int:
    """
    Ingest a single question's conversation into a BenchmarkableStore.

    Each message becomes a factual memory. User-role messages get higher
    importance (0.6) than assistant-role messages (0.4).

    Args:
        store: A BenchmarkableStore (reset() must already have been called).
        question: The question whose messages to ingest.

    Returns:
        Number of memories stored.
    """
    count = 0
    for msg in question.messages:
        # messages are normalised to lowercase speaker in from_dict
        speaker = msg.get("speaker", "user")
        content = msg.get("text", "").strip()
        if not content:
            continue
        importance = 0.6 if speaker == "user" else 0.4
        store.store(content, category="factual", importance=importance)
        count += 1
    return count


# ── Evaluation ──


def evaluate_question(
    store: Any,
    question: ConvoMemQuestion,
    judge: Any,
    top_k: int = 10,
) -> ConvoMemResult:
    """Evaluate a single ConvoMem question against an ingested store."""
    results = store.recall(question.question, top_k=top_k)
    recalled = results[0] if results else ""
    context = " | ".join(results[:5]) if results else ""

    jr = judge.judge_answer(question.question, question.answer, context)

    metrics = compute_metric_suite(
        retrieved=results[:top_k],
        relevant=[question.answer],
        gold_answer=question.answer,
        predicted_answer=context,
    )

    return ConvoMemResult(
        question_id=question.question_id,
        evidence_type=question.evidence_type,
        n_evidence=question.n_evidence,
        question=question.question,
        gold_answer=question.answer,
        recalled=recalled,
        context=context,
        correct=jr.correct,
        recall_count=len(results),
        metrics=metrics,
    )


def run_convomem(
    questions: list[ConvoMemQuestion],
    judge: Any,
    backend_cls: Any | None = None,
    backend_kwargs: dict | None = None,
    top_k: int = 10,
    verbose: bool = False,
) -> ConvoMemSummary:
    """
    Run ConvoMem evaluation across a list of questions.

    A fresh backend instance is created and reset per question to prevent
    cross-question contamination.

    Args:
        questions: Pre-loaded ConvoMemQuestion list.
        judge: A HeuristicJudge or MemoryJudge instance.
        backend_cls: Class to instantiate per question. Defaults to FlatMemoryStore.
        backend_kwargs: Keyword args passed to backend_cls(). Defaults to {}.
        top_k: Memories to recall per question.
        verbose: If True, print per-question status lines.

    Returns:
        ConvoMemSummary with aggregated scores by evidence_type and n_evidence.
    """
    if backend_cls is None:
        from benchmarks.baseline.flat_store import FlatMemoryStore
        backend_cls = FlatMemoryStore

    backend_kwargs = backend_kwargs or {}

    results: list[ConvoMemResult] = []
    correct = 0

    for i, question in enumerate(questions):
        store = backend_cls(**backend_kwargs)
        store.reset()

        n_stored = ingest_conversation_into_store(store, question)
        result = evaluate_question(store, question, judge, top_k=top_k)
        results.append(result)
        if result.correct:
            correct += 1

        if verbose:
            status = "✓" if result.correct else "✗"
            print(
                f"  [{i+1}/{len(questions)}] {status} "
                f"{question.evidence_type} n_ev={question.n_evidence} "
                f"stored={n_stored} recalled={result.recall_count}"
            )

    # Aggregate by evidence type
    by_type: dict[str, dict[str, Any]] = {}
    for etype in EVIDENCE_TYPES:
        subset = [r for r in results if r.evidence_type == etype]
        if subset:
            tc = sum(1 for r in subset if r.correct)
            by_type[etype] = {"total": len(subset), "correct": tc, "score": tc / len(subset)}

    # Aggregate by n_evidence level
    by_n_evidence: dict[int, dict[str, Any]] = {}
    for level in N_EVIDENCE_LEVELS:
        subset = [r for r in results if r.n_evidence == level]
        if subset:
            tc = sum(1 for r in subset if r.correct)
            by_n_evidence[level] = {"total": len(subset), "correct": tc, "score": tc / len(subset)}

    # Mean retrieval metrics
    all_metrics = [r.metrics for r in results if r.metrics]
    mean_metrics: dict[str, float] = {}
    if all_metrics:
        for key in all_metrics[0]:
            values = [m[key] for m in all_metrics if key in m]
            mean_metrics[key] = sum(values) / len(values) if values else 0.0

    total = len(questions)
    return ConvoMemSummary(
        total=total,
        correct=correct,
        score=correct / total if total > 0 else 0.0,
        by_type=by_type,
        by_n_evidence=by_n_evidence,
        results=results,
        mean_metrics=mean_metrics,
    )
