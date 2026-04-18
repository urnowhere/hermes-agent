"""Runtime wrapper for emitting Hermes Web Console GUI events."""

from __future__ import annotations

import asyncio
import inspect
import threading
import uuid
from typing import Any, Callable, Dict, Optional

from gateway.web_console.event_bus import GuiEvent
from gateway.web_console.state import WebConsoleState, get_web_console_state
from gateway.web_console.services.approval_service import ApprovalService

RuntimeRunner = Callable[..., Any]
_TERMINAL_APPROVAL_CALLBACK_LOCK = threading.Lock()


class ChatService:
    """Minimal chat runtime wrapper that publishes GUI events for a session."""

    def __init__(
        self,
        *,
        state: WebConsoleState | None = None,
        runtime_runner: RuntimeRunner | None = None,
        human_service: ApprovalService | None = None,
    ) -> None:
        self.state = state or get_web_console_state()
        self.runtime_runner = runtime_runner or self._default_runtime_runner
        self.human_service = human_service
        self._session_db = None
        try:
            from hermes_state import SessionDB
            self._session_db = SessionDB()
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning("SessionDB unavailable: %s", e)

    async def run_chat(
        self,
        *,
        prompt: str,
        session_id: str,
        conversation_history: list[dict[str, Any]] | None = None,
        ephemeral_system_prompt: str | None = None,
        run_id: str | None = None,
        runtime_context: Optional[Dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """Run a single chat turn and publish GUI events to the session channel."""
        run_id = run_id or str(uuid.uuid4())
        history = list(conversation_history or [])
        runtime_context = dict(runtime_context or {})
        loop = asyncio.get_running_loop()
        pending_tasks: list[asyncio.Task[Any]] = []

        async def publish(event_type: str, payload: dict[str, Any] | None = None) -> None:
            await self.state.event_bus.publish(
                session_id,
                GuiEvent(
                    type=event_type,
                    session_id=session_id,
                    run_id=run_id,
                    payload=payload or {},
                ),
            )

        def runtime_gui_event_callback(event_type: str, payload: dict[str, Any] | None = None) -> None:
            # print(f"DEBUG: gui_event_callback -> {event_type}", flush=True)
            coro = publish(event_type, payload)
            try:
                running_loop = asyncio.get_running_loop()
            except RuntimeError:
                running_loop = None

            if running_loop is loop:
                pending_tasks.append(loop.create_task(coro))
                return

            future = asyncio.run_coroutine_threadsafe(coro, loop)
            future.result()

        await publish("run.started", {"prompt": prompt})
        await publish("message.user", {"content": prompt})

        try:
            print("DEBUG: Waiting for run_in_executor...", flush=True)
            result = self.runtime_runner(
                prompt=prompt,
                session_id=session_id,
                conversation_history=history,
                ephemeral_system_prompt=ephemeral_system_prompt,
                gui_event_callback=runtime_gui_event_callback,
                run_id=run_id,
                **runtime_context,
            )
            if inspect.isawaitable(result):
                result = await result
            print("DEBUG: run_in_executor returned!", flush=True)

            if pending_tasks:
                print(f"DEBUG: Gathering {len(pending_tasks)} pending tasks...", flush=True)
                await asyncio.gather(*pending_tasks)
            print("DEBUG: Pending tasks gathered!", flush=True)

            result_dict = dict(result or {})
            if result_dict.get("failed"):
                error_message = result_dict.get("error") or "Run failed"
                print(f"DEBUG: Run failed: {error_message}", flush=True)
                await publish("run.failed", {"error": error_message})
                return result_dict

            print("DEBUG: Extracting assistant text...", flush=True)
            assistant_text = self._extract_assistant_text(result_dict)
            reasoning_text = self._extract_reasoning_text(result_dict)
            print("DEBUG: Publishing message.assistant.completed...", flush=True)
            completed_payload: dict[str, Any] = {"content": assistant_text}
            if reasoning_text:
                completed_payload["reasoning"] = reasoning_text
            await publish("message.assistant.completed", completed_payload)
            print("DEBUG: Publishing run.completed...", flush=True)
            await publish(
                "run.completed",
                {
                    "completed": result_dict.get("completed", True),
                    "final_response": assistant_text,
                    "usage": result_dict.get("usage"),
                },
            )
            print("DEBUG: run_chat successfully completing!", flush=True)
            return result_dict
        except Exception as exc:
            print(f"DEBUG: Exception in run_chat: {exc}", flush=True)
            if pending_tasks:
                await asyncio.gather(*pending_tasks, return_exceptions=True)
            await publish("run.failed", {"error": str(exc), "error_type": type(exc).__name__})
            raise

    async def _default_runtime_runner(
        self,
        *,
        prompt: str,
        session_id: str,
        conversation_history: list[dict[str, Any]] | None = None,
        ephemeral_system_prompt: str | None = None,
        gui_event_callback: Callable[[str, dict[str, Any] | None], None] | None = None,
        run_id: str | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Run Hermes through the existing AIAgent path in a worker thread."""
        loop = asyncio.get_running_loop()

        def _run() -> dict[str, Any]:
            import importlib
            from gateway.run import _resolve_gateway_model, _resolve_runtime_agent_kwargs
            from gateway.run import GatewayRunner
            from hermes_cli.models import resolve_fast_mode_overrides
            from run_agent import AIAgent

            terminal_tool = importlib.import_module("tools.terminal_tool")

            runtime_kwargs = _resolve_runtime_agent_kwargs()
            clarify_callback = None
            approval_callback = None
            if self.human_service is not None:
                clarify_callback = self.human_service.create_clarify_callback(session_id=session_id, run_id=run_id)
                approval_callback = self.human_service.create_approval_callback(session_id=session_id, run_id=run_id)

            previous_approval_callback = getattr(terminal_tool, "_approval_callback", None)

            def _execute_with_agent() -> dict[str, Any]:
                is_quick_ask = kwargs.get("quick_ask", False)
                active_model = _resolve_gateway_model()
                service_tier = GatewayRunner._load_service_tier()
                request_overrides = resolve_fast_mode_overrides(active_model) if service_tier else None
                agent = AIAgent(
                    model=active_model,
                    **runtime_kwargs,
                    quiet_mode=True,
                    verbose_logging=False,
                    session_id=session_id,
                    platform="web_console",
                    service_tier=service_tier,
                    request_overrides=request_overrides,
                    ephemeral_system_prompt=ephemeral_system_prompt,
                    clarify_callback=clarify_callback,
                    gui_event_callback=gui_event_callback,
                    skip_memory=True if is_quick_ask else runtime_kwargs.get("skip_memory", False),
                    disabled_toolsets=["core"] if is_quick_ask else None,
                    session_db=self._session_db,
                )
                return agent.run_conversation(
                    user_message=prompt,
                    conversation_history=conversation_history,
                    task_id=run_id,
                )

            if approval_callback is None:
                print("DEBUG: Executing without approval callback...", flush=True)
                return _execute_with_agent()

            print("DEBUG: Acquiring _TERMINAL_APPROVAL_CALLBACK_LOCK...", flush=True)
            with _TERMINAL_APPROVAL_CALLBACK_LOCK:
                terminal_tool.set_approval_callback(approval_callback)
                try:
                    print("DEBUG: Executing with agent inside lock...", flush=True)
                    res = _execute_with_agent()
                    print("DEBUG: Agent execution complete!", flush=True)
                    return res
                finally:
                    terminal_tool.set_approval_callback(previous_approval_callback)

        return await loop.run_in_executor(None, _run)

    @staticmethod
    def _extract_assistant_text(result: dict[str, Any]) -> str:
        final_response = result.get("final_response")
        if isinstance(final_response, str):
            return final_response

        messages = result.get("messages") or []
        for message in reversed(messages):
            if message.get("role") == "assistant":
                content = message.get("content")
                if isinstance(content, str):
                    return content
        return ""

    @staticmethod
    def _extract_reasoning_text(result: dict[str, Any]) -> str:
        """Extract reasoning/thinking text from the last assistant message."""
        messages = result.get("messages") or []
        for message in reversed(messages):
            if message.get("role") == "assistant":
                reasoning = message.get("reasoning") or message.get("thinking")
                if isinstance(reasoning, str) and reasoning.strip():
                    return reasoning
        return ""
