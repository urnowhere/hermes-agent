from agent.display import _detect_tool_failure


def test_detect_tool_failure_accepts_structured_tool_results():
    result = [
        {"type": "text", "text": "Image loaded from read_file."},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,QUFB", "detail": "auto"}},
    ]

    is_failure, suffix = _detect_tool_failure("read_file", result)

    assert is_failure is False
    assert suffix == ""
