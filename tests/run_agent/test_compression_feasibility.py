"""Tests for _check_compression_model_feasibility() — warns when the
auxiliary compression model's context is smaller than the main model's
compression threshold.

Two-phase design:
  1. __init__  → runs the check, prints via _vprint (CLI), stores warning
  2. run_conversation (first call) → replays stored warning through
     status_callback (gateway platforms)
"""

from unittest.mock import MagicMock, patch

import pytest

from run_agent import AIAgent
from agent.context_compressor import ContextCompressor


def _make_agent(
    *,
    compression_enabled: bool = True,
    threshold_percent: float = 0.50,
    main_context: int = 200_000,
) -> AIAgent:
    """Build a minimal AIAgent with a compressor, skipping __init__."""
    agent = AIAgent.__new__(AIAgent)
    agent.model = "test-main-model"
    agent.provider = "openrouter"
    agent.base_url = "https://openrouter.ai/api/v1"
    agent.api_key = "sk-test"
    agent.api_mode = "chat_completions"
    agent.quiet_mode = True
    agent.log_prefix = ""
    agent.compression_enabled = compression_enabled
    agent._print_fn = None
    agent.suppress_status_output = False
    agent._stream_consumers = []
    agent._executing_tools = False
    agent._mute_post_response = False
    agent.status_callback = None
    agent.tool_progress_callback = None
    agent._compression_warning = None
    agent._aux_compression_context_length_config = None
    # Tools feed into the headroom calculation in _check_compression_model_feasibility.
    # Tests that want to assert specific threshold values can override this.
    agent.tools = []

    compressor = MagicMock(spec=ContextCompressor)
    compressor.context_length = main_context
    compressor.threshold_tokens = int(main_context * threshold_percent)
    compressor.threshold_percent = threshold_percent
    agent.context_compressor = compressor

    return agent


# ── Core warning logic ──────────────────────────────────────────────


@patch("agent.model_metadata.get_model_context_length")
@patch("agent.auxiliary_client.get_text_auxiliary_client")
def test_auto_corrects_threshold_when_aux_context_below_threshold(mock_get_client, mock_ctx_len):
    """Auto-correction: aux >= 64K floor but < threshold → lower threshold
    to aux_context so compression still works this session."""
    agent = _make_agent(main_context=200_000, threshold_percent=0.50)
    # threshold = 100,000 — aux has 80,000 (above 64K floor, below threshold)
    mock_client = MagicMock()
    mock_client.base_url = "https://openrouter.ai/api/v1"
    mock_client.api_key = "sk-aux"
    mock_get_client.return_value = (mock_client, "google/gemini-3-flash-preview")

    # First call: aux model context_length; second call: main model context_length
    # (threshold is re-derived from main model after aux is fetched)
    mock_ctx_len.side_effect = [80_000, 200_000]

    messages = []
    agent._emit_status = lambda msg: messages.append(msg)

    agent._check_compression_model_feasibility()

    assert len(messages) == 1
    assert "Compression model" in messages[0]
    assert "80,000" in messages[0]        # aux context
    assert "100,000" in messages[0]       # old threshold
    assert "Auto-lowered" in messages[0]
    # Actionable persistence guidance included
    assert "config.yaml" in messages[0]
    assert "auxiliary:" in messages[0]
    assert "compression:" in messages[0]
    assert "threshold:" in messages[0]
    # Warning stored for gateway replay
    assert agent._compression_warning is not None
    # Threshold on the live compressor was actually lowered, accounting for
    # the request-overhead headroom (empty tools list → ~12K headroom only).
    assert agent.context_compressor.threshold_tokens == 68_000


