"""
Patches hermes-agent run_agent to use httpx-based OpenAI client instead of the SDK.
Installs BEFORE any hermes module is imported.
"""
import sys, os, logging, json

# Setup debug logging first
logging.basicConfig(level=logging.DEBUG, stream=sys.stderr)
logger = logging.getLogger("patch")

# 1. Load .env FIRST
import pathlib as _pathlib
_SCRIPT_DIR = _pathlib.Path(__file__).parent.resolve()
DEFAULT_HERMES_HOME = _SCRIPT_DIR / ".hermes"
os.environ.setdefault("HERMES_HOME", str(DEFAULT_HERMES_HOME))
try:
    from dotenv import load_dotenv
    _env_path = _SCRIPT_DIR / ".hermes" / ".env"
    if _env_path.exists():
        load_dotenv(str(_env_path), override=True, encoding="utf-8")
    else:
        logger.debug(f"[PATCH] .env not found at {_env_path}")
except Exception as e:
    logger.debug(f"dotenv load failed: {e}")

logger.debug("[PATCH] Starting patch installation")

import importlib, importlib.util, httpx
from typing import Iterator

# ──────────────────────────────────────────────────────────────────────────────
# Fake "duck-typed" objects that look like OpenAI SDK response objects
# so hermes-agent's attribute-access patterns work.
# ──────────────────────────────────────────────────────────────────────────────

class _Delta:
    """Mimics openai.types.chat.chat_completion_chunk.ChoiceDelta."""
    def __init__(self, d: dict):
        self.content = d.get("content")
        self.role = d.get("role")
        tc = d.get("tool_calls")
        self.tool_calls = [_ToolCallDelta(t) for t in tc] if tc else None
        self.reasoning_content = d.get("reasoning_content")
        self.reasoning = d.get("reasoning")

class _ToolCallDelta:
    """Mimics openai.types.chat.chat_completion_chunk.ChoiceMessageToolCall."""
    def __init__(self, t: dict):
        self.id = t.get("id", "")
        self.type = t.get("type", "function")
        f = t.get("function", {})
        self.function = _FuncDelta(f)
        self.index = t.get("index", 0)
        self.extra_content = t.get("extra_content")

class _FuncDelta:
    """Mimics openai.types.chat.chat_completion_chunk.ChoiceMessageToolCallFunction."""
    def __init__(self, f: dict):
        self.name = f.get("name", "")
        self.arguments = f.get("arguments", "")

class _Chunk:
    """Mimics openai.types.chat.chat_completion_chunk.ChatCompletionChunk."""
    def __init__(self, data: dict, model: str):
        self.model = model or data.get("model", "")
        self.id = data.get("id", "")
        self.object = "chat.completion.chunk"
        raw = data.get("choices", [{}])
        c = raw[0] if raw else {}
        self.choices = [_ChoiceDelta(c)]
        u = data.get("usage")
        self.usage = _Usage(u) if u else None

class _ChoiceDelta:
    """Mimics openai.types.chat.chat_completion_chunk.Choice."""
    def __init__(self, c: dict):
        self.delta = _Delta(c.get("delta", {}))
        self.finish_reason = c.get("finish_reason")
        self.index = c.get("index", 0)

class _Message:
    """Mimics openai.types.chat.chat_completion_message.ChatCompletionMessage."""
    def __init__(self, d: dict):
        self.role = d.get("role", "assistant")
        self.content = d.get("content", "")
        tc = d.get("tool_calls")
        self.tool_calls = [_FullToolCall(t) for t in tc] if tc else None
        self.tool_call_id = d.get("tool_call_id")
        self.name = d.get("name")
        self.finish_reason = d.get("finish_reason", "stop")

class _FullToolCall:
    """Full tool call for non-streaming response."""
    def __init__(self, t: dict):
        self.id = t.get("id", "")
        self.type = t.get("type", "function")
        f = t.get("function", {})
        self.function = _FuncDelta(f)
        self.index = t.get("index", 0)

class _CompChoice:
    """Mimics openai.types.chat.chat_completion.Choice."""
    def __init__(self, c: dict):
        self.message = _Message(c.get("message", {}))
        self.finish_reason = c.get("finish_reason", "stop")
        self.index = c.get("index", 0)

class _Completion:
    """Mimics openai.types.chat.chat_completion.ChatCompletion (non-streaming)."""
    def __init__(self, data: dict, model: str):
        self.model = model or data.get("model", "")
        self.id = data.get("id", "")
        self.object = "chat.completion"
        raw = data.get("choices", [{}])
        self.choices = [_CompChoice(c) for c in raw] if raw else [_CompChoice({})]
        u = data.get("usage")
        self.usage = _Usage(u) if u else None
        logger.debug(f"[_Completion] created: id={self.id}, #choices={len(self.choices)}")

