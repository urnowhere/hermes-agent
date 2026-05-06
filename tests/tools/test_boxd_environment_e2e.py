"""End-to-end tests for the boxd cloud VM environment backend.

These tests hit a real boxd cluster — they create, exec into, and destroy
actual VMs. Cost is small (each test creates a single short-lived VM and
explicitly destroys it) but the tests are gated behind two env vars so
default CI never runs them:

    HERMES_LIVE_TESTS=1 BOXD_API_KEY=bxd_... \
        pytest tests/tools/test_boxd_environment_e2e.py -v

Each test uses a unique task_id (UUID-suffixed) so parallel runs and stale
VMs from prior failed runs never collide. Every test's `try`/`finally`
guarantees `env.cleanup()` (or a manual `box.destroy()`) even on assertion
failure — leaking VMs is the worst possible failure mode for an e2e test.

What the unit tests in test_boxd_environment.py cover (mocked SDK):
    - class shape, error handling, file-sync packing, etc.

What this file covers (real SDK + real cluster):
    - VM lifecycle: create succeeds, exec returns real output, destroy works
    - Persistence: suspend → resume same task_id → state actually preserved
    - Bulk file sync: tar archive really lands and unpacks on the VM
    - cwd persistence across exec calls
    - terminal_tool factory wires through to a working backend
"""
from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest


# Load ~/.hermes/.env so the runner doesn't have to shell-source it first.
def _load_user_env() -> None:
    env_file = Path.home() / ".hermes" / ".env"
    if not env_file.exists():
        return
    for raw in env_file.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_user_env()


LIVE = os.environ.get("HERMES_LIVE_TESTS") == "1"
HAS_KEY = bool(os.environ.get("BOXD_API_KEY") or os.environ.get("BOXD_TOKEN"))

# tests/conftest.py installs an autouse fixture that strips every
# credential-shaped env var ("API_KEY", "TOKEN", etc.) for hermeticity.
# We capture the real values at module import — before that fixture runs —
# so each test can put them back via monkeypatch.
_REAL_BOXD_API_KEY = os.environ.get("BOXD_API_KEY", "")
_REAL_BOXD_TOKEN = os.environ.get("BOXD_TOKEN", "")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not LIVE, reason="e2e — set HERMES_LIVE_TESTS=1 to enable"),
    pytest.mark.skipif(not HAS_KEY, reason="BOXD_API_KEY (or BOXD_TOKEN) not set"),
]


@pytest.fixture(autouse=True)
def _restore_boxd_credentials(monkeypatch):
    """Put boxd credentials back after conftest's hermetic-environment
    fixture has stripped them. The SDK's ``Compute()`` reads from these."""
    if _REAL_BOXD_API_KEY:
        monkeypatch.setenv("BOXD_API_KEY", _REAL_BOXD_API_KEY)
    if _REAL_BOXD_TOKEN:
        monkeypatch.setenv("BOXD_TOKEN", _REAL_BOXD_TOKEN)


@pytest.fixture(scope="session")
def shared_compute():
    """Single ``Compute()`` shared across the whole e2e session.

    Reasoning: opening + closing a fresh ``Compute()`` per test (which
    is what the default ``BoxdEnvironment`` constructor does) trips an
    SDK-side auth state issue after ~6 cycles in a single Python
    process, manifesting as ``AuthenticationError: authentication
    failed`` on subsequent ``CreateVm`` calls. Sharing one client
    sidesteps it. Production hermes only spins up one env per task_id
    and reuses it, so it never hits the bug.
    """
    from boxd import Compute
    c = Compute()
    yield c
    try:
        c.close()
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _unique_task_id(label: str) -> str:
    """Per-test unique id — keeps parallel + repeated runs collision-free."""
    return f"e2e-{label}-{uuid.uuid4().hex[:8]}"


def _force_destroy(compute, task_id: str) -> None:
    """Bulletproof teardown: destroy the VM by name, swallowing any error.

    Used as a belt-and-braces cleanup in `finally` blocks for tests that
    don't go through env.cleanup() for some reason (e.g. an assertion
    fails before cleanup is reached). Uses the shared ``Compute`` so we
    don't churn auth state.
    """
    try:
        from boxd import NotFoundError
        try:
            box = compute.box.get(f"hermes-{task_id}")
            box.destroy()
        except NotFoundError:
            pass
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Lifecycle: create / exec / destroy
# ---------------------------------------------------------------------------