@patch("agent.model_metadata.get_model_context_length", return_value=32_768)
@patch("agent.auxiliary_client.get_text_auxiliary_client")
def test_rejects_aux_below_minimum_context(mock_get_client, mock_ctx_len):
    """Hard floor: aux context < MINIMUM_CONTEXT_LENGTH (64K) → session
    refuses to start (ValueError), mirroring the main-model rejection."""
    agent = _make_agent(main_context=200_000, threshold_percent=0.50)
    mock_client = MagicMock()
    mock_client.base_url = "https://openrouter.ai/api/v1"
    mock_client.api_key = "sk-aux"
    mock_get_client.return_value = (mock_client, "tiny-aux-model")

    agent._emit_status = lambda msg: None

    with pytest.raises(ValueError) as exc_info:
        agent._check_compression_model_feasibility()

    err = str(exc_info.value)
    assert "tiny-aux-model" in err
    assert "32,768" in err
    assert "64,000" in err
    assert "below the minimum" in err


@patch("agent.model_metadata.get_model_context_length", return_value=200_000)
@patch("agent.auxiliary_client.get_text_auxiliary_client")
def test_no_warning_when_aux_context_sufficient(mock_get_client, mock_ctx_len):
    """No warning when aux model context >= main model threshold."""
    agent = _make_agent(main_context=200_000, threshold_percent=0.50)
    # threshold = 100,000 — aux has 200,000 (sufficient)
    mock_client = MagicMock()
    mock_client.base_url = "https://openrouter.ai/api/v1"
    mock_client.api_key = "sk-aux"
    mock_get_client.return_value = (mock_client, "google/gemini-2.5-flash")

    messages = []
    agent._emit_status = lambda msg: messages.append(msg)

    agent._check_compression_model_feasibility()

    assert len(messages) == 0
    assert agent._compression_warning is None


def test_feasibility_check_passes_live_main_runtime():
    """Compression feasibility should probe using the live session runtime."""
    agent = _make_agent(main_context=200_000, threshold_percent=0.50)
    agent.model = "gpt-5.4"
    agent.provider = "openai-codex"
    agent.base_url = "https://chatgpt.com/backend-api/codex"
    agent.api_key = "codex-token"
    agent.api_mode = "codex_responses"

    mock_client = MagicMock()
    mock_client.base_url = "https://chatgpt.com/backend-api/codex"
    mock_client.api_key = "codex-token"

    with patch("agent.auxiliary_client.get_text_auxiliary_client", return_value=(mock_client, "gpt-5.4")) as mock_get_client, \
         patch("agent.model_metadata.get_model_context_length", return_value=200_000):
        agent._emit_status = lambda msg: None
        agent._check_compression_model_feasibility()

    mock_get_client.assert_called_once_with(
        "compression",
        main_runtime={
            "model": "gpt-5.4",
            "provider": "openai-codex",
            "base_url": "https://chatgpt.com/backend-api/codex",
            "api_key": "codex-token",
            "api_mode": "codex_responses",
        },
    )


@patch("agent.model_metadata.get_model_context_length")
@patch("agent.auxiliary_client.get_text_auxiliary_client")
def test_feasibility_check_passes_config_context_length(mock_get_client, mock_ctx_len):
    """auxiliary.compression.context_length from config is forwarded to
    get_model_context_length so custom endpoints that lack /models still
    report the correct context window (fixes #8499)."""
    agent = _make_agent(main_context=200_000, threshold_percent=0.85)
    agent._aux_compression_context_length_config = 1_000_000
    mock_client = MagicMock()
    mock_client.base_url = "http://custom-endpoint:8080/v1"
    mock_client.api_key = "sk-custom"
    mock_get_client.return_value = (mock_client, "custom/big-model")

    # First call: aux model context_length; second call: main model context_length
    mock_ctx_len.side_effect = [1_000_000, 200_000]

    agent._emit_status = lambda msg: None
    agent._check_compression_model_feasibility()

    # Verify the AUX model call (first) has the config_context_length override
    aux_call = mock_ctx_len.call_args_list[0]
    assert aux_call.kwargs["config_context_length"] == 1_000_000
    assert aux_call.args[0] == "custom/big-model"


