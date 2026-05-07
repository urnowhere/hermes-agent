"""Formsy context engine implementation."""

import json
import logging
from pathlib import Path
from typing import Any, Optional

from hermes_constants import get_hermes_home
from agent.context_engine import ContextEngine
from plugins.formsy import RuntimeClient
from .config import EngineConfigManager, EngineConfig
from .client import EngineClient
from .message_converter import (
    convert_compile_bundle_to_messages,
    detect_scene,
    extract_task,
)

logger = logging.getLogger("formsy.context_engine")


class FormsyContextEngine(ContextEngine):
    """Formsy context engine for Hermes."""

    def __init__(self):
        self._config: Optional[EngineConfig] = None
        self._runtime_client: Optional[RuntimeClient] = None
        self._engine_client: Optional[EngineClient] = None
        self._session_id: Optional[str] = None
        self._turn_counter: int = 0
        self._context: dict[str, Any] = {}

        self.last_prompt_tokens = 0
        self.last_completion_tokens = 0
        self.last_total_tokens = 0
        self.threshold_tokens = 0
        self.context_length = 0
        self.compression_count = 0

    @property
    def name(self) -> str:
        return "formsy"

    async def _initialize_runtime(self, config: dict, hermes_home: Path) -> None:
        """Initialize Formsy runtime resources."""
        logger.info("Initializing formsy context engine")

        config_manager = EngineConfigManager(hermes_home)
        self._config = config_manager.load_config(config)

        self._runtime_client = RuntimeClient(
            base_url=self._config.base_url,
            memory_search_endpoint=self._config.memory_search_endpoint,
            api_key_env=self._config.api_key_env,
            timeout_s=self._config.timeout_s,
            max_retries=self._config.max_retries,
        )

        await self._runtime_client.__aenter__()
        self._engine_client = EngineClient(self._runtime_client)

        logger.info("Engine initialized successfully")

    def update_from_response(self, usage: dict[str, Any]) -> None:
        """Update tracked token usage from an API response."""
        self._turn_counter += 1
        self.last_prompt_tokens = int(usage.get("prompt_tokens") or 0)
        self.last_completion_tokens = int(usage.get("completion_tokens") or 0)
        self.last_total_tokens = int(usage.get("total_tokens") or 0)

    def should_compress(self, prompt_tokens: int = None) -> bool:
        """Return True when token usage exceeds the configured threshold."""
        tokens = self.last_prompt_tokens if prompt_tokens is None else prompt_tokens
        return bool(self.threshold_tokens and tokens >= self.threshold_tokens)

    def compress(
        self,
        messages: list[dict],
        current_tokens: int = None,
        focus_topic: Optional[str] = None,
    ) -> list[dict]:
        """Compile context via FormalCC Runtime."""
        if current_tokens is not None:
            self.last_prompt_tokens = current_tokens

        compiled = self._run_async(
            self._compress_async(messages, current_tokens=current_tokens, focus_topic=focus_topic)
        )
        return compiled if isinstance(compiled, list) else messages

    def on_session_start(self, session_id: str, **kwargs) -> None:
        """Initialize runtime state when a Hermes session starts."""
        self._session_id = session_id
        self._context = dict(kwargs)
        self._context["session_id"] = session_id

        if self._engine_client:
            return

        hermes_home = Path(kwargs.get("hermes_home") or get_hermes_home())
        config = kwargs.get("config") if isinstance(kwargs.get("config"), dict) else {}
        self._run_async(self._initialize_runtime(config, hermes_home))

    def update_model(
        self,
        model: str,
        context_length: int,
        base_url: str = "",
        api_key: str = "",
        provider: str = "",
    ) -> None:
        """Update context-window metadata used by Hermes preflight checks."""
        super().update_model(model, context_length, base_url, api_key, provider)
        self._context.update({
            "model": model,
            "base_url": base_url,
            "provider": provider,
        })

    def on_session_end(self, session_id: str, messages: list[dict[str, Any]]) -> None:
        """Close the underlying async HTTP client at a real session boundary."""
        if not self._runtime_client:
            return
        self._run_async(self._runtime_client.__aexit__(None, None, None))
        self._runtime_client = None
        self._engine_client = None

    def on_session_reset(self) -> None:
        """Reset per-session counters and cached context."""
        super().on_session_reset()
        self._turn_counter = 0
        self._session_id = None
        self._context = {}

    def get_tool_schemas(self) -> list[dict[str, Any]]:
        """Expose Formsy memory/context search to the agent."""
        return [
            {
                "name": "memory_search",
                "description": (
                    "Search Formsy's compiled code memory/context for information "
                    "relevant to a natural-language query. Use memory_search proactively "
                    "and repeatedly to understand the codebase faster. Prefer several "
                    "targeted queries, such as symbols, file paths, PR behavior, call flow, "
                    "and edge cases, over one broad query. The memory compile step has "
                    "already completed before the task starts, so this tool is ready to "
                    "use immediately. For SWE-bench tasks, pass repo_id and revision from "
                    "the task metadata directly, for example repo_id='django__django-14053' "
                    "and revision='latest'."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "Natural-language query describing the code, behavior, or fact to find.",
                        },
                        "repo_id": {
                            "type": "string",
                            "description": "External repository identifier required by Formsy query API. Use the task metadata repo_id directly, e.g. django__django-14053.",
                        },
                        "revision": {
                            "type": "string",
                            "description": "Logical revision label to query. Use the task metadata revision directly when provided; otherwise use latest.",
                            "default": "latest",
                        },
                        "budget": {
                            "type": "integer",
                            "description": "Context token budget for the Formsy query.",
                            "default": 4000,
                            "minimum": 1,
                        },
                        "limit": {
                            "type": "integer",
                            "description": "Deprecated. Kept for compatibility; Formsy query uses budget instead.",
                            "default": 5,
                            "minimum": 1,
                            "maximum": 20,
                        },
                    },
                    "required": ["query"],
                },
            },
            {
                "name": "memory_read",
                "description": (
                    "Read exact source context from Formsy's compiled repository memory. "
                    "Use memory_read after memory_search returns a relevant file path or "
                    "line range. This is the preferred way to inspect source code for "
                    "SWE-bench tasks when direct file-content reads are discouraged."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "Repository-relative file path to read.",
                        },
                        "repo_id": {
                            "type": "string",
                            "description": "External repository identifier required by Formsy query API. Use the task metadata repo_id directly, e.g. django__django-14053.",
                        },
                        "revision": {
                            "type": "string",
                            "description": "Logical revision label to query. Use the task metadata revision directly when provided; otherwise use latest.",
                            "default": "latest",
                        },
                        "start_line": {
                            "type": "integer",
                            "description": "Optional 1-indexed first source line to read.",
                            "minimum": 1,
                        },
                        "end_line": {
                            "type": "integer",
                            "description": "Optional inclusive 1-indexed last source line to read.",
                            "minimum": 1,
                        },
                    },
                    "required": ["path"],
                },
            },
        ]

    def handle_tool_call(self, name: str, args: dict[str, Any], **kwargs) -> str:
        """Handle Formsy context-engine tool calls."""
        if name == "memory_read":
            return self._handle_memory_read(args)
        if name != "memory_search":
            return super().handle_tool_call(name, args, **kwargs)

        query = str(args.get("query") or "").strip()
        if not query:
            return json.dumps({"ok": False, "error": "memory_search requires a non-empty query"})

        if not self._engine_client:
            return json.dumps({"ok": False, "query": query, "error": "Formsy engine client is not initialized"})

        session_id = self._session_id or self._context.get("session_id") or "unknown"
        repo_id = str(args.get("repo_id") or (self._config.repo_id if self._config else "") or "").strip()
        if not repo_id:
            return json.dumps({
                "ok": False,
                "query": query,
                "error": "memory_search requires repo_id. Set formsy.repo_id or pass repo_id in the tool call.",
            })
        revision = str(args.get("revision") or (self._config.revision if self._config else "latest") or "latest")
        budget = self._coerce_positive_int(args.get("budget"), self._config.query_budget if self._config else 4000)
        result = self._run_async(
            self._engine_client.memory_search(
                repo_id=repo_id,
                session_id=session_id,
                query=query,
                revision=revision,
                budget=budget,
            )
        )
        if result is None:
            return json.dumps({"ok": False, "query": query, "error": "Formsy memory search failed"})

        payload = {
            "ok": True,
            "query": query,
            "extra_context": self._extract_extra_context(result),
        }
        for key in ("matches", "suggested_queries", "coverage", "missing_context"):
            if key in result:
                payload[key] = result[key]
        return json.dumps(payload)

    def _handle_memory_read(self, args: dict[str, Any]) -> str:
        path = str(args.get("path") or "").strip()
        if not path:
            return json.dumps({"ok": False, "error": "memory_read requires a non-empty path"})

        if not self._engine_client:
            return json.dumps({"ok": False, "path": path, "error": "Formsy engine client is not initialized"})

        session_id = self._session_id or self._context.get("session_id") or "unknown"
        repo_id = str(args.get("repo_id") or (self._config.repo_id if self._config else "") or "").strip()
        if not repo_id:
            return json.dumps({
                "ok": False,
                "path": path,
                "error": "memory_read requires repo_id. Set formsy.repo_id or pass repo_id in the tool call.",
            })
        revision = str(args.get("revision") or (self._config.revision if self._config else "latest") or "latest")
        start_line = self._optional_positive_int(args.get("start_line"))
        end_line = self._optional_positive_int(args.get("end_line"))
        result = self._run_async(
            self._engine_client.memory_read(
                repo_id=repo_id,
                session_id=session_id,
                path=path,
                revision=revision,
                start_line=start_line,
                end_line=end_line,
            )
        )
        if result is None:
            return json.dumps({"ok": False, "path": path, "error": "Formsy memory read failed"})

        payload = {"ok": True}
        if isinstance(result, dict):
            payload.update(result)
        return json.dumps(payload)

    def _run_async(self, coro):
        """Run Formsy async API calls from the synchronous ContextEngine API."""
        try:
            from model_tools import _run_async
            return _run_async(coro)
        except Exception:
            logger.exception("Formsy async call failed")
            return None

    @staticmethod
    def _coerce_positive_int(value: Any, default: int) -> int:
        try:
            number = int(value)
        except (TypeError, ValueError):
            number = default
        return max(1, number)

    @staticmethod
    def _optional_positive_int(value: Any) -> int | None:
        if value is None:
            return None
        try:
            number = int(value)
        except (TypeError, ValueError):
            return None
        return max(1, number)

    @staticmethod
    def _extract_extra_context(result: Any) -> str:
        if not isinstance(result, dict):
            return ""
        extra = result.get("extra_context")
        if isinstance(extra, str):
            return extra
        memory_block = result.get("memory_block")
        if isinstance(memory_block, str):
            return memory_block
        return ""

    async def _compress_async(
        self,
        messages: list[dict],
        current_tokens: int = None,
        focus_topic: Optional[str] = None,
    ) -> list[dict]:
        """Compile context via Formsy Runtime."""
        if not self._engine_client:
            logger.warning("Engine client not initialized; returning original messages")
            return messages

        context = dict(self._context)
        session_id = self._session_id or context.get("session_id") or "unknown"
        self._session_id = session_id
        turn_id = f"{session_id}_turn_{self._turn_counter:04d}"

        # Detect scene from context
        scene = detect_scene(context)

        # Build identity hints
        identity: Optional[dict] = None
        if repo_id := context.get("repo_id"):
            identity = {
                "repo_id": repo_id,
                "revision": context.get("revision", "main"),
            }
        elif document_id := context.get("document_id"):
            identity = {"document_id": document_id}

        # Extract task from messages
        task = extract_task(messages)

        # Build hints
        hints: dict[str, Any] = {}
        if focus_topic:
            hints["focus_topic"] = focus_topic
        if current_tokens is not None:
            hints["current_tokens"] = current_tokens
        hints["bypass_router"] = False

        logger.info(
            f"Compiling context: session={session_id}, scene={scene}, "
            f"focus_topic={focus_topic}"
        )

        bundle = await self._engine_client.compile(
            workspace_id=self._config.workspace_id,
            session_id=session_id,
            turn_id=turn_id,
            scene=scene,
            identity=identity,
            task=task,
            hints=hints,
        )

        if bundle is None:
            # Graceful degradation: return original messages
            logger.warning("Compile failed; returning original messages")
            return messages

        compiled = convert_compile_bundle_to_messages(bundle)

        if not compiled:
            logger.warning("Compile returned empty messages; returning originals")
            return messages

        self.compression_count += 1
        logger.info(f"Compiled to {len(compiled)} messages (was {len(messages)})")
        return compiled
