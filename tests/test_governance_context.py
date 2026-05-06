import json
from pathlib import Path


def _write_fixture_source(home: Path) -> Path:
    source = home / "sovereign-mind" / "governance" / "rules.source.json"
    source.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "$schema": "fixture",
        "generated_at": "2026-05-04T00:00:00Z",
        "kind": "fixture.governance",
        "mode": "primary",
        "source_hash": "fixturehash",
        "version": "test",
        "rules": [
            {
                "id": "ABS-1",
                "title": "WRITE-APPROVAL",
                "tier": "absolute",
                "status": "active",
                "enforcement_mode": "layer-b",
                "memory_line": "ABS-1: writes require approval and verification.",
                "canonical_body": "FULL ABS-1 BODY: exact write approval rule body.",
                "canonical_body_sha256": "a" * 64,
                "source_file": "AGENTS.md",
                "source_line": 10,
                "status_source": "fixture",
                "status_evidence": "fixture",
                "adr_links": [],
                "deflation_signals": [],
            },
            {
                "id": "ABS-2",
                "title": "NO-FABRICATION",
                "tier": "absolute",
                "status": "active",
                "enforcement_mode": "layer-e",
                "memory_line": "ABS-2: never fabricate claims.",
                "canonical_body": "FULL ABS-2 BODY: exact no fabrication rule body.",
                "canonical_body_sha256": "b" * 64,
                "source_file": "AGENTS.md",
                "source_line": 20,
                "status_source": "fixture",
                "status_evidence": "fixture",
                "adr_links": [],
                "deflation_signals": [],
            },
            {
                "id": "ABS-34",
                "title": "PRIVACY",
                "tier": "absolute",
                "status": "active",
                "enforcement_mode": "layer-e",
                "memory_line": "ABS-34: protect private data.",
                "canonical_body": "FULL ABS-34 BODY: exact privacy rule body.",
                "canonical_body_sha256": "c" * 64,
                "source_file": "AGENTS.md",
                "source_line": 30,
                "status_source": "fixture",
                "status_evidence": "fixture",
                "adr_links": [],
                "deflation_signals": [],
            },
        ],
    }
    source.write_text(json.dumps(payload), encoding="utf-8")
    return source


def test_compiled_core_uses_memory_lines_not_full_canonical_bodies(tmp_path):
    _write_fixture_source(tmp_path)
    from agent.governance_context import build_compiled_governance_core

    core = build_compiled_governance_core(hermes_home=tmp_path)

    assert "COMPILED GOVERNANCE CORE" in core
    assert "fixturehash" in core
    assert "ABS-1: writes require approval" in core
    assert "ABS-2: never fabricate" in core
    assert "FULL ABS-1 BODY" not in core
    assert "FULL ABS-2 BODY" not in core


def test_exact_retrieval_fetches_write_rule_for_write_actions(tmp_path):
    _write_fixture_source(tmp_path)
    from agent.governance_context import build_governance_retrieval_context

    ctx = build_governance_retrieval_context(
        "Patch run_agent.py and write tests", hermes_home=tmp_path
    )

    assert "EXACT GOVERNANCE SOURCE FETCH" in ctx
    assert "trigger: write" in ctx
    assert "ABS-1" in ctx
    assert "FULL ABS-1 BODY: exact write approval rule body." in ctx


def test_exact_retrieval_always_fetches_core_truth_rule_even_without_specific_trigger(tmp_path):
    _write_fixture_source(tmp_path)
    from agent.governance_context import build_governance_retrieval_context

    ctx = build_governance_retrieval_context("hello", hermes_home=tmp_path)

    assert "EXACT GOVERNANCE SOURCE FETCH" in ctx
    assert "trigger: always" in ctx
    assert "ABS-2" in ctx
    assert "FULL ABS-2 BODY: exact no fabrication rule body." in ctx


def test_governance_source_required_only_when_configured(tmp_path):
    import os

    from agent.governance_context import is_governance_source_required

    old_value = os.environ.pop("HERMES_GOVERNANCE_SOURCE_REQUIRED", None)
    try:
        assert is_governance_source_required(hermes_home=tmp_path) is False

        (tmp_path / "sovereign-mind" / "governance").mkdir(parents=True)
        assert is_governance_source_required(hermes_home=tmp_path) is True

        empty_home = tmp_path / "empty"
        empty_home.mkdir()
        os.environ["HERMES_GOVERNANCE_SOURCE_REQUIRED"] = "true"
        assert is_governance_source_required(hermes_home=empty_home) is True
    finally:
        if old_value is None:
            os.environ.pop("HERMES_GOVERNANCE_SOURCE_REQUIRED", None)
        else:
            os.environ["HERMES_GOVERNANCE_SOURCE_REQUIRED"] = old_value


def test_compact_memory_block_preserves_non_governance_notes(tmp_path):
    _write_fixture_source(tmp_path)
    from agent.governance_context import compact_governance_memory_block

    memory_block = (
        "══════════════════════════════════════════════\n"
        "MEMORY (your personal notes) [100% — 999/100 chars]\n"
        "══════════════════════════════════════════════\n"
        "═══ COMPRESSED INDEX — OPERATIONAL FORM ═══\n"
        "huge canonical compressed body\n"
        "# END MEMORY.md COMPRESSED FORM v3 — DO NOT EDIT WITHOUT SCHG CYCLE\n"
        "\n§\n"
        "Hermes model-routing preference: no Gemini."
    )

    compacted = compact_governance_memory_block(memory_block, hermes_home=tmp_path)

    assert "COMPILED GOVERNANCE CORE" in compacted
    assert "huge canonical compressed body" not in compacted
    assert "Hermes model-routing preference: no Gemini." in compacted
