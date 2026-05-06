"""Tests that switch_model preserves config_context_length."""

from unittest.mock import MagicMock, patch

from run_agent import AIAgent
from agent.context_compressor import ContextCompressor


def _make_agent_with_compressor(config_context_length=None) -> AIAgent:
    """Build a minimal AIAgent with a context_compressor, skipping __init__."""
    agent = AIAgent.__new__(AIAgent)

    # Primary model settings
    agent.model = "primary-model"
    agent.provider = "openrouter"
    agent.base_url = "https://openrouter.ai/api/v1"
    agent.api_key = "sk-primary"
    agent.api_mode = "chat_completions"
    agent.client = MagicMock()
    agent.quiet_mode = True

    # Store config_context_length for later use in switch_model
    agent._config_context_length = config_context_length

    # Context compressor with primary model values
    compressor = ContextCompressor(
        model="primary-model",
        threshold_percent=0.50,
        base_url="https://openrouter.ai/api/v1",
        api_key="sk-primary",
        provider="openrouter",
        quiet_mode=True,
        config_context_length=config_context_length,
    )
    agent.context_compressor = compressor

    # For switch_model
    agent._primary_runtime = {}

    return agent


@patch.object(AIAgent, "_check_compression_model_feasibility", return_value=None)
@patch("agent.context_compressor.get_model_context_length", return_value=1_050_000)
def test_init_reads_compression_threshold_tokens_override(mock_ctx_len, _mock_feasibility):
    """config.yaml compression.threshold_tokens should override the percentage threshold."""
    cfg = {
        "compression": {
            "threshold": 0.40,
            "threshold_tokens": 180_000,
        },
    }

    with (
        patch("hermes_cli.config.load_config", return_value=cfg),
        patch("run_agent.get_tool_definitions", return_value=[]),
        patch("run_agent.check_toolset_requirements", return_value={}),
        patch("run_agent.OpenAI"),
    ):
        agent = AIAgent(
            api_key="test-key-1234567890",
            base_url="https://chatgpt.com/backend-api/codex",
            provider="openai-codex",
            model="gpt-5.4",
            quiet_mode=True,
            skip_context_files=True,
            skip_memory=True,
        )

    assert agent._compression_threshold_tokens_config == 180_000
    assert agent.context_compressor.threshold_tokens_override == 180_000
    assert agent.context_compressor.threshold_tokens == 180_000


@patch("agent.model_metadata.get_model_context_length", return_value=131_072)
def test_switch_model_preserves_config_context_length(mock_ctx_len):
    """When switching models, config_context_length should be passed to get_model_context_length."""
    agent = _make_agent_with_compressor(config_context_length=32_768)

    assert agent.context_compressor.model == "primary-model"
    assert agent.context_compressor.context_length == 32_768  # From config override

    # Switch model
    agent.switch_model("new-model", "openrouter", api_key="sk-new", base_url="https://openrouter.ai/api/v1")

    # Verify get_model_context_length was called with config_context_length
    mock_ctx_len.assert_called_once()
    call_kwargs = mock_ctx_len.call_args.kwargs
    assert call_kwargs.get("config_context_length") == 32_768

    # Verify compressor was updated
    assert agent.context_compressor.model == "new-model"


def test_switch_model_without_config_context_length():
    """When switching models without config override, config_context_length should be None."""
    agent = _make_agent_with_compressor(config_context_length=None)

    with patch("agent.model_metadata.get_model_context_length", return_value=128_000) as mock_ctx_len:
        # Switch model
        agent.switch_model("new-model", "openrouter", api_key="sk-new", base_url="https://openrouter.ai/api/v1")

        # Verify get_model_context_length was called with None
        mock_ctx_len.assert_called_once()
        call_kwargs = mock_ctx_len.call_args.kwargs
        assert call_kwargs.get("config_context_length") is None


@patch("agent.model_metadata.get_model_context_length", return_value=200_000)
def test_switch_model_preserves_threshold_tokens_override(mock_ctx_len):
    """Absolute compression thresholds should survive switch_model()."""
    agent = _make_agent_with_compressor(config_context_length=None)
    agent.context_compressor = ContextCompressor(
        model="primary-model",
        threshold_percent=0.50,
        threshold_tokens_override=80_000,
        base_url="https://openrouter.ai/api/v1",
        api_key="sk-primary",
        provider="openrouter",
        quiet_mode=True,
    )

    agent.switch_model("new-model", "openrouter", api_key="sk-new", base_url="https://openrouter.ai/api/v1")

    assert agent.context_compressor.model == "new-model"
    assert agent.context_compressor.threshold_tokens_override == 80_000
    assert agent.context_compressor.threshold_tokens == 80_000
