"""A2A Server — Expose Hermes as an A2A-discoverable agent.

Implements the A2A AgentExecutor interface to handle incoming tasks from
remote A2A clients. Each task gets its own AIAgent session, and results
are streamed back via the A2A event queue.

Architecture:
    - A2AStarletteApplication serves the Agent Card and JSON-RPC endpoints
    - HermesAgentExecutor wraps AIAgent to handle A2A task execution
    - AIAgent runs synchronously in a thread pool (same pattern as ACP adapter)
    - Task updates are streamed via EventQueue
"""

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

from a2a_adapter.session import SessionManager

logger = logging.getLogger(__name__)

# Thread pool for running sync AIAgent (same pattern as ACP adapter)
_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="a2a-agent")

# ---------------------------------------------------------------------------
# Graceful imports — a2a-sdk[http-server] is an optional dependency
# ---------------------------------------------------------------------------

try:
    from a2a.server.agent_execution import AgentExecutor, RequestContext
    from a2a.server.events import EventQueue
    from a2a.server.apps import A2AStarletteApplication
    from a2a.server.request_handlers import DefaultRequestHandler
    from a2a.server.tasks import InMemoryTaskStore
    from a2a.types import (
        AgentCapabilities,
        AgentCard,
        AgentSkill,
        TaskArtifactUpdateEvent,
        TaskState,
        TaskStatus,
        TaskStatusUpdateEvent,
    )
    from a2a.utils.artifact import new_text_artifact
    from a2a.utils.message import new_agent_text_message
    from a2a.utils.task import new_task

    _A2A_SERVER_AVAILABLE = True
except ImportError as e:
    _A2A_SERVER_AVAILABLE = False
    logger.debug("A2A server dependencies not available: %s", e)


def is_server_available() -> bool:
    """Check if A2A server dependencies are installed."""
    return _A2A_SERVER_AVAILABLE


# ---------------------------------------------------------------------------
# Agent Card builder
# ---------------------------------------------------------------------------

def build_agent_card(
    host: str = "127.0.0.1",
    port: int = 9990,
    name: str = "Hermes Agent",
    version: str = "0.5.0",
) -> "AgentCard":
    """Build an A2A Agent Card describing Hermes' capabilities.

    The card is served at /.well-known/agent.json and tells remote agents
    what Hermes can do.
    """
    if not _A2A_SERVER_AVAILABLE:
        raise ImportError("a2a-sdk[http-server] is required for A2A server mode")

    # 0.0.0.0 is a bind address, not reachable — resolve to localhost for the card
    card_host = "127.0.0.1" if host == "0.0.0.0" else host
    url = f"http://{card_host}:{port}"

    skills = [
        AgentSkill(
            id="general_assistant",
            name="General Assistant",
            description=(
                "General-purpose AI agent with access to terminal, file system, "
                "web search, browser automation, code execution, and more. "
                "Can handle software engineering, research, analysis, and automation tasks."
            ),
            tags=["coding", "research", "automation", "analysis", "devops"],
            examples=[
                "Write a Python script that fetches weather data",
                "Search the web for recent news about AI agents",
                "Read and analyze the files in /tmp/project",
                "Debug this error message: ...",
            ],
        ),
        AgentSkill(
            id="code_execution",
            name="Code Execution",
            description="Execute Python code in a sandboxed environment and return results.",
            tags=["python", "code", "execution"],
            examples=[
                "Run this Python code: print('hello')",
                "Calculate the factorial of 100",
            ],
        ),
        AgentSkill(
            id="web_research",
            name="Web Research",
            description="Search the web and extract content from URLs for research tasks.",
            tags=["search", "web", "research"],
            examples=[
                "Find the latest documentation for FastAPI",
                "What are the top GitHub repos for agent frameworks?",
            ],
        ),
        AgentSkill(
            id="file_operations",
            name="File Operations",
            description="Read, write, search, and patch files on the local filesystem.",
            tags=["files", "filesystem", "read", "write"],
            examples=[
                "Read the file at /tmp/data.json",
                "Search for files containing 'TODO' in the current directory",
            ],
        ),
    ]

    return AgentCard(
        name=name,
        description=(
            "Hermes Agent — a self-improving AI agent that creates skills from experience, "
            "improves them during use, and runs anywhere. Supports terminal, file system, "
            "web search, browser automation, code execution, delegation, and more."
        ),
        url=url,
        version=version,
        default_input_modes=["text"],
        default_output_modes=["text"],
        capabilities=AgentCapabilities(streaming=True),
        skills=skills,
    )


