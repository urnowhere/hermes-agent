#!/usr/bin/env python3
"""
A2A (Agent-to-Agent) Protocol Client Support

Connects to remote A2A agents via HTTP, discovers their capabilities through
Agent Cards, and enables Hermes to delegate tasks to agents built on any
framework (LangChain, CrewAI, Google ADK, AutoGen, etc.).

Configuration is read from ~/.hermes/config.yaml under the ``a2a_agents`` key.
The ``a2a-sdk`` Python package is optional -- if not installed, this module is a
no-op and logs a debug message.

Example config::

    a2a_agents:
      researcher:
        url: "http://localhost:9999"
        auth:
          type: "bearer"
          token: "sk-..."
        timeout: 120
      coder:
        url: "http://remote-agent:8080"

Features:
    - Agent Card discovery from any A2A-compliant endpoint
    - Synchronous and streaming message sending
    - Multi-turn task support (INPUT_REQUIRED state)
    - Config-driven named agents with direct URL fallback
    - Thread-safe agent card caching
    - Credential stripping in error messages
    - Optional dependency -- graceful degradation when a2a-sdk not installed

Architecture:
    A dedicated background event loop (_a2a_loop) runs in a daemon thread,
    mirroring the MCP tool pattern. Async A2A SDK calls are scheduled onto
    this loop via ``run_coroutine_threadsafe()``.

Thread safety:
    _agent_cards cache and _a2a_loop/_a2a_thread are accessed from multiple
    threads. All mutations are protected by _lock.
"""

import asyncio
import json
import logging
import os
import re
import threading
from typing import Any, Dict, Optional
from uuid import uuid4

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Graceful import -- A2A SDK is an optional dependency
# ---------------------------------------------------------------------------

_A2A_AVAILABLE = False
try:
    import httpx
    from a2a.client import A2ACardResolver
    from a2a.client.helpers import create_text_message_object

    # Try new ClientFactory pattern (v0.3.25+)
    _A2A_CLIENT_FACTORY = False
    try:
        from a2a.client.client import ClientConfig
        from a2a.client.client_factory import ClientFactory
        _A2A_CLIENT_FACTORY = True
    except ImportError:
        pass

    # Fallback: legacy A2AClient (deprecated but functional)
    _A2A_LEGACY_CLIENT = False
    if not _A2A_CLIENT_FACTORY:
        try:
            from a2a.client.legacy import A2AClient
            _A2A_LEGACY_CLIENT = True
        except ImportError:
            pass

    # Types for building requests
    try:
        from a2a.types import (
            MessageSendParams,
            SendMessageRequest,
            SendStreamingMessageRequest,
        )
        _A2A_TYPES_AVAILABLE = True
    except ImportError:
        _A2A_TYPES_AVAILABLE = False

    _A2A_AVAILABLE = True
    logger.debug("A2A SDK loaded (factory=%s, legacy=%s, types=%s)",
                  _A2A_CLIENT_FACTORY, _A2A_LEGACY_CLIENT, _A2A_TYPES_AVAILABLE)
except ImportError:
    logger.debug("a2a-sdk package not installed -- A2A tool support disabled")

from tools.registry import registry

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_DEFAULT_TIMEOUT = 120  # seconds for A2A calls