@patch("agent.model_metadata.get_model_context_length")
@patch("agent.auxiliary_client.get_text_auxiliary_client")
def test_feasibility_check_ignores_invalid_context_length(mock_get_client, mock_ctx_len):
    """Non-integer context_length in config is silently ignored."""
    agent = _make_agent(main_context=200_000, threshold_percent=0.50)
    agent._aux_compression_context_length_config = None
    mock_client = MagicMock()
    mock_client.base_url = "http://custom:8080/v1"
    mock_client.api_key = "sk-test"
    mock_get_client.return_value = (mock_client, "custom/model")

    # First call: aux model context_length; second call: main model context_length
    mock_ctx_len.side_effect = [128_000, 200_000]

    agent._emit_status = lambda msg: None
    agent._check_compression_model_feasibility()

    # Verify the AUX model call (first) was made with config_context_length=None
    aux_call = mock_ctx_len.call_args_list[0]
    assert aux_call.kwargs["config_context_length"] is None
    assert aux_call.args[0] == "custom/model"


def test_init_feasibility_check_uses_aux_context_override_from_config():
    """Real AIAgent init should cache and forward auxiliary.compression.context_length."""

    class _StubCompressor:
        def __init__(self, *args, **kwargs):
            self.context_length = 200_000
            self.threshold_tokens = 100_000
            self.threshold_percent = 0.50

        def get_tool_schemas(self):
            return []

        def on_session_start(self, *args, **kwargs):
            return None

    cfg = {
        "auxiliary": {
            "compression": {
                "context_length": 1_000_000,
            },
        },
    }
    mock_client = MagicMock()
    mock_client.base_url = "http://custom-endpoint:8080/v1"
    mock_client.api_key = "sk-custom"

    with (
        patch("hermes_cli.config.load_config", return_value=cfg),
        patch("run_agent.get_tool_definitions", return_value=[]),
        patch("run_agent.check_toolset_requirements", return_value={}),
        patch("run_agent.OpenAI"),
        patch("run_agent.ContextCompressor", new=_StubCompressor),
        patch("agent.auxiliary_client.get_text_auxiliary_client", return_value=(mock_client, "custom/big-model")),
        patch("agent.model_metadata.get_model_context_length") as mock_ctx_len,
    ):
        # AIAgent.__init__ calls get_model_context_length multiple times:
        # 1. ContextCompressor.__init__ (main model) → returns 200_000
        # 2. _check_compression_model_feasibility: aux call → returns 1_000_000
        # 3. _check_compression_model_feasibility: main call → returns 200_000
        # We use a lambda to handle any number of calls gracefully.
        call_count = {"n": 0}
        def _side_effect(*args, **kwargs):
            call_count["n"] += 1
            n = call_count["n"]
            # First call: ContextCompressor init — main model, no override
            if n == 1:
                return ""
            # Second call: aux model (in _check_compression_model_feasibility)
            if n == 2:
                return 1_000_000
            # Third call: main model (re-derived threshold)
            return 200_000

        mock_ctx_len.side_effect = _side_effect
        agent = AIAgent(
            api_key="test-key-1234567890",
            base_url="https://openrouter.ai/api/v1",
            quiet_mode=True,
            skip_context_files=True,
            skip_memory=True,
        )

    assert agent._aux_compression_context_length_config == 1_000_000
    # Find the aux model call — it should have config_context_length=1_000_000
    # and first positional arg "custom/big-model"
    aux_calls = [
        c for c in mock_ctx_len.call_args_list
        if c.args and c.args[0] == "custom/big-model"
    ]
    assert len(aux_calls) >= 1
    assert aux_calls[0].kwargs["config_context_length"] == 1_000_000


@patch("agent.auxiliary_client.get_text_auxiliary_client")
def test_warns_when_no_auxiliary_provider(mock_get_client):
    """Warning emitted when no auxiliary provider is configured."""
    agent = _make_agent()
    mock_get_client.return_value = (None, None)

    messages = []
    agent._emit_status = lambda msg: messages.append(msg)

    agent._check_compression_model_feasibility()

    assert len(messages) == 1
    assert "No auxiliary LLM provider" in messages[0]
    assert agent._compression_warning is not None


def test_skips_check_when_compression_disabled():
    """No check performed when compression is disabled."""
    agent = _make_agent(compression_enabled=False)

    messages = []
    agent._emit_status = lambda msg: messages.append(msg)

    agent._check_compression_model_feasibility()

    assert len(messages) == 0
    assert agent._compression_warning is None


