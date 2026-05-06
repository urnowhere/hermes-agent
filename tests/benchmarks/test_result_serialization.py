import json

from benchmarks.capabilities import BackendCapabilities
from benchmarks.interface import AggregateResult, BenchmarkConfig, CategoryResult, RunResult
from benchmarks.runner import (
    BACKENDS,
    CATEGORY_RUNNERS,
    build_result_data,
    build_skipped_category_reasons,
    load_run_checkpoint,
    print_results,
    run_single,
    save_run_checkpoint,
    shared_category_view,
)


def _sample_run(seed=42):
    return RunResult(
        seed=seed,
        results_by_category={
            "semantic_recall": CategoryResult(
                "semantic_recall",
                total=10,
                correct=8,
                score=0.8,
                sub_scores={"easy": 1.0},
                retrieval_metrics={"recall_at_5": 0.8},
                recall_tokens=50,
                recall_chars=200,
            )
        },
        overall_score=0.8,
        wall_time_seconds=1.25,
        token_usage={"recall_tokens": 50},
        retrieval_metrics={"recall_at_5": 0.8},
        cost_metrics={"tokens_per_query": 5.0},
    )


def test_build_skipped_category_reasons_explains_missing_capabilities():
    skipped = build_skipped_category_reasons(
        requested_categories=["semantic_recall", "temporal_decay"],
        executed_categories=["semantic_recall"],
        capabilities=BackendCapabilities(),
    )

    assert skipped == {"temporal_decay": "missing capabilities: time_simulation"}


def test_build_result_data_uses_rich_schema_and_runtime_metadata():
    config = BenchmarkConfig(
        backend_name="baseline-flat",
        profile="balanced",
        embedding_model="tfidf",
        parameters={"suites": ["a"]},
        num_runs=1,
        seeds=[42],
    )
    agg = AggregateResult(
        num_runs=1,
        mean_score=0.8,
        std_score=0.0,
        ci_95_lower=0.8,
        ci_95_upper=0.8,
        per_category_mean={"semantic_recall": 0.8},
        per_category_std={"semantic_recall": 0.0},
    )

    data = build_result_data(config, agg, [_sample_run()])
    encoded = json.dumps(data)

    assert data["schema_version"] == "2.0"
    assert data["backend"] == "baseline-flat"
    assert "requested_categories" in data
    assert data["executed_categories"] == ["semantic_recall"]
    assert isinstance(data["skipped_categories"], dict)
    assert "score_views" in data
    assert data["score_views"]["core"]["score"] == 0.8
    assert data["score_views"]["discriminative"]["score"] == 0.8
    assert data["official_comparison_score"] == 0.8
    assert data["saturation"]["saturated_categories"] == []
    assert data["saturation"]["saturated_fraction"] == 0.0
    assert "runtime" in data
    assert "python" in data["runtime"]
    assert data["runs"][0]["categories"]["semantic_recall"]["sub_scores"] == {"easy": 1.0}
    assert "secret" not in encoded.lower()


def test_print_results_includes_fair_comparison_views(capsys):
    config = BenchmarkConfig(
        backend_name="baseline-flat",
        parameters={"suites": ["a"]},
        num_runs=1,
        seeds=[42],
    )
    agg = AggregateResult(
        num_runs=1,
        mean_score=0.8,
        std_score=0.0,
        ci_95_lower=0.8,
        ci_95_upper=0.8,
        per_category_mean={"semantic_recall": 0.8},
        per_category_std={"semantic_recall": 0.0},
    )

    print_results(agg, config, [_sample_run()])

    out = capsys.readouterr().out
    assert "Fair comparison views:" in out
    assert "Executed score:       0.800 over 1 categories" in out
    assert "Core score:           0.800 over 1 categories" in out
    assert "Discriminative score: 0.800 over 1 categories" in out
    assert "Saturated categories: 0/1" in out


def test_shared_category_view_scores_only_intersection():
    payloads = [
        {
            "backend": "a",
            "executed_categories": ["semantic_recall", "temporal_decay"],
            "runs": [
                {
                    "categories": {
                        "semantic_recall": {"correct": 8, "total": 10},
                        "temporal_decay": {"correct": 1, "total": 10},
                    }
                }
            ],
        },
        {
            "backend": "b",
            "executed_categories": ["semantic_recall", "scopes"],
            "runs": [
                {
                    "categories": {
                        "semantic_recall": {"correct": 6, "total": 10},
                        "scopes": {"correct": 10, "total": 10},
                    }
                }
            ],
        },
    ]

    view = shared_category_view(payloads)

    assert view["categories"] == ["semantic_recall"]
    assert view["backends"]["a"] == {"score": 0.8, "correct": 8, "total": 10}
    assert view["backends"]["b"] == {"score": 0.6, "correct": 6, "total": 10}


