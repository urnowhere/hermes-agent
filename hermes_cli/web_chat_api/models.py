"""
Hermes Chat UI -- Session model and CRUD operations.
Manages chat session persistence, retrieval, and lifecycle.
"""

import json
import logging
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def generate_session_id() -> str:
    """Generate a unique session ID."""
    return f"chat-{uuid.uuid4().hex[:12]}"


def get_session_file(session_id: str, state_dir: Path) -> Path:
    """Get the path to a session file."""
    sessions_dir = state_dir / "sessions"
    sessions_dir.mkdir(parents=True, exist_ok=True)
    return sessions_dir / f"{session_id}.json"


def create_session(
    title: str = "New Chat",
    workspace: str = "",
    model: str = "",
    state_dir: Optional[Path] = None
) -> Dict[str, Any]:
    """
    Create a new chat session.

    Args:
        title: Session title
        workspace: Workspace directory path
        model: Model string to use
        state_dir: State directory (defaults to CHAT_STATE_DIR)

    Returns:
        Session dict with session_id, title, workspace, model, messages, timestamps
    """
    from .config import CHAT_STATE_DIR

    state_dir = state_dir or CHAT_STATE_DIR
    session_id = generate_session_id()

    session = {
        "session_id": session_id,
        "title": title,
        "workspace": workspace,
        "model": model,
        "messages": [],
        "created_at": time.time(),
        "updated_at": time.time(),
        "metadata": {}
    }

    session_file = get_session_file(session_id, state_dir)
    session_file.write_text(json.dumps(session, indent=2))

    logger.info(f"Created session {session_id}: {title}")
    return session


def get_session(
    session_id: str,
    state_dir: Optional[Path] = None
) -> Optional[Dict[str, Any]]:
    """
    Retrieve a session by ID.

    Args:
        session_id: Session ID to retrieve
        state_dir: State directory

    Returns:
        Session dict or None if not found
    """
    from .config import CHAT_STATE_DIR

    state_dir = state_dir or CHAT_STATE_DIR
    session_file = get_session_file(session_id, state_dir)

    if not session_file.exists():
        return None

    try:
        content = session_file.read_text()
        session = json.loads(content)
        return session
    except (json.JSONDecodeError, OSError) as e:
        logger.error(f"Failed to read session {session_id}: {e}")
        return None


def update_session(
    session_id: str,
    state_dir: Optional[Path] = None,
    **kwargs
) -> Optional[Dict[str, Any]]:
    """
    Update a session with new values.

    Args:
        session_id: Session ID to update
        state_dir: State directory
        **kwargs: Fields to update (title, workspace, model, messages, etc.)

    Returns:
        Updated session dict or None if not found
    """
    from .config import CHAT_STATE_DIR

    state_dir = state_dir or CHAT_STATE_DIR
    session = get_session(session_id, state_dir)

    if session is None:
        return None

    # Update fields
    for key, value in kwargs.items():
        if key not in ("session_id", "created_at"):
            session[key] = value

    session["updated_at"] = time.time()

    # Persist
    session_file = get_session_file(session_id, state_dir)
    session_file.write_text(json.dumps(session, indent=2))

    logger.info(f"Updated session {session_id}: {list(kwargs.keys())}")
    return session


def delete_session(
    session_id: str,
    state_dir: Optional[Path] = None
) -> bool:
    """
    Delete a session.

    Args:
        session_id: Session ID to delete
        state_dir: State directory

    Returns:
        True if deleted, False if not found
    """
    from .config import CHAT_STATE_DIR

    state_dir = state_dir or CHAT_STATE_DIR
    session_file = get_session_file(session_id, state_dir)

    if not session_file.exists():
        return False

    try:
        session_file.unlink()
        logger.info(f"Deleted session {session_id}")
        return True
    except OSError as e:
        logger.error(f"Failed to delete session {session_id}: {e}")
        return False


def list_sessions(
    state_dir: Optional[Path] = None,
    limit: int = 100,
    include_messages: bool = False
) -> List[Dict[str, Any]]:
    """
    List all sessions, sorted by updated_at descending.

    Args:
        state_dir: State directory
        limit: Maximum number of sessions to return
        include_messages: Whether to include full message history

    Returns:
        List of session dicts (summary or full)
    """
    from .config import CHAT_STATE_DIR

    state_dir = state_dir or CHAT_STATE_DIR
    sessions_dir = state_dir / "sessions"

    if not sessions_dir.exists():
        return []

    sessions = []
    for session_file in sessions_dir.glob("*.json"):
        try:
            content = session_file.read_text()
            session = json.loads(content)

            if not include_messages:
                # Return summary without messages
                session = {
                    "session_id": session["session_id"],
                    "title": session.get("title", "Untitled"),
                    "workspace": session.get("workspace", ""),
                    "model": session.get("model", ""),
                    "created_at": session.get("created_at", 0),
                    "updated_at": session.get("updated_at", 0),
                    "message_count": len(session.get("messages", []))
                }

            sessions.append(session)
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"Failed to read session file {session_file}: {e}")

    # Sort by updated_at descending
    sessions.sort(key=lambda s: s.get("updated_at", 0), reverse=True)

    return sessions[:limit]


def add_message(
    session_id: str,
    role: str,
    content: str,
    state_dir: Optional[Path] = None,
    **kwargs
) -> Optional[Dict[str, Any]]:
    """
    Add a message to a session.

    Args:
        session_id: Session ID
        role: Message role (user/assistant/tool)
        content: Message content
        state_dir: State directory
        **kwargs: Additional message fields (tool_calls, tool_call_id, etc.)

    Returns:
        Updated session dict or None if not found
    """
    message = {
        "role": role,
        "content": content,
        "timestamp": time.time(),
        **kwargs
    }

    return update_session(
        session_id,
        state_dir=state_dir,
        messages=lambda m: m + [message] if isinstance(m, list) else [message]
    )


# Thread-safe session cache
_SESSION_CACHE: Dict[str, Dict[str, Any]] = {}
_SESSION_CACHE_LOCK = threading.Lock()


def cached_get_session(
    session_id: str,
    state_dir: Optional[Path] = None,
    use_cache: bool = True
) -> Optional[Dict[str, Any]]:
    """
    Get session with optional caching.

    Args:
        session_id: Session ID
        state_dir: State directory
        use_cache: Whether to use cache

    Returns:
        Cached or fresh session dict
    """
    if use_cache:
        with _SESSION_CACHE_LOCK:
            if session_id in _SESSION_CACHE:
                return _SESSION_CACHE[session_id]

    session = get_session(session_id, state_dir)

    if session and use_cache:
        with _SESSION_CACHE_LOCK:
            _SESSION_CACHE[session_id] = session

    return session


def invalidate_session_cache(session_id: Optional[str] = None) -> None:
    """
    Invalidate session cache.

    Args:
        session_id: Specific session to invalidate, or None for all
    """
    with _SESSION_CACHE_LOCK:
        if session_id:
            _SESSION_CACHE.pop(session_id, None)
        else:
            _SESSION_CACHE.clear()
