from pathlib import Path


WORKFLOW_PATH = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "tests.yml"


def test_tests_workflow_includes_windows_matrix_job():
    text = WORKFLOW_PATH.read_text(encoding="utf-8")
    assert "windows-latest" in text
    assert "ubuntu-latest" in text
    assert "strategy:" in text
    assert "matrix:" in text


def test_tests_workflow_has_cross_platform_targeted_pytest_commands():
    text = WORKFLOW_PATH.read_text(encoding="utf-8")
    assert "if: runner.os == 'Windows'" in text
    assert "if: runner.os != 'Windows'" in text
    assert "uv venv .venv --python 3.11" in text
    assert "tests/scripts/test_install_ps1.py" in text
    assert "tests/scripts/test_ci_workflows.py" in text
    assert "tests/tools/test_local_env_windows.py" in text
    assert "python -m pytest tests/ -q --ignore=tests/integration --ignore=tests/e2e --tb=short -n auto" not in text


def test_tests_workflow_runs_for_all_pull_requests():
    text = WORKFLOW_PATH.read_text(encoding="utf-8")
    assert "pull_request:\n    branches: [main]" not in text
