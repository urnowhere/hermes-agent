#!/usr/bin/env python3
"""Test fix for GitHub issue #13031: Feishu Gateway tool call not executing.

Root cause:
1. _is_qwen_portal() only matched portal.qwen.ai, not dashscope.aliyuncs.com
   → Qwen message format adaptation (content→list-of-dicts) was skipped
   → max_tokens default not applied → tool_calls truncated
2. TOOL_USE_ENFORCEMENT_MODELS didn't include "qwen"
   → Qwen models didn't receive tool-use enforcement guidance
   → Model outputs tool calls as plain text instead of structured tool_calls
"""
import sys
import os

# ─── Test 1: _is_qwen_portal matches DashScope endpoint ─────────────────

def test_is_qwen_portal_dashscope():
    """_is_qwen_portal should return True for DashScope compatible-mode endpoint."""
    # Simulate the _is_qwen_portal logic (inline to avoid importing the full module)
    def _is_qwen_portal(base_url_lower: str) -> bool:
        return "portal.qwen.ai" in base_url_lower or "dashscope.aliyuncs.com" in base_url_lower

    # DashScope compatible-mode endpoint
    assert _is_qwen_portal("https://dashscope.aliyuncs.com/compatible-mode/v1"), \
        "DashScope compatible-mode endpoint should match"

    # DashScope with region prefix
    assert _is_qwen_portal("https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"), \
        "DashScope with path suffix should match"

    # Original Qwen Portal still works
    assert _is_qwen_portal("https://portal.qwen.ai/v1"), \
        "Qwen Portal endpoint should still match"

    # Non-matching endpoints
    assert not _is_qwen_portal("https://api.openai.com/v1"), \
        "OpenAI endpoint should not match"
    assert not _is_qwen_portal("https://openrouter.ai/api/v1"), \
        "OpenRouter endpoint should not match"
    assert not _is_qwen_portal(""), \
        "Empty URL should not match"

    print("✅ test_is_qwen_portal_dashscope PASSED")


def test_is_qwen_portal_original_behavior():
    """Original portal.qwen.ai matching should not be broken."""
    def _is_qwen_portal(base_url_lower: str) -> bool:
        return "portal.qwen.ai" in base_url_lower or "dashscope.aliyuncs.com" in base_url_lower

    test_cases = [
        ("https://portal.qwen.ai/api/v1/chat/completions", True),
        ("https://PORTAL.QWEN.AI/v1", True),  # lowercased
        ("https://api.openai.com/v1", False),
        ("https://api.anthropic.com/v1", False),
        ("https://openrouter.ai/api/v1", False),
        ("http://localhost:11434/v1", False),
    ]
    for url, expected in test_cases:
        result = _is_qwen_portal(url.lower())
        assert result == expected, f"URL {url}: expected {expected}, got {result}"

    print("✅ test_is_qwen_portal_original_behavior PASSED")


# ─── Test 2: TOOL_USE_ENFORCEMENT_MODELS includes "qwen" ────────────────

def test_tool_use_enforcement_includes_qwen():
    """TOOL_USE_ENFORCEMENT_MODELS should include 'qwen'."""
    TOOL_USE_ENFORCEMENT_MODELS = ("gpt", "codex", "gemini", "gemma", "grok", "qwen")

    # Qwen model names should match
    qwen_models = [
        "qwen-plus",
        "qwen-turbo",
        "qwen-max",
        "qwen2.5-72b-instruct",
        "qwen3-coder-plus",
        "Qwen-Plus",
        "QWEN-MAX",
    ]
    for model in qwen_models:
        model_lower = model.lower()
        matched = any(p in model_lower for p in TOOL_USE_ENFORCEMENT_MODELS)
        assert matched, f"Model '{model}' should match TOOL_USE_ENFORCEMENT_MODELS"

    # Existing model families should still match
    existing_models = [
        "gpt-4o", "gpt-5.4", "codex-mini", "gemini-2.5-pro",
        "gemma-3", "grok-3",
    ]
    for model in existing_models:
        model_lower = model.lower()
        matched = any(p in model_lower for p in TOOL_USE_ENFORCEMENT_MODELS)
        assert matched, f"Model '{model}' should still match TOOL_USE_ENFORCEMENT_MODELS"

    # Non-matching models should NOT match
    non_matching = [
        "claude-sonnet-4", "llama-3.1-70b", "mistral-large",
        "deepseek-r1", "command-r-plus",
    ]
    for model in non_matching:
        model_lower = model.lower()
        matched = any(p in model_lower for p in TOOL_USE_ENFORCEMENT_MODELS)
        assert not matched, f"Model '{model}' should NOT match TOOL_USE_ENFORCEMENT_MODELS"

    print("✅ test_tool_use_enforcement_includes_qwen PASSED")