@patch("agent.auxiliary_client.get_text_auxiliary_client")
def test_exception_does_not_crash(mock_get_client):
    """Exceptions in the check are caught — never blocks startup."""
    agent = _make_agent()
    mock_get_client.side_effect = RuntimeError("boom")

    messages = []
    agent._emit_status = lambda msg: messages.append(msg)

    # Should not raise
    agent._check_compression_model_feasibility()

    # No user-facing message (error is debug-logged)
    assert len(messages) == 0


@patch("agent.model_metadata.get_model_context_length", return_value=100_000)
@patch("agent.auxiliary_client.get_text_auxiliary_client")
def test_exact_threshold_boundary_no_warning(mock_get_client, mock_ctx_len):
    """No warning when aux context exactly equals the threshold."""
    agent = _make_agent(main_context=200_000, threshold_percent=0.50)
    mock_client = MagicMock()
    mock_client.base_url = "https://openrouter.ai/api/v1"
    mock_client.api_key = "sk-aux"
    mock_get_client.return_value = (mock_client, "test-model")

    messages = []
    agent._emit_status = lambda msg: messages.append(msg)

    agent._check_compression_model_feasibility()

    assert len(messages) == 0


@patch("agent.model_metadata.get_model_context_length")
@patch("agent.auxiliary_client.get_text_auxiliary_client")
def test_just_below_threshold_auto_corrects(mock_get_client, mock_ctx_len):
    """Auto-correct fires when aux context is one token below the threshold
    (and above the 64K hard floor)."""
    agent = _make_agent(main_context=200_000, threshold_percent=0.50)
    mock_client = MagicMock()
    mock_client.base_url = "https://openrouter.ai/api/v1"
    mock_client.api_key = "sk-aux"
    mock_get_client.return_value = (mock_client, "small-model")

    # First call: aux model context_length; second call: main model context_length
    mock_ctx_len.side_effect = [99_999, 200_000]

    messages = []
    agent._emit_status = lambda msg: messages.append(msg)

    agent._check_compression_model_feasibility()

    assert len(messages) == 1
    assert "small-model" in messages[0]
    assert "Auto-lowered" in messages[0]
    assert agent.context_compressor.threshold_tokens == 87_999


# ── Headroom for system prompt + tool schemas ────────────────────────


@patch("agent.model_metadata.get_model_context_length", return_value=128_000)
@patch("agent.auxiliary_client.get_text_auxiliary_client")
def test_auto_lowered_threshold_reserves_headroom_for_tools_and_system(mock_get_client, mock_ctx_len):
    """When aux context binds the threshold, new_threshold must leave room
    for the system prompt and tool schemas that auxiliary callers
    (compression summariser, flush_memories) prepend to the message list.

    Without headroom, a full-budget message window + ~25K system/tool
    overhead overflows the aux model with HTTP 400.  Regression guard for
    the flush_memories-on-busy-toolset overflow path.
    """
    # Main context 200K, threshold 70% = 140K.  Aux pins at 128K (below
    # threshold → triggers auto-correct).
    agent = _make_agent(main_context=200_000, threshold_percent=0.70)

    # Build a realistic tool schema load.
    agent.tools = [
        {
            "type": "function",
            "function": {
                "name": f"tool_{i}",
                "description": "x" * 200,
                "parameters": {"type": "object", "properties": {"arg": {"type": "string", "description": "y" * 120}}},
            },
        }
        for i in range(50)
    ]

    mock_client = MagicMock()
    mock_client.base_url = "https://openrouter.ai/api/v1"
    mock_client.api_key = "sk-aux"
    mock_get_client.return_value = (mock_client, "model-with-128k")

    agent._emit_status = lambda msg: None
    agent._check_compression_model_feasibility()

    new_threshold = agent.context_compressor.threshold_tokens

    # Must have strictly reserved headroom: new_threshold < aux_context.
    assert new_threshold < 128_000, (
        f"threshold {new_threshold} did not reserve headroom below aux=128,000 "
        f"— system prompt + tools would overflow the aux model"
    )
    # Must respect the 64K hard floor.
    from agent.model_metadata import MINIMUM_CONTEXT_LENGTH
    assert new_threshold >= MINIMUM_CONTEXT_LENGTH