# ---------------------------------------------------------------------------
# Agent Executor — bridges A2A tasks to Hermes AIAgent
# ---------------------------------------------------------------------------

if _A2A_SERVER_AVAILABLE:

    class HermesAgentExecutor(AgentExecutor):
        """Execute A2A tasks using Hermes AIAgent.

        Each task creates or reuses a session with its own AIAgent instance.
        The agent runs synchronously in a thread pool, and results are
        streamed back via the A2A event queue.
        """

        def __init__(self, session_manager: Optional[SessionManager] = None):
            self.session_manager = session_manager or SessionManager()

        async def execute(
            self,
            context: RequestContext,
            event_queue: EventQueue,
        ) -> None:
            """Handle an incoming A2A task."""
            # Extract user message text
            user_text = self._extract_text(context)
            if not user_text:
                await self._send_error(context, event_queue, "No text content in message")
                return

            # Create or get session for this task (supports multi-turn)
            task_id = context.task_id
            session = self.session_manager.get_or_create_session(task_id)

            # Emit task + WORKING status
            task = context.current_task or new_task(context.message)
            await event_queue.enqueue_event(task)

            await event_queue.enqueue_event(
                TaskStatusUpdateEvent(
                    task_id=context.task_id,
                    context_id=context.context_id,
                    status=TaskStatus(
                        state=TaskState.working,
                        message=new_agent_text_message("Processing request..."),
                    ),
                )
            )

            # Run AIAgent in thread pool (sync agent, async server)
            loop = asyncio.get_running_loop()
            try:
                result = await loop.run_in_executor(
                    _executor,
                    lambda: self._run_agent(session, user_text, task_id=task_id),
                )

                # Extract response text
                response_text = result.get("final_response", "")
                if not response_text:
                    response_text = "(Agent produced no response)"

                # Emit artifact with result
                await event_queue.enqueue_event(
                    TaskArtifactUpdateEvent(
                        task_id=context.task_id,
                        context_id=context.context_id,
                        artifact=new_text_artifact(
                            name="response",
                            text=response_text,
                        ),
                    )
                )

                # Mark completed
                await event_queue.enqueue_event(
                    TaskStatusUpdateEvent(
                        task_id=context.task_id,
                        context_id=context.context_id,
                        status=TaskStatus(
                            state=TaskState.completed,
                        ),
                    )
                )

            except Exception as e:
                logger.exception("A2A task execution failed: %s", e)
                await self._send_error(context, event_queue, str(e))

        async def cancel(
            self,
            context: RequestContext,
            event_queue: EventQueue,
        ) -> None:
            """Cancel a running task.

            Sets the cancel event AND calls agent.interrupt() to actually
            stop in-progress terminal/code execution (matching ACP pattern).
            """
            task_id = context.task_id
            # Signal cancellation
            cancelled = self.session_manager.cancel_session(task_id)

            # Also interrupt the agent to stop running tools
            session = self.session_manager.get_session(task_id)
            if session and getattr(session, "agent", None):
                try:
                    if hasattr(session.agent, "interrupt"):
                        session.agent.interrupt()
                except Exception:
                    logger.debug("Failed to interrupt A2A session %s", task_id, exc_info=True)

            state = (
                TaskState.canceled
                if cancelled
                else TaskState.failed
            )
            msg = "Task cancelled" if cancelled else "Task not found or already completed"

            await event_queue.enqueue_event(
                TaskStatusUpdateEvent(
                    task_id=context.task_id,
                    context_id=context.context_id,
                    status=TaskStatus(
                        state=state,
                        message=new_agent_text_message(msg),
                    ),
                )
            )

        def _extract_text(self, context: RequestContext) -> str:
            """Extract text from the incoming A2A message."""
            message = context.message
            if not message:
                return ""

            parts = getattr(message, "parts", [])
            texts = []
            for part in parts:
                # Handle protobuf Part (has .text field directly)
                text = getattr(part, "text", None)
                if text:
                    texts.append(text)
                    continue
                # Handle pydantic Part (has .root.text)
                root = getattr(part, "root", None)
                if root:
                    text = getattr(root, "text", None)
                    if text:
                        texts.append(text)

            return "\n".join(texts)

        def _run_agent(self, session: "SessionState", user_text: str, task_id: str = "") -> dict:
            """Run AIAgent synchronously (called from thread pool)."""
            agent = session.agent
            result = agent.run_conversation(
                user_message=user_text,
                conversation_history=session.history,
                task_id=task_id,
            )

            # Replace history (not extend) to avoid duplicating prior context
            if result.get("messages"):
                session.history = result["messages"]

            return result

        async def _send_error(
            self,
            context: RequestContext,
            event_queue: EventQueue,
            error_msg: str,
        ) -> None:
            """Send a FAILED status update."""
            await event_queue.enqueue_event(
                TaskStatusUpdateEvent(
                    task_id=context.task_id,
                    context_id=context.context_id,
                    status=TaskStatus(
                        state=TaskState.failed,
                        message=new_agent_text_message(f"Error: {error_msg}"),
                    ),
                )
            )


