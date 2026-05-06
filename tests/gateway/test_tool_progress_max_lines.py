import pytest

from gateway.run import (
    _append_tool_progress_line,
    _resolve_tool_progress_max_lines,
    _trim_tool_progress_lines,
)


def test_tool_progress_trim_keeps_latest_five_lines():
    lines = [f"tool-{i}" for i in range(1, 11)]

    assert _trim_tool_progress_lines(lines, 5) == [
        "tool-6",
        "tool-7",
        "tool-8",
        "tool-9",
        "tool-10",
    ]


def test_tool_progress_trim_leaves_lines_untouched_when_limit_disabled():
    lines = ["tool-1", "tool-2"]

    assert _trim_tool_progress_lines(lines, 0) == lines
    assert _trim_tool_progress_lines(lines, None) == lines


def test_tool_progress_max_lines_prefers_platform_override():
    config = {
        "display": {
            "tool_progress_max_lines": 9,
            "platforms": {
                "telegram": {"tool_progress_max_lines": 5},
            },
        }
    }

    assert _resolve_tool_progress_max_lines(config, "telegram") == 5


def test_tool_progress_max_lines_defaults_to_disabled_for_invalid_values():
    config = {"display": {"platforms": {"telegram": {"tool_progress_max_lines": "junk"}}}}

    assert _resolve_tool_progress_max_lines(config, "telegram") == 0


@pytest.mark.parametrize(
    ("mode", "rendered_lines"),
    [
        ("new", ["🔎 search: \"alpha\"", "📖 read: \"beta\"", "✏️ write: \"gamma\""]),
        ("all", ["🔎 search: \"alpha\"", "🔎 search: \"beta\"", "✏️ write: \"gamma\""]),
        (
            "verbose",
            [
                "🔎 search(['query'])\n{\"query\": \"alpha\"}",
                "📖 read(['path'])\n{\"path\": \"beta\"}",
                "✏️ write(['path'])\n{\"path\": \"gamma\"}",
            ],
        ),
    ],
)
def test_tool_progress_max_lines_caps_rendered_line_sets_for_progress_modes(mode, rendered_lines):
    progress_lines = []

    for rendered in rendered_lines:
        _append_tool_progress_line(progress_lines, rendered, max_lines=2)

    assert progress_lines == rendered_lines[-2:], mode
