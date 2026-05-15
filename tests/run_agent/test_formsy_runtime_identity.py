"""Regression tests for shared FormSy runtime identity plumbing."""

from unittest.mock import patch

from agent.context_engine import ContextEngine
from agent.memory_provider import MemoryProvider
from agent.runtime_identity import ResolvedIdentitySnapshot


class _CaptureEngine(ContextEngine):
    def __init__(self) -> None:
        self.session_snapshot = None

    @property
    def name(self) -> str:
        return "capture-engine"

    def update_from_response(self, usage):
        pass

    def should_compress(self, prompt_tokens=None):
        return False

    def compress(self, messages, current_tokens=None, focus_topic=None):
        return messages

    def on_session_start(self, session_id: str, **kwargs) -> None:
        self.session_snapshot = kwargs.get("runtime_identity_snapshot")


class _CaptureProvider(MemoryProvider):
    def __init__(self) -> None:
        self.init_snapshot = None
        self.turn_snapshots = []

    @property
    def name(self) -> str:
        return "capture-provider"

    def is_available(self) -> bool:
        return True

    def initialize(self, session_id: str, **kwargs) -> None:
        self.init_snapshot = kwargs.get("runtime_identity_snapshot")

    def get_tool_schemas(self):
        return []

    def on_turn_start(self, turn_number: int, message: str, **kwargs) -> None:
        self.turn_snapshots.append(kwargs.get("runtime_identity_snapshot"))


def test_agent_shares_same_runtime_identity_snapshot_with_context_and_memory():
    engine = _CaptureEngine()
    provider = _CaptureProvider()
    cfg = {
        "context": {"engine": "capture-engine"},
        "memory": {"provider": "capture-provider"},
        "formsy": {"workspace_id": "ws_formsy"},
        "agent": {},
    }

    with (
        patch("hermes_cli.config.load_config", return_value=cfg),
        patch("plugins.context_engine.load_context_engine", return_value=engine),
        patch("plugins.memory.load_memory_provider", return_value=provider),
        patch("agent.model_metadata.get_model_context_length", return_value=131_072),
        patch("run_agent.get_tool_definitions", return_value=[]),
        patch("run_agent.check_toolset_requirements", return_value={}),
        patch("run_agent.OpenAI"),
    ):
        from run_agent import AIAgent

        agent = AIAgent(
            api_key="test-key-1234567890",
            base_url="https://openrouter.ai/api/v1",
            quiet_mode=True,
            skip_context_files=True,
            skip_memory=False,
        )

    assert isinstance(agent._runtime_identity_snapshot, ResolvedIdentitySnapshot)
    assert provider.init_snapshot is agent._runtime_identity_snapshot
    assert engine.session_snapshot is agent._runtime_identity_snapshot
    assert agent._runtime_identity_snapshot.workspace_id == "ws_formsy"


def test_runtime_identity_snapshot_missing_user_id_does_not_fallback_to_session_id():
    cfg = {
        "formsy": {"workspace_id": "ws_formsy"},
        "agent": {},
    }

    with (
        patch("hermes_cli.config.load_config", return_value=cfg),
        patch("agent.model_metadata.get_model_context_length", return_value=131_072),
        patch("run_agent.get_tool_definitions", return_value=[]),
        patch("run_agent.check_toolset_requirements", return_value={}),
        patch("run_agent.OpenAI"),
    ):
        from run_agent import AIAgent

        agent = AIAgent(
            api_key="test-key-1234567890",
            base_url="https://openrouter.ai/api/v1",
            quiet_mode=True,
            skip_context_files=True,
            skip_memory=True,
            session_id="session-123",
        )

    assert agent._runtime_identity_snapshot.user_id is None
    assert "missing_user_id" in agent._runtime_identity_snapshot.limited_scope_flags
    assert agent._runtime_identity_snapshot.session_id == "session-123"
