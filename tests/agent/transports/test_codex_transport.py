"""Tests for the ResponsesApiTransport (Codex)."""

import json
import pytest
from types import SimpleNamespace

from agent.transports import get_transport
from agent.transports.types import NormalizedResponse, ToolCall


@pytest.fixture
def transport():
    import agent.transports.codex  # noqa: F401
    return get_transport("codex_responses")


class TestCodexTransportBasic:

    def test_api_mode(self, transport):
        assert transport.api_mode == "codex_responses"

    def test_registered_on_import(self, transport):
        assert transport is not None

    def test_convert_tools(self, transport):
        tools = [{
            "type": "function",
            "function": {
                "name": "terminal",
                "description": "Run a command",
                "parameters": {"type": "object", "properties": {"command": {"type": "string"}}},
            }
        }]
        result = transport.convert_tools(tools)
        assert len(result) == 1
        assert result[0]["type"] == "function"
        assert result[0]["name"] == "terminal"


class TestCodexBuildKwargs:

    def test_basic_kwargs(self, transport):
        messages = [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "Hello"},
        ]
        kw = transport.build_kwargs(
            model="gpt-5.4",
            messages=messages,
            tools=[],
        )
        assert kw["model"] == "gpt-5.4"
        assert kw["instructions"] == "You are helpful."
        assert "input" in kw
        assert kw["store"] is False

    def test_system_extracted_from_messages(self, transport):
        messages = [
            {"role": "system", "content": "Custom system prompt"},
            {"role": "user", "content": "Hi"},
        ]
        kw = transport.build_kwargs(model="gpt-5.4", messages=messages, tools=[])
        assert kw["instructions"] == "Custom system prompt"

    def test_no_system_uses_default(self, transport):
        messages = [{"role": "user", "content": "Hi"}]
        kw = transport.build_kwargs(model="gpt-5.4", messages=messages, tools=[])
        assert kw["instructions"]  # should be non-empty default

    def test_reasoning_config(self, transport):
        messages = [{"role": "user", "content": "Hi"}]
        kw = transport.build_kwargs(
            model="gpt-5.4", messages=messages, tools=[],
            reasoning_config={"effort": "high"},
        )
        assert kw.get("reasoning", {}).get("effort") == "high"

    def test_reasoning_disabled(self, transport):
        messages = [{"role": "user", "content": "Hi"}]
        kw = transport.build_kwargs(
            model="gpt-5.4", messages=messages, tools=[],
            reasoning_config={"enabled": False},
        )
        assert "reasoning" not in kw or kw.get("include") == []

    def test_session_id_sets_cache_key(self, transport):
        messages = [{"role": "user", "content": "Hi"}]
        kw = transport.build_kwargs(
            model="gpt-5.4", messages=messages, tools=[],
            session_id="test-session-123",
        )
        assert kw.get("prompt_cache_key") == "test-session-123"

    def test_github_responses_no_cache_key(self, transport):
        messages = [{"role": "user", "content": "Hi"}]
        kw = transport.build_kwargs(
            model="gpt-5.4", messages=messages, tools=[],
            session_id="test-session",
            is_github_responses=True,
        )
        assert "prompt_cache_key" not in kw

    def test_max_tokens(self, transport):
        messages = [{"role": "user", "content": "Hi"}]
        kw = transport.build_kwargs(
            model="gpt-5.4", messages=messages, tools=[],
            max_tokens=4096,
        )
        assert kw.get("max_output_tokens") == 4096

    def test_codex_backend_no_max_output_tokens(self, transport):
        messages = [{"role": "user", "content": "Hi"}]
        kw = transport.build_kwargs(
            model="gpt-5.4", messages=messages, tools=[],
            max_tokens=4096,
            is_codex_backend=True,
        )
        assert "max_output_tokens" not in kw

    def test_xai_headers(self, transport):
        messages = [{"role": "user", "content": "Hi"}]
        kw = transport.build_kwargs(
            model="grok-3", messages=messages, tools=[],
            session_id="conv-123",
            is_xai_responses=True,
        )
        assert kw.get("extra_headers", {}).get("x-grok-conv-id") == "conv-123"

    def test_xai_headers_preserve_request_override_headers(self, transport):
        messages = [{"role": "user", "content": "Hi"}]
        kw = transport.build_kwargs(
            model="grok-3", messages=messages, tools=[],
            session_id="conv-123",
            is_xai_responses=True,
            request_overrides={"extra_headers": {"X-Test": "1", "X-Trace": "abc"}},
        )
        assert kw.get("extra_headers") == {
            "X-Test": "1",
            "X-Trace": "abc",
            "x-grok-conv-id": "conv-123",
        }

    def test_codex_native_web_search_live_adds_responses_tool(self, transport):
        messages = [{"role": "user", "content": "What happened today?"}]
        kw = transport.build_kwargs(
            model="gpt-5.4",
            messages=messages,
            tools=[],
            is_codex_backend=True,
            native_web_search_mode="live",
        )

        assert kw["tools"] == [
            {"type": "web_search", "external_web_access": True}
        ]
        assert "web_search_call.action.sources" in kw["include"]

    def test_codex_native_web_search_cached_adds_responses_tool(self, transport):
        messages = [{"role": "user", "content": "What did we already know?"}]
        kw = transport.build_kwargs(
            model="gpt-5.4",
            messages=messages,
            tools=[],
            is_codex_backend=True,
            native_web_search_mode="cached",
        )

        assert kw["tools"] == [
            {"type": "web_search", "external_web_access": False}
        ]
        assert "web_search_call.action.sources" in kw["include"]

    def test_codex_native_web_search_suppresses_managed_search_function(self, transport):
        messages = [{"role": "user", "content": "What happened today?"}]
        tools = [
            {
                "type": "function",
                "function": {
                    "name": "web_search",
                    "description": "Hermes managed web search",
                    "parameters": {"type": "object", "properties": {"query": {"type": "string"}}},
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "web_extract",
                    "description": "Hermes managed web extraction",
                    "parameters": {"type": "object", "properties": {"urls": {"type": "array"}}},
                },
            },
        ]

        kw = transport.build_kwargs(
            model="gpt-5.4",
            messages=messages,
            tools=tools,
            is_codex_backend=True,
            native_web_search_mode="live",
        )

        assert kw["tools"] == [
            {
                "type": "function",
                "name": "web_extract",
                "description": "Hermes managed web extraction",
                "strict": False,
                "parameters": {"type": "object", "properties": {"urls": {"type": "array"}}},
            },
            {"type": "web_search", "external_web_access": True},
        ]

    def test_codex_native_web_search_merges_sources_include_without_duplicates(self, transport):
        messages = [{"role": "user", "content": "Search"}]
        kw = transport.build_kwargs(
            model="gpt-5.4",
            messages=messages,
            tools=[],
            is_codex_backend=True,
            native_web_search_mode="live",
            request_overrides={"include": ["custom.include", "web_search_call.action.sources"]},
        )

        assert kw["include"] == ["custom.include", "web_search_call.action.sources"]

    def test_codex_native_web_search_only_for_codex_backend(self, transport):
        messages = [{"role": "user", "content": "Hi"}]
        kw = transport.build_kwargs(
            model="gpt-5.4",
            messages=messages,
            tools=[],
            native_web_search_mode="live",
        )

        assert kw["tools"] is None

    def test_preflight_allows_native_web_search_tool(self, transport):
        sanitized = transport.preflight_kwargs({
            "model": "gpt-5.4",
            "instructions": "You are helpful.",
            "input": [{"role": "user", "content": "Search the web"}],
            "tools": [{"type": "web_search", "external_web_access": True}],
            "store": False,
        })

        assert sanitized["tools"] == [
            {"type": "web_search", "external_web_access": True}
        ]

    def test_preflight_parses_native_web_search_false_strings(self, transport):
        sanitized = transport.preflight_kwargs({
            "model": "gpt-5.4",
            "instructions": "You are helpful.",
            "input": [{"role": "user", "content": "Search cached context"}],
            "tools": [{"type": "web_search", "external_web_access": "false"}],
            "store": False,
        })

        assert sanitized["tools"] == [
            {"type": "web_search", "external_web_access": False}
        ]

    def test_preflight_allows_native_web_search_preview_tool(self, transport):
        sanitized = transport.preflight_kwargs({
            "model": "gpt-5.4",
            "instructions": "You are helpful.",
            "input": [{"role": "user", "content": "Search preview"}],
            "tools": [{"type": "web_search_preview", "external_web_access": "true"}],
            "store": False,
        })

        assert sanitized["tools"] == [
            {"type": "web_search_preview", "external_web_access": True}
        ]

    def test_minimal_effort_clamped(self, transport):
        messages = [{"role": "user", "content": "Hi"}]
        kw = transport.build_kwargs(
            model="gpt-5.4", messages=messages, tools=[],
            reasoning_config={"effort": "minimal"},
        )
        # "minimal" should be clamped to "low"
        assert kw.get("reasoning", {}).get("effort") == "low"


