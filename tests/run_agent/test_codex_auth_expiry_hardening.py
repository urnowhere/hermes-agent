from unittest.mock import MagicMock, patch

from run_agent import AIAgent


def _make_agent():
    with (
        patch("run_agent.get_tool_definitions", return_value=[]),
        patch("run_agent.check_toolset_requirements", return_value={}),
        patch("run_agent.OpenAI"),
    ):
        agent = AIAgent(
            api_key="test-key",
            base_url="https://chatgpt.com/backend-api/codex",
            provider="openai-codex",
            model="gpt-5.4",
            quiet_mode=True,
            skip_context_files=True,
            skip_memory=True,
        )
        agent.client = MagicMock()
        return agent


def test_summarize_api_error_recognizes_codex_token_expired_payload():
    agent = _make_agent()

    err = Exception("401 - {'error': {'message': 'Provided authentication token is expired. Please try signing in again.', 'type': None, 'code': 'token_expired', 'param': None}, 'status': 401}")

    summary = agent._summarize_api_error(err)

    assert "Codex token expired" in summary
    assert "sign in again" in summary.lower()


def test_codex_auth_hint_prefers_login_command():
    agent = _make_agent()

    hint = agent._auth_recovery_hint(
        provider="openai-codex",
        status_code=401,
        summarized_error="Codex token expired — Provided authentication token is expired. Please sign in again.",
    )

    assert hint is not None
    assert "hermes login --provider openai-codex" in hint
    assert "gateway restart" in hint


def test_auth_recovery_hint_for_generic_codex_401_mentions_reauth():
    agent = _make_agent()

    hint = agent._auth_recovery_hint(
        provider="openai-codex",
        status_code=401,
        summarized_error="HTTP 401: unauthorized",
    )

    assert hint is not None
    assert "hermes login --provider openai-codex" in hint


def test_emit_status_includes_codex_reauth_hint_on_auth_failure():
    agent = _make_agent()
    messages = []
    agent.status_callback = lambda kind, message: messages.append((kind, message))

    hint = agent._auth_recovery_hint(
        provider="openai-codex",
        status_code=401,
        summarized_error="Codex token expired — Provided authentication token is expired. Please sign in again.",
    )
    agent._emit_status(f"💡 {hint}")

    assert messages
    assert messages[-1][0] == "lifecycle"
    assert "hermes login --provider openai-codex" in messages[-1][1]
