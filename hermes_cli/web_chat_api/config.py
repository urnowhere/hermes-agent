"""
Hermes Chat UI -- Configuration and state management (adapted from Hermes WebUI).
Provides session state, stream management, and model resolution for chat UI.
"""
import os
import threading
from pathlib import Path
from typing import Dict, Any, Optional

# State directory for chat UI sessions
CHAT_STATE_DIR = Path(os.getenv("HERMES_WEBUI_STATE_DIR", str(Path.home() / ".hermes" / "webui")))
CHAT_SESSION_DIR = CHAT_STATE_DIR / "sessions"

# Ensure directories exist
CHAT_STATE_DIR.mkdir(parents=True, exist_ok=True)
CHAT_SESSION_DIR.mkdir(parents=True, exist_ok=True)

# Global state locks and containers
LOCK = threading.Lock()
STREAMS: Dict[str, Any] = {}
STREAMS_LOCK = threading.Lock()
CANCEL_FLAGS: Dict[str, threading.Event] = {}
COMPLETED_STREAMS: set = set()  # Track completed streams
AGENT_INSTANCES: Dict[str, Any] = {}

# Per-session agent locks
_SESSION_LOCKS: Dict[str, threading.Lock] = {}


def _get_session_agent_lock(session_id: str) -> threading.Lock:
    """Get or create a lock for a specific session."""
    with LOCK:
        if session_id not in _SESSION_LOCKS:
            _SESSION_LOCKS[session_id] = threading.Lock()
        return _SESSION_LOCKS[session_id]


def _set_thread_env(env_vars: Dict[str, str]) -> Dict[str, Optional[str]]:
    """Set environment variables for a thread, return previous values."""
    prev = {}
    for key, value in env_vars.items():
        prev[key] = os.environ.get(key)
        os.environ[key] = value
    return prev


def _clear_thread_env(prev: Dict[str, Optional[str]]) -> None:
    """Restore environment variables from previous state."""
    for key, value in prev.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value


def resolve_model_provider(model_str: str) -> tuple:
    """Resolve model string into (model, provider, base_url).

    Uses the same runtime provider resolution as CLI mode to ensure
    consistent behavior between CLI and Web UI.

    Handles formats like:
    - "anthropic/claude-sonnet-4.6" -> ("claude-sonnet-4.6", "anthropic", None)
    - "openai/gpt-4o" -> ("gpt-4o", "openai", None)
    - "minimax/minimax-m2.7" with NVIDIA NIM config -> uses NVIDIA endpoint
    - "minimaxai/minimax-m2.7" -> falls back to config.yaml provider when
      the prefix is not a recognized built-in provider
    """
    if not model_str:
        return None, None, None

    model_str = str(model_str).strip()

    # Try to use the same runtime provider resolution as CLI
    try:
        from hermes_cli.runtime_provider import resolve_runtime_provider
        from hermes_cli.auth import PROVIDER_REGISTRY

        # Extract provider from model string if present
        if '/' in model_str:
            provider_prefix = model_str.split('/', 1)[0].lower()
        else:
            provider_prefix = None

        # If the prefix is a recognized built-in provider, use it directly.
        # Otherwise, fall back to the config's configured provider so that
        # model strings with non-standard prefixes (e.g. "minimaxai/...")
        # still resolve to the endpoint configured in config.yaml.
        if provider_prefix and provider_prefix not in PROVIDER_REGISTRY:
            # Not a built-in provider — use the config's provider instead
            try:
                from hermes_cli.config import load_config
                cfg = load_config()
                model_cfg = cfg.get('model', {})
                if isinstance(model_cfg, dict):
                    cfg_provider = model_cfg.get('provider')
                    if isinstance(cfg_provider, str) and cfg_provider.strip():
                        provider_prefix = cfg_provider.strip().lower()
                    else:
                        provider_prefix = None
            except Exception:
                pass

        # Resolve using runtime provider (reads config.yaml)
        runtime = resolve_runtime_provider(requested=provider_prefix)

        # Use the FULL original model string — many providers (e.g. NVIDIA NIM)
        # require the complete model identifier including the provider prefix.
        model = model_str

        return model, runtime.get('provider'), runtime.get('base_url')

    except Exception as e:
        # Fallback to simple parsing if runtime provider fails
        print(f"[resolve_model_provider] Runtime provider failed: {e}, using fallback")

        if '/' in model_str:
            parts = model_str.split('/', 1)
            provider = parts[0].lower()
            model = parts[1]

            # Known provider base URLs (fallback only)
            base_url_map = {
                'openrouter': 'https://openrouter.ai/api/v1',
                'nous': 'https://inference.nousresearch.com/v1',
            }
            base_url = base_url_map.get(provider)
            return model, provider, base_url

        return model_str, None, None


def get_toolsets_for_chat() -> list:
    """Get enabled toolsets for chat sessions."""
    try:
        from hermes_cli.tools_config import _get_platform_tools
        from hermes_cli.config import load_config
        config = load_config()
        return list(_get_platform_tools(config, 'cli', include_default_mcp_servers=True))
    except Exception:
        return []


def get_default_model() -> str:
    """Get default model from Hermes config."""
    try:
        from hermes_cli.config import load_config
        config = load_config()
        model_cfg = config.get('model', '')
        if isinstance(model_cfg, dict):
            return model_cfg.get('default', model_cfg.get('name', ''))
        return str(model_cfg) if model_cfg else ''
    except Exception:
        return ''


def get_hermes_home() -> Path:
    """Get HERMES_HOME directory."""
    try:
        from hermes_constants import get_hermes_home as _get_home
        return _get_home()
    except ImportError:
        return Path(os.getenv("HERMES_HOME", str(Path.home() / ".hermes")))


# HERMES_HOME save/restore for per-profile isolation
_HERMES_HOME_STACK: list = []


def save_hermes_home() -> str:
    """Save current HERMES_HOME and return it."""
    current = os.getenv("HERMES_HOME", str(Path.home() / ".hermes"))
    _HERMES_HOME_STACK.append(current)
    return current


def restore_hermes_home() -> None:
    """Restore HERMES_HOME from stack."""
    if _HERMES_HOME_STACK:
        prev = _HERMES_HOME_STACK.pop()
        os.environ["HERMES_HOME"] = prev


# Stream creation and management helpers
def create_stream(stream_id: str) -> None:
    """Create a new stream queue for the given stream ID."""
    import queue
    with STREAMS_LOCK:
        STREAMS[stream_id] = queue.Queue()


def set_stream_complete(stream_id: str) -> None:
    """Mark a stream as complete."""
    with STREAMS_LOCK:
        if stream_id in STREAMS:
            STREAMS[stream_id].put(None)  # Sentinel value
        COMPLETED_STREAMS.add(stream_id)


def get_stream_status(stream_id: str) -> dict:
    """Get the current status of a stream."""
    with STREAMS_LOCK:
        if stream_id not in STREAMS:
            return {"stream_id": stream_id, "status": "not_found"}
        if stream_id in COMPLETED_STREAMS:
            return {"stream_id": stream_id, "status": "completed"}
        return {"stream_id": stream_id, "status": "running"}