@patch("agent.model_metadata.get_model_context_length", return_value=80_000)
@patch("agent.auxiliary_client.get_text_auxiliary_client")
def test_headroom_floors_at_minimum_context(mock_get_client, mock_ctx_len):
    """If headroom subtraction would push below 64K floor, clamp to 64K
    rather than refusing the session — the aux is still workable for a
    smaller message window.
    """
    # Aux at 80K, with enough tools to push headroom > 16K → naive subtract
    # would land at < 64K.  The max(..., MINIMUM_CONTEXT_LENGTH) clamp must
    # keep the session running.
    agent = _make_agent(main_context=200_000, threshold_percent=0.50)
    agent.tools = [
        {
            "type": "function",
            "function": {
                "name": f"tool_{i}",
                "description": "z" * 2_000,  # fat descriptions
                "parameters": {},
            },
        }
        for i in range(30)
    ]

    mock_client = MagicMock()
    mock_client.base_url = "https://openrouter.ai/api/v1"
    mock_client.api_key = "sk-aux"
    mock_get_client.return_value = (mock_client, "small-aux-model")

    agent._emit_status = lambda msg: None
    agent._check_compression_model_feasibility()

    from agent.model_metadata import MINIMUM_CONTEXT_LENGTH
    assert agent.context_compressor.threshold_tokens == MINIMUM_CONTEXT_LENGTH


# ── Two-phase: __init__ + run_conversation replay ───────────────────


@patch("agent.model_metadata.get_model_context_length")
@patch("agent.auxiliary_client.get_text_auxiliary_client")
def test_warning_stored_for_gateway_replay(mock_get_client, mock_ctx_len):
    """__init__ stores the warning; _replay sends it through status_callback."""
    agent = _make_agent(main_context=200_000, threshold_percent=0.50)
    mock_client = MagicMock()
    mock_client.base_url = "https://openrouter.ai/api/v1"
    mock_client.api_key = "sk-aux"
    mock_get_client.return_value = (mock_client, "google/gemini-3-flash-preview")

    # First call: aux model context_length; second call: main model context_length
    mock_ctx_len.side_effect = [80_000, 200_000]

    # Phase 1: __init__ — _emit_status prints (CLI) but callback is None
    vprint_messages = []
    agent._emit_status = lambda msg: vprint_messages.append(msg)
    agent._check_compression_model_feasibility()

    assert len(vprint_messages) == 1  # CLI got it
    assert agent._compression_warning is not None  # stored for replay

    # Phase 2: gateway wires callback post-init, then run_conversation replays
    callback_events = []
    agent.status_callback = lambda ev, msg: callback_events.append((ev, msg))
    agent._replay_compression_warning()

    assert any(
        ev == "lifecycle" and "Auto-lowered" in msg
        for ev, msg in callback_events
    )


@patch("agent.model_metadata.get_model_context_length", return_value=200_000)
@patch("agent.auxiliary_client.get_text_auxiliary_client")
def test_no_replay_when_no_warning(mock_get_client, mock_ctx_len):
    """_replay_compression_warning is a no-op when there's no stored warning."""
    agent = _make_agent(main_context=200_000, threshold_percent=0.50)
    mock_client = MagicMock()
    mock_client.base_url = "https://openrouter.ai/api/v1"
    mock_client.api_key = "sk-aux"
    mock_get_client.return_value = (mock_client, "big-model")

    agent._emit_status = lambda msg: None
    agent._check_compression_model_feasibility()

    assert agent._compression_warning is None

    callback_events = []
    agent.status_callback = lambda ev, msg: callback_events.append((ev, msg))
    agent._replay_compression_warning()

    assert len(callback_events) == 0


def test_replay_without_callback_is_noop():
    """_replay_compression_warning doesn't crash when status_callback is None."""
    agent = _make_agent()
    agent._compression_warning = "some warning"
    agent.status_callback = None

    # Should not raise
    agent._replay_compression_warning()