# Credential patterns to strip from error messages
_CREDENTIAL_PATTERN = re.compile(
    r"(?:"
    r"sk-[A-Za-z0-9_]{1,255}"
    r"|Bearer\s+\S+"
    r"|token=[^\s&,;\"']{1,255}"
    r"|key=[^\s&,;\"']{1,255}"
    r")",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Background event loop (mirrors mcp_tool.py pattern)
# ---------------------------------------------------------------------------

_lock = threading.Lock()
_async_lock: Optional[asyncio.Lock] = None  # Created lazily on the background loop
_a2a_loop: Optional[asyncio.AbstractEventLoop] = None
_a2a_thread: Optional[threading.Thread] = None
_agent_cards: Dict[str, Any] = {}  # url -> AgentCard cache
_config_cache: Optional[Dict[str, Any]] = None


def _get_async_lock() -> asyncio.Lock:
    """Get or create the async lock on the background event loop."""
    global _async_lock
    if _async_lock is None:
        _async_lock = asyncio.Lock()
    return _async_lock


def _ensure_loop() -> asyncio.AbstractEventLoop:
    """Start the background event loop if not already running."""
    global _a2a_loop, _a2a_thread
    with _lock:
        if _a2a_loop is not None and _a2a_loop.is_running():
            return _a2a_loop

        loop = asyncio.new_event_loop()
        _a2a_loop = loop

        def _run():
            asyncio.set_event_loop(loop)
            loop.run_forever()

        thread = threading.Thread(target=_run, daemon=True, name="a2a-loop")
        thread.start()
        _a2a_thread = thread
        return loop


def _run_on_loop(coro, timeout: Optional[float] = None) -> Any:
    """Schedule a coroutine on the background loop and wait for the result."""
    loop = _ensure_loop()
    future = asyncio.run_coroutine_threadsafe(coro, loop)
    return future.result(timeout=timeout or (_DEFAULT_TIMEOUT + 30))


def _sanitize_error(text: str) -> str:
    """Strip credential-like patterns from error text."""
    return _CREDENTIAL_PATTERN.sub("[REDACTED]", text)


# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------

def _load_a2a_config() -> Dict[str, Any]:
    """Load a2a_agents config from ~/.hermes/config.yaml."""
    global _config_cache
    if _config_cache is not None:
        return _config_cache

    try:
        import yaml
        config_path = os.path.join(
            os.path.expanduser(os.getenv("HERMES_HOME", "~/.hermes")),
            "config.yaml",
        )
        if os.path.exists(config_path):
            with open(config_path, "r") as f:
                config = yaml.safe_load(f) or {}
            _config_cache = config.get("a2a_agents", {})
        else:
            _config_cache = {}
    except Exception as e:
        logger.debug("Failed to load A2A config: %s", e)
        _config_cache = {}

    return _config_cache


def _resolve_agent_url(agent: str) -> tuple:
    """Resolve an agent name or URL to (url, config_dict).

    Returns:
        Tuple of (base_url, config_dict). config_dict may be empty for direct URLs.
    """
    # Direct URL
    if agent.startswith("http://") or agent.startswith("https://"):
        return agent.rstrip("/"), {}

    # Config lookup
    config = _load_a2a_config()
    if agent in config:
        entry = config[agent]
        url = entry.get("url", "").rstrip("/")
        if not url:
            raise ValueError(f"A2A agent '{agent}' has no 'url' in config")
        return url, entry

    raise ValueError(
        f"Unknown A2A agent '{agent}'. "
        f"Provide a direct URL or configure it in ~/.hermes/config.yaml under a2a_agents."
    )


# ---------------------------------------------------------------------------
# Registry & skill matching helpers
# ---------------------------------------------------------------------------

def _build_agent_registry() -> list:
    """Merge config agents + cached discovered cards into a unified list.

    Each entry: {name, url, source, status, skills, description}.
    Thread-safe reads of _agent_cards via _lock. No network calls.
    """
    agents = []

    # Config agents
    config = _load_a2a_config()
    for name, entry in config.items():
        url = entry.get("url", "").rstrip("/")
        if not url:
            continue

        with _lock:
            card = _agent_cards.get(url)

        info = {
            "name": name,
            "url": url,
            "source": "config",
            "status": "discovered" if card else "configured",
            "skills": [],
            "description": "",
        }
        if card:
            formatted = _format_agent_card(card)
            info["skills"] = formatted.get("skills", [])
            info["description"] = formatted.get("description", "")

        agents.append(info)

    # Discovered agents not in config
    config_urls = {e.get("url", "").rstrip("/") for e in config.values()}
    with _lock:
        discovered = dict(_agent_cards)

    for url, card in discovered.items():
        if url in config_urls:
            continue
        formatted = _format_agent_card(card)
        agents.append({
            "name": formatted.get("name", url),
            "url": url,
            "source": "discovered",
            "status": "discovered",
            "skills": formatted.get("skills", []),
            "description": formatted.get("description", ""),
        })

    return agents


def _match_skills_to_goal(goal: str, agent_info: dict) -> float:
    """Simple keyword overlap scoring (0.0-1.0).

    Tokenizes goal into lowercase words (>2 chars), compares against
    skill names/descriptions/ids + agent description. No LLM needed.
    """
    if not goal:
        return 0.0

    goal_words = {w.lower() for w in goal.split() if len(w) > 2}
    if not goal_words:
        return 0.0

    # Build corpus from agent info
    corpus_parts = [agent_info.get("description", "")]
    for skill in agent_info.get("skills", []):
        corpus_parts.append(skill.get("name", ""))
        corpus_parts.append(skill.get("description", ""))
        corpus_parts.append(skill.get("id", ""))

    corpus = " ".join(corpus_parts).lower()
    corpus_words = {w for w in corpus.split() if len(w) > 2}

    if not corpus_words:
        return 0.0

    overlap = goal_words & corpus_words
    return len(overlap) / len(goal_words)


def _auto_select_agents(goal: str) -> list:
    """Score all registry agents against goal, return those with positive scores.

    Falls back to all agents if no skill matches.
    Raises ValueError if registry is empty.

    Returns:
        List of (url, config_dict) tuples sorted by score descending.
    """
    agents = _build_agent_registry()
    if not agents:
        raise ValueError("No agents in registry. Configure agents or discover them first.")

    config = _load_a2a_config()

    scored = []
    for info in agents:
        score = _match_skills_to_goal(goal, info)
        scored.append((score, info))

    # Sort by score descending
    scored.sort(key=lambda x: x[0], reverse=True)

    def _agent_config(info: dict) -> dict:
        """Get config for an agent, preserving auth for discovered agents."""
        cfg = config.get(info["name"], {})
        if cfg:
            return cfg
        # For discovered agents not in config, build a minimal config
        # from the registry entry so the URL and any cached state are preserved
        return {"url": info["url"], "_name": info["name"]}

    # Filter to positive scores
    selected = [(info["url"], _agent_config(info))
                for score, info in scored if score > 0.0]

    # Fall back to all agents if nothing matched
    if not selected:
        selected = [(info["url"], _agent_config(info))
                    for _, info in scored]

    return selected


# ---------------------------------------------------------------------------
# Core async operations
# ---------------------------------------------------------------------------

async def _async_discover(url: str, config: dict) -> dict:
    """Fetch and cache an Agent Card from a remote A2A agent."""
    async_lock = _get_async_lock()
    async with async_lock:
        if url in _agent_cards:
            return _format_agent_card(_agent_cards[url])

    timeout = config.get("timeout", _DEFAULT_TIMEOUT)
    headers = {}
    auth = config.get("auth", {})
    if auth.get("type") == "bearer" and auth.get("token"):
        headers["Authorization"] = f"Bearer {auth['token']}"

    async with httpx.AsyncClient(timeout=timeout, headers=headers) as client:
        resolver = A2ACardResolver(
            httpx_client=client,
            base_url=url,
        )
        card = await resolver.get_agent_card()

    async with async_lock:
        _agent_cards[url] = card

    # Also update under threading lock for sync readers (_build_agent_registry)
    with _lock:
        _agent_cards[url] = card

    return _format_agent_card(card)


async def _async_call(url: str, config: dict, message: str, stream: bool = False) -> str:
    """Send a message to a remote A2A agent and return the response."""
    # Ensure we have the agent card
    with _lock:
        card = _agent_cards.get(url)

    if not card:
        await _async_discover(url, config)
        with _lock:
            card = _agent_cards.get(url)

    if not card:
        return json.dumps({"error": f"Failed to discover agent at {url}"})

    timeout = config.get("timeout", _DEFAULT_TIMEOUT)
    headers = {}
    auth = config.get("auth", {})
    if auth.get("type") == "bearer" and auth.get("token"):
        headers["Authorization"] = f"Bearer {auth['token']}"

    async with httpx.AsyncClient(timeout=timeout, headers=headers) as http_client:
        if _A2A_CLIENT_FACTORY:
            return await _call_with_factory(http_client, card, message, stream)
        elif _A2A_LEGACY_CLIENT:
            return await _call_with_legacy(http_client, card, message, stream)
        else:
            return json.dumps({"error": "No A2A client implementation available. Update a2a-sdk."})


async def _call_with_factory(http_client, card, message: str, stream: bool) -> str:
    """Send message using the new ClientFactory pattern."""
    from a2a.types import Message, Part, TextPart, Role, SendMessageRequest, MessageSendParams

    # ClientConfig accepts streaming flag only; pass httpx_client to the
    # factory so auth headers and timeout are preserved on the actual client.
    cfg = ClientConfig(streaming=stream)
    factory = ClientFactory(config=cfg)
    client = factory.create(card, httpx_client=http_client)

    msg = Message(
        role=Role.user,
        parts=[Part(root=TextPart(text=message))],
        message_id=uuid4().hex,
    )
    params = MessageSendParams(message=msg)
    request = SendMessageRequest(id=uuid4().hex, params=params)

    try:
        if stream:
            results = []
            async for event in client.send_message_streaming(request):
                results.append(str(event))
            return _format_call_response(results)
        else:
            response = await client.send_message(request)
            return json.dumps(_extract_response(response), indent=2)
    finally:
        try:
            await client.close()
        except Exception:
            pass


async def _call_with_legacy(http_client, card, message: str, stream: bool) -> str:
    """Send message using deprecated A2AClient."""
    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        client = A2AClient(httpx_client=http_client, agent_card=card)

    msg_obj = create_text_message_object(content=message)

    if not _A2A_TYPES_AVAILABLE:
        return json.dumps({"error": "A2A message types not available."})

    params = MessageSendParams(message=msg_obj)

    if stream:
        request = SendStreamingMessageRequest(id=uuid4().hex, params=params)
        results = []
        async for event in client.send_message_streaming(request):
            results.append(str(event))
        return _format_call_response(results)
    else:
        request = SendMessageRequest(id=uuid4().hex, params=params)
        response = await client.send_message(request)
        return json.dumps(_extract_response(response), indent=2)


# ---------------------------------------------------------------------------
# Response formatting
# ---------------------------------------------------------------------------

def _format_agent_card(card) -> dict:
    """Format an AgentCard into a clean dict for the LLM."""
    result = {
        "name": getattr(card, "name", "Unknown"),
        "description": getattr(card, "description", ""),
        "url": getattr(card, "url", ""),
    }

    # Skills
    skills = getattr(card, "skills", None)
    if skills:
        result["skills"] = []
        for skill in skills:
            skill_info = {
                "id": getattr(skill, "id", ""),
                "name": getattr(skill, "name", ""),
                "description": getattr(skill, "description", ""),
            }
            result["skills"].append(skill_info)

    # Capabilities
    caps = getattr(card, "capabilities", None)
    if caps:
        result["capabilities"] = {
            "streaming": getattr(caps, "streaming", False),
            "pushNotifications": getattr(caps, "pushNotifications", False),
        }

    # Auth schemes
    security_schemes = getattr(card, "securitySchemes", None)
    if security_schemes:
        result["auth_schemes"] = list(security_schemes.keys()) if isinstance(security_schemes, dict) else []

    return result


def _format_call_response(results: list) -> str:
    """Format collected streaming results into a response string."""
    if not results:
        return json.dumps({"status": "completed", "response": "(empty response)"})

    # Join all chunks
    combined = "\n".join(results)
    return json.dumps({"status": "completed", "response": combined}, indent=2)


def _extract_response(response) -> dict:
    """Extract meaningful content from a SendMessageResponse."""
    result = {"status": "unknown", "response": ""}

    try:
        # Navigate the response structure
        root = getattr(response, "root", response)

        # Check for error
        error = getattr(root, "error", None)
        if error:
            return {"status": "error", "error": str(error)}

        # Get the result (Task or Message)
        task_or_msg = getattr(root, "result", None)
        if task_or_msg is None:
            return {"status": "completed", "response": str(response)}

        # If it's a Task, check status and extract artifacts
        status = getattr(task_or_msg, "status", None)
        if status:
            state = getattr(status, "state", None)
            result["status"] = str(state) if state else "unknown"

            # Check for INPUT_REQUIRED (multi-turn)
            message = getattr(status, "message", None)
            if message:
                parts = getattr(message, "parts", [])
                texts = []
                for part in parts:
                    text = getattr(part, "text", None) or getattr(getattr(part, "root", None), "text", None)
                    if text:
                        texts.append(text)
                if texts:
                    result["response"] = "\n".join(texts)

        # Extract artifacts
        artifacts = getattr(task_or_msg, "artifacts", None)
        if artifacts:
            artifact_texts = []
            for artifact in artifacts:
                parts = getattr(artifact, "parts", [])
                for part in parts:
                    text = getattr(part, "text", None) or getattr(getattr(part, "root", None), "text", None)
                    if text:
                        artifact_texts.append(text)
            if artifact_texts:
                result["artifacts"] = artifact_texts
                if not result.get("response"):
                    result["response"] = "\n".join(artifact_texts)

    except Exception as e:
        result = {"status": "error", "error": f"Failed to parse response: {e}"}

    return result


async def _async_orchestrate(goal: str, agent_targets: list, mode: str,
                             default_timeout: float = _DEFAULT_TIMEOUT) -> dict:
    """Fan out a goal to multiple agents.

    Args:
        goal: The task text to send to each agent.
        agent_targets: List of (url, config) tuples.
        mode: "all" (collect all), "first" (first success), or "best" (alias for all).
        default_timeout: Per-agent timeout in seconds.

    Returns:
        {mode, agents_called, results: [{agent, url, status, response, duration_ms}]}
    """
    import time

    async def _call_one(url: str, config: dict) -> dict:
        start = time.monotonic()
        try:
            agent_timeout = config.get("timeout", default_timeout)
            response = await asyncio.wait_for(
                _async_call(url, config, goal),
                timeout=agent_timeout,
            )
            duration = int((time.monotonic() - start) * 1000)
            return {
                "agent": config.get("_name", url),
                "url": url,
                "status": "success",
                "response": response,
                "duration_ms": duration,
            }
        except asyncio.TimeoutError:
            duration = int((time.monotonic() - start) * 1000)
            return {
                "agent": config.get("_name", url),
                "url": url,
                "status": "timeout",
                "response": None,
                "duration_ms": duration,
            }
        except Exception as e:
            duration = int((time.monotonic() - start) * 1000)
            return {
                "agent": config.get("_name", url),
                "url": url,
                "status": "error",
                "response": _sanitize_error(str(e)),
                "duration_ms": duration,
            }

    results = []
    agents_called = len(agent_targets)

    if mode in ("all", "best"):
        results = await asyncio.gather(
            *[_call_one(url, cfg) for url, cfg in agent_targets],
            return_exceptions=False,
        )
        results = list(results)
    elif mode == "first":
        tasks = [asyncio.create_task(_call_one(url, cfg))
                 for url, cfg in agent_targets]
        done = set()
        pending = set(tasks)
        first_success = None

        while pending:
            newly_done, pending = await asyncio.wait(
                pending, return_when=asyncio.FIRST_COMPLETED
            )
            done.update(newly_done)
            for t in newly_done:
                result = t.result()
                if result["status"] == "success":
                    first_success = result
                    break
            if first_success:
                break

        # Cancel remaining
        for t in pending:
            t.cancel()

        if first_success:
            results = [first_success]
        else:
            # All failed — return all results
            results = [t.result() for t in done]

    return {
        "mode": mode,
        "agents_called": agents_called,
        "results": results,
    }


# ---------------------------------------------------------------------------
# Tool handlers (sync, bridged to async via background loop)
# ---------------------------------------------------------------------------

def a2a_discover(args: dict, **kwargs) -> str:
    """Discover a remote A2A agent by fetching its Agent Card.

    Args:
        args: {"agent": "name_or_url"} - Config name or direct URL of the agent.

    Returns:
        JSON string with agent name, description, skills, and capabilities.
    """
    agent = args.get("agent", "").strip()
    if not agent:
        return json.dumps({"error": "Missing required 'agent' parameter (name or URL)"})

    try:
        url, config = _resolve_agent_url(agent)
        result = _run_on_loop(_async_discover(url, config))
        return json.dumps(result, indent=2)
    except Exception as e:
        error_msg = _sanitize_error(str(e))
        logger.exception("A2A discover failed for '%s': %s", agent, error_msg)
        return json.dumps({"error": f"Failed to discover agent: {error_msg}"})


def a2a_call(args: dict, **kwargs) -> str:
    """Send a task to a remote A2A agent.

    Args:
        args: {
            "agent": "name_or_url",  - Config name or direct URL
            "message": "text",       - The task/message to send
            "stream": false          - Optional: use streaming mode
        }

    Returns:
        JSON string with the agent's response.
    """
    agent = args.get("agent", "").strip()
    message = args.get("message", "").strip()
    stream = args.get("stream", False)

    if not agent:
        return json.dumps({"error": "Missing required 'agent' parameter"})
    if not message:
        return json.dumps({"error": "Missing required 'message' parameter"})

    try:
        url, config = _resolve_agent_url(agent)
        result = _run_on_loop(_async_call(url, config, message, stream))
        return result
    except Exception as e:
        error_msg = _sanitize_error(str(e))
        logger.exception("A2A call failed for '%s': %s", agent, error_msg)
        return json.dumps({"error": f"A2A call failed: {error_msg}"})


def a2a_list(args: dict, **kwargs) -> str:
    """List all known A2A agents from config and discovery cache.

    Returns:
        JSON string with {agents: [...], total: int}.
    """
    try:
        agents = _build_agent_registry()
        return json.dumps({"agents": agents, "total": len(agents)}, indent=2)
    except Exception as e:
        error_msg = _sanitize_error(str(e))
        logger.exception("A2A list failed: %s", error_msg)
        return json.dumps({"error": f"Failed to list agents: {error_msg}"})


def a2a_orchestrate(args: dict, **kwargs) -> str:
    """Fan out a goal to multiple A2A agents in parallel.

    Args:
        args: {
            "goal": "text",             - Required: task to send
            "agents": ["name_or_url"],  - Optional: explicit agent list
            "mode": "all|first|best"    - Optional: orchestration mode (default: all)
        }

    Returns:
        JSON string with orchestration results.
    """
    goal = args.get("goal", "").strip()
    if not goal:
        return json.dumps({"error": "Missing required 'goal' parameter"})

    mode = args.get("mode", "all").strip().lower()
    if mode not in ("all", "first", "best"):
        return json.dumps({"error": f"Invalid mode '{mode}'. Must be one of: all, first, best"})

    try:
        explicit_agents = args.get("agents", [])
        if explicit_agents:
            # Resolve each explicit agent
            targets = []
            for agent in explicit_agents:
                url, config = _resolve_agent_url(agent)
                # Copy to avoid mutating cached config
                config = dict(config)
                config["_name"] = agent
                targets.append((url, config))
        else:
            # Auto-select from registry
            targets = _auto_select_agents(goal)
            # Tag with names
            for idx, (url, cfg) in enumerate(targets):
                cfg = dict(cfg)
                cfg["_name"] = cfg.get("_name", url)
                targets[idx] = (url, cfg)

        if not targets:
            return json.dumps({"error": "No agents available for orchestration"})

        # Compute total timeout (max agent timeout + buffer)
        max_timeout = max(
            (cfg.get("timeout", _DEFAULT_TIMEOUT) for _, cfg in targets),
            default=_DEFAULT_TIMEOUT,
        )
        result = _run_on_loop(
            _async_orchestrate(goal, targets, mode),
            timeout=max_timeout + 60,
        )
        return json.dumps(result, indent=2)

    except Exception as e:
        error_msg = _sanitize_error(str(e))
        logger.exception("A2A orchestrate failed: %s", error_msg)
        return json.dumps({"error": f"Orchestration failed: {error_msg}"})


# ---------------------------------------------------------------------------
# Availability check
# ---------------------------------------------------------------------------

def check_a2a_available() -> bool:
    """Return True if a2a-sdk is installed."""
    return _A2A_AVAILABLE


# ---------------------------------------------------------------------------
# Tool schemas (OpenAI function-calling format)
# ---------------------------------------------------------------------------

A2A_DISCOVER_SCHEMA = {
    "name": "a2a_discover",
    "description": (
        "Discover a remote A2A (Agent-to-Agent) agent by fetching its Agent Card. "
        "Returns the agent's name, description, skills, and capabilities. "
        "Use this to learn what a remote agent can do before sending it tasks. "
        "Accepts either a configured agent name or a direct URL."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "agent": {
                "type": "string",
                "description": (
                    "The agent to discover. Can be a name from config "
                    "(e.g. 'researcher') or a direct URL (e.g. 'http://localhost:9999')."
                ),
            },
        },
        "required": ["agent"],
    },
}