class TestCodexValidateResponse:

    def test_none_response(self, transport):
        assert transport.validate_response(None) is False

    def test_empty_output(self, transport):
        r = SimpleNamespace(output=[], output_text=None)
        assert transport.validate_response(r) is False

    def test_valid_output(self, transport):
        r = SimpleNamespace(output=[{"type": "message", "content": []}])
        assert transport.validate_response(r) is True

    def test_output_text_fallback_not_valid(self, transport):
        """validate_response is strict — output_text doesn't make it valid.
        The caller handles output_text fallback with diagnostic logging."""
        r = SimpleNamespace(output=None, output_text="Some text")
        assert transport.validate_response(r) is False


class TestCodexMapFinishReason:

    def test_completed(self, transport):
        assert transport.map_finish_reason("completed") == "stop"

    def test_incomplete(self, transport):
        assert transport.map_finish_reason("incomplete") == "length"

    def test_failed(self, transport):
        assert transport.map_finish_reason("failed") == "stop"

    def test_unknown(self, transport):
        assert transport.map_finish_reason("unknown_status") == "stop"


class TestCodexNormalizeResponse:

    def test_text_response(self, transport):
        """Normalize a simple text Codex response."""
        r = SimpleNamespace(
            output=[
                SimpleNamespace(
                    type="message",
                    role="assistant",
                    content=[SimpleNamespace(type="output_text", text="Hello world")],
                    status="completed",
                ),
            ],
            status="completed",
            incomplete_details=None,
            usage=SimpleNamespace(input_tokens=10, output_tokens=5,
                                  input_tokens_details=None, output_tokens_details=None),
        )
        nr = transport.normalize_response(r)
        assert isinstance(nr, NormalizedResponse)
        assert nr.content == "Hello world"
        assert nr.finish_reason == "stop"

    def test_message_items_preserved_in_provider_data(self, transport):
        """Codex assistant message item ids/phases must survive transport normalization."""
        r = SimpleNamespace(
            output=[
                SimpleNamespace(
                    type="message",
                    role="assistant",
                    id="msg_abc",
                    phase="final_answer",
                    content=[SimpleNamespace(type="output_text", text="Hello world")],
                    status="completed",
                ),
            ],
            status="completed",
            incomplete_details=None,
            usage=SimpleNamespace(input_tokens=10, output_tokens=5,
                                  input_tokens_details=None, output_tokens_details=None),
        )
        nr = transport.normalize_response(r)
        assert nr.codex_message_items == [
            {
                "type": "message",
                "role": "assistant",
                "status": "completed",
                "content": [{"type": "output_text", "text": "Hello world"}],
                "id": "msg_abc",
                "phase": "final_answer",
            }
        ]

    def test_message_annotations_preserved_in_codex_message_items(self, transport):
        r = SimpleNamespace(
            output=[
                SimpleNamespace(
                    type="message",
                    status="completed",
                    content=[
                        SimpleNamespace(
                            type="output_text",
                            text="OpenAI announced updates.",
                            annotations=[
                                SimpleNamespace(
                                    type="url_citation",
                                    url="https://example.com/openai-updates",
                                    title="OpenAI updates",
                                    start_index=0,
                                    end_index=6,
                                )
                            ],
                        )
                    ],
                )
            ],
            status="completed",
            model="gpt-5.4",
        )

        nr = transport.normalize_response(r)

        assert nr.provider_data["codex_message_items"][0]["content"][0]["annotations"] == [
            {
                "type": "url_citation",
                "url": "https://example.com/openai-updates",
                "title": "OpenAI updates",
                "start_index": 0,
                "end_index": 6,
            }
        ]

    def test_web_search_call_items_preserved_in_provider_data(self, transport):
        """Native Codex web-search calls should survive normalization as trace data."""
        r = SimpleNamespace(
            output=[
                SimpleNamespace(
                    type="web_search_call",
                    id="ws_abc123",
                    status="completed",
                    action=SimpleNamespace(
                        type="search",
                        query="AI news this week",
                        queries=["AI news this week", "frontier AI May 2026"],
                    ),
                ),
                SimpleNamespace(
                    type="message",
                    role="assistant",
                    content=[SimpleNamespace(type="output_text", text="Found current AI news.")],
                    status="completed",
                ),
            ],
            status="completed",
            incomplete_details=None,
            usage=SimpleNamespace(input_tokens=10, output_tokens=5,
                                  input_tokens_details=None, output_tokens_details=None),
        )

        nr = transport.normalize_response(r)

        assert nr.codex_web_search_items == [
            {
                "type": "web_search_call",
                "id": "ws_abc123",
                "status": "completed",
                "action": {
                    "type": "search",
                    "query": "AI news this week",
                    "queries": ["AI news this week", "frontier AI May 2026"],
                },
            }
        ]
        assert nr.provider_data["codex_web_search_items"] == nr.codex_web_search_items

    def test_tool_call_response(self, transport):
        """Normalize a Codex response with tool calls."""
        r = SimpleNamespace(
            output=[
                SimpleNamespace(
                    type="function_call",
                    call_id="call_abc123",
                    name="terminal",
                    arguments=json.dumps({"command": "ls"}),
                    id="fc_abc123",
                    status="completed",
                ),
            ],
            status="completed",
            incomplete_details=None,
            usage=SimpleNamespace(input_tokens=10, output_tokens=20,
                                  input_tokens_details=None, output_tokens_details=None),
        )
        nr = transport.normalize_response(r)
        assert nr.finish_reason == "tool_calls"
        assert len(nr.tool_calls) == 1
        tc = nr.tool_calls[0]
        assert tc.name == "terminal"
        assert '"command"' in tc.arguments
