"""Tests for utils.atomic_jsonl_write — crash-safe JSONL file writes."""

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from utils import atomic_jsonl_write


def _read_lines(path: Path) -> list:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


class TestAtomicJsonlWrite:
    """Core atomic write behavior."""

    def test_writes_valid_jsonl(self, tmp_path):
        target = tmp_path / "lines.jsonl"
        items = [{"i": 1}, {"i": 2}, {"i": 3}]
        atomic_jsonl_write(target, items)

        assert _read_lines(target) == items

    def test_each_item_on_its_own_line(self, tmp_path):
        target = tmp_path / "lines.jsonl"
        atomic_jsonl_write(target, [{"a": 1}, {"b": 2}])

        lines = target.read_text(encoding="utf-8").splitlines()
        assert lines == ['{"a": 1}', '{"b": 2}']

    def test_creates_parent_directories(self, tmp_path):
        target = tmp_path / "deep" / "nested" / "dir" / "lines.jsonl"
        atomic_jsonl_write(target, [{"ok": True}])

        assert target.exists()
        assert _read_lines(target) == [{"ok": True}]

    def test_overwrites_existing_file(self, tmp_path):
        target = tmp_path / "lines.jsonl"
        target.write_text('{"old": true}\n', encoding="utf-8")

        atomic_jsonl_write(target, [{"new": True}])
        assert _read_lines(target) == [{"new": True}]

    def test_empty_iterable_produces_empty_file(self, tmp_path):
        target = tmp_path / "empty.jsonl"
        atomic_jsonl_write(target, [])

        assert target.exists()
        assert target.read_text(encoding="utf-8") == ""

    def test_preserves_original_on_serialization_error(self, tmp_path):
        target = tmp_path / "lines.jsonl"
        original = [{"preserved": True}]
        target.write_text(json.dumps(original[0]) + "\n", encoding="utf-8")

        with pytest.raises(TypeError):
            atomic_jsonl_write(target, [{"bad": object()}])

        assert _read_lines(target) == original

    def test_no_leftover_temp_files_on_success(self, tmp_path):
        target = tmp_path / "lines.jsonl"
        atomic_jsonl_write(target, [{"i": 1}])

        tmp_files = [f for f in tmp_path.iterdir() if ".tmp" in f.name]
        assert tmp_files == []
        assert target.exists()

    def test_no_leftover_temp_files_on_failure(self, tmp_path):
        target = tmp_path / "lines.jsonl"

        with pytest.raises(TypeError):
            atomic_jsonl_write(target, [{"bad": object()}])

        tmp_files = [f for f in tmp_path.iterdir() if ".tmp" in f.name]
        assert tmp_files == []

    def test_cleans_up_temp_file_on_baseexception(self, tmp_path):
        """KeyboardInterrupt/SystemExit must not leave stray .tmp files."""

        class SimulatedAbort(BaseException):
            pass

        target = tmp_path / "lines.jsonl"
        original = [{"preserved": True}]
        target.write_text(json.dumps(original[0]) + "\n", encoding="utf-8")

        with patch("utils.json.dumps", side_effect=SimulatedAbort):
            with pytest.raises(SimulatedAbort):
                atomic_jsonl_write(target, [{"new": True}])

        tmp_files = [f for f in tmp_path.iterdir() if ".tmp" in f.name]
        assert tmp_files == []
        assert _read_lines(target) == original

    def test_mid_write_failure_preserves_prior_file(self, tmp_path):
        """A crash after some lines are written must not clobber the target."""
        target = tmp_path / "lines.jsonl"
        original = [{"i": 0}, {"i": 1}]
        target.write_text(
            "\n".join(json.dumps(item) for item in original) + "\n",
            encoding="utf-8",
        )

        call_count = {"n": 0}
        real_dumps = json.dumps

        def fail_after_two(obj, **kwargs):
            call_count["n"] += 1
            if call_count["n"] > 2:
                raise IOError("simulated mid-write crash")
            return real_dumps(obj, **kwargs)

        with patch("utils.json.dumps", side_effect=fail_after_two):
            with pytest.raises(IOError):
                atomic_jsonl_write(target, [{"j": i} for i in range(5)])

        # Target file still holds the original content — tempfile discarded.
        assert _read_lines(target) == original
        tmp_files = [f for f in tmp_path.iterdir() if ".tmp" in f.name]
        assert tmp_files == []

    def test_accepts_string_path(self, tmp_path):
        target = str(tmp_path / "string_path.jsonl")
        atomic_jsonl_write(target, [{"string": True}])

        assert _read_lines(Path(target)) == [{"string": True}]

    def test_unicode_content(self, tmp_path):
        target = tmp_path / "unicode.jsonl"
        items = [{"emoji": "🎉"}, {"japanese": "日本語"}]
        atomic_jsonl_write(target, items)

        assert _read_lines(target) == items

    def test_accepts_generator_of_items(self, tmp_path):
        target = tmp_path / "gen.jsonl"
        atomic_jsonl_write(target, ({"i": i} for i in range(3)))

        assert _read_lines(target) == [{"i": 0}, {"i": 1}, {"i": 2}]

    def test_forwards_dump_kwargs(self, tmp_path):
        class CustomValue:
            def __str__(self):
                return "custom-value"

        target = tmp_path / "custom.jsonl"
        atomic_jsonl_write(target, [{"value": CustomValue()}], default=str)

        assert _read_lines(target) == [{"value": "custom-value"}]
