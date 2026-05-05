"""Session management for the A2A server adapter.

Each A2A task maps to a Hermes session with its own AIAgent instance.
Thread-safe via threading.Lock, matching the ACP adapter pattern.
"""

import logging
import threading
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class SessionState:
    """State for a single A2A task/session."""

    session_id: str
    agent: Any  # AIAgent instance
    cwd: str = "."
    model: str = ""
    history: List[Dict[str, Any]] = field(default_factory=list)
    cancel_event: Any = None  # threading.Event


class SessionManager:
    """Thread-safe session lifecycle management for A2A tasks."""

    def __init__(self, toolset: str = "hermes-cli"):
        self._sessions: Dict[str, SessionState] = {}
        self._lock = threading.Lock()
        self._toolset = toolset

    def create_session(self, cwd: str = ".", model: str = "") -> SessionState:
        """Create a new session with a fresh AIAgent."""
        session_id = uuid.uuid4().hex
        cancel_event = threading.Event()
        agent = self._make_agent(
            session_id=session_id, cwd=cwd, model=model,
        )
        state = SessionState(
            session_id=session_id,
            agent=agent,
            cwd=cwd,
            model=model,
            cancel_event=cancel_event,
        )
        with self._lock:
            self._sessions[session_id] = state
        logger.info("Created A2A session %s", session_id)
        return state

    def get_session(self, session_id: str) -> Optional[SessionState]:
        """Thread-safe session lookup."""
        with self._lock:
            return self._sessions.get(session_id)

    def get_or_create_session(self, task_id: str, cwd: str = ".") -> SessionState:
        """Get existing session for a task or create a new one.

        Sessions are keyed by task_id only (not also by session_id) to
        avoid dual-storage where list_sessions() returns duplicates.
        """
        with self._lock:
            if task_id in self._sessions:
                return self._sessions[task_id]

        # Build session outside lock (_make_agent is slow)
        cancel_event = threading.Event()
        agent = self._make_agent(session_id=task_id, cwd=cwd)
        state = SessionState(
            session_id=task_id,
            agent=agent,
            cwd=cwd,
            cancel_event=cancel_event,
        )

        with self._lock:
            # Double-check: another thread may have created it while we were
            # building the agent (TOCTOU guard)
            if task_id in self._sessions:
                # Discard the agent we just built to avoid resource leaks
                logger.debug("Discarding duplicate agent for task %s (race)", task_id)
                return self._sessions[task_id]
            self._sessions[task_id] = state
        logger.info("Created A2A session for task %s", task_id)
        return state

    def cancel_session(self, session_id: str) -> bool:
        """Signal a session to cancel."""
        state = self.get_session(session_id)
        if state and state.cancel_event:
            state.cancel_event.set()
            logger.info("Cancelled A2A session %s", session_id)
            return True
        return False

    def list_sessions(self) -> List[dict]:
        """Return lightweight session info dicts."""
        with self._lock:
            return [
                {
                    "session_id": s.session_id,
                    "cwd": s.cwd,
                    "model": s.model,
                    "history_length": len(s.history),
                }
                for s in self._sessions.values()
            ]

    def _make_agent(self, *, session_id: str, cwd: str, model: str = "") -> Any:
        """Factory: create an AIAgent for a session."""
        from run_agent import AIAgent
        from hermes_cli.config import load_config

        config = load_config()
        model_cfg = config.get("model")
        default_model = model_cfg if model_cfg else "anthropic/claude-sonnet-4-20250514"

        kwargs = {
            "platform": "a2a",
            "enabled_toolsets": [self._toolset],
            "quiet_mode": True,
            "session_id": session_id,
            "model": model or default_model,
        }

        # Resolve runtime provider for API credentials
        try:
            from hermes_cli.runtime_provider import resolve_runtime_provider
            runtime = resolve_runtime_provider()
            kwargs.update({
                "provider": runtime.get("provider"),
                "api_mode": runtime.get("api_mode"),
                "base_url": runtime.get("base_url"),
                "api_key": runtime.get("api_key"),
            })
        except Exception as e:
            logger.warning("Failed to resolve runtime provider: %s", e)

        return AIAgent(**kwargs)
