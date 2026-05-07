"""FormalCC Memory Provider implementation."""

import logging
from pathlib import Path
from typing import Optional, Any
from abc import ABC, abstractmethod

from plugins.formsy import RuntimeClient
from plugins.formsy.models import SyncMode
from .config import ConfigManager, MemoryConfig
from .client import MemoryClient

logger = logging.getLogger("formalcc.memory.provider")


class MemoryProvider(ABC):
    """Abstract base class for Hermes memory providers."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Provider name."""
        pass

    @abstractmethod
    def is_available(self) -> bool:
        """Check if provider is available (no network calls)."""
        pass

    @abstractmethod
    async def initialize(self, config: dict, hermes_home: Path) -> None:
        """Initialize provider with config and home directory."""
        pass

    @abstractmethod
    async def prefetch(self, context: dict) -> Optional[str]:
        """Prefetch memory before model call."""
        pass

    @abstractmethod
    async def sync_turn(self, turn_data: dict) -> None:
        """Sync turn data to memory (non-blocking)."""
        pass

    @abstractmethod
    async def session_end(self, session_data: dict) -> None:
        """Finalize session and flush memory."""
        pass

    @abstractmethod
    def get_tool_schemas(self) -> list[dict]:
        """Return tool schemas for memory tools."""
        pass

    @abstractmethod
    async def handle_tool_call(self, tool_name: str, arguments: dict) -> dict:
        """Handle memory tool invocations."""
        pass

    @abstractmethod
    def get_config_schema(self) -> dict:
        """Return JSON schema for provider config."""
        pass

    @abstractmethod
    async def save_config(self, config: dict) -> None:
        """Save provider configuration."""
        pass