def test_e2e_lifecycle_create_exec_destroy(shared_compute):
    """Spin up a real VM, run a real command, destroy it. The bedrock test —
    if this fails, every other e2e is meaningless."""
    from tools.environments.boxd import BoxdEnvironment

    task_id = _unique_task_id("lifecycle")
    env = None
    try:
        env = BoxdEnvironment(
            task_id=task_id,
            persistent_filesystem=False,
            cpu=2, memory=2048, disk=10240,
            compute=shared_compute,
        )
        # Real exec, real output
        r = env.execute("uname -s && echo $((6*7))")
        assert r["returncode"] == 0
        assert "Linux" in r["output"]
        assert "42" in r["output"]
    finally:
        if env is not None:
            env.cleanup()
        _force_destroy(shared_compute, task_id)


# ---------------------------------------------------------------------------
# cwd persists across exec calls (real CWD marker round-trip)
# ---------------------------------------------------------------------------


def test_e2e_cwd_persists_across_calls(shared_compute):
    """The CWD marker mechanism in base._wrap_command must survive a real
    round-trip through the boxd SDK exec path."""
    from tools.environments.boxd import BoxdEnvironment

    task_id = _unique_task_id("cwd")
    env = None
    try:
        env = BoxdEnvironment(
            task_id=task_id,
            persistent_filesystem=False,
            cpu=2, memory=2048, disk=10240,
            compute=shared_compute,
        )
        # Switch directory once — env.cwd should now be /tmp
        env.execute("true", cwd="/tmp")
        assert env.cwd == "/tmp"
        # Next call (no explicit cwd) should still be in /tmp
        r = env.execute("pwd")
        assert r["returncode"] == 0
        assert r["output"].strip().endswith("/tmp")
    finally:
        if env is not None:
            env.cleanup()
        _force_destroy(shared_compute, task_id)


# ---------------------------------------------------------------------------
# Persistence: suspend → re-create same task_id → state preserved
# ---------------------------------------------------------------------------


def test_e2e_persistence_round_trip(shared_compute):
    """Persistent=True: write a file to the home dir (persistent disk),
    suspend (cleanup), re-create with the same task_id, file is still there.
    Avoid /tmp since some boxd configs back tmpfs with RAM that doesn't
    survive cold restart paths."""
    from tools.environments.boxd import BoxdEnvironment

    task_id = _unique_task_id("persist")
    marker = f"hermes-e2e-{uuid.uuid4().hex}"
    env1 = None
    env2 = None
    try:
        # Session 1: write a marker file inside the VM (in $HOME, on disk)
        env1 = BoxdEnvironment(
            task_id=task_id,
            persistent_filesystem=True,
            cpu=2, memory=2048, disk=10240,
            compute=shared_compute,
        )
        marker_path = f"{env1._remote_home}/hermes-e2e-marker.txt"
        r = env1.execute(f"echo {marker} > {marker_path}")
        assert r["returncode"] == 0, f"write failed: {r['output']!r}"
        env1.cleanup()  # suspends, doesn't destroy
        env1 = None

        # Session 2: re-attach with the same task_id, file should still be there
        env2 = BoxdEnvironment(
            task_id=task_id,
            persistent_filesystem=True,
            cpu=2, memory=2048, disk=10240,
            compute=shared_compute,
        )
        marker_path = f"{env2._remote_home}/hermes-e2e-marker.txt"
        r = env2.execute(f"cat {marker_path}")
        assert r["returncode"] == 0, f"cat failed: {r['output']!r}"
        assert marker in r["output"], (
            f"marker missing after suspend/resume — got: {r['output']!r}"
        )
        env2.cleanup()  # suspend again — _force_destroy in finally cleans up
        env2 = None
    finally:
        for e in (env1, env2):
            if e is not None:
                try:
                    e.cleanup()
                except Exception:
                    pass
        # The two sessions left a suspended VM — destroy it directly via SDK
        # (we can't construct a 3rd BoxdEnvironment with persistent=False to
        # destroy because that path goes straight to create() and collides
        # with the existing name).
        _force_destroy(shared_compute, task_id)


# ---------------------------------------------------------------------------
# Bulk file sync: tar archive really lands and unpacks
# ---------------------------------------------------------------------------


