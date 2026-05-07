"""Regression guard for #14688.

macOS ships ``/usr/bin/python3`` = 3.9.  Agents' terminal tools routinely
invoke ``python3 setup.py --check`` (the google-workspace skill's auth
entry-point) via that system interpreter rather than Hermes' venv python,
because ``SKILL.md`` hints are not always honoured by smaller local models.

Before this guard, three files — ``hermes_constants.py`` (imported
transitively) and the two skill scripts — declared annotations using PEP 604
unions (``X | None``).  On Python 3.9 that syntax is evaluated at
function-def time and raises ``TypeError: unsupported operand type(s) for |:
'type' and 'NoneType'`` BEFORE the script ever reaches its try/except
fallbacks.  Net effect: the agent saw a crash and interpreted it as "auth
broken", ran setup from scratch, and looped.

Fix: each of the affected files starts with ``from __future__ import
annotations`` (PEP 563).  That converts every annotation in the file into a
string, so ``Path | None`` never fires at import time and the module loads
cleanly on Python 3.9 the same way it does on 3.10+.

This test fails if a future edit drops the pragma from any of the three
files — which would silently re-break macOS users with system-``python3``
toolchains.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]

# Files that MUST carry ``from __future__ import annotations`` so Python 3.9
# (still the default interpreter on macOS) can import them without firing on
# a PEP 604 union at def-time.  Keep this list pinned — expanding it should
# be a deliberate act that updates this docstring too.
_PY39_COMPAT_FILES = (
    REPO_ROOT / "hermes_constants.py",
    REPO_ROOT / "skills/productivity/google-workspace/scripts/setup.py",
    REPO_ROOT / "skills/productivity/google-workspace/scripts/google_api.py",
)


def _has_future_annotations_import(path: Path) -> bool:
    """Return True iff the module starts with ``from __future__ import annotations``.

    Uses ``ast`` rather than a substring scan — a string literal mentioning
    the pragma inside a docstring would otherwise pass the check.
    """
    tree = ast.parse(path.read_text())
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.module == "__future__":
            if any(alias.name == "annotations" for alias in node.names):
                return True
        # ``from __future__`` imports must precede all non-docstring,
        # non-import statements.  A missing import by the time we hit real
        # code means the file is not protected.
        if isinstance(node, (ast.Expr,)) and isinstance(node.value, ast.Constant):
            # Module docstring — keep looking.
            continue
        if isinstance(node, ast.ImportFrom) and node.module != "__future__":
            return False
        if isinstance(node, ast.Import):
            return False
    return False


@pytest.mark.parametrize("path", _PY39_COMPAT_FILES, ids=lambda p: p.name)
def test_file_has_future_annotations_import(path: Path) -> None:
    assert path.exists(), f"missing file: {path}"
    assert _has_future_annotations_import(path), (
        f"{path.relative_to(REPO_ROOT)} dropped ``from __future__ import "
        f"annotations``.  Python 3.9 users will hit TypeError on any PEP 604 "
        f"union at module-load time — see #14688."
    )


@pytest.mark.parametrize("path", _PY39_COMPAT_FILES, ids=lambda p: p.name)
def test_file_uses_pep_604_unions(path: Path) -> None:
    """The future-import guard is only meaningful if the file actually uses
    ``X | None`` style unions.  If every annotation is plain, the guard would
    just be cosmetic and we'd prefer to know — update the pinned list above.
    """
    src = path.read_text()
    # Syntactic check: any ``| None`` or ``| str`` etc. in annotation-ish
    # context.  A dumb substring scan is fine here — we just want to confirm
    # the module has SOMETHING worth deferring.  This prevents the pinned
    # list from going stale if someone removes all unions from a file.
    has_union_annotation = any(
        token in src
        for token in (" | None", " | str", " | Path", " | dict", " | list", " | int", " | bool")
    )
    assert has_union_annotation, (
        f"{path.relative_to(REPO_ROOT)} no longer uses PEP 604 unions.  The "
        f"future-annotations pragma is still harmless, but the pinned list "
        f"in this test should be re-evaluated — does this file still need "
        f"Py3.9 compat?"
    )


def test_hermes_constants_module_loads_and_behaves() -> None:
    """End-to-end smoke: import the module and exercise the functions that
    previously crashed at import time under Python 3.9.  Under 3.11 this
    mostly proves we didn't break anything — the real 3.9 check is
    ``test_file_has_future_annotations_import`` above, since CI does not
    have a 3.9 runner.
    """
    import hermes_constants

    # get_optional_skills_dir has the annotation from the original #14688
    # traceback (``Path | None``).  It must still be callable with and
    # without an argument.
    result_none = hermes_constants.get_optional_skills_dir()
    assert isinstance(result_none, Path)

    override = Path("/tmp/hermes-test-skills")
    result_override = hermes_constants.get_optional_skills_dir(default=override)
    assert result_override == override

    # parse_reasoning_effort also had a ``dict | None`` return annotation.
    assert hermes_constants.parse_reasoning_effort("") is None
    assert hermes_constants.parse_reasoning_effort("none") == {"enabled": False}
    assert hermes_constants.parse_reasoning_effort("high") == {
        "enabled": True,
        "effort": "high",
    }


@pytest.mark.parametrize("path", _PY39_COMPAT_FILES, ids=lambda p: p.name)
def test_file_parses_as_python_3_9(path: Path) -> None:
    """Each guarded file must parse under Python 3.9 grammar.

    ``ast.parse(..., feature_version=(3, 9))`` tells the Python parser to
    reject syntax introduced after 3.9 — match/case, ``except*``, PEP 695
    type params, etc.  Without this, using ``py_compile`` under the CI
    interpreter (3.11) would pass even for files that crash on a macOS
    system ``python3`` (still 3.9) — which is exactly the class of bug
    #14688 reported.

    Note: PEP 604 ``X | None`` in an annotation is *valid 3.9 grammar* —
    the crash is at runtime when the annotation is evaluated.  So this
    check is intentionally paired with ``test_file_has_future_annotations_import``:
    the parser guards against Py3.10+ syntax that the pragma can't save,
    the AST import-check guards against the pragma being removed.

    Credit: @Copilot flagged on #15158 that the prior ``py_compile``-only
    version gave a false sense of 3.9 safety.
    """
    source = path.read_text()
    try:
        ast.parse(source, filename=str(path), feature_version=(3, 9))
    except SyntaxError as e:
        pytest.fail(
            f"{path.relative_to(REPO_ROOT)} is not valid Python 3.9 syntax "
            f"(would crash on macOS /usr/bin/python3): {e}"
        )
