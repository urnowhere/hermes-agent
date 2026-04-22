"""Integration tests for the Novita terminal backend.

Requires NOVITA_API_KEY to be set. Run with:
    TERMINAL_ENV=novita pytest tests/integration/test_novita_terminal.py -v
"""

import json
import os
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

# Skip entire module if no API key
if not os.getenv("NOVITA_API_KEY"):
    pytest.skip("NOVITA_API_KEY not set", allow_module_level=True)

# Import terminal_tool via importlib to avoid tools/__init__.py side effects
import importlib.util

parent_dir = Path(__file__).parent.parent.parent
sys.path.insert(0, str(parent_dir))

spec = importlib.util.spec_from_file_location(
    "terminal_tool", parent_dir / "tools" / "terminal_tool.py"
)
terminal_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(terminal_module)

terminal_tool = terminal_module.terminal_tool
cleanup_vm = terminal_module.cleanup_vm


@pytest.fixture(autouse=True)
def _force_novita(monkeypatch):
    monkeypatch.setenv("TERMINAL_ENV", "novita")
    monkeypatch.setenv("TERMINAL_CONTAINER_PERSISTENT", "false")


@pytest.fixture()
def task_id(request):
    """Provide a unique task_id and clean up the sandbox after the test."""
    tid = f"novita_test_{request.node.name}"
    yield tid
    cleanup_vm(tid)


def _run(command, task_id, **kwargs):
    result = terminal_tool(command, task_id=task_id, **kwargs)
    return json.loads(result)


class TestNovitaBasic:
    def test_echo(self, task_id):
        r = _run("echo 'Hello from Novita!'", task_id)
        assert r["exit_code"] == 0
        assert "Hello from Novita!" in r["output"]

    def test_python_version(self, task_id):
        r = _run("python3 --version", task_id)
        assert r["exit_code"] == 0
        assert "Python" in r["output"]

    def test_nonzero_exit(self, task_id):
        r = _run("exit 42", task_id)
        assert r["exit_code"] == 42

    def test_os_info(self, task_id):
        r = _run("uname -a", task_id)
        assert r["exit_code"] == 0
        assert "Linux" in r["output"]


class TestNovitaFilesystem:
    def test_write_and_read_file(self, task_id):
        _run("echo 'test content' > /tmp/novita_test.txt", task_id)
        r = _run("cat /tmp/novita_test.txt", task_id)
        assert r["exit_code"] == 0
        assert "test content" in r["output"]

    def test_persistence_within_session(self, task_id):
        _run("pip install cowsay 2>/dev/null", task_id, timeout=120)
        r = _run('python3 -c "import cowsay; print(cowsay.__file__)"', task_id)
        assert r["exit_code"] == 0
        assert "cowsay" in r["output"]


class TestNovitaPersistence:
    def test_filesystem_survives_pause_and_resume(self):
        """Write a file, pause the sandbox, resume it, assert the file persists."""
        task = "novita_test_persist"
        try:
            os.environ["TERMINAL_CONTAINER_PERSISTENT"] = "true"

            # Write a marker file and pause the sandbox
            _run("echo 'survive' > /tmp/persist_test.txt", task)
            cleanup_vm(task)  # pauses (not kills) because persistent=true

            # Resume with the same task_id — file should still exist
            r = _run("cat /tmp/persist_test.txt", task)
            assert r["exit_code"] == 0
            assert "survive" in r["output"]
        finally:
            os.environ["TERMINAL_CONTAINER_PERSISTENT"] = "false"
            cleanup_vm(task)


class TestNovitaIsolation:
    def test_different_tasks_isolated(self):
        task_a = "novita_test_iso_a"
        task_b = "novita_test_iso_b"
        try:
            _run("echo 'secret' > /tmp/isolated.txt", task_a)
            r = _run("cat /tmp/isolated.txt 2>&1 || echo NOT_FOUND", task_b)
            assert "secret" not in r["output"] or "NOT_FOUND" in r["output"]
        finally:
            cleanup_vm(task_a)
            cleanup_vm(task_b)
