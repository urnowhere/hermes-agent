"""Docker smoke-test wrapper for dashboard authentication modes."""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
COMPOSE = ROOT / "docker" / "dashboard-auth" / "docker-compose.yml"


def test_dashboard_auth_docker_files_are_present():
    assert COMPOSE.exists()
    for name in ["Dockerfile", "smoke.py", "nginx-trusted-proxy.conf", "nginx-tailscale-mock.conf"]:
        assert (COMPOSE.parent / name).exists()


def test_dashboard_auth_docker_compose_smoke():
    if os.environ.get("HERMES_RUN_DOCKER_AUTH_TESTS") != "1":
        import pytest
        pytest.skip("set HERMES_RUN_DOCKER_AUTH_TESTS=1 to run the Docker compose smoke test")
    if shutil.which("docker") is None:
        import pytest
        pytest.skip("docker CLI is not installed")

    probe = subprocess.run(["docker", "compose", "version"], cwd=ROOT, text=True, capture_output=True)
    if probe.returncode != 0:
        import pytest
        pytest.skip("docker compose plugin is not installed")

    # The repository-wide test fixture enforces a 30s SIGALRM timeout, but the
    # first Compose image build legitimately takes longer while installing Python
    # and frontend dependencies. Disable that alarm for this explicitly opt-in
    # Docker smoke test; callers still control the overall subprocess timeout.
    if sys.platform != "win32":
        signal.alarm(0)

    compose = ["docker", "compose", "-f", str(COMPOSE)]
    subprocess.run([*compose, "build"], cwd=ROOT, check=True)
    try:
        subprocess.run(
            [*compose, "up", "--abort-on-container-exit", "--exit-code-from", "test-runner"],
            cwd=ROOT,
            check=True,
        )
    finally:
        subprocess.run([*compose, "down", "-v"], cwd=ROOT, check=False)
