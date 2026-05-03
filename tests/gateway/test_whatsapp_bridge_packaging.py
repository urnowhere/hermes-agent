"""Regression guard for #15336 — WhatsApp bridge.js must ship in installed wheels.

Before this fix, ``scripts/whatsapp-bridge/bridge.js`` lived outside
any Python package, so ``pip install`` (and downstream Nix /
Docker / Homebrew installs) simply didn't include it in the wheel.
Users who started Hermes from a packaged install hit:

    ✗ Bridge script not found at /nix/store/.../site-packages/scripts/whatsapp-bridge/bridge.js

The bridge files were moved into ``gateway/whatsapp_bridge/`` (a real
sub-package of ``gateway``) and registered as setuptools
``package-data`` so they end up at ``site-packages/gateway/
whatsapp_bridge/`` on every wheel-based install path.

These tests pin two invariants:

1. The bridge directory is *findable* from the ``gateway`` package's
   ``__file__`` — the resolution path that
   ``WhatsAppAdapter._DEFAULT_BRIDGE_DIR`` and the doctor / setup
   commands now use.
2. The expected files (``bridge.js``, ``allowlist.js``, ``package.json``)
   exist in that directory.  If a future refactor removes one of them
   without updating callers, this test fails before the next release
   ships.
"""
import os
from pathlib import Path


def test_bridge_dir_resolves_from_gateway_package():
    """``WhatsAppAdapter._DEFAULT_BRIDGE_DIR`` must compute to a real
    directory that exists in the source tree (and therefore — given
    the ``[tool.setuptools.package-data]`` entry pinned in
    ``pyproject.toml`` — also exists at ``site-packages/gateway/
    whatsapp_bridge/`` after a wheel install)."""
    from gateway.platforms.whatsapp import WhatsAppAdapter

    bridge_dir = WhatsAppAdapter._DEFAULT_BRIDGE_DIR
    assert isinstance(bridge_dir, Path)
    assert bridge_dir.exists(), (
        f"_DEFAULT_BRIDGE_DIR resolved to {bridge_dir} which does not "
        f"exist on disk — the bridge files were likely moved without "
        f"updating the resolver, or the move regressed (#15336)"
    )
    assert bridge_dir.is_dir()


def test_bridge_dir_lives_inside_gateway_package():
    """The bridge directory must be ``gateway/whatsapp_bridge/`` so the
    ``gateway.whatsapp_bridge`` package-data entry in ``pyproject.toml``
    actually targets it.  If a future refactor moves the directory
    elsewhere without updating ``pyproject.toml``, the wheel will silently
    stop including the bridge files again — same #15336 regression we
    just fixed.
    """
    import gateway as _gateway_pkg
    from gateway.platforms.whatsapp import WhatsAppAdapter

    bridge_dir = WhatsAppAdapter._DEFAULT_BRIDGE_DIR
    expected_parent = Path(_gateway_pkg.__file__).resolve().parent
    assert bridge_dir.parent == expected_parent, (
        f"bridge dir parent is {bridge_dir.parent}, expected "
        f"{expected_parent}.  If you moved the bridge, update "
        f"pyproject.toml ``[tool.setuptools.package-data]`` to match."
    )
    assert bridge_dir.name == "whatsapp_bridge"


def test_bridge_dir_contains_required_files():
    """Pin the file list the consumers depend on — ``bridge.js``
    (the entry-point), ``allowlist.js`` (loaded by bridge.js at
    runtime), and ``package.json`` (so ``npm install`` works in
    the ``hermes setup`` flow)."""
    from gateway.platforms.whatsapp import WhatsAppAdapter

    bridge_dir = WhatsAppAdapter._DEFAULT_BRIDGE_DIR
    for required in ("bridge.js", "allowlist.js", "package.json"):
        target = bridge_dir / required
        assert target.exists(), (
            f"required bridge file missing: {target}.  Either the file "
            f"was deleted (and callers need updating) or the move "
            f"regressed (#15336)"
        )


