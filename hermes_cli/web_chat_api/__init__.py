"""
Hermes Chat UI API -- Web chat integration for Hermes Agent.
Provides SSE streaming chat functionality based on Hermes WebUI.
"""
from hermes_cli.web_chat_api.config import (
    STREAMS, STREAMS_LOCK, CANCEL_FLAGS, AGENT_INSTANCES,
    LOCK, CHAT_SESSION_DIR, CHAT_STATE_DIR,
    resolve_model_provider, get_default_model, get_toolsets_for_chat,
    get_hermes_home,
)
from hermes_cli.web_chat_api.helpers import (
    require, bad, redact_session_data,
    safe_resolve, IMAGE_EXTS, MD_EXTS, MIME_MAP,
)
from hermes_cli.web_chat_api.chat_stream import (
    run_chat_stream, cancel_stream, is_stream_active,
    ChatSession, get_session, update_session,
    sse_format, sse_done,
)