@patch("agent.model_metadata.get_model_context_length")
@patch("agent.auxiliary_client.get_text_auxiliary_client")
def test_run_conversation_clears_warning_after_replay(mock_get_client, mock_ctx_len):
    """After replay in run_conversation, _compression_warning is cleared
    so the warning is not sent again on subsequent turns."""
    agent = _make_agent(main_context=200_000, threshold_percent=0.50)
    mock_client = MagicMock()
    mock_client.base_url = "https://openrouter.ai/api/v1"
    mock_client.api_key = "sk-aux"
    mock_get_client.return_value = (mock_client, "small-model")

    # First call: aux model context_length; second call: main model context_length
    mock_ctx_len.side_effect = [80_000, 200_000]

    agent._emit_status = lambda msg: None
    agent._check_compression_model_feasibility()

    assert agent._compression_warning is not None

    # Simulate what run_conversation does
    callback_events = []
    agent.status_callback = lambda ev, msg: callback_events.append((ev, msg))
    if agent._compression_warning:
        agent._replay_compression_warning()
        agent._compression_warning = None  # as in run_conversation

    assert len(callback_events) == 1

    # Second turn — nothing replayed
    callback_events.clear()
    if agent._compression_warning:
        agent._replay_compression_warning()
        agent._compression_warning = None

    assert len(callback_events) == 0


# ── Bug #12977: custom_providers context_length not propagated ──────
#
# The bug: when custom_providers provides a context_length for the main model,
# _check_compression_model_feasibility did not propagate it to the aux model's
# get_model_context_length call when the aux model is the same as the main model
# (fallback scenario).  Additionally, self._config_context_length was never
# updated with the custom_providers value, so the threshold re-derivation
# (added in the fix) would also compute the wrong threshold.
#
# Three-part fix:
#   1. Update self._config_context_length after custom_providers resolution
#      (run_agent.py __init__ line ~1622)
#   2. When aux model matches main model (same name + base_url), fall back to
#      self._config_context_length for the aux get_model_context_length call
#      (run_agent.py _check_compression_model_feasibility)
#   3. Re-derive threshold from get_model_context_length instead of reading
#      the potentially-stale threshold_tokens from the compressor


@patch("agent.model_metadata.get_model_context_length")
@patch("agent.auxiliary_client.get_text_auxiliary_client")
def test_aux_gets_custom_provider_context_when_aux_matches_main(
    mock_get_client, mock_ctx_len,
):
    """When the aux compression model is the same as the main model and
    custom_providers provides a context_length override, the feasibility check
    must propagate that override to the aux model's context query.

    Scenario (from issue #12977):
      - custom_providers.models.glm-5.1.context_length: 200000
      - No separate aux compression model → falls back to main model
      - compression threshold: 0.65 → correct threshold = 130_000
      - Built-in default for the model would be 128_000

    Without fix: aux_context = 128_000 (config_context_length=None)
                 threshold = 130_000 (correctly from compressor)
                 128_000 < 130_000 → false warning + auto-lower

    With fix:    aux_context = 200_000 (config_context_length propagated)
                 threshold = 130_000 (re-derived correctly)
                 200_000 >= 130_000 → no warning (correct)
    """
    # Compressor was correctly initialised with 200K from custom_providers
    agent = _make_agent(main_context=200_000, threshold_percent=0.65)
    agent._config_context_length = 200_000

    # Aux model = main model (the fallback scenario from the issue)
    mock_client = MagicMock()
    mock_client.base_url = "https://openrouter.ai/api/v1"
    mock_client.api_key = "sk-test"
    mock_get_client.return_value = (mock_client, "test-main-model")

    # Mock behaviour: return the override when config_context_length is given,
    # otherwise return the built-in default (128K) — simulates what
    # get_model_context_length does without the fix.
    def _ctx_len(model, base_url="", api_key="", config_context_length=None, provider=""):
        if config_context_length is not None:
            return config_context_length
        return 128_000  # built-in default (wrong for this custom_provider model)

    mock_ctx_len.side_effect = _ctx_len

    messages = []
    agent._emit_status = lambda msg: messages.append(msg)

    agent._check_compression_model_feasibility()

    assert len(messages) == 0, (
        f"Expected no warning with custom_providers context_length=200K and "
        f"threshold=130K, but got: {messages}"
    )
    assert agent._compression_warning is None