def test_pyproject_package_data_covers_bridge_files():
    """``pyproject.toml`` must declare ``gateway.whatsapp_bridge`` as
    a package and include the bridge file globs as package-data;
    otherwise the wheel-build path leaves the directory empty even
    though the source tree has it.

    Parses ``pyproject.toml`` directly so this test catches the
    regression even when running outside an installed wheel.
    """
    try:
        import tomllib  # py3.11+
    except ImportError:  # pragma: no cover — older Pythons
        import tomli as tomllib

    repo_root = Path(__file__).resolve().parents[2]
    with open(repo_root / "pyproject.toml", "rb") as fp:
        cfg = tomllib.load(fp)

    package_data = (
        cfg.get("tool", {})
        .get("setuptools", {})
        .get("package-data", {})
    )
    bridge_globs = package_data.get("gateway.whatsapp_bridge")
    assert bridge_globs, (
        "pyproject.toml is missing the "
        "``[tool.setuptools.package-data] \"gateway.whatsapp_bridge\"`` "
        "entry — wheel installs will not contain bridge.js (#15336)"
    )
    # Pin the patterns we rely on so a future trim doesn't silently
    # drop the package.json or the JS sources.
    pattern_str = " ".join(bridge_globs)
    for needle in ("*.js", "package.json"):
        assert needle in pattern_str, (
            f"package-data globs for gateway.whatsapp_bridge are missing "
            f"{needle!r}: {bridge_globs!r}"
        )


def test_bridge_init_marker_present():
    """The ``__init__.py`` marker is what makes
    ``gateway/whatsapp_bridge/`` a regular setuptools package (rather
    than a PEP 420 namespace package, which has spottier package-data
    support across setuptools versions).  Without it, ``find_packages``
    skips the directory and the wheel ships nothing."""
    from gateway.platforms.whatsapp import WhatsAppAdapter

    init = WhatsAppAdapter._DEFAULT_BRIDGE_DIR / "__init__.py"
    assert init.exists(), (
        f"missing {init} — without it the directory isn't a real "
        f"setuptools package and package-data is skipped on some "
        f"setuptools versions (#15336)"
    )


# ---------------------------------------------------------------------------
# Runtime-vs-template separation (#15460 follow-up)
#
# The template location lives in site-packages (read-only on Nix / system
# pip installs).  ``npm install`` has to run in a writable location, so
# the adapter materialises the JS files into ``HERMES_HOME/whatsapp-bridge/``
# on first start.  These tests pin that separation so a future refactor
# can't silently point npm back at site-packages.
# ---------------------------------------------------------------------------