# ---------------------------------------------------------------------------
# Application builder
# ---------------------------------------------------------------------------

def _make_bearer_middleware(bearer_token: str):
    """Create Starlette middleware that validates bearer tokens.

    Agent Card discovery (/.well-known/agent.json) is always open so
    remote clients can discover the agent before authenticating.
    """
    from starlette.middleware.base import BaseHTTPMiddleware
    from starlette.responses import JSONResponse

    class BearerAuthMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request, call_next):
            # Allow unauthenticated Agent Card discovery
            if request.url.path == "/.well-known/agent.json":
                return await call_next(request)

            auth = request.headers.get("authorization", "")
            if not auth.startswith("Bearer ") or auth[7:] != bearer_token:
                return JSONResponse(
                    {"error": "Unauthorized — provide a valid Bearer token"},
                    status_code=401,
                )
            return await call_next(request)

    return BearerAuthMiddleware


def build_application(
    host: str = "127.0.0.1",
    port: int = 9990,
    name: str = "Hermes Agent",
    bearer_token: Optional[str] = None,
    toolset: str = "hermes-cli",
) -> "A2AStarletteApplication":
    """Build the A2A Starlette application ready to run with uvicorn."""
    if not _A2A_SERVER_AVAILABLE:
        raise ImportError(
            "A2A server dependencies not installed. "
            "Install with: pip install 'hermes-agent[a2a]' or pip install 'a2a-sdk[http-server]'"
        )

    card = build_agent_card(host=host, port=port, name=name)
    session_manager = SessionManager(toolset=toolset)
    executor = HermesAgentExecutor(session_manager=session_manager)
    handler = DefaultRequestHandler(
        agent_executor=executor,
        task_store=InMemoryTaskStore(),
    )

    app = A2AStarletteApplication(
        agent_card=card,
        http_handler=handler,
    )

    if bearer_token:
        middleware_cls = _make_bearer_middleware(bearer_token)
        # Add middleware before build() so it's included in the middleware
        # stack compilation. Patch build() to inject the middleware.
        original_build = app.build

        def _build_with_auth():
            starlette_app = original_build()
            starlette_app.add_middleware(middleware_cls)
            return starlette_app

        app.build = _build_with_auth

    return app
