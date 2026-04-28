# Windows Native Test Failures

## Summary

After running the full test suite on Windows, I found **7 categories of failures** (excluding the 35 originally listed in the workflow):

### 1. UTF-8 Encoding Issues (3 tests)
**Files:** `tests/agent/test_auxiliary_config_bridge.py`
**Tests:** 
- `test_gateway_has_auxiliary_bridge`
- `test_gateway_no_compression_env_bridge`  
- `test_cli_defaults_can_merge_auxiliary`

**Issue:** Tests use `Path.read_text()` without specifying encoding, causing `UnicodeDecodeError` when reading files with non-ASCII characters (emojis) on Windows.

**Fix Required:** Add `encoding="utf-8", errors="replace"` to all `read_text()` calls in these tests.

---

### 2. Windows Path Handling (1 test)
**File:** `tests/agent/test_context_references.py`
**Test:** `test_blocks_sensitive_home_and_hermes_paths`

**Issue:** `os.path.expanduser("~")` on Windows uses `USERPROFILE` env var, not `HOME`. The test only patches `HOME`.

**Fix Required:** Also patch `USERPROFILE` on Windows, or use `pathlib.Path.home()` which handles both.

---

### 3. shlex Path Parsing on Windows (1 test)
**File:** `tests/agent/test_subdirectory_hints.py`
**Test:** `test_terminal_command_path_extraction`

**Issue:** `shlex.split()` treats backslashes as escape characters on Windows, corrupting Windows paths like `C:\path\to\file`.

**Fix Already Applied:** Changed `shlex.split(cmd)` to `shlex.split(cmd, posix=False)` in `agent/subdirectory_hints.py`.

---

### 4. File Permissions (8 tests)
**File:** `tests/cron/test_file_permissions.py`
**Tests:** All 8 tests in the file

**Issue:** Windows doesn't support Unix-style permissions (0o700, 0o600). Tests expect specific permission modes that don't apply on Windows.

**Fix Required:** Skip all tests in this file on Windows using `@unittest.skipUnless(sys.platform != "win32", ...)` or `@pytest.mark.skipif`.

---

### 5. Windows Console Access (1 test)
**File:** `tests/cli/test_branch_command.py`
**Test:** All tests in `TestBranchCommandCLI` class

**Issue:** Tests import `cli` which triggers `prompt_toolkit` initialization. `prompt_toolkit` tries to access the Windows console and fails with `NoConsoleScreenBufferError` in CI/headless environments.

**Fix Required:** Either:
- Set `PYCHARM_HOSTED=1` or `PYTHONLEGACYWINDOWSSTDIO=1` env var before import
- Or skip these tests on Windows
- Or refactor tests to not import the full CLI module

---

### 6. Environment Variable Clearing (1 test)
**File:** `tests/gateway/test_email.py`
**Test:** `test_email_not_loaded_without_env`

**Issue:** Test uses `@patch.dict(os.environ, {}, clear=True)` which clears ALL env vars including `HOME`/`USERPROFILE`, causing `Path.home()` to fail on Windows.

**Fix Required:** Don't use `clear=True`, or preserve `HOME`/`USERPROFILE`/`HERMES_HOME` when clearing.

---

### 7. Missing _session_model_overrides (1 test)
**File:** `tests/cron/test_codex_execution_paths.py`
**Test:** `test_gateway_run_agent_codex_path_handles_internal_401_refresh`

**Issue:** Test creates `GatewayRunner` with `__new__` but doesn't initialize `_session_model_overrides` dict, causing `AttributeError`.

**Fix Required:** Add `runner._session_model_overrides = {}` to the test setup.

---

## Approach

Since I should not edit test files directly, the approach is to:

1. **Create copies** of affected test files under `windows-native/tests/`
2. **Apply fixes** to the copies
3. **Update the workflow** to run the fixed copies instead of originals, or
4. **Skip problematic tests** using pytest markers in the workflow command

## Files to Copy and Fix

| Original File | Windows-Native Copy | Fix Required |
|--------------|-------------------|--------------|
| `tests/agent/test_auxiliary_config_bridge.py` | `windows-native/tests/agent/test_auxiliary_config_bridge.py` | Add UTF-8 encoding to read_text() |
| `tests/agent/test_context_references.py` | `windows-native/tests/agent/test_context_references.py` | Patch USERPROFILE on Windows |
| `tests/cron/test_file_permissions.py` | `windows-native/tests/cron/test_file_permissions.py` | Skip on Windows |
| `tests/cli/test_branch_command.py` | `windows-native/tests/cli/test_branch_command.py` | Skip on Windows or mock console |
| `tests/gateway/test_email.py` | `windows-native/tests/gateway/test_email.py` | Don't clear env completely |
| `tests/cron/test_codex_execution_paths.py` | `windows-native/tests/cron/test_codex_execution_paths.py` | Add _session_model_overrides |

## Already Fixed

- `agent/subdirectory_hints.py`: Changed `shlex.split(cmd)` to `shlex.split(cmd, posix=False)` to handle Windows paths correctly.

## Next Steps

1. Create the `windows-native/tests/` directory structure
2. Copy and fix each test file
3. Update `.github/workflows/windows-native.yml` to either:
   - Run the fixed copies from `windows-native/tests/`, or
   - Add skip markers for the problematic tests