def test_runtime_bridge_dir_lives_under_hermes_home(tmp_path, monkeypatch):
    """The writable runtime dir must resolve under ``HERMES_HOME`` —
    never site-packages.  Honouring ``HERMES_HOME`` means profile-
    isolated installs and Docker volumes work without extra wiring."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes"))
    from gateway.platforms.whatsapp import _resolve_runtime_bridge_dir

    runtime_dir = _resolve_runtime_bridge_dir()
    assert runtime_dir == tmp_path / "hermes" / "whatsapp-bridge"


def test_ensure_runtime_bridge_files_copies_template(tmp_path, monkeypatch):
    """First-boot flow: runtime dir is empty, template has the JS
    sources; the helper must create the runtime dir and copy the
    expected files.  ``node_modules`` and the Python ``__init__.py``
    marker must be skipped — npm manages the former, the latter is
    Python-internal."""
    from gateway.platforms.whatsapp import (
        _BRIDGE_TEMPLATE_FILES,
        _ensure_runtime_bridge_files,
    )

    template_dir = tmp_path / "template"
    template_dir.mkdir()
    for fname in _BRIDGE_TEMPLATE_FILES:
        (template_dir / fname).write_text(f"stub-{fname}")
    (template_dir / "__init__.py").write_text("# python marker")
    (template_dir / "node_modules").mkdir()
    (template_dir / "node_modules" / "pretend").write_text("should not copy")

    runtime_dir = tmp_path / "runtime"
    _ensure_runtime_bridge_files(template_dir, runtime_dir)

    for fname in _BRIDGE_TEMPLATE_FILES:
        assert (runtime_dir / fname).exists(), f"missing {fname} in runtime"
    assert not (runtime_dir / "__init__.py").exists(), (
        "Python package marker leaked into runtime — the runtime dir "
        "is a pure Node app, not a Python package"
    )
    assert not (runtime_dir / "node_modules").exists(), (
        "node_modules was copied from template; npm should manage "
        "this at the runtime location"
    )


def test_ensure_runtime_bridge_files_chmods_writable(tmp_path):
    """Template files may ship at mode 0o444 (read-only on Nix store).
    The runtime copy must be writable (0o644) so ``npm install`` can
    overwrite ``package-lock.json`` when resolving dependencies."""
    from gateway.platforms.whatsapp import _ensure_runtime_bridge_files

    template_dir = tmp_path / "template"
    template_dir.mkdir()
    src = template_dir / "package.json"
    src.write_text("{}")
    os.chmod(src, 0o444)

    runtime_dir = tmp_path / "runtime"
    _ensure_runtime_bridge_files(template_dir, runtime_dir)

    dst = runtime_dir / "package.json"
    assert dst.exists()
    mode = dst.stat().st_mode & 0o777
    assert mode & 0o200, (
        f"runtime bridge file {dst} mode {oct(mode)} is not writable — "
        f"npm install will fail with EROFS / EACCES"
    )


def test_ensure_runtime_bridge_files_is_mtime_aware(tmp_path, monkeypatch):
    """Idempotent re-runs must NOT re-copy when the runtime copy is
    already up-to-date — cheap start-up cost.  Only stale runtime
    files (template newer than runtime) get refreshed."""
    from gateway.platforms.whatsapp import _ensure_runtime_bridge_files

    template_dir = tmp_path / "template"
    template_dir.mkdir()
    src = template_dir / "bridge.js"
    src.write_text("v1")

    runtime_dir = tmp_path / "runtime"
    _ensure_runtime_bridge_files(template_dir, runtime_dir)

    # Snapshot mtime of the runtime copy and re-run with the template
    # unchanged — runtime file should NOT be re-written.
    runtime_file = runtime_dir / "bridge.js"
    first_mtime = runtime_file.stat().st_mtime_ns

    _ensure_runtime_bridge_files(template_dir, runtime_dir)
    second_mtime = runtime_file.stat().st_mtime_ns
    assert second_mtime == first_mtime, (
        "runtime bridge file was re-copied despite template being "
        "unchanged — should be a no-op"
    )

    # Now bump the template mtime to simulate a Hermes upgrade
    # shipping a newer bridge.js.  The runtime copy must refresh.
    import time as _time
    future_mtime_ns = first_mtime + 1_000_000_000  # 1s in the future
    os.utime(src, ns=(future_mtime_ns, future_mtime_ns))
    _ensure_runtime_bridge_files(template_dir, runtime_dir)
    third_mtime = runtime_file.stat().st_mtime_ns
    assert third_mtime != first_mtime, (
        "template was updated but runtime file stayed stale — "
        "Hermes upgrades won't propagate bridge fixes to users"
    )


def test_ensure_runtime_bridge_files_handles_missing_template(tmp_path):
    """Development checkouts that never ran ``pip install -e .`` may
    have no template dir at all.  The helper must no-op cleanly in
    that case — the caller is responsible for handling the
    missing-bridge user-facing error."""
    from gateway.platforms.whatsapp import _ensure_runtime_bridge_files

    template_dir = tmp_path / "does-not-exist"
    runtime_dir = tmp_path / "runtime"

    # Must not raise.
    _ensure_runtime_bridge_files(template_dir, runtime_dir)
    # Must not fabricate an empty runtime dir either — nothing to put
    # in it would just confuse callers.
    assert not runtime_dir.exists()


def test_adapter_default_bridge_script_points_at_runtime(tmp_path, monkeypatch):
    """End-to-end: a freshly-constructed ``WhatsAppAdapter`` must have
    its ``_bridge_script`` default pointing at the HERMES_HOME runtime
    location, never at site-packages.  Regression guard against the
    bug Copilot caught on #15460 (npm install hitting read-only fs).
    """
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes"))

    from gateway.config import PlatformConfig
    from gateway.platforms.whatsapp import WhatsAppAdapter

    config = PlatformConfig(enabled=True, extra={})
    adapter = WhatsAppAdapter(config)
    assert Path(adapter._bridge_script).parent == tmp_path / "hermes" / "whatsapp-bridge"


