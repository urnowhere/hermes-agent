# Hermes Agent Windows-Native Patch

A runtime patch that enables Hermes Agent to run natively on Windows without WSL (Windows Subsystem for Linux).

## Prerequisites

1. **Python 3.11+** installed on Windows
2. **Git for Windows** installed (required for bash shell access)
3. **Terminal emulator** that supports UTF-8 (Windows Terminal recommended)

## Quick Start

### 1. Set Environment Variables

Add these to your PowerShell profile (`$PROFILE`) or set them in your current session:

```powershell
# Required: Path to Git Bash
$env:HERMES_GIT_BASH_PATH = "C:\Program Files\Git\bin\bash.exe"

# Required: UTF-8 encoding for console
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"

# Required: Add patches to Python path
$env:PYTHONPATH = "windows-native"

# Optional: Storage directory for tool results (defaults to %TEMP%\hermes-results)
$env:HERMES_STORAGE_DIR = "$env:TEMP\hermes-results"
```

### 2. Create Virtual Environment

```powershell
# Create venv
python -m venv .venv-windows-native

# Activate (for both dev and production)
.venv-windows-native\Scripts\Activate.ps1
```

### 3. Install Hermes Agent

**For Development** (with test dependencies):
```powershell
pip install -e ".[dev]"
```

**For Production/Usage** (just the agent):
```powershell
pip install -e .
# or
pip install hermes-agent
```

### 3. Run Hermes Agent

```powershell
# Standard CLI
hermes

# Or with Python directly
python -m cli
```

## How It Works

The `windows-native/patches/__init__.py` module applies runtime patches when imported:

1. **UTF-8 Encoding Fix**: Forces UTF-8 mode for console output (emojis, special chars)
2. **Path Fixes**: Replaces `/tmp/` with Windows temp directory (`%TEMP%\hermes-results`)
3. **Terminal Backend**: Forces `TERMINAL_ENV=local` for dockerless operation
4. **Voice Mode Disabled**: Faster-whisper not available on Windows
5. **fcntl Replacement**: Mocks Unix file locking with Windows `msvcrt`
6. **Firecrawl Mock**: Optional dependency mocking for tests
7. **Windows Path Detection**: Fixes file drop detection for `C:\` style paths

## Tested Functionality

### ✅ Working (Tested)

| Category | Tests | Description |
|----------|-------|-------------|
| **Agent Core** | 12+ tests | Conversation loop, tool orchestration, prompt caching |
| **CLI Features** | 20+ tests | Slash commands, provider resolution, secret capture, tools config |
| **Tools (Core)** | 15+ tests | Registry, file operations, tool result storage |
| **Gateway** | 5 tests | Feishu adapter, email adapter, Telegram conflict handling |
| **Windows-Specific** | 8 tests | UTF-8 encoding, Windows paths, drive letter detection |

**Specific Features Tested:**
- File operations (read/write/patch) with Windows paths
- Agent conversation loop with tool calls
- CLI slash commands (`/help`, `/tools`, `/plan`, `/branch`, etc.)
- Provider model resolution (Anthropic, OpenAI, etc.)
- Gateway adapters (Feishu, Email, Telegram)
- Windows path detection (`C:\Users\...` format)
- UTF-8 emoji rendering in console
- Subdirectory hints with Windows paths
- Secret capture in CLI
- Quick commands (`/q`, `/x`)

### ⚠️ Partially Tested / Known Limitations

| Feature | Status | Notes |
|---------|--------|-------|
| **Voice Mode** | Disabled | Faster-whisper not available on Windows; returns `False` on availability check |
| **Web Scraping** | Mocked | Firecrawl module mocked if not installed; install `firecrawl-py` to enable |
| **Background Processes** | Limited | Tested in CI but interactive usage may vary |
| **MCP Tools** | Not Tested | Requires external MCP servers; skipped in test suite |

### ❌ Not Tested / Known Failures

| Category | Reason | Tests Affected |
|----------|--------|----------------|
| **Unix File Permissions** | Windows doesn't support 0o700/0o600 | `tests/cron/test_file_permissions.py` (8 tests) |
| **Curses/TTY Features** | `prompt_toolkit` console access in headless CI | `tests/cli/test_branch_command.py` (CLI class) |
| **Signal Handling** | Unix signals (SIGKILL, etc.) | `tests/tools/test_mcp_stability.py` |
| **Symlinks** | Requires admin privileges on Windows | `tests/tools/test_worktree*.py`, `test_credential_files.py` |
| **Cron/Scheduler** | Unix cron daemon | `tests/cron/` (some tests) |
| **ACP Server** | Missing dependencies | `tests/acp/` (6 tests) |
| **Integration Tests** | External services | `tests/integration/` |
| **API Server** | Aiohttp dependency | `tests/gateway/test_api_server*.py`, `test_webhook*.py` |

**Specifically Not Tested:**
- Background process notifications in gateway
- MCP tool stability under load
- Worktree security features
- Credential file handling with symlinks
- ACP server (VS Code/Zed/JetBrains integration)
- Webhook adapters
- HA (Home Assistant) integration

## Troubleshooting

### "NoConsoleScreenBufferError" or "Fatal Python error"

This happens when `prompt_toolkit` tries to access the console in CI/headless mode:

```powershell
# Set this before running hermes
$env:PYCHARM_HOSTED = "1"
# or
$env:PYTHONLEGACYWINDOWSSTDIO = "1"
```

### UTF-8 Encoding Errors

If you see garbled text or Unicode errors:

```powershell
# Ensure these are set
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"

