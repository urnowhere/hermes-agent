"""Regression tests for CLI prompt_toolkit key binding declarations."""

from __future__ import annotations

import ast
from pathlib import Path

from prompt_toolkit.key_binding import KeyBindings


REPO_ROOT = Path(__file__).resolve().parents[2]


def _literal_kb_add_sequences(path: Path) -> list[tuple[int, tuple[str, ...]]]:
    tree = ast.parse(path.read_text())
    sequences: list[tuple[int, tuple[str, ...]]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (
            isinstance(func, ast.Attribute)
            and func.attr == "add"
            and isinstance(func.value, ast.Name)
            and func.value.id == "kb"
        ):
            continue
        keys: list[str] = []
        for arg in node.args:
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                keys.append(arg.value)
            else:
                break
        else:
            if keys:
                sequences.append((node.lineno, tuple(keys)))
    return sequences


def test_cli_keybindings_are_valid_prompt_toolkit_keys():
    """Invalid key names break `hermes` startup before the input prompt appears."""
    sequences = _literal_kb_add_sequences(REPO_ROOT / "cli.py")
    assert sequences

    for lineno, sequence in sequences:
        kb = KeyBindings()
        try:
            decorator = kb.add(*sequence)
        except Exception as exc:  # pragma: no cover - assertion path includes details
            raise AssertionError(
                f"Invalid prompt_toolkit key binding at cli.py:{lineno}: {sequence!r}"
            ) from exc

        @decorator
        def _handler(event):  # pragma: no cover - never executed
            return None