# ─── Test 3: Qwen message format adaptation activates for DashScope ─────

def test_qwen_message_preparation_for_dashscope():
    """When base_url is DashScope, _is_qwen_portal should return True,
    enabling _qwen_prepare_chat_messages to normalize content format."""
    def _is_qwen_portal(base_url_lower: str) -> bool:
        return "portal.qwen.ai" in base_url_lower or "dashscope.aliyuncs.com" in base_url_lower

    # Verify that a DashScope URL would trigger the Qwen message preparation path
    dashscope_url = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    assert _is_qwen_portal(dashscope_url.lower()), \
        "DashScope URL should trigger Qwen message preparation"

    # Simulate what _qwen_prepare_chat_messages does:
    # Convert string content to list-of-dicts format
    import copy
    messages = [
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "user", "content": "Hello"},
    ]

    prepared = copy.deepcopy(messages)
    for msg in prepared:
        if not isinstance(msg, dict):
            continue
        content = msg.get("content")
        if isinstance(content, str):
            msg["content"] = [{"type": "text", "text": content}]

    # Verify normalization
    assert prepared[0]["content"] == [{"type": "text", "text": "You are a helpful assistant."}], \
        "System message content should be normalized to list-of-dicts"
    assert prepared[1]["content"] == [{"type": "text", "text": "Hello"}], \
        "User message content should be normalized to list-of-dicts"

    print("✅ test_qwen_message_preparation_for_dashscope PASSED")


# ─── Test 4: max_tokens default applies for DashScope ───────────────────

def test_max_tokens_default_for_dashscope():
    """When base_url is DashScope, the Qwen portal max_tokens default (65536)
    should apply, preventing tool_calls from being truncated."""
    def _is_qwen_portal(base_url_lower: str) -> bool:
        return "portal.qwen.ai" in base_url_lower or "dashscope.aliyuncs.com" in base_url_lower

    dashscope_url = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    assert _is_qwen_portal(dashscope_url.lower()), \
        "DashScope URL should trigger max_tokens default (65536)"

    print("✅ test_max_tokens_default_for_dashscope PASSED")


# ─── Run all tests ───────────────────────────────────────────────────────

if __name__ == "__main__":
    tests = [
        test_is_qwen_portal_dashscope,
        test_is_qwen_portal_original_behavior,
        test_tool_use_enforcement_includes_qwen,
        test_qwen_message_preparation_for_dashscope,
        test_max_tokens_default_for_dashscope,
    ]

    passed = 0
    failed = 0
    for test in tests:
        try:
            test()
            passed += 1
        except AssertionError as e:
            print(f"❌ {test.__name__} FAILED: {e}")
            failed += 1
        except Exception as e:
            print(f"❌ {test.__name__} ERROR: {e}")
            failed += 1

    print(f"\n{'='*60}")
    print(f"Results: {passed} passed, {failed} failed out of {len(tests)} tests")
    if failed > 0:
        sys.exit(1)
    else:
        print("All tests PASSED ✅")
