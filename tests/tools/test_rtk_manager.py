"""Tests for tools/rtk_manager.

Covers binary discovery, download, verification, and command rewriting.
All network and subprocess calls are mocked — tests run offline.
"""

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from tools.rtk_manager import (
    RTK_CACHE_DIR,
    RTK_VERSION,
    _detect_target_triple,
    _download_url,
    _verify_binary,
    ensure_rtk,
    rewrite_command,
)


class TestDetectTargetTriple:
    def test_linux_x86_64(self):
        with (
            patch("platform.machine", return_value="x86_64"),
            patch("platform.system", return_value="Linux"),
        ):
            assert _detect_target_triple() == "x86_64-unknown-linux-musl"

    def test_linux_aarch64(self):
        with (
            patch("platform.machine", return_value="aarch64"),
            patch("platform.system", return_value="Linux"),
        ):
            assert _detect_target_triple() == "aarch64-unknown-linux-musl"

    def test_darwin_arm64(self):
        with (
            patch("platform.machine", return_value="arm64"),
            patch("platform.system", return_value="Darwin"),
        ):
            assert _detect_target_triple() == "aarch64-apple-darwin"

    def test_windows_amd64(self):
        with (
            patch("platform.machine", return_value="AMD64"),
            patch("platform.system", return_value="Windows"),
        ):
            assert _detect_target_triple() == "x86_64-pc-windows-msvc"

    def test_unsupported_arch(self):
        with (
            patch("platform.machine", return_value="riscv64"),
            patch("platform.system", return_value="Linux"),
        ):
            assert _detect_target_triple() is None

    def test_unsupported_os(self):
        with (
            patch("platform.machine", return_value="x86_64"),
            patch("platform.system", return_value="FreeBSD"),
        ):
            assert _detect_target_triple() is None


class TestDownloadUrl:
    def test_linux_url(self):
        url = _download_url("x86_64-unknown-linux-musl")
        assert url == (
            f"https://github.com/rtk-ai/rtk/releases/download/"
            f"{RTK_VERSION}/rtk-x86_64-unknown-linux-musl.tar.gz"
        )

    def test_windows_url(self):
        url = _download_url("x86_64-pc-windows-msvc")
        assert url == (
            f"https://github.com/rtk-ai/rtk/releases/download/"
            f"{RTK_VERSION}/rtk-x86_64-pc-windows-msvc.zip"
        )


class TestVerifyBinary:
    def test_valid_binary(self, tmp_path):
        fake_rtk = tmp_path / "rtk"
        fake_rtk.write_text("#!/bin/sh\necho 'rtk 0.38.0'")
        fake_rtk.chmod(0o755)
        assert _verify_binary(fake_rtk) is True

    def test_invalid_binary(self, tmp_path):
        fake_rtk = tmp_path / "rtk"
        fake_rtk.write_text("#!/bin/sh\necho 'hello world'")
        fake_rtk.chmod(0o755)
        assert _verify_binary(fake_rtk) is False

    def test_missing_binary(self, tmp_path):
        assert _verify_binary(tmp_path / "does_not_exist") is False

    def test_non_executable(self, tmp_path):
        fake_rtk = tmp_path / "rtk"
        fake_rtk.write_text("rtk 0.38.0")
        # No chmod — not executable
        assert _verify_binary(fake_rtk) is False