A2A_CALL_SCHEMA = {
    "name": "a2a_call",
    "description": (
        "Send a task to a remote A2A agent and get its response. "
        "The remote agent can be built on any framework (LangChain, CrewAI, "
        "Google ADK, etc.) as long as it supports the A2A protocol. "
        "Use a2a_discover first to see what the agent can do."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "agent": {
                "type": "string",
                "description": (
                    "The agent to call. Can be a name from config "
                    "or a direct URL."
                ),
            },
            "message": {
                "type": "string",
                "description": "The task or message to send to the remote agent.",
            },
            "stream": {
                "type": "boolean",
                "description": "If true, use streaming mode for real-time responses. Default: false.",
                "default": False,
            },
        },
        "required": ["agent", "message"],
    },
}

A2A_LIST_SCHEMA = {
    "name": "a2a_list",
    "description": (
        "List all known A2A agents from configuration and discovery cache. "
        "Shows each agent's name, URL, status, skills, and whether it was "
        "configured or dynamically discovered. Use this to see what agents "
        "are available before orchestrating tasks."
    ),
    "parameters": {
        "type": "object",
        "properties": {},
        "required": [],
    },
}

A2A_ORCHESTRATE_SCHEMA = {
    "name": "a2a_orchestrate",
    "description": (
        "Fan out a goal to multiple A2A agents in parallel and collect results. "
        "Modes: 'all' sends to every agent and collects all responses, "
        "'first' returns the first successful response and cancels the rest, "
        "'best' is an alias for 'all'. If no agents are specified, auto-selects "
        "agents whose skills match the goal."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "goal": {
                "type": "string",
                "description": "The task or goal to send to the agents.",
            },
            "agents": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Optional list of agent names or URLs. "
                    "If omitted, auto-selects agents based on skill matching."
                ),
            },
            "mode": {
                "type": "string",
                "enum": ["all", "first", "best"],
                "description": (
                    "Orchestration mode: 'all' collects every response, "
                    "'first' returns the first success, 'best' is an alias for 'all'. "
                    "Default: 'all'."
                ),
                "default": "all",
            },
        },
        "required": ["goal"],
    },
}

# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

registry.register(
    name="a2a_discover",
    toolset="a2a",
    schema=A2A_DISCOVER_SCHEMA,
    handler=a2a_discover,
    check_fn=check_a2a_available,
    is_async=False,
    description="Discover remote A2A agents and their capabilities",
    emoji="🌐",
)

registry.register(
    name="a2a_call",
    toolset="a2a",
    schema=A2A_CALL_SCHEMA,
    handler=a2a_call,
    check_fn=check_a2a_available,
    is_async=False,
    description="Send tasks to remote A2A agents",
    emoji="📡",
)

registry.register(
    name="a2a_list",
    toolset="a2a",
    schema=A2A_LIST_SCHEMA,
    handler=a2a_list,
    check_fn=check_a2a_available,
    is_async=False,
    description="List all known A2A agents",
    emoji="📋",
)

registry.register(
    name="a2a_orchestrate",
    toolset="a2a",
    schema=A2A_ORCHESTRATE_SCHEMA,
    handler=a2a_orchestrate,
    check_fn=check_a2a_available,
    is_async=False,
    description="Fan out tasks to multiple A2A agents in parallel",
    emoji="🎭",
)
