"""Client wrapper for context engine."""

import logging
from typing import Optional, Any

from plugins.formsy import RuntimeClient
from plugins.formsy.errors import RuntimeAPIError, TimeoutError as FormalCCTimeoutError

logger = logging.getLogger("formsy.context_engine.client")


class EngineClient:
    """Client for context engine Runtime API calls."""

    def __init__(self, runtime_client: RuntimeClient):
        self.runtime_client = runtime_client
        self.last_error: str = ""

    async def compile_repo(
        self,
        repo_id: str,
        files: list[dict[str, Any]],
        revision: str = "latest",
        metadata: Optional[dict[str, Any]] = None,
        session_id: str = "",
        mode: str = "merge",
    ) -> Optional[dict[str, Any]]:
        """Compile repository source for memory search/read endpoints."""
        try:
            self.last_error = ""
            return await self.runtime_client.compile_repo(
                repo_id=repo_id,
                files=files,
                revision=revision,
                metadata=metadata,
                session_id=session_id or None,
                mode=mode,
            )
        except (RuntimeAPIError, FormalCCTimeoutError) as e:
            self.last_error = f"{e.__class__.__name__}: {e}"
            logger.warning(f"Repository compile failed: {e}")
            return None
        except Exception as e:
            self.last_error = f"{e.__class__.__name__}: {e}"
            logger.error(f"Unexpected error in repository compile: {e}")
            return None

    async def compile_status(
        self,
        repo_id: str,
        revision: str = "latest",
        session_id: str = "",
    ) -> Optional[dict[str, Any]]:
        """Return repository compile status if the server already has it."""
        try:
            self.last_error = ""
            return await self.runtime_client.compile_status(
                repo_id=repo_id,
                revision=revision,
                session_id=session_id or None,
            )
        except RuntimeAPIError as e:
            self.last_error = f"{e.__class__.__name__}: {e}"
            if getattr(e, "status_code", None) != 404:
                logger.warning(f"Repository compile status failed: {e}")
            return None
        except FormalCCTimeoutError as e:
            self.last_error = f"{e.__class__.__name__}: {e}"
            logger.warning(f"Repository compile status timed out: {e}")
            return None
        except Exception as e:
            self.last_error = f"{e.__class__.__name__}: {e}"
            logger.error(f"Unexpected error in repository compile status: {e}")
            return None

    async def memory_search(
        self,
        repo_id: str,
        session_id: str,
        query: str,
        revision: str = "latest",
        budget: int = 4000,
        metadata: Optional[dict[str, Any]] = None,
        identity: Optional[dict[str, Any]] = None,
    ) -> Optional[dict[str, Any]]:
        """Search Formsy memory/context for relevant snippets."""
        try:
            self.last_error = ""
            return await self.runtime_client.memory_search(
                repo_id=repo_id,
                session_id=session_id,
                query=query,
                revision=revision,
                budget=budget,
                metadata=metadata,
                **({"identity": identity} if identity else {}),
            )
        except (RuntimeAPIError, FormalCCTimeoutError) as e:
            self.last_error = f"{e.__class__.__name__}: {e}"
            logger.warning(f"Memory search failed: {e}")
            return None
        except Exception as e:
            self.last_error = f"{e.__class__.__name__}: {e}"
            logger.error(f"Unexpected error in memory search: {e}")
            return None

    async def memory_read(
        self,
        repo_id: str,
        session_id: str,
        path: str,
        revision: str = "latest",
        start_line: int | None = None,
        end_line: int | None = None,
        identity: Optional[dict[str, Any]] = None,
    ) -> Optional[dict[str, Any]]:
        """Read exact source context from Formsy compiled repository memory."""
        try:
            self.last_error = ""
            return await self.runtime_client.memory_read(
                repo_id=repo_id,
                session_id=session_id,
                path=path,
                revision=revision,
                start_line=start_line,
                end_line=end_line,
                **({"identity": identity} if identity else {}),
            )
        except (RuntimeAPIError, FormalCCTimeoutError) as e:
            self.last_error = f"{e.__class__.__name__}: {e}"
            logger.warning(f"Memory read failed: {e}")
            return None
        except Exception as e:
            self.last_error = f"{e.__class__.__name__}: {e}"
            logger.error(f"Unexpected error in memory read: {e}")
            return None