def test_e2e_bulk_upload_and_download(tmp_path, shared_compute):
    """The tar+write_file bulk-upload path on a real VM: pack 5 files, ship
    them as a tar, unpack on the VM, verify each file exists with the right
    contents. Then download the .hermes/ tarball back."""
    from tools.environments.boxd import BoxdEnvironment

    task_id = _unique_task_id("bulk")
    env = None
    try:
        env = BoxdEnvironment(
            task_id=task_id,
            persistent_filesystem=False,
            cpu=2, memory=2048, disk=10240,
            compute=shared_compute,
        )

        # Build 5 host files with distinct contents
        files = []
        for i in range(5):
            host = tmp_path / f"f{i}.txt"
            host.write_text(f"content-{i}\n")
            remote = f"{env._remote_home}/.hermes/bulkdir/f{i}.txt"
            files.append((str(host), remote))

        env._boxd_bulk_upload(files)

        # Verify each file is on the VM with correct contents
        for i in range(5):
            r = env.execute(
                f"cat {env._remote_home}/.hermes/bulkdir/f{i}.txt"
            )
            assert r["returncode"] == 0
            assert f"content-{i}" in r["output"], (
                f"file f{i}.txt missing or wrong: got {r['output']!r}"
            )

        # Bulk download the .hermes/ tree as a tar — verify we get bytes
        dest = tmp_path / "downloaded.tar"
        env._boxd_bulk_download(dest)
        assert dest.exists()
        assert dest.stat().st_size > 0
    finally:
        if env is not None:
            env.cleanup()
        _force_destroy(shared_compute, task_id)


# ---------------------------------------------------------------------------
# stdin via heredoc actually works against the real SDK exec path
# ---------------------------------------------------------------------------


def test_e2e_stdin_heredoc(shared_compute):
    from tools.environments.boxd import BoxdEnvironment

    task_id = _unique_task_id("stdin")
    env = None
    try:
        env = BoxdEnvironment(
            task_id=task_id,
            persistent_filesystem=False,
            cpu=2, memory=2048, disk=10240,
            compute=shared_compute,
        )
        # Pipe a known string into wc -c — should report exact byte count
        payload = "hermes-e2e-stdin-payload"
        r = env.execute("wc -c", stdin_data=payload)
        assert r["returncode"] == 0
        # wc -c counts the heredoc bytes including the trailing newline the
        # heredoc syntax injects → len(payload) + 1
        assert str(len(payload) + 1) in r["output"]
    finally:
        if env is not None:
            env.cleanup()
        _force_destroy(shared_compute, task_id)


# ---------------------------------------------------------------------------
# Real $HOME detection matches what bash actually reports inside the VM
# ---------------------------------------------------------------------------


def test_e2e_home_detection_matches_real_remote_home(shared_compute):
    """The cwd resolution depends on detecting $HOME on the real remote VM —
    this is the kind of thing a unit test can't catch (different VM users,
    different shells, etc.)."""
    from tools.environments.boxd import BoxdEnvironment

    task_id = _unique_task_id("home")
    env = None
    try:
        env = BoxdEnvironment(
            task_id=task_id,
            persistent_filesystem=False,
            cpu=2, memory=2048, disk=10240,
            compute=shared_compute,
        )
        # Cross-check our detected _remote_home against a fresh $HOME read
        r = env.execute("echo $HOME")
        assert r["returncode"] == 0
        actual_home = r["output"].strip().splitlines()[-1]
        assert env._remote_home == actual_home, (
            f"_remote_home={env._remote_home!r} disagrees with actual "
            f"$HOME={actual_home!r}"
        )
    finally:
        if env is not None:
            env.cleanup()
        _force_destroy(shared_compute, task_id)


# ---------------------------------------------------------------------------
# Factory dispatch through terminal_tool._create_environment
# ---------------------------------------------------------------------------


def test_e2e_factory_dispatch(shared_compute):
    """terminal_tool._create_environment("boxd", ...) returns a working
    BoxdEnvironment connected to the real cluster."""
    from tools.environments.boxd import BoxdEnvironment
    from tools.terminal_tool import _create_environment

    task_id = _unique_task_id("factory")
    env = None
    try:
        env = _create_environment(
            env_type="boxd",
            image="",
            cwd="/root",
            timeout=60,
            container_config={
                "container_cpu": 2,
                "container_memory": 2048,
                "container_disk": 10240,
                "container_persistent": False,
            },
            task_id=task_id,
        )
        assert isinstance(env, BoxdEnvironment)
        r = env.execute("echo factory-dispatch-ok")
        assert r["returncode"] == 0
        assert "factory-dispatch-ok" in r["output"]
    finally:
        if env is not None:
            env.cleanup()
        _force_destroy(shared_compute, task_id)
