"""
Hermes Chat UI -- Simplified SSE streaming engine for Hermes Agent.
Core chat functionality with SSE streaming support.
"""
import json
import logging
import os
import queue
import threading
import time
import asyncio
from pathlib import Path
from typing import Optional, Dict, Any, Callable

logger = logging.getLogger(__name__)

from hermes_cli.web_chat_api.config import (
    STREAMS, STREAMS_LOCK, CANCEL_FLAGS, AGENT_INSTANCES,
    LOCK, CHAT_SESSION_DIR, get_hermes_home,
    resolve_model_provider, get_toolsets_for_chat,
)

# Environment lock for HERMES_HOME isolation
_ENV_LOCK = threading.Lock()

# Session storage (uses Hermes Agent's SessionDB)
_session_db = None

def _get_session_db():
    """Get or create SessionDB instance."""
    global _session_db
    if _session_db is None:
        try:
            from hermes_state import SessionDB
            _session_db = SessionDB()
        except Exception as e:
            logger.warning(f"SessionDB init failed: {e}")
    return _session_db


def _get_ai_agent():
    """Get AIAgent class."""
    try:
        from run_agent import AIAgent
        return AIAgent
    except ImportError as e:
        logger.error(f"AIAgent import failed: {e}")
        return None


class ChatSession:
    """Simple session model for chat UI."""
    def __init__(self, session_id: str, workspace: str = None, model: str = None):
        self.session_id = session_id
        self.workspace = workspace or str(Path.cwd())
        self.model = model or ''
        self.messages = []
        self.title = ''
        self.created_at = time.time()
        self.last_active = time.time()


# In-memory session cache
SESSIONS: Dict[str, ChatSession] = {}
_SESSIONS_LOCK = threading.Lock()


def get_session(session_id: str) -> Optional[ChatSession]:
    """Get or create a session."""
    with _SESSIONS_LOCK:
        if session_id not in SESSIONS:
            SESSIONS[session_id] = ChatSession(session_id)
        return SESSIONS[session_id]


def update_session(session_id: str, **kwargs) -> ChatSession:
    """Update session fields."""
    session = get_session(session_id)
    for key, value in kwargs.items():
        if hasattr(session, key):
            setattr(session, key, value)
    session.last_active = time.time()
    return session


def title_from(user_text: str, assistant_text: str, max_len: int = 60) -> str:
    """Generate a simple title from first exchange."""
    if user_text:
        # Take first line or first N chars
        first_line = user_text.strip().split('\n')[0]
        if len(first_line) > max_len:
            return first_line[:max_len-3] + '...'
        return first_line
    return 'New Chat'


