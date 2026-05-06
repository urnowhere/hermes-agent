"""Muninn memory plugin — MemoryProvider for Disco Elysium semantic memory.

Connects to the Muninn API (http://127.0.0.1:8901) for semantic routing,
peer activation, and memory storage. All communication is via HTTP —
no direct imports from the muninn package.

Lifecycle:
  initialize()    → creates httpx session, fetches /stats
  system_prompt_block() → instructions for the LLM
  prefetch()      → routes user query through /api/v1/route, formats context
  queue_prefetch()→ starts background thread for next prefetch
  sync_turn()     → stores turn as event (step 5)
  get_tool_schemas() → muninn_search, muninn_add_memory, etc (step 6)
  shutdown()      → closes session
"""

from __future__ import annotations

import json
import hashlib
import logging
import subprocess
import threading
import time
from typing import Any, Dict, List, Optional, Union

from agent.memory_provider import MemoryProvider
from tools.registry import tool_error

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Tool schemas (module-level constants for Hermes tool registry)
# ---------------------------------------------------------------------------

SEARCH_SCHEMA = {
    "name": "muninn_search",
    "description": (
        "Buscar memorias almacenadas en Muninn por significado semántico. "
        "Retorna fragmentos de conversaciones previas, preferencias y hechos "
        "relevantes rankeados por similitud. Útil cuando necesitas recordar "
        "algo específico que el usuario dijo en el pasado."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Texto a buscar semánticamente."},
            "top_k": {"type": "integer", "description": "Cuantos resultados devolver (default: 5, max: 20)."},
        },
        "required": ["query"],
    },
}

ADD_MEMORY_SCHEMA = {
    "name": "muninn_add_memory",
    "description": (
        "Guardar una nueva memoria en Muninn vinculada a uno o más peers "
        "(dominios de conocimiento). Usa peer_ids existentes: sombra_muerte, "
        "sombra_rechazo, proyecto_juego, programacion, valle_alto, gym_rutina, "
        "finanzas_patrimonio, relaciones_personales, suenos_analisis, "
        "autoevaluacion, memoria_durable, o ninguno para auto-routing."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "content": {"type": "string", "description": "Contenido de la memoria a guardar."},
            "tags": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Tags para categorizar (opcional).",
            },
            "peer_id": {
                "type": "string",
                "description": "ID del peer al que vincular (opcional — si se omite, Muninn auto-rutea).",
            },
            "memory_type": {
                "type": "string",
                "description": "Tipo de memoria: hecho, preferencia, patron, idea (default: hecho).",
            },
        },
        "required": ["content"],
    },
}

LIST_PEERS_SCHEMA = {
    "name": "muninn_list_peers",
    "description": (
        "Listar todos los peers (dominios de conocimiento) disponibles en Muninn "
        "con sus facets y descripciones. Útil para saber qué dominios existen y "
        "a cuál vincular una nueva memoria."
    ),
    "parameters": {
        "type": "object",
        "properties": {},
        "required": [],
    },
}

MUNINN_URL = "http://127.0.0.1:8901"
PING_TIMEOUT = 2.0
ROUTE_TIMEOUT = 10.0
MAX_CONTEXT_TOKENS = 500  # compact context injection

# P1: Short timeout for prefetch — if Muninn is slow, don't block Hermes
PREFETCH_TIMEOUT = 4.0

# P3: Max iterations for iterative query expansion
MAX_ITERATIVE_ROUTES = 2  # Original query + 1 expansion pass
ITERATIVE_SCORE_BOOST = 0.02  # Small bonus for peers found via expansion

# P3: Score threshold for conditional expansion
# Only expand query if the top-1 peer has a low score (< 0.40)
# High scores mean the embedding already found the right peer
ITERATION_SCORE_THRESHOLD = 0.40

# P5: LRU route cache — avoids re-running BGE-M3 for repeated/similar queries
_ROUTE_CACHE_MAX = 32
_ROUTE_CACHE_TTL = 300  # seconds (5 minutes)
_route_cache: Dict[str, tuple] = {}  # hash -> (result_str, timestamp)
_route_cache_order: list = []  # ordered by access for LRU eviction

# P1: Buffer for deferred memory — when Muninn responds late, store here
# Format: list of strings (context blocks to inject in next turn)
_DEFERRED_MEMORY_BUFFER: List[str] = []
_DEFERRED_LOCK = threading.Lock()


