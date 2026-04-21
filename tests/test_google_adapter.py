import json

from google.genai import types


def test_build_google_kwargs_uses_sdk_types_and_strips_google_prefix():
    from agent.google_adapter import build_google_kwargs

    kwargs = build_google_kwargs(
        model="google/gemini-2.5-flash",
        messages=[
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {
                            "name": "search",
                            "arguments": '{"q": "hermes"}',
                        },
                        "extra_content": {"google": {"thought_signature": "c2lnLTE="}},
                    }
                ],
            }
        ],
        tools=[
            {
                "type": "function",
                "function": {
                    "name": "search",
                    "description": "Search docs",
                    "parameters": {
                        "type": "object",
                        "properties": {"q": {"type": "string"}},
                        "required": ["q"],
                    },
                },
            }
        ],
        max_tokens=256,
    )

    assert kwargs["model"] == "gemini-2.5-flash"
    assert isinstance(kwargs["contents"][0], types.Content)
    assert kwargs["contents"][0].parts[0].thought_signature == b"sig-1"
    config = kwargs["config"]
    assert isinstance(config, types.GenerateContentConfig)
    assert config.tools[0].function_declarations[0].name == "search"
    assert config.max_output_tokens == 256


def test_normalize_google_response_keeps_tool_calls_and_usage():
    from agent.google_adapter import normalize_google_response

    response = types.GenerateContentResponse(
        candidates=[
            types.Candidate(
                content=types.Content(
                    role="model",
                    parts=[
                        types.Part(text="thinking", thought=True),
                        types.Part.from_function_call(name="search", args={"q": "hermes"}),
                    ],
                ),
                finishReason="STOP",
            )
        ],
        usageMetadata=types.GenerateContentResponseUsageMetadata(
            promptTokenCount=10,
            candidatesTokenCount=5,
            totalTokenCount=15,
        ),
    )

    normalized, finish_reason = normalize_google_response(response, "gemini-2.5-flash")
    assert finish_reason == "tool_calls"
    assert normalized.usage.prompt_tokens == 10
    assert normalized.usage.completion_tokens == 5
    assert normalized.choices[0].message.reasoning == "thinking"
    assert normalized.choices[0].message.tool_calls[0].function.name == "search"
    assert json.loads(normalized.choices[0].message.tool_calls[0].function.arguments) == {"q": "hermes"}