async def run_chat_stream(
    session_id: str,
    user_message: str,
    model: str = None,
    workspace: str = None,
    on_token: Callable = None,
    on_tool: Callable = None,
    on_complete: Callable = None,
    on_error: Callable = None,
):
    """Run a chat session with SSE streaming.

    Args:
        session_id: Unique session identifier
        user_message: User's input message
        model: Model string (e.g., "anthropic/claude-sonnet-4.6")
        workspace: Working directory for the session
        on_token: Callback for each token (delta)
        on_tool: Callback for tool progress
        on_complete: Callback when stream completes
        on_error: Callback for errors
    """
    stream_id = f"{session_id}:{int(time.time()*1000)}"
    cancel_event = threading.Event()

    with STREAMS_LOCK:
        CANCEL_FLAGS[stream_id] = cancel_event

    # Get session
    session = get_session(session_id)
    if workspace:
        session.workspace = workspace
    if model:
        session.model = model

    # Resolve model/provider — fall back to config default when no model specified
    model_str = model or session.model or ''
    if not model_str:
        from hermes_cli.web_chat_api.config import get_default_model
        model_str = get_default_model() or ''

    resolved_model, resolved_provider, resolved_base_url = resolve_model_provider(model_str)
    print(f"[CHAT_STREAM] model_str={model_str!r}, resolved: model={resolved_model}, provider={resolved_provider}, base_url={resolved_base_url}", flush=True)

    # Get API key via runtime provider
    resolved_api_key = None
    api_mode = None
    try:
        from hermes_cli.runtime_provider import resolve_runtime_provider
        rt = resolve_runtime_provider(requested=resolved_provider)
        resolved_api_key = rt.get("api_key")
        if not resolved_provider:
            resolved_provider = rt.get("provider")
        if not resolved_base_url:
            resolved_base_url = rt.get("base_url")
        api_mode = rt.get("api_mode")
    except Exception as e:
        logger.warning(f"resolve_runtime_provider failed: {e}")

    # Get toolsets
    toolsets = get_toolsets_for_chat()
    print(f"[CHAT_STREAM] toolsets={toolsets}", flush=True)

    # Set environment for workspace
    env_prev = {}
    with _ENV_LOCK:
        env_prev['TERMINAL_CWD'] = os.environ.get('TERMINAL_CWD')
        os.environ['TERMINAL_CWD'] = session.workspace
        env_prev['HERMES_SESSION_KEY'] = os.environ.get('HERMES_SESSION_KEY')
        os.environ['HERMES_SESSION_KEY'] = session_id

    try:
        AIAgent = _get_ai_agent()
        if AIAgent is None:
            if on_error:
                on_error("AIAgent not available")
            return

        # Build messages history
        messages = session.messages.copy()
        messages.append({"role": "user", "content": user_message})

        # Token collection for final response
        collected_tokens = []

        def _on_token_callback(delta, **kwargs):
            if cancel_event.is_set():
                return
            collected_tokens.append(delta)
            if on_token:
                on_token(delta)

        # Tool call ID generator for correlating start/complete events
        _tool_call_counter = 0
        _pending_tool_calls = {}  # key: name, value: {tid, preview, args}

        def _on_tool_callback(event_type, name, preview, args, **kwargs):
            """Handle tool progress events from the agent.

            Args:
                event_type: "tool.started" or "tool.completed"
                name: Tool name
                preview: Tool preview (for started) or None (for completed)
                args: Tool arguments (for started) or None (for completed)
                kwargs: Additional data (duration, is_error, etc.)
            """
            nonlocal _tool_call_counter
            if cancel_event.is_set():
                return

            # Generate or retrieve tool call ID
            tid = None
            if event_type == "tool.started":
                _tool_call_counter += 1
                tid = f"webui-{_tool_call_counter}"
                # Store for later completion event
                _pending_tool_calls[name] = {'tid': tid, 'preview': preview, 'args': args}
                kwargs['tid'] = tid
                print(f"[TOOL] started: name={name}, tid={tid}", flush=True)
            elif event_type == "tool.completed":
                # Just retrieve the tid, don't remove yet - tool_complete_callback will remove
                pending = _pending_tool_calls.get(name)
                if pending:
                    tid = pending['tid']
                else:
                    _tool_call_counter += 1
                    tid = f"webui-{_tool_call_counter}"
                kwargs['tid'] = tid
                print(f"[TOOL] completed (progress): name={name}, tid={tid}", flush=True)

            if on_tool:
                on_tool(event_type, name, preview, args, kwargs, tid=tid)

        def _on_tool_complete_callback(llm_tid, name, args, result):
            """Handle tool completion with full result from the agent.

            Args:
                llm_tid: The LLM's tool call id (e.g., 'call_abc123')
                name: Tool name
                args: Tool arguments
                result: Full tool output result
            """
            if cancel_event.is_set():
                return

            print(f"[TOOL_COMPLETE] called: llm_tid={llm_tid}, name={name}", flush=True)

            # Look up the pending call by name to get our tid, then remove it
            pending = _pending_tool_calls.pop(name, None)
            tid = pending['tid'] if pending else None

            if not tid:
                # Fallback: create a new tid (shouldn't happen if flow is correct)
                _tool_call_counter += 1
                tid = f"webui-{_tool_call_counter}"
                print(f"[TOOL_COMPLETE] created new tid={tid} (no pending call)", flush=True)
            else:
                print(f"[TOOL_COMPLETE] found tid={tid} from pending call", flush=True)

            preview = pending.get('preview', '') if pending else ''

            # Send tool complete event with full result
            if on_tool:
                print(f"[TOOL_COMPLETE] sending event: tid={tid}, result_len={len(result) if result else 0}", flush=True)
                on_tool("tool.completed", name, preview, args, {
                    'duration': 0,
                    'is_error': False,
                    'tid': tid,
                    'result': result,
                }, tid=tid)

        # Create agent
        print(f"[CHAT_STREAM] Creating AIAgent with model={resolved_model!r}, provider={resolved_provider!r}, base_url={resolved_base_url!r}, toolsets={toolsets}, session_id={session_id}", flush=True)
        agent = AIAgent(
            model=resolved_model,
            provider=resolved_provider,
            base_url=resolved_base_url,
            api_key=resolved_api_key,
            api_mode=api_mode,
            platform='webui',
            quiet_mode=True,
            enabled_toolsets=toolsets,
            session_id=session_id,
            session_db=_get_session_db(),
            stream_delta_callback=_on_token_callback,
            tool_progress_callback=_on_tool_callback,
            tool_complete_callback=_on_tool_complete_callback,
        )

        with STREAMS_LOCK:
            AGENT_INSTANCES[stream_id] = agent

        # Check cancel before start
        if cancel_event.is_set():
            agent.interrupt("Cancelled before start")
            if on_error:
                on_error("Cancelled by user")
            return

        # Run conversation in thread pool without blocking event loop
        loop = asyncio.get_event_loop()

        def _run_agent():
            """Run agent synchronously in thread."""
            try:
                result = agent.run_conversation(user_message)
                print(f"[CHAT_STREAM] agent.run_conversation completed: result keys={result.keys() if isinstance(result, dict) else type(result)}", flush=True)
                if isinstance(result, dict):
                    print(f"[CHAT_STREAM] result: final_response={result.get('final_response', '')[:200]}..., completed={result.get('completed')}", flush=True)
                return ('success', result)
            except Exception as e:
                logger.exception(f"Agent execution error: {e}")
                print(f"[CHAT_STREAM] agent.run_conversation error: {e}", flush=True)
                return ('error', str(e))

        # Execute agent in thread pool with timeout (default 10 minutes)
        timeout_seconds = int(os.environ.get('HERMES_CHAT_TIMEOUT', '600'))

        try:
            # Run in executor without blocking event loop
            future = loop.run_in_executor(None, _run_agent)

            # Wait with timeout and periodic cancel checks
            start_time = time.time()
            while not future.done():
                # Check for cancellation
                if cancel_event.is_set():
                    try:
                        agent.interrupt("Cancelled by user")
                    except Exception:
                        pass
                    # Wait briefly for graceful shutdown
                    try:
                        await asyncio.wait_for(asyncio.shield(future), timeout=5.0)
                    except asyncio.TimeoutError:
                        pass
                    if on_error:
                        on_error("Cancelled by user")
                    return

                # Check for timeout
                if time.time() - start_time > timeout_seconds:
                    try:
                        agent.interrupt("Execution timeout")
                    except Exception:
                        pass
                    if on_error:
                        on_error(f"Execution timeout ({timeout_seconds}s)")
                    return

                # Sleep without blocking event loop
                await asyncio.sleep(0.1)

            # Get result
            status, result = await future

            if status == 'success':
                final_response = result.get('final_response', '')

                # Update session
                session.messages.append({"role": "user", "content": user_message})
                session.messages.append({"role": "assistant", "content": final_response})

                # Generate title if needed
                if not session.title and len(session.messages) >= 2:
                    user_text = session.messages[0].get('content', '')
                    session.title = title_from(user_text, final_response)

                # Persist to disk using models.py
                try:
                    from hermes_cli.web_chat_api.session_adapter import save_session_after_chat
                    save_session_after_chat(
                        session_id=session_id,
                        messages=session.messages,
                        title=session.title
                    )
                except Exception as e:
                    logger.warning(f"Failed to persist session {session_id}: {e}")

                if on_complete:
                    on_complete(final_response, session.messages)
            else:
                if on_error:
                    on_error(result)

        except Exception as e:
            logger.exception(f"Agent execution wrapper error: {e}")
            if on_error:
                on_error(str(e))

    except Exception as e:
        logger.exception(f"Chat stream error: {e}")
        if on_error:
            on_error(str(e))
    finally:
        # Restore environment
        with _ENV_LOCK:
            for key, value in env_prev.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

        # Cleanup
        with STREAMS_LOCK:
            CANCEL_FLAGS.pop(stream_id, None)
            AGENT_INSTANCES.pop(stream_id, None)


def cancel_stream(stream_id: str) -> bool:
    """Cancel an active stream."""
    with STREAMS_LOCK:
        if stream_id in CANCEL_FLAGS:
            CANCEL_FLAGS[stream_id].set()
            # Also interrupt agent if running
            if stream_id in AGENT_INSTANCES:
                try:
                    AGENT_INSTANCES[stream_id].interrupt("Cancelled by user")
                except Exception:
                    pass
            return True
    return False


def is_stream_active(stream_id: str) -> bool:
    """Check if a stream is still active."""
    with STREAMS_LOCK:
        return stream_id in CANCEL_FLAGS and not CANCEL_FLAGS[stream_id].is_set()


# SSE helper functions

def sse_format(event: str, data: Any) -> str:
    """Format data as SSE message."""
    data_str = json.dumps(data, ensure_ascii=False)
    return f"event: {event}\ndata: {data_str}\n\n"


def sse_done() -> str:
    """Format SSE done signal."""
    return "event: done\ndata: {}\n\n"