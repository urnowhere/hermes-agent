"""Tests for copilot-acp api_mode detection (#14437).

Without the fix, copilot-acp falls through to the default chat_completions
api_mode. The streaming path then attempts to iterate a SimpleNamespace
object returned by CopilotACPClient, causing a TypeError crash.
"""
import re
import pytest


def _extract_api_mode_chain(source: str) -> list[tuple[str, str]]:
    """Parse the api_mode if/elif/else chain from run_agent.py __init__."""
    # Find provider == "X" patterns paired with api_mode assignments
    results = []
    for match in re.finditer(
        r'self\.provider\s*==\s*"([^"]+)".*?self\.api_mode\s*=\s*"([^"]+)"',
        source,
        re.DOTALL,
    ):
        results.append((match.group(1), match.group(2)))
    return results


class TestCopilotACPApiMode:
    """copilot-acp provider must use codex_responses api_mode (#14437)."""

    def test_copilot_acp_in_api_mode_chain(self):
        """Verify that copilot-acp is explicitly handled before the else clause."""
        import inspect
        from run_agent import AIAgent
        source = inspect.getsource(AIAgent.__init__)
        chain = _extract_api_mode_chain(source)
        providers = {provider for provider, _ in chain}
        assert "copilot-acp" in providers, (
            "copilot-acp is not in the api_mode detection chain — "
            "will fall through to chat_completions and crash"
        )

    def test_copilot_acp_gets_codex_responses(self):
        """Verify copilot-acp is mapped to codex_responses, not chat_completions."""
        import inspect
        from run_agent import AIAgent
        source = inspect.getsource(AIAgent.__init__)
        chain = _extract_api_mode_chain(source)
        mode_map = dict(chain)
        assert mode_map.get("copilot-acp") == "codex_responses", (
            f"copilot-acp mapped to '{mode_map.get('copilot-acp', 'MISSING')}' "
            f"instead of 'codex_responses'"
        )
