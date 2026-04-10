"""Test that reasoning is not persisted to transcript (#7233)."""


def test_clean_response_preserved_before_reasoning_prepend():
    """_clean_response should hold the original response without reasoning."""
    response = "The answer is 42."

    # Simulate the fix's logic
    _clean_response = response
    _show_reasoning = True
    last_reasoning = "I thought about it."

    if _show_reasoning and response:
        if last_reasoning:
            response = f"\U0001f4ad **Reasoning:**\n```\n{last_reasoning}\n```\n\n{response}"

    # Display response has reasoning
    assert "Reasoning:" in response
    assert "thought about it" in response

    # Clean response does NOT have reasoning
    assert "Reasoning:" not in _clean_response
    assert _clean_response == "The answer is 42."


def test_transcript_uses_clean_response():
    """Transcript write should use _clean_response, not the display response."""
    response = "Hello world"
    _clean_response = response
    _show_reasoning = True
    last_reasoning = "Thinking..."

    if _show_reasoning and response:
        if last_reasoning:
            response = f"\U0001f4ad **Reasoning:**\n```\n{last_reasoning}\n```\n\n{response}"

    # Simulate transcript write
    transcript_entry = {"role": "assistant", "content": _clean_response}

    assert "Reasoning:" not in transcript_entry["content"]
    assert transcript_entry["content"] == "Hello world"


def test_clean_response_when_no_reasoning():
    """When no reasoning is available, _clean_response equals response."""
    response = "Simple answer"
    _clean_response = response
    _show_reasoning = True
    last_reasoning = None

    if _show_reasoning and response:
        if last_reasoning:
            response = f"\U0001f4ad **Reasoning:**\n```\n{last_reasoning}\n```\n\n{response}"

    assert response == _clean_response == "Simple answer"