@patch("agent.model_metadata.get_model_context_length")
@patch("agent.auxiliary_client.get_text_auxiliary_client")
def test_different_aux_model_does_not_get_main_config_override(
    mock_get_client, mock_ctx_len,
):
    """When the aux compression model is DIFFERENT from the main model,
    the main model's config_context_length must NOT be applied to the aux
    call.  Only when aux_model == self.model AND base_urls match should
    the fallback activate.
    """
    agent = _make_agent(main_context=200_000, threshold_percent=0.50)
    agent._config_context_length = 1_000_000  # main model's custom override

    # Aux model is DIFFERENT from main model
    mock_client = MagicMock()
    mock_client.base_url = "https://openrouter.ai/api/v1"
    mock_client.api_key = "sk-aux"
    mock_get_client.return_value = (mock_client, "different-aux-model")

    # The mock should be called with config_context_length=None for the aux
    # call (since the models differ) and config_context_length=1_000_000 for
    # the main model call.
    call_log = []

    def _ctx_len(model, base_url="", api_key="", config_context_length=None, provider=""):
        call_log.append({"model": model, "config_context_length": config_context_length})
        if config_context_length is not None:
            return config_context_length
        return 200_000  # aux model's own real context

    mock_ctx_len.side_effect = _ctx_len

    messages = []
    agent._emit_status = lambda msg: messages.append(msg)

    agent._check_compression_model_feasibility()

    # First call: aux model — should NOT get the main model's override
    aux_call = call_log[0]
    assert aux_call["model"] == "different-aux-model"
    assert aux_call["config_context_length"] is None, (
        "Aux model should NOT receive main model's config_context_length "
        "when models differ"
    )

    # Second call: main model — should get the override for threshold derivation
    main_call = call_log[1]
    assert main_call["config_context_length"] == 1_000_000, (
        "Main model threshold derivation should use config_context_length"
    )


@patch("agent.model_metadata.get_model_context_length")
@patch("agent.auxiliary_client.get_text_auxiliary_client")
def test_false_positive_warning_eliminated_by_custom_provider_propagation(
    mock_get_client, mock_ctx_len,
):
    """Regression test for the exact false-positive scenario from issue #12977.

    User configured custom_providers with context_length: 200000, compression
    threshold 0.65.  The old code queried the aux model (which IS the main
    model) with config_context_length=None, getting 128_000 back.  The
    compressor's threshold_tokens was correctly 130_000 (200K * 0.65), so
    128_000 < 130_000 triggered a false warning and unnecessary auto-lowering.

    The fix propagates the custom_providers context_length to the aux call
    when the aux model matches the main model, so aux_context = 200_000.
    """
    agent = _make_agent(main_context=200_000, threshold_percent=0.65)
    agent._config_context_length = 200_000

    mock_client = MagicMock()
    mock_client.base_url = "https://openrouter.ai/api/v1"
    mock_client.api_key = "sk-test"
    mock_get_client.return_value = (mock_client, "test-main-model")

    def _ctx_len(model, base_url="", api_key="", config_context_length=None, provider=""):
        if config_context_length is not None:
            return config_context_length
        return 128_000  # built-in default — the WRONG value

    mock_ctx_len.side_effect = _ctx_len

    messages = []
    agent._emit_status = lambda msg: messages.append(msg)

    agent._check_compression_model_feasibility()

    # The compressor's threshold_tokens was NOT auto-lowered
    assert agent.context_compressor.threshold_tokens == 130_000, (
        f"threshold_tokens should remain 130K (200K*0.65), "
        f"got {agent.context_compressor.threshold_tokens}"
    )
    assert agent._compression_warning is None, (
        f"Should not warn: aux has 200K >= threshold 130K, "
        f"but got: {agent._compression_warning}"
    )