class MuninnMemoryProvider(MemoryProvider):
    """Disco Elysium-inspired semantic memory via Muninn API."""

    def __init__(self):
        self._session = None
        self._stats = {}
        self._cron_skipped = False

        # Background prefetch state
        self._prefetch_result = ""
        self._prefetch_lock = threading.Lock()
        self._prefetch_thread: Optional[threading.Thread] = None

    # -- Property -----------------------------------------------------------

    @property
    def name(self) -> str:
        return "muninn"

    # -- Core lifecycle -----------------------------------------------------

    def is_available(self) -> bool:
        """Quick ping to Muninn API. No heavy imports."""
        try:
            result = subprocess.run(
                ["curl", "-sf", f"{MUNINN_URL}/"],
                capture_output=True, text=True, timeout=PING_TIMEOUT,
            )
            return result.returncode == 0
        except Exception:
            return False

    def initialize(self, session_id: str, **kwargs) -> None:
        """Connect to Muninn, cache stats, warm up prefetch."""
        agent_context = kwargs.get("agent_context", "")
        platform = kwargs.get("platform", "cli")
        if agent_context in ("cron", "flush") or platform == "cron":
            logger.debug("Muninn skipped: cron/flush context")
            self._cron_skipped = True
            return

        try:
            import httpx
            self._session = httpx.Client(base_url=MUNINN_URL, timeout=5.0)
            resp = self._session.get("/stats")
            if resp.status_code == 200:
                self._stats = resp.json()
                logger.info(
                    "Muninn initialized: %d peers, %d facets, %d memories",
                    self._stats.get("total_peers", 0),
                    self._stats.get("total_facets", 0),
                    self._stats.get("total_memories", 0),
                )
            else:
                logger.warning("Muninn /stats returned %d", resp.status_code)
        except ImportError:
            logger.debug("httpx not installed — muninn plugin inactive")
            self._session = None
        except Exception as e:
            logger.warning("Muninn init failed: %s", e)
            self._session = None

    def system_prompt_block(self) -> str:
        """Return Muninn instructions for the system prompt (static, prompt-cache friendly)."""
        if self._cron_skipped or not self._session:
            return ""
        peers = self._stats.get("total_peers", 0)
        facets = self._stats.get("total_facets", 0)
        memories = self._stats.get("total_memories", 0)

        return (
            f"## Muninn Semantic Memory ({MUNINN_URL})\n"
            f"Muninn activates relevant semantic peers before each turn. "
            f"Available: {peers} peers, {facets} facets, {memories} memories.\n"
            f"Activated peers appear in context with their representation. "
            f"Use them to inform your response — they contain relevant knowledge "
            f"about the user's domains, projects, archetypes, and shadow concepts."
        )

    def prefetch(self, query: str, *, session_id: str = "") -> str:
        """Return cached prefetch result or route fresh.

        P1: If Muninn is slow (> PREFETCH_TIMEOUT), return whatever we have
        (including deferred memory from previous slow responses) rather than
        blocking Hermes entirely.
        """
        if self._cron_skipped or not self._session or not query:
            return ""

        # Check if background prefetch has a result ready
        with self._prefetch_lock:
            if self._prefetch_result:
                result = self._prefetch_result
                self._prefetch_result = ""
                return result

        # P1: Inject deferred memory from previous slow responses
        deferred_parts = []
        with _DEFERRED_LOCK:
            if _DEFERRED_MEMORY_BUFFER:
                deferred_parts = list(_DEFERRED_MEMORY_BUFFER)
                _DEFERRED_MEMORY_BUFFER.clear()

        # Fresh route (cold path — first turn) with short timeout
        fresh = self._do_route(query, timeout=PREFETCH_TIMEOUT)
        if not fresh:
            # P1: Muninn was too slow or failed — log and continue
            logger.debug("Muninn prefetch timed out or failed — using deferred memory")
            if deferred_parts:
                return "\n".join(deferred_parts)
            return ""

        if deferred_parts:
            return "\n".join(deferred_parts) + "\n" + fresh
        return fresh

    def queue_prefetch(self, query: str, *, session_id: str = "") -> None:
        """Start background route for the NEXT turn."""
        if self._cron_skipped or not self._session or not query:
            return

        def _prefetch_worker(q: str) -> None:
            try:
                result = self._do_route(q)
                if result:
                    with self._prefetch_lock:
                        self._prefetch_result = result
            except Exception as e:
                logger.debug("Muninn background prefetch failed: %s", e)

        self._prefetch_thread = threading.Thread(
            target=_prefetch_worker,
            args=(query,),
            daemon=True,
            name="muninn-prefetch",
        )
        self._prefetch_thread.start()

    def _do_route(self, text: str, timeout: Optional[float] = None) -> str:
        """Call /api/v1/route and format activated peers as context.

        Args:
            text: The user query to route.
            timeout: Max seconds to wait for Muninn. If None, uses ROUTE_TIMEOUT.

        P1: Short timeout prevents blocking Hermes when Muninn is slow.
        If Muninn responds AFTER the timeout, the result is stored in
        _DEFERRED_MEMORY_BUFFER for the next turn.

        P3: Iterative query expansion. After the first routing pass, take
        the top-2 activated peers and use their content to build an expanded
        query for a second pass. This catches peers that don't share vocabulary
        with the original query but DO share vocabulary with related peers.
        """
        route_timeout = timeout if timeout is not None else ROUTE_TIMEOUT

        # P5: Check route cache first
        cache_key = hashlib.md5(text.encode()).hexdigest()
        now = time.time()
        if cache_key in _route_cache:
            cached_result, cached_time = _route_cache[cache_key]
            if now - cached_time < _ROUTE_CACHE_TTL:
                logger.debug("P5: Cache HIT for query (age=%.1fs)", now - cached_time)
                # Move to end for LRU
                _route_cache_order.remove(cache_key)
                _route_cache_order.append(cache_key)
                return cached_result
            else:
                # Expired — remove
                del _route_cache[cache_key]
                _route_cache_order.remove(cache_key)

        try:
            # -- Pass 1: Original routing --
            all_peers = {}  # peer_id -> peer_data (dedup map across iterations)
            expanded_text = text

            for iteration in range(MAX_ITERATIVE_ROUTES):
                if not expanded_text:
                    break

                resp = self._session.post(
                    "/api/v1/route",
                    json={"text": expanded_text, "top_k": 8, "strategy": "faceted"},
                    timeout=route_timeout,
                )
                if resp.status_code != 200:
                    logger.warning("Muninn route iteration %d returned %d",
                                   iteration, resp.status_code)
                    break

                data = resp.json()
                activations = data.get("activations", [])
                if not activations:
                    break

                # Merge results: iteration 0 is baseline, iteration 1 gets score boost
                score_multiplier = 1.0 + ITERATIVE_SCORE_BOOST if iteration > 0 else 1.0
                system_peers_to_exclude = {"peer_usuario", "peer_skills"}

                for a in activations:
                    pid = a.get("peer_id", "")
                    if pid in system_peers_to_exclude:
                        continue
                    if pid not in all_peers:
                        a["_iteration"] = iteration
                        a["total_score"] = a.get("total_score", 0) * score_multiplier
                        all_peers[pid] = a
                    else:
                        # Keep higher score across iterations
                        existing = all_peers[pid]
                        new_score = a.get("total_score", 0) * score_multiplier
                        if new_score > existing.get("total_score", 0):
                            existing["total_score"] = new_score
                            existing["_iteration"] = iteration

                # P3: Build expanded query IF top-1 score is low (conditional fallback)
                if iteration == 0 and len(activations) >= 1:
                    top1 = activations[0]
                    top1_score = top1.get("total_score", 0)
                    top1_rep = top1.get("representation", "")
                    top1_name = top1.get("peer_name", "")

                    # Only expand if top-1 score is below threshold
                    # High score = embedding already found the right peer
                    if top1_score < ITERATION_SCORE_THRESHOLD and top1_rep:
                        # Build expanded query: original + key content from top peer
                        expansion_parts = [text]
                        expansion_parts.append(top1_rep[:300])
                        expanded_text = " ".join(expansion_parts)
                        logger.debug(
                            "P3: Query expanded (top-1 score %.3f < %.2f) with '%s' content",
                            top1_score, ITERATION_SCORE_THRESHOLD, top1_name,
                        )
                    else:
                        # Skip expansion — top-1 already confident
                        logger.debug(
                            "P3: Skipping expansion (top-1 score %.3f >= %.2f)",
                            top1_score, ITERATION_SCORE_THRESHOLD,
                        )
                        break
                else:
                    break  # Only one expansion pass

            if not all_peers:
                return ""

            # Filter meaningful results across all iterations
            meaningful = sorted(
                [a for a in all_peers.values()
                 if a.get("total_score", 0) > 0.0001
                 and (a.get("total_score", 0) > 0.005
                      or a.get("similarity", 0) > 0.30)],
                key=lambda x: x.get("total_score", 0),
                reverse=True,
            )

            if not meaningful:
                return ""

            # Format as compact context block
            lines = []
            for a in meaningful[:5]:
                pid = a.get("peer_id", "?")
                pname = a.get("peer_name", pid)
                pdomain = a.get("peer_domain", "")
                score = a.get("total_score", 0)
                rep = a.get("representation", "")
                it = a.get("_iteration", 0)

                header = pname
                if pdomain:
                    header += f" ({pdomain})"

                lines.append(f"  ~~ {header} ~~")
                if rep:
                    lines.append(f"     {rep}")
                tag = f" [activation: {score:.2f}]"
                if it > 0:
                    tag += " [expanded]"
                lines.append(tag)

            result = "\n".join(lines)

            # P5: Store in cache
            _route_cache[cache_key] = (result, time.time())
            _route_cache_order.append(cache_key)
            # Evict oldest if over max
            while len(_route_cache_order) > _ROUTE_CACHE_MAX:
                oldest_key = _route_cache_order.pop(0)
                _route_cache.pop(oldest_key, None)

            return result

        except Exception as e:
            logger.debug("Muninn route error: %s", e)

            # P1: If this was a short prefetch timeout, retry with full timeout
            # in background and store result in deferred buffer
            if timeout is not None and timeout < ROUTE_TIMEOUT:
                self._deferred_route_retry(text)
            return ""

    # -- P1: Deferred memory retry -------------------------------------

    def _deferred_route_retry(self, text: str) -> None:
        """Retry routing with full timeout in background.
        If successful, store result in deferred buffer for next turn.
        """
        def _retry_worker(q: str) -> None:
            try:
                result = self._do_route(q, timeout=ROUTE_TIMEOUT)
                if result:
                    with _DEFERRED_LOCK:
                        _DEFERRED_MEMORY_BUFFER.append(result)
                        # Keep at most 3 deferred blocks
                        while len(_DEFERRED_MEMORY_BUFFER) > 3:
                            _DEFERRED_MEMORY_BUFFER.pop(0)
                    logger.debug("Muninn deferred memory stored for next turn")
            except Exception:
                pass  # Silently drop — next turn will try fresh

        t = threading.Thread(
            target=_retry_worker,
            args=(text,),
            daemon=True,
            name="muninn-deferred",
        )
        t.start()

    # -- P2: Async sync_turn ------------------------------------------

    def sync_turn(self, user_content: str, assistant_content: str, *,
                  session_id: str = "") -> None:
        """Store turn as event in Muninn for learning.

        P2: Runs in background thread — fire-and-forget. Never blocks
        the conversation flow.
        """
        if self._cron_skipped or not self._session:
            return

        thread = threading.Thread(
            target=self._sync_turn_worker,
            args=(user_content, assistant_content, session_id),
            daemon=True,
            name="muninn-sync",
        )
        thread.start()

    def _sync_turn_worker(self, user_content: str, assistant_content: str,
                          session_id: str = "") -> None:
        """Actual sync work, runs in background thread."""
        try:
            # Build concise event text from both messages
            # Truncate to avoid huge events (Muninn will embed the text)
            user_trunc = user_content[:500] if user_content else ""
            asst_trunc = assistant_content[:1000] if assistant_content else ""

            # Use user message as the main content (gets embedded by Muninn)
            # and the assistant response + metadata goes into metadata
            payload = {
                "type": "conversation_turn",
                "content": user_trunc,
                "peer_ids": [],
                "metadata": {
                    "assistant_message": asst_trunc,
                    "session_id": session_id or "",
                    "source": "hermes-plugin",
                    "timestamp": time.time(),
                },
            }

            resp = self._session.post(
                "/api/v1/events",
                json=payload,
                timeout=ROUTE_TIMEOUT,
            )
            if resp.status_code not in (200, 201):
                logger.debug("Muninn sync_turn returned %d", resp.status_code)

        except Exception as e:
            logger.debug("Muninn sync_turn failed: %s", e)

    # -- Hook: mirror built-in memory writes ---------------------------

    def on_memory_write(
        self,
        action: str,
        target: str,
        content: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Mirror built-in memory writes to Muninn.

        Called when the built-in memory tool writes an entry (add/replace/remove).
        'add' creates a new memory in Muninn linked to the appropriate peer.
        'replace' creates a new memory with a note about what it replaces.
        'remove' is a no-op (Muninn doesn't support deletion via API yet).
        """
        if self._cron_skipped or not self._session or not content:
            return

        try:
            # Map target to peer
            peer_map = {
                "memory": "memoria_durable",
                "user": "peer_usuario",
            }
            peer_id = peer_map.get(target, "memoria_durable")

            if action == "add":
                self._create_memory(content, peer_id, metadata)
            elif action == "replace":
                # No direct update — create new with context
                note = f"[replaces previous {target} entry] {content}"
                self._create_memory(note, peer_id, metadata)
            # action == "remove": no-op, Muninn doesn't support delete via API

        except Exception as e:
            logger.debug("Muninn on_memory_write failed: %s", e)

    def _create_memory(self, content: str, peer_id: str,
                       metadata: Optional[Dict[str, Any]] = None) -> None:
        """Internal helper: POST a memory to Muninn."""
        tags = ["hermes-sync"]
        meta = dict(metadata or {})
        meta.setdefault("source", "hermes-plugin")

        payload = {
            "content": content,
            "type": "hecho",
            "source": "hermes-plugin",
            "confidence": 0.7,
            "peer_ids": [peer_id],
            "tags": tags,
            "metadata": meta,
        }

        resp = self._session.post(
            "/api/v1/memories",
            json=payload,
            timeout=ROUTE_TIMEOUT,
        )
        if resp.status_code not in (200, 201):
            logger.debug("Muninn on_memory_write returned %d", resp.status_code)

    # -- Tools --------------------------------------------------------

    def get_tool_schemas(self) -> List[Dict[str, Any]]:
        return [SEARCH_SCHEMA, ADD_MEMORY_SCHEMA, LIST_PEERS_SCHEMA]

    def handle_tool_call(self, tool_name: str, args: Dict[str, Any],
                         **kwargs) -> str:
        if self._cron_skipped or not self._session:
            return json.dumps({"error": "Muninn not available"})

        try:
            if tool_name == "muninn_search":
                return self._handle_search(args)
            elif tool_name == "muninn_add_memory":
                return self._handle_add_memory(args)
            elif tool_name == "muninn_list_peers":
                return self._handle_list_peers()
            else:
                return json.dumps({"error": f"Unknown tool: {tool_name}"})
        except Exception as e:
            logger.debug("Muninn tool '%s' failed: %s", tool_name, e)
            return json.dumps({"error": str(e)})

    def _handle_search(self, args: Dict[str, Any]) -> str:
        query = args.get("query", "")
        top_k = min(args.get("top_k", 5), 20)

        resp = self._session.post(
            "/api/v1/search",
            json={"query": query, "top_k": top_k},
            timeout=ROUTE_TIMEOUT,
        )
        if resp.status_code != 200:
            return json.dumps({"error": f"Search returned {resp.status_code}"})

        results = resp.json()
        if not results:
            return json.dumps({"results": [], "message": "No se encontraron resultados."})

        return json.dumps({"results": results, "count": len(results)})

    def _handle_add_memory(self, args: Dict[str, Any]) -> str:
        content = args.get("content", "")
        tags = args.get("tags", [])
        peer_id = args.get("peer_id")
        memory_type = args.get("memory_type", "hecho")

        payload = {
            "content": content,
            "type": memory_type,
            "tags": tags,
        }
        if peer_id:
            payload["peer_id"] = peer_id

        resp = self._session.post(
            "/api/v1/memories",
            json=payload,
            timeout=ROUTE_TIMEOUT,
        )
        if resp.status_code in (200, 201):
            data = resp.json()
            return json.dumps({"success": True, "memory_id": data.get("id")})
        else:
            return json.dumps({"error": f"Add memory returned {resp.status_code}"})

    def _handle_list_peers(self) -> str:
        resp = self._session.get("/api/v1/peers", timeout=ROUTE_TIMEOUT)
        if resp.status_code != 200:
            return json.dumps({"error": f"List peers returned {resp.status_code}"})

        peers = resp.json()
        simplified = []
        for p in peers:
            simplified.append({
                "id": p.get("id"),
                "name": p.get("name"),
                "domain": p.get("domain"),
                "description": p.get("description"),
                "facets": len(p.get("facets", [])),
            })

        return json.dumps({"peers": simplified, "count": len(simplified)})

    def shutdown(self) -> None:
        """Clean up HTTP session."""
        if self._session:
            try:
                self._session.close()
            except Exception:
                pass
            self._session = None
        logger.debug("Muninn provider shut down")