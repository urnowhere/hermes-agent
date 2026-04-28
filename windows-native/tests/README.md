# Windows Native Test Files - Fixed

This directory contains copies of test files with Windows-specific fixes applied.

## Fixed Files

### 1. UTF-8 Encoding Issues
**File**: `agent/test_auxiliary_config_bridge.py`
**Fix**: Added `encoding="utf-8", errors="replace"` to all `Path.read_text()` calls (lines 203, 217, 304)
**Reason**: Windows default encoding (cp1252) cannot read files with non-ASCII characters (emojis)

### 2. Windows Path Handling
**File**: `agent/test_context_references.py`
**Fix**: Added `monkeypatch.setenv("USERPROFILE", str(tmp_path))` to `test_blocks_sensitive_home_and_hermes_paths` (line 290)
**Reason**: On Windows, `os.path.expanduser("~")` uses `USERPROFILE` env var, not `HOME`

### 3. File Permissions (Unix-only)
**File**: `cron/test_file_permissions.py`
**Fix**: Added `@unittest.skipUnless(sys.platform != "win32", "Unix permissions not supported on Windows")` to all 3 test classes
**Reason**: Windows doesn't support Unix-style file permissions (0o700, 0o600)

### 4. Windows Console Access
**File**: `cli/test_branch_command.py`
**Fix**: Added `@pytest.mark.skipif(sys.platform == "win32", reason="prompt_toolkit requires Windows console")` to `TestBranchCommandCLI` class
**Reason**: `prompt_toolkit` tries to access Windows console and fails in CI/headless environments

### 5. Environment Variable Clearing
**File**: `gateway/test_email.py`
**Fix**: Replaced `@patch.dict(os.environ, {}, clear=True)` with manual env clearing that preserves `HOME`/`USERPROFILE`/`HERMES_HOME`
**Reason**: Clearing all env vars breaks `Path.home()` on Windows

### 6. Missing _session_model_overrides
**File**: `cron/test_codex_execution_paths.py`
**Fix**: Added `runner._session_model_overrides = {}` to test setup (line 156)
**Reason**: `GatewayRunner` expects this attribute to exist

## Usage

To run these fixed tests:

```bash
# Run all fixed tests
pytest windows-native/tests/ -v

# Run specific fixed test
pytest windows-native/tests/agent/test_auxiliary_config_bridge.py::TestGatewayBridgeCodeParity -v
```

## Notes

- These are copies of original test files from `tests/` directory
- Fixes are Windows-specific compatibility patches
- Original files in `tests/` remain unchanged
- These tests can be run alongside original tests