class _Usage:
    """Mimics openai.types.chat.chat_completion_usage.ChatCompletionUsage."""
    def __init__(self, u: dict):
        if u is None:
            self.prompt_tokens = 0; self.completion_tokens = 0; self.total_tokens = 0
            return
        self.prompt_tokens = u.get("prompt_tokens", 0)
        self.completion_tokens = u.get("completion_tokens", 0)
        self.total_tokens = u.get("total_tokens", 0)
        ptd = u.get("prompt_tokens_details")
        self.prompt_tokens_details = _PromptTokensDetails(ptd) if ptd else None

class _PromptTokensDetails:
    def __init__(self, ptd: dict):
        self.cached_tokens = ptd.get("cached_tokens", 0) if ptd else 0

# ──────────────────────────────────────────────────────────────────────────────
# _HttpxOpenAI — the fake OpenAI SDK client
# ──────────────────────────────────────────────────────────────────────────────

class _ChatNamespace:
    def __init__(self, client):
        self._client = client
    @property
    def completions(self):
        return _ChatCompletions(self._client)

class _ChatCompletions:
    def __init__(self, client):
        self._client = client
        logger.debug(f"[_ChatCompletions] created for client {client}")

    def create(self, model: str, messages, stream: bool = False, **kwargs):
        logger.debug(f"[_ChatCompletions.create] model={model}, stream={stream}")
        
        # CRITICAL FIX: Pop timeout BEFORE building payload to avoid JSON serialization error
        timeout = kwargs.pop("timeout", None)
        call_timeout = getattr(timeout, "read", 60.0) if timeout else 60.0
        
        payload = {"model": model, "messages": messages, **kwargs}
        if "tools" in payload:
            t_count = len(payload["tools"])
            first = str(payload["tools"][0])[:300] if t_count > 0 else "none"
            names = [str(t)[:80] for t in payload["tools"]]
            logger.debug(f"[PAYLOAD] tools_count={t_count}, all_names={names}")
        else:
            logger.debug("[PAYLOAD] NO TOOLS!")
        headers = {**self._client.default_headers}
        headers["Authorization"] = f"Bearer {self._client.api_key}"
        headers["Content-Type"] = "application/json"

        httpx_client = self._client._httpx_client
        base = self._client.base_url.rstrip("/")

        if stream:
            url = f"{base}/chat/completions"
            logger.debug(f"[STREAM] POST to {url}")
            try:
                resp = httpx_client.post(url, json=payload, headers=headers, timeout=call_timeout)
                resp.raise_for_status()
                data = resp.json()
                logger.debug(f"[STREAM] got full response, keys={list(data.keys())}")
                logger.debug(f"[STREAM] MiniMax response: {str(data)[:500]}")
                return _StreamFromCompleteResp(data, model)
            except Exception as e:
                logger.debug(f"[STREAM] Error: {e}")
                raise
        else:
            url = f"{base}/chat/completions"
            logger.debug(f"[NON-STREAM] POST to {url}")
            try:
                resp = httpx_client.post(url, json=payload, headers=headers, timeout=call_timeout)
                resp.raise_for_status()
                data = resp.json()
                logger.debug(f"[NON-STREAM] keys={list(data.keys())}")
                return _Completion(data, model)
            except Exception as e:
                logger.debug(f"[NON-STREAM] Error: {e}")
                raise

class _StreamFromCompleteResp:
    """Wraps a complete MiniMax non-streaming response as a streaming iterator."""
    def __init__(self, data: dict, model: str):
        self._data = data
        self._model = model
        self.model = model
        self.response = None
        self._chunks = self._build_chunks(data)

    def _build_chunks(self, data: dict):
        chunks = []
        choice = data.get("choices", [{}])[0]
        msg = choice.get("message", {})
        content = msg.get("content", "")
        role = msg.get("role", "assistant")
        tool_calls = msg.get("tool_calls")
        finish_reason = choice.get("finish_reason", "stop")

        chunks.append(_Chunk({
            "id": data.get("id", ""),
            "choices": [{"delta": {"role": role}, "finish_reason": None, "index": 0}]
        }, self._model))

        if tool_calls:
            # Yield tool_calls chunk BEFORE content
            for i, tc in enumerate(tool_calls):
                chunks.append(_Chunk({
                    "id": data.get("id", ""),
                    "choices": [{
                        "delta": {"tool_calls": [tc], "index": i},
                        "finish_reason": None,
                        "index": i
                    }]
                }, self._model))

        if content:
            chunks.append(_Chunk({
                "id": data.get("id", ""),
                "choices": [{"delta": {"content": content}, "finish_reason": None, "index": 0}]
            }, self._model))

        chunks.append(_Chunk({
            "id": data.get("id", ""),
            "choices": [{"delta": {}, "finish_reason": finish_reason, "index": 0}]
        }, self._model))
        return chunks

    def __iter__(self):
        return iter(self._chunks)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


