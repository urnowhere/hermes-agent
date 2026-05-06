"""``_copy_reasoning_content_for_api`` step 3 promotion is gated to
providers that actually need it (DeepSeek/Kimi).

Step 3 promotes ``msg["reasoning"]`` → ``api_msg["reasoning_content"]``
for any assistant message that carries reasoning text in the unified
``reasoning`` field.  For Qwen-on-vLLM and other custom providers, this
re-feed violates the provider's multi-turn spec and causes:
  - context balloon across multi-turn conversations
  - thinking-mode death loops where the model re-thinks its own
    prior reasoning back at itself (Qwen's documented failure mode)

OpenRouter / Anthropic / OpenAI carry reasoning continuity via the
separate ``reasoning_details`` field (untouched by this helper), so
gating step 3 is safe — they never relied on this path.

DeepSeek / Kimi keep their existing behaviour because they are gated
in via ``_needs_kimi_tool_reasoning`` / ``_needs_deepseek_tool_reasoning``.
"""
from __future__ import annotations

from run_agent import AIAgent


def _make_agent(provider: str = "", model: str = "", base_url: str = "") -> AIAgent:
    agent = object.__new__(AIAgent)
    agent.provider = provider
    agent.model = model
    agent.base_url = base_url
    return agent


class TestStep3GatedToThinkingPadProviders:
    """The reasoning→reasoning_content promotion only runs for DeepSeek/Kimi."""

    def test_qwen_vllm_reasoning_not_promoted(self) -> None:
        """Qwen on a self-hosted vLLM endpoint must NOT get reasoning_content
        re-fed across turns — Qwen's multi-turn spec forbids it."""
        agent = _make_agent(
            provider="custom",
            model="qwen3.6-27b-fp8",
            base_url="http://vllm.local:8000/v1",
        )
        source = {
            "role": "assistant",
            "content": "final answer",
            "reasoning": "long Qwen <think> trace captured on the prior turn",
        }
        api_msg: dict = {}
        agent._copy_reasoning_content_for_api(source, api_msg)
        assert "reasoning_content" not in api_msg

    def test_openrouter_reasoning_not_promoted(self) -> None:
        """OpenRouter routes reasoning continuity via reasoning_details;
        the reasoning_content path is not used."""
        agent = _make_agent(
            provider="openrouter",
            model="anthropic/claude-sonnet-4.6",
            base_url="https://openrouter.ai/api/v1",
        )
        source = {
            "role": "assistant",
            "content": "answer",
            "reasoning": "any reasoning text",
        }
        api_msg: dict = {}
        agent._copy_reasoning_content_for_api(source, api_msg)
        assert "reasoning_content" not in api_msg

    def test_minimax_reasoning_not_promoted(self) -> None:
        """MiniMax was an early consumer of step 3 but never relied on
        reasoning_content for multi-turn correctness; it should be safe
        to drop now that DeepSeek/Kimi are the only confirmed users."""
        agent = _make_agent(
            provider="minimax",
            model="MiniMax-M1",
            base_url="https://api.minimax.chat/v1",
        )
        source = {
            "role": "assistant",
            "content": "answer",
            "reasoning": "minimax reasoning",
        }
        api_msg: dict = {}
        agent._copy_reasoning_content_for_api(source, api_msg)
        assert "reasoning_content" not in api_msg

    def test_deepseek_reasoning_still_promoted(self) -> None:
        """Regression check: DeepSeek must keep its reasoning re-feed
        because the chat completions API requires it on tool-call replay."""
        agent = _make_agent(provider="deepseek", model="deepseek-v4-flash")
        source = {
            "role": "assistant",
            "content": "answer",
            "reasoning": "deepseek thought trace",
        }
        api_msg: dict = {}
        agent._copy_reasoning_content_for_api(source, api_msg)
        assert api_msg.get("reasoning_content") == "deepseek thought trace"

    def test_kimi_reasoning_still_promoted(self) -> None:
        """Regression check: Kimi multi-turn requires the reasoning re-feed."""
        agent = _make_agent(
            provider="moonshot",
            model="kimi-k2-thinking",
            base_url="https://api.moonshot.cn/v1",
        )
        source = {
            "role": "assistant",
            "content": "answer",
            "reasoning": "kimi reasoning",
        }
        api_msg: dict = {}
        agent._copy_reasoning_content_for_api(source, api_msg)
        assert api_msg.get("reasoning_content") == "kimi reasoning"

    def test_explicit_reasoning_content_still_preserved(self) -> None:
        """Step 1 (explicit copy) is unaffected by the gate — explicit
        reasoning_content is always passed through verbatim, regardless
        of provider."""
        agent = _make_agent(
            provider="custom",
            model="qwen3.6-27b-fp8",
            base_url="http://vllm.local:8000/v1",
        )
        source = {
            "role": "assistant",
            "content": "answer",
            "reasoning_content": "explicit content set by build path",
        }
        api_msg: dict = {}
        agent._copy_reasoning_content_for_api(source, api_msg)
        assert api_msg["reasoning_content"] == "explicit content set by build path"