def test_run_checkpoint_round_trips_category_metrics(tmp_path):
    config = BenchmarkConfig(
        backend_name="baseline-flat",
        profile="balanced",
        embedding_model="tfidf",
        parameters={"suites": ["a"]},
        num_runs=1,
        seeds=[42],
    )
    run = _sample_run(seed=42)

    path = save_run_checkpoint(tmp_path, config, run, completed=True)
    loaded, metadata = load_run_checkpoint(tmp_path, config, seed=42)

    assert path.exists()
    assert metadata["completed"] is True
    assert loaded is not None
    assert loaded.seed == 42
    assert loaded.overall_score == 0.8
    assert loaded.token_usage == {"recall_tokens": 50}
    assert loaded.retrieval_metrics == {"recall_at_5": 0.8}
    assert loaded.cost_metrics == {"tokens_per_query": 5.0}
    category = loaded.results_by_category["semantic_recall"]
    assert category.correct == 8
    assert category.total == 10
    assert category.score == 0.8
    assert category.sub_scores == {"easy": 1.0}
    assert category.retrieval_metrics == {"recall_at_5": 0.8}
    assert category.recall_tokens == 50
    assert category.recall_chars == 200


def test_run_single_resume_skips_checkpointed_categories(tmp_path, monkeypatch):
    class TinyBackend:
        def __init__(self, **kwargs):
            pass
        def store(self, *args, **kwargs):
            pass
        def recall(self, *args, **kwargs):
            return []
        def simulate_time(self, days):
            pass
        def simulate_access(self, content_substring):
            pass
        def consolidate(self):
            pass
        def get_stats(self):
            return {}
        def reset(self):
            pass

    completed = RunResult(
        seed=42,
        results_by_category={
            "semantic_recall": CategoryResult(
                "semantic_recall",
                total=1,
                correct=1,
                score=1.0,
                recall_tokens=3,
                recall_chars=12,
            )
        },
        overall_score=1.0,
        token_usage={"recall_tokens": 3, "recall_chars": 12, "recall_queries": 1, "avg_recall_tokens_per_query": 3},
        wall_time_seconds=0.1,
        retrieval_metrics={},
        cost_metrics={"score": 1.0},
    )
    config = BenchmarkConfig(
        backend_name="tiny-resume",
        parameters={
            "suites": ["z"],
            "checkpoint_dir": str(tmp_path),
            "checkpoint_enabled": True,
            "resume": True,
        },
        num_runs=1,
        seeds=[42],
    )
    save_run_checkpoint(tmp_path, config, completed, completed=False)

    calls = []
    def already_done_runner(backend, scenarios, judge):
        calls.append("semantic_recall")
        return CategoryResult("semantic_recall", total=1, correct=0, score=0.0)
    def missing_runner(backend, scenarios, judge):
        calls.append("contradictions")
        return CategoryResult(
            "contradictions",
            total=2,
            correct=1,
            score=0.5,
            recall_tokens=5,
            recall_chars=20,
            retrieval_metrics={"recall_at_5": 0.5},
        )

    monkeypatch.setitem(BACKENDS, "tiny-resume", TinyBackend)
    monkeypatch.setattr("benchmarks.runner.load_fixtures", lambda suite: {"semantic_recall": [{}], "contradictions": [{}, {}]})
    monkeypatch.setitem(CATEGORY_RUNNERS, "semantic_recall", already_done_runner)
    monkeypatch.setitem(CATEGORY_RUNNERS, "contradictions", missing_runner)

    run = run_single(config, seed=42)
    loaded, metadata = load_run_checkpoint(tmp_path, config, seed=42)

    assert calls == ["contradictions"]
    assert set(run.results_by_category) == {"semantic_recall", "contradictions"}
    assert run.overall_score == 2 / 3
    assert metadata["completed"] is True
    assert loaded is not None
    assert set(loaded.results_by_category) == {"semantic_recall", "contradictions"}