class TestEnsureRtk:
    def test_uses_path_binary(self, tmp_path):
        fake_rtk = tmp_path / "rtk"
        fake_rtk.write_text("#!/bin/sh\necho 'rtk 0.38.0'")
        fake_rtk.chmod(0o755)

        with patch("tools.rtk_manager.shutil.which", return_value=str(fake_rtk)):
            result = ensure_rtk(auto_download=False)
            assert result == fake_rtk

    def test_uses_cached_binary(self, tmp_path):
        fake_rtk = tmp_path / "rtk"
        fake_rtk.write_text("#!/bin/sh\necho 'rtk 0.38.0'")
        fake_rtk.chmod(0o755)

        with (
            patch("tools.rtk_manager.shutil.which", return_value=None),
            patch("tools.rtk_manager.RTK_CACHE_DIR", tmp_path),
        ):
            result = ensure_rtk(auto_download=False)
            assert result == fake_rtk

    def test_no_binary_no_download_returns_none(self):
        with patch("tools.rtk_manager.shutil.which", return_value=None):
            result = ensure_rtk(auto_download=False)
            assert result is None

    def test_corrupt_cached_binary_removed(self, tmp_path):
        fake_rtk = tmp_path / "rtk"
        fake_rtk.write_text("corrupt")
        fake_rtk.chmod(0o755)

        with (
            patch("tools.rtk_manager.shutil.which", return_value=None),
            patch("tools.rtk_manager.RTK_CACHE_DIR", tmp_path),
        ):
            result = ensure_rtk(auto_download=False)
            assert result is None
            assert not fake_rtk.exists()  # Should be cleaned up

    def test_download_success(self, tmp_path):
        fake_rtk = tmp_path / "rtk"
        fake_rtk.write_text("#!/bin/sh\necho 'rtk 0.38.0'")
        fake_rtk.chmod(0o755)

        with (
            patch("tools.rtk_manager.shutil.which", return_value=None),
            patch("tools.rtk_manager.RTK_CACHE_DIR", tmp_path),
            patch("tools.rtk_manager._download_and_extract", return_value=True),
        ):
            result = ensure_rtk(auto_download=True)
            assert result is not None

    def test_download_failure(self, tmp_path):
        with (
            patch("tools.rtk_manager.shutil.which", return_value=None),
            patch("tools.rtk_manager.RTK_CACHE_DIR", tmp_path),
            patch("tools.rtk_manager._download_and_extract", return_value=False),
        ):
            result = ensure_rtk(auto_download=True)
            assert result is None


class TestRewriteCommand:
    def test_successful_rewrite(self, tmp_path):
        fake_rtk = tmp_path / "rtk"
        fake_rtk.write_text(
            "#!/bin/sh\n"
            'if [ "$1" = "rewrite" ] && [ "$2" = "git status" ]; then\n'
            '  echo "rtk git status"\n'
            "  exit 3\n"
            "fi\n"
            "exit 1\n"
        )
        fake_rtk.chmod(0o755)

        with patch("tools.rtk_manager.ensure_rtk", return_value=fake_rtk):
            result = rewrite_command("git status", auto_download=False)
            assert result == "rtk git status"

    def test_no_rewrite_available(self, tmp_path):
        fake_rtk = tmp_path / "rtk"
        fake_rtk.write_text("#!/bin/sh\nexit 1\n")
        fake_rtk.chmod(0o755)

        with patch("tools.rtk_manager.ensure_rtk", return_value=fake_rtk):
            result = rewrite_command("echo hello", auto_download=False)
            assert result is None

    def test_rtk_not_found(self):
        with patch("tools.rtk_manager.ensure_rtk", return_value=None):
            result = rewrite_command("git status", auto_download=False)
            assert result is None

    def test_rewrite_same_as_input(self, tmp_path):
        """When RTK returns the same command, we should return None."""
        fake_rtk = tmp_path / "rtk"
        fake_rtk.write_text(
            '#!/bin/sh\nif [ "$2" = "rewrite" ]; then\n  echo "$3"\n  exit 3\nfi\n'
        )
        fake_rtk.chmod(0o755)

        with patch("tools.rtk_manager.ensure_rtk", return_value=fake_rtk):
            result = rewrite_command("echo hello", auto_download=False)
            assert result is None

    def test_subprocess_exception(self):
        with (
            patch("tools.rtk_manager.ensure_rtk", return_value=Path("/fake/rtk")),
            patch("subprocess.run", side_effect=OSError("boom")),
        ):
            result = rewrite_command("git status", auto_download=False)
            assert result is None