class FormalCCMemoryProvider(MemoryProvider):
    """FormalCC Memory Provider for Hermes."""

    def __init__(self):
        self._config: Optional[MemoryConfig] = None
        self._runtime_client: Optional[RuntimeClient] = None
        self._memory_client: Optional[MemoryClient] = None
        self._hermes_home: Optional[Path] = None
        self._session_id: Optional[str] = None
        self._turn_counter: int = 0
    
    @property
    def name(self) -> str:
        """Provider name."""
        return "formalcc-memory"
    
    def is_available(self) -> bool:
        """Check if provider is available."""
        return self._config is not None
    
    async def initialize(self, config: dict, hermes_home: Path) -> None:
        """Initialize provider."""
        logger.info("Initializing formalcc-memory provider")
        
        self._hermes_home = hermes_home
        config_manager = ConfigManager(hermes_home)
        self._config = config_manager.load_config(config)
        
        # Initialize runtime client
        self._runtime_client = RuntimeClient(
            base_url=self._config.base_url,
            api_key_env=self._config.api_key_env,
            timeout_s=self._config.timeout_s,
            max_retries=self._config.max_retries,
        )
        
        # Initialize memory client
        await self._runtime_client.__aenter__()
        self._memory_client = MemoryClient(self._runtime_client)
        
        logger.info("Provider initialized successfully")
    
    async def prefetch(self, context: dict) -> Optional[str]:
        """Prefetch memory before model call."""
        if not self._memory_client:
            logger.warning("Memory client not initialized")
            return None
        
        session_id = context.get("session_id", "unknown")
        self._session_id = session_id
        self._turn_counter += 1
        turn_id = f"{session_id}_turn_{self._turn_counter:04d}"
        
        query = context.get("query", context.get("user_message", ""))
        hints = context.get("hints", {})
        
        memory_block = await self._memory_client.prefetch(
            workspace_id=self._config.workspace_id,
            session_id=session_id,
            turn_id=turn_id,
            query=query,
            hints=hints,
        )
        
        return memory_block if memory_block else None
    
    async def sync_turn(self, turn_data: dict) -> None:
        """Sync turn data to memory."""
        if not self._memory_client:
            logger.warning("Memory client not initialized")
            return

        session_id = turn_data.get("session_id", self._session_id)
        turn_id = turn_data.get("turn_id", f"{session_id}_turn_{self._turn_counter:04d}")
        messages = turn_data.get("messages")
        if messages is None:
            messages = [
                {
                    "role": "user",
                    "content": turn_data.get("user_message", ""),
                },
                {
                    "role": "assistant",
                    "content": turn_data.get("assistant_message", ""),
                },
            ]

        await self._memory_client.sync_turn(
            workspace_id=self._config.workspace_id,
            session_id=session_id,
            turn_id=turn_id,
            messages=messages,
            identity=turn_data.get("identity"),
            sync_mode=SyncMode(turn_data.get("sync_mode", SyncMode.ASYNC_BEST_EFFORT)),
        )
    
    async def session_end(self, session_data: dict) -> None:
        """Finalize session."""
        if not self._runtime_client:
            return
        
        try:
            from shared.models import SessionEndRequest
            
            request = SessionEndRequest(
                workspace_id=self._config.workspace_id,
                session_id=session_data.get("session_id", self._session_id),
                identity=session_data.get("identity"),
                summary_hint=session_data.get("summary_hint"),
            )
            
            await self._runtime_client.session_end(request)
            logger.info("Session ended successfully")
        
        except Exception as e:
            logger.error(f"Session end failed: {e}")
        
        finally:
            if self._runtime_client:
                await self._runtime_client.__aexit__(None, None, None)
    
    def get_tool_schemas(self) -> list[dict]:
        """Return tool schemas."""
        if not self._config or not self._config.enable_memory_tools:
            return []
        
        return [
            {
                "name": "cc_memory_search",
                "description": "Search FormalCC memory for relevant context",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "Search query"
                        },
                        "top_k": {
                            "type": "integer",
                            "description": "Maximum results",
                            "default": 5
                        }
                    },
                    "required": ["query"]
                }
            },
            {
                "name": "cc_memory_profile",
                "description": "Get memory profile and statistics",
                "parameters": {
                    "type": "object",
                    "properties": {}
                }
            }
        ]
    
    async def handle_tool_call(self, tool_name: str, arguments: dict) -> dict:
        """Handle memory tool invocations."""
        if not self._runtime_client:
            return {"error": "Memory client not initialized"}
        
        try:
            if tool_name == "cc_memory_search":
                query = arguments.get("query", "")
                top_k = arguments.get("top_k", arguments.get("limit", 5))
                
                result = await self._runtime_client.memory_search(
                    workspace_id=self._config.workspace_id,
                    session_id=self._session_id or "unknown",
                    query=query,
                    top_k=top_k,
                )
                
                return {"result": result}
            
            elif tool_name == "cc_memory_profile":
                return {
                    "workspace_id": self._config.workspace_id,
                    "session_id": self._session_id,
                    "turn_count": self._turn_counter,
                }
            
            else:
                return {"error": f"Unknown tool: {tool_name}"}
        
        except Exception as e:
            logger.error(f"Tool call failed: {e}")
            return {"error": str(e)}
    
    def get_config_schema(self) -> dict:
        """Return JSON schema for config."""
        return {
            "type": "object",
            "properties": {
                "base_url": {"type": "string"},
                "api_key_env": {"type": "string"},
                "workspace_id": {"type": "string"},
                "tenant_id": {"type": "string"},
                "timeout_s": {"type": "integer"},
                "max_retries": {"type": "integer"},
                "enable_memory_tools": {"type": "boolean"},
                "enable_diagnostics": {"type": "boolean"},
            }
        }
    
    async def save_config(self, config: dict) -> None:
        """Save provider configuration."""
        if not self._hermes_home:
            raise RuntimeError("Provider not initialized")
        
        config_manager = ConfigManager(self._hermes_home)
        config_file = config_manager.config_file
        
        import json
        with open(config_file, "w") as f:
            json.dump(config, f, indent=2)
        
        logger.info(f"Config saved to {config_file}")
