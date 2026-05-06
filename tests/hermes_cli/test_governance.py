from pathlib import Path
from types import SimpleNamespace

from hermes_cli import governance


def test_component_catalog_has_core_system_areas():
    names = {entry["name"] for entry in governance.component_catalog()}

    assert "agent-runtime" in names
    assert "messaging-gateway" in names
    assert "configuration" in names
    assert "tool-plane" in names
    assert "scheduler" in names
    assert "knowledge-and-evals" in names


def test_classify_paths_maps_changes_to_components():
    classified = governance.classify_paths(
        [
            "gateway/run.py",
            "hermes_cli/config.py",
            "tools/file_tools.py",
            "tests/gateway/test_run.py",
        ]
    )

    assert "messaging-gateway" in classified
    assert "configuration" in classified
    assert "tool-plane" in classified
    assert "tests" in classified
    assert classified["messaging-gateway"] == ["gateway/run.py"]


def test_cross_boundary_production_change_requires_decision_record():
    report = governance.build_report(
        paths=[
            "gateway/run.py",
            "hermes_cli/config.py",
            "run_agent.py",
            "tests/gateway/test_run.py",
        ]
    )

    finding_ids = {finding["id"] for finding in report["findings"]}
    assert "decision-record-needed" in finding_ids
    assert report["requires_decision_record"] is True
    assert report["summary"]["severity"] == "warning"


def test_decision_record_path_satisfies_decision_record_requirement():
    report = governance.build_report(
        paths=[
            "gateway/run.py",
            "hermes_cli/config.py",
            "docs/decisions/2026-04-27-global-systems-office.md",
            "tests/gateway/test_run.py",
        ]
    )

    finding_ids = {finding["id"] for finding in report["findings"]}
    assert "decision-record-needed" not in finding_ids
    assert report["requires_decision_record"] is False


def test_production_change_without_tests_warns_about_verification():
    report = governance.build_report(paths=["gateway/run.py"])

    finding_ids = {finding["id"] for finding in report["findings"]}
    assert "verification-missing" in finding_ids


def test_format_report_is_human_readable():
    report = governance.build_report(paths=["gateway/run.py"])
    text = governance.format_report(report)

    assert "Global Systems Office" in text
    assert "verification-missing" in text
    assert "messaging-gateway" in text


def test_governance_command_catalog_json(capsys):
    code = governance.governance_command(
        SimpleNamespace(governance_command="catalog", json=True)
    )

    output = capsys.readouterr().out
    assert code == 0
    assert "agent-runtime" in output


def test_governance_command_check_fail_on_warning():
    code = governance.governance_command(
        SimpleNamespace(
            governance_command="check",
            root=None,
            json=False,
            fail_on="warning",
            paths=["gateway/run.py"],
        )
    )

    assert code == 1


def test_adr_template_contains_required_sections(capsys):
    code = governance.governance_command(
        SimpleNamespace(governance_command="adr-template")
    )

    output = capsys.readouterr().out
    assert code == 0
    assert "# ADR:" in output
    assert "## Context" in output
    assert "## Decision" in output
    assert "## Consequences" in output
