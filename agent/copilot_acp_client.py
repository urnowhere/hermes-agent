"""OpenAI-compatible shim that forwards Hermes requests to `copilot --acp`.

This adapter lets Hermes treat the GitHub Copilot ACP server as a chat-style
backend. Each request starts a short-lived ACP session, sends the formatted
conversation as a single prompt, collects text chunks, and converts the result
back into the minimal shape Hermes expects from an OpenAI client.
"""

from __future__ import annotations

import os
import shlex

from agent.acp_client_base import BaseACPClient

ACP_MARKER_BASE_URL = "acp://copilot"


class CopilotACPClient(BaseACPClient):
    """Minimal OpenAI-client-compatible facade for Copilot ACP."""

    _MARKER_BASE_URL = ACP_MARKER_BASE_URL
    _DEFAULT_API_KEY = "copilot-acp"
    _PROVIDER_NAME = "Copilot"

    @classmethod
    def _default_command(cls) -> str:
        return (
            os.getenv("HERMES_COPILOT_ACP_COMMAND", "").strip()
            or os.getenv("COPILOT_CLI_PATH", "").strip()
            or "copilot"
        )

    @classmethod
    def _default_args(cls) -> list[str]:
        raw = os.getenv("HERMES_COPILOT_ACP_ARGS", "").strip()
        return shlex.split(raw) if raw else ["--acp", "--stdio"]
