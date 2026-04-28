"""Pytest configuration for Windows-native tests.

Applies Windows compatibility patches before running tests.
"""

import sys
import os
from pathlib import Path

# Add parent directories to path
SCRIPT_DIR = Path(__file__).parent.resolve()
WINDOWS_NATIVE_DIR = SCRIPT_DIR.parent
PROJECT_ROOT = WINDOWS_NATIVE_DIR.parent

if str(WINDOWS_NATIVE_DIR) not in sys.path:
    sys.path.insert(0, str(WINDOWS_NATIVE_DIR))
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def pytest_configure(config):
    """Apply Windows patches after pytest is configured."""
    if sys.platform == "win32":
        try:
            from patches import apply_patches_for_pytest

            apply_patches_for_pytest()
        except ImportError:
            pass