# In PowerShell, also ensure console encoding is UTF-8
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
```

### Git Bash Not Found

If you get errors about bash not being found:

```powershell
# Find where Git is installed
where.exe git

# Then set the path to bash.exe (usually in same directory as git.exe)
$env:HERMES_GIT_BASH_PATH = "C:\Program Files\Git\bin\bash.exe"
```

### Tool Result Storage Errors

If `/tmp/` errors occur:

```powershell
# Set Windows temp directory
$env:HERMES_STORAGE_DIR = "$env:TEMP\hermes-results"
```

## Running Tests

To run the Windows-native test suite:

```powershell
# Run all tests
python windows-native\run_tests.py

# Run specific test file
python windows-native\run_tests.py tests/agent/test_auxiliary_config_bridge.py

# Run with verbose output
python windows-native\run_tests.py -v tests/cli/
```

The test runner automatically:
- Adds `windows-native` to `PYTHONPATH`
- Excludes Unix-specific tests
- Excludes integration tests requiring external services
- Runs tests from both `windows-native/tests/` (fixed copies) and `tests/` (original)

## Architecture Notes

### Patches Applied at Import Time

The patches are applied when `windows-native/patches/__init__.py` is imported. This happens automatically when:
1. `PYTHONPATH` includes `windows-native`
2. Any code imports from the patches module

### Windows-Native Tests Directory

The `windows-native/tests/` directory contains copies of failing tests with Windows-specific fixes:
- UTF-8 encoding fixes for file reads
- `USERPROFILE` environment variable patches
- Skipped tests for Unix-only features
- Mock setup for missing modules

## Comparison: WSL vs Windows-Native

| Feature | WSL | Windows-Native |
|---------|-----|----------------|
| **File System** | Linux ext4 | Windows NTFS |
| **Path Format** | `/home/user/...` | `C:\Users\...` |
| **Shell** | Bash (native) | Git Bash (external) |
| **Performance** | Good | Better (no VM overhead) |
| **Setup Complexity** | Medium | Low |
| **Unicode Support** | Excellent | Good (with patches) |
| **Voice Mode** | Available | Disabled |

## Contributing

If you find additional Windows-specific issues:

1. Check if it's a path issue (`/tmp/` vs `%TEMP%`)
2. Check if it's an encoding issue (UTF-8)
3. Check if it uses Unix syscalls (`fcntl`, `os.getuid`, etc.)
4. Add a patch to `windows-native/patches/__init__.py`
5. Add a test to `windows-native/tests/`

## License

Same as Hermes Agent (see main repository).