class _StreamResponse:
    """Mimics openai._legacy_response.DefaultGeneratorChunk — an iterable stream."""
    def __init__(self, lines_iter, model: str):
        self._lines = lines_iter
        self._model = model
        self.model = model
        self.response = None
    def __iter__(self) -> Iterator[_Chunk]:
        return self._iter_chunks()
    def _iter_chunks(self):
        try:
            for line in self._lines:
                if not line.startswith("data: "):
                    continue
                data = line[6:].strip()
                if data == "[DONE]":
                    logger.debug("[Stream] received [DONE]")
                    break
                try:
                    d = json.loads(data)
                    yield _Chunk(d, self._model)
                except Exception as e:
                    logger.debug(f"[Stream chunk] parse error: {e}")
        except Exception as e:
            logger.debug(f"[Stream] iterator error: {e}")
            raise
    def __enter__(self):
        return self
    def __exit__(self, *args):
        pass

class _StreamResponseManual:
    """Streaming response for httpx stream() without context manager."""
    def __init__(self, resp, model: str):
        self._resp = resp
        self._model = model
        self.model = model
        self.response = resp
    def __iter__(self) -> Iterator[_Chunk]:
        try:
            for line in self._resp.iter_lines():
                if not line.startswith("data: "):
                    continue
                data = line[6:].strip()
                if data == "[DONE]":
                    break
                try:
                    d = json.loads(data)
                    yield _Chunk(d, self._model)
                except Exception as e:
                    logger.debug(f"[Stream chunk] parse error: {e}")
        finally:
            self._resp.close()
    def __enter__(self):
        return self
    def __exit__(self, *args):
        self._resp.close()

class _HttpxOpenAI:
    """Drop-in replacement for openai.OpenAI using httpx — no pydantic_core."""
    def __init__(self, api_key=None, base_url=None, timeout=None,
                 default_headers=None, http_client=None, _strict_selective=False, **kwargs):
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY", "")
        self.base_url = (base_url or "https://api.openai.com/v1").rstrip("/")
        if self.base_url.endswith("/chat/completions"):
            self.base_url = self.base_url[:-len("/chat/completions")]
        self.timeout = timeout
        self.default_headers = dict(default_headers) if default_headers else {}
        self._kwargs = kwargs

        headers = {**self.default_headers}
        if self.api_key:
            headers.setdefault("Authorization", f"Bearer {self.api_key}")
        timeout_val = httpx.Timeout(timeout) if timeout else httpx.Timeout(60.0)
        self._httpx_client = httpx.Client(
            base_url=self.base_url,
            headers=headers,
            timeout=timeout_val,
            follow_redirects=True,
        )
        logger.debug(f"[_HttpxOpenAI] base_url={self.base_url}, api_key={self.api_key[:10]}...")

    @property
    def chat(self):
        return _ChatNamespace(self)

    def close(self):
        self._httpx_client.close()

# ──────────────────────────────────────────────────────────────────────────────
# Fake APIError for hermes-agent's error handling
# ──────────────────────────────────────────────────────────────────────────────

class _APIError(Exception):
    """Fake openai.APIError for error handling compatibility."""
    def __init__(self, message, request=None, response=None):
        super().__init__(message)
        self.request = request
        self.response = response

# ──────────────────────────────────────────────────────────────────────────────
# Install fake 'openai' module into sys.modules
# ──────────────────────────────────────────────────────────────────────────────

_fake_openai = type(sys)('openai')
_fake_openai.OpenAI = _HttpxOpenAI
_fake_openai.APIError = _APIError
_fake_openai.__file__ = str(_SCRIPT_DIR / "patch_openai_client.py")
_fake_openai.__spec__ = importlib.machinery.ModuleSpec("openai", None)

for submod in ["types", "types.chat", "types.chat.chat_completion",
               "types.chat.chat_completion_chunk", "types.chat.chat_completion_message",
               "types.chat.chat_completion_message_tool_call",
               "types.chat.chat_completion_message_tool_call_function",
               "types.chat.chat_completion_usage",
               "types.chat.chat_completion_message_param",
               "_models", "_models_base"]:
    if submod not in sys.modules:
        sm = type(sys)(f'openai.{submod}')
        sys.modules[f'openai.{submod}'] = sm

sys.modules['openai'] = _fake_openai

class _OpenAIFinder:
    def find_module(self, fullname, path=None):
        if fullname == 'openai' or fullname.startswith('openai.'):
            return self
        return None
    def load_module(self, fullname):
        if fullname in sys.modules:
            return sys.modules[fullname]
        if fullname == 'openai':
            return _fake_openai
        sm = type(sys)(fullname)
        sys.modules[fullname] = sm
        return sm

sys.meta_path = [f for f in sys.meta_path if type(f).__name__ != '_OpenAIFinder']
sys.meta_path.insert(0, _OpenAIFinder())

try:
    import run_agent
    run_agent.OpenAI = _HttpxOpenAI
    run_agent._OPENAI_CLS_CACHE = _HttpxOpenAI
    logger.debug("[PATCH] run_agent.OpenAI patched")
except Exception as e:
    logger.debug(f"[PATCH] run_agent patch failed: {e}")

logger.debug("[PATCH] httpx OpenAI client installed")
