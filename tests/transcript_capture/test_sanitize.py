
from agent.transcript_capture.sanitize import (
    drop_reasoning_fields,
    force_redact_text,
    sanitize_media,
    summarize_tool_event,
)


def test_force_redact_text_redacts_runtime_constructed_tokens():
    secret = "sk-" + "A" * 28
    github = "ghp_" + "B" * 28
    text = f"token={secret} github={github} Authorization: Bearer {secret}"
    out = force_redact_text(text)
    assert secret not in out
    assert github not in out
    assert "***" in out or "..." in out


def test_drop_reasoning_fields_recursively():
    data = {
        "content": "keep",
        "reasoning": "drop",
        "nested": {"thinking": "drop", "safe": "keep", "reasoning_details": ["drop"]},
        "items": [{"thought": "drop", "value": 3}],
    }
    out = drop_reasoning_fields(data)
    assert out == {"content": "keep", "nested": {"safe": "keep"}, "items": [{"value": 3}]}


def test_summarize_tool_event_omits_raw_args_and_results_by_default():
    summary = summarize_tool_event(
        {"tool_name": "terminal", "args": {"command": "echo secret"}, "result": "secret output", "tool_call_id": "abc", "timestamp": 1.2}
    )
    assert summary["role"] == "tool"
    assert summary["tool_name"] == "terminal"
    assert "args" not in summary
    assert "result" not in summary
    assert "secret output" not in str(summary)


def test_sanitize_media_strips_signed_query_strings():
    media = sanitize_media("https://files.example.com/path/image.png?X-Amz-Signature=abc&token=def")
    assert media["url"] == "https://files.example.com/path/image.png"
    assert media["host"] == "files.example.com"
    assert "Signature" not in str(media)
    assert "token" not in str(media)


def test_summarize_tool_event_never_accepts_raw_capture_escape_hatch():
    summary = summarize_tool_event({"tool_name": "terminal", "args": {"command": "echo secret"}, "result": "secret output"})
    assert "args" not in summary
    assert "result" not in summary
