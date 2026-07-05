from __future__ import annotations

from plugins.formsy.constraint_keeper.evidence import (
    changed_files_from_diff,
    classify_terminal_result,
    hash_text,
    is_edit_surface,
    is_final_submit,
    is_validation_command,
    truncate_with_hash,
)


def test_detects_swebench_terminal_final_submit():
    assert is_final_submit(
        "terminal",
        {"command": "echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT && cat patch.txt"},
    )
    assert not is_final_submit("terminal", {"command": "echo still working"})


def test_classifies_only_validation_success_as_test_result():
    event = classify_terminal_result(
        {"command": "python -m pytest tests/auth", "exit_code": 0},
        "2 passed",
    )

    assert event is not None
    assert event["event_kind"] == "test_result"
    assert event["payload"]["passed"] is True
    assert classify_terminal_result({"command": "echo ok", "exit_code": 0}, "ok") is None


def test_classifies_nonzero_terminal_result_as_failure():
    event = classify_terminal_result(
        {"command": "python - <<'PY'\nprint(1)\nPY", "exit_code": 1},
        "Traceback\nAssertionError: boom",
    )

    assert event is not None
    assert event["event_kind"] == "failure"
    assert event["payload"]["exit_code"] == 1
    assert event["payload"]["fingerprint"]


def test_classifies_masked_pytest_failure_output_as_failure():
    event = classify_terminal_result(
        {
            "command": (
                "python3 -m pytest tests/package/test_client_response.py -v "
                "2>&1 | tail -20"
            ),
            "exit_code": 0,
        },
        (
            "=========================== short test summary info ============================\n"
            "FAILED tests/package/test_client_response.py::test_response_decoding\n"
            "=================== 3 failed, 28 passed, 2 warnings in 0.61s ==================="
        ),
    )

    assert event is not None
    assert event["event_kind"] == "failure"
    assert event["payload"]["passed"] is False
    assert event["payload"]["failure_kind"] == "masked_validation_failure"
    assert event["payload"]["exit_code"] == 0


def test_detects_terminal_mutation_patterns_as_edit_surface():
    assert is_edit_surface(
        "terminal",
        {"command": "python - <<'PY'\nopen('x.py','w').write('1')\nPY"},
    )
    assert is_edit_surface("apply_patch", {"patch": "*** Begin Patch"})
    assert not is_edit_surface("terminal", {"command": "git diff --stat"})


def test_validation_command_patterns_include_django_runner():
    assert is_validation_command("python tests/runtests.py forms_tests")
    assert is_validation_command("pytest tests/test_forms.py")
    assert not is_validation_command("python - <<'PY'\nprint('repro')\nPY")


def test_validation_command_patterns_include_python_compile_checks():
    assert is_validation_command(
        "python3 -m py_compile lib/ansible/executor/play_iterator.py"
    )
    assert is_validation_command(
        "cd /repo && python3.11 -m py_compile lib/ansible/executor/play_iterator.py && echo Syntax OK"
    )
    assert is_validation_command("python -m compileall lib/ansible/executor")


def test_classifies_successful_python_compile_as_test_result():
    event = classify_terminal_result(
        {
            "command": (
                "cd /repo && python3 -m py_compile "
                "lib/ansible/executor/play_iterator.py && echo Syntax OK"
            ),
            "exit_code": 0,
        },
        "Syntax OK",
    )

    assert event is not None
    assert event["event_kind"] == "test_result"
    assert event["payload"]["passed"] is True
    assert "py_compile" in event["payload"]["command"]


def test_classifies_successful_focused_python_validation_script_as_test_result():
    event = classify_terminal_result(
        {
            "command": (
                "cd /repo && python3 test_gzip_validation.py && echo EXIT_CODE:$?"
            ),
            "exit_code": 0,
        },
        "Test 1 PASS\nAll tests passed!\nEXIT_CODE:0",
    )

    assert event is not None
    assert event["event_kind"] == "test_result"
    assert event["payload"]["passed"] is True
    assert "test_gzip_validation.py" in event["payload"]["command"]


def test_truncate_with_hash_preserves_full_output_hash():
    truncated = truncate_with_hash("x" * 1000, limit=64)

    assert truncated["truncated"] is True
    assert len(truncated["text"]) <= 64
    assert truncated["hash"] == hash_text("x" * 1000)


def test_changed_files_from_unified_diff():
    diff = """diff --git a/app/a.py b/app/a.py
index 000..111 100644
--- a/app/a.py
+++ b/app/a.py
@@ -1 +1 @@
-old
+new
diff --git a/tests/test_a.py b/tests/test_a.py
--- a/tests/test_a.py
+++ b/tests/test_a.py
"""

    assert changed_files_from_diff(diff) == ["app/a.py", "tests/test_a.py"]
