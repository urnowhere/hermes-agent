"""OpenJiuwen context engine plugin for Hermes.

This adapter uses OpenJiuwen's real context-engine stack under
``openjiuwen.core.context_engine`` and maps it to Hermes' ``ContextEngine``
contract.
"""
# pylint: disable=broad-exception-caught

from __future__ import annotations

import asyncio
import importlib
import inspect
import json
import logging
import os
import threading
from dataclasses import dataclass
from typing import Any, Dict, List

from agent.context_engine import ContextEngine

logger = logging.getLogger(__name__)


def _as_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_optional_positive_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    parsed = _as_int(value, 0)
    return parsed if parsed > 0 else None


def _hermes_message_role(msg: Dict[str, Any]) -> str:
    """Normalize legacy/alternate tool roles so OpenJiuwen sees real ToolMessage."""
    role = msg.get("role") or "user"
    if role == "function":
        return "tool"
    if msg.get("tool_call_id") and role in {"user", "assistant"}:
        return "tool"
    return role


def _load_config() -> dict:
    """Load OpenJiuwen-specific config without requiring host-code changes."""
    config = {
        "max_context_message_num": None,
        "default_window_message_num": None,
        "default_window_round_num": _as_int(os.environ.get("OPENJIUWEN_DEFAULT_WINDOW_ROUND_NUM"), 10),
        "offload_message_type": ["tool"],
        "protected_tool_names": [
            "read_file:*SKILL.md",
            "reload_original_context_messages",
        ],
        "content_max_chars_for_compression": _as_int(
            os.environ.get("OPENJIUWEN_CONTENT_MAX_CHARS_FOR_COMPRESSION"),
            200000,
        ),
    }

    try:
        from hermes_constants import get_hermes_home

        plugin_path = get_hermes_home() / "openjiuwen_context_engine.json"
        if plugin_path.exists():
            file_cfg = json.loads(plugin_path.read_text(encoding="utf-8"))
            if isinstance(file_cfg, dict):
                for key in config:
                    if file_cfg.get(key) is not None and file_cfg.get(key) != "":
                        config[key] = file_cfg[key]
    except Exception:
        pass

    config["max_context_message_num"] = _as_optional_positive_int(config.get("max_context_message_num"))
    config["default_window_message_num"] = _as_optional_positive_int(config.get("default_window_message_num"))
    default_window_round_num = _as_int(config.get("default_window_round_num"), 10)
    config["default_window_round_num"] = default_window_round_num if default_window_round_num > 0 else 10
    if not isinstance(config.get("offload_message_type"), list):
        config["offload_message_type"] = ["tool"]
    if not isinstance(config.get("protected_tool_names"), list):
        config["protected_tool_names"] = []
    config["content_max_chars_for_compression"] = max(
        1,
        _as_int(config.get("content_max_chars_for_compression"), 200000),
    )
    return config


def _load_hermes_compression_defaults() -> dict:
    defaults = {
        "threshold_percent": 0.50,
        "protect_last_n": 20,
        "summary_target_ratio": 0.20,
    }
    try:
        from hermes_cli.config import load_config

        cfg = load_config()
        compression = cfg.get("compression") if isinstance(cfg, dict) else {}
        if isinstance(compression, dict):
            if compression.get("threshold") is not None:
                defaults["threshold_percent"] = _as_float(
                    compression.get("threshold"),
                    defaults["threshold_percent"],
                )
            if compression.get("protect_last_n") is not None:
                defaults["protect_last_n"] = _as_int(
                    compression.get("protect_last_n"),
                    defaults["protect_last_n"],
                )
            if compression.get("target_ratio") is not None:
                defaults["summary_target_ratio"] = _as_float(
                    compression.get("target_ratio"),
                    defaults["summary_target_ratio"],
                )
    except Exception:
        pass

    defaults["protect_last_n"] = max(1, defaults["protect_last_n"])
    defaults["summary_target_ratio"] = max(0.10, min(defaults["summary_target_ratio"], 0.80))
    return defaults


@dataclass
class _Bindings:
    ContextEngine: Any
    ContextEngineConfig: Any
    MessageSummaryOffloaderConfig: Any
    DialogueCompressorConfig: Any
    CurrentRoundCompressorConfig: Any
    RoundLevelCompressorConfig: Any
    ModelRequestConfig: Any
    ModelClientConfig: Any
    BaseMessage: Any
    SystemMessage: Any
    UserMessage: Any
    AssistantMessage: Any
    ToolMessage: Any


def _load_bindings() -> _Bindings:
    """Load concrete classes from openjiuwen runtime."""
    importlib.import_module("openjiuwen")

    from openjiuwen.core.context_engine import ContextEngine as OJContextEngine
    from openjiuwen.core.context_engine import ContextEngineConfig
    from openjiuwen.core.context_engine.processor.compressor.current_round_compressor import (
        CurrentRoundCompressorConfig,
    )
    from openjiuwen.core.context_engine.processor.compressor.dialogue_compressor import (
        DialogueCompressorConfig,
    )
    from openjiuwen.core.context_engine.processor.compressor.round_level_compressor import (
        RoundLevelCompressorConfig,
    )
    from openjiuwen.core.context_engine.processor.offloader.message_summary_offloader import (
        MessageSummaryOffloaderConfig,
    )
    from openjiuwen.core.foundation.llm.schema.config import (
        ModelClientConfig,
        ModelRequestConfig,
    )
    from openjiuwen.core.foundation.llm.schema.message import (
        AssistantMessage,
        BaseMessage,
        SystemMessage,
        ToolMessage,
        UserMessage,
    )

    return _Bindings(
        ContextEngine=OJContextEngine,
        ContextEngineConfig=ContextEngineConfig,
        MessageSummaryOffloaderConfig=MessageSummaryOffloaderConfig,
        DialogueCompressorConfig=DialogueCompressorConfig,
        CurrentRoundCompressorConfig=CurrentRoundCompressorConfig,
        RoundLevelCompressorConfig=RoundLevelCompressorConfig,
        ModelRequestConfig=ModelRequestConfig,
        ModelClientConfig=ModelClientConfig,
        BaseMessage=BaseMessage,
        SystemMessage=SystemMessage,
        UserMessage=UserMessage,
        AssistantMessage=AssistantMessage,
        ToolMessage=ToolMessage,
    )


def _run_coro(coro):
    """Run coroutine from both sync and async contexts."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)

    result: dict = {}
    error: dict = {}

    def _runner():
        try:
            result["value"] = asyncio.run(coro)
        except Exception as exc:
            error["value"] = exc

    t = threading.Thread(target=_runner, daemon=True)
    t.start()
    t.join()
    if "value" in error:
        raise error["value"]
    return result.get("value")


def _resolve_maybe_awaitable(value):
    """Resolve awaitable return values in sync context."""
    if inspect.isawaitable(value):
        return _run_coro(value)
    return value


def _message_count_from_context(ctx) -> int | None:
    get_messages = getattr(ctx, "get_messages", None)
    if get_messages is not None:
        try:
            messages = _resolve_maybe_awaitable(get_messages())
            if messages is not None:
                return len(messages)
        except Exception:
            pass
    messages = getattr(ctx, "messages", None)
    if messages is not None:
        try:
            return len(messages)
        except TypeError:
            return None
    return None


def _stat_value(stat, name: str):
    if stat is None:
        return None
    if isinstance(stat, dict):
        return stat.get(name)
    return getattr(stat, name, None)


def _config_value(config: Any, name: str):
    if isinstance(config, dict):
        return config.get(name)
    return getattr(config, name, None)


def _set_config_value(config: Any, name: str, value: Any, changes: List[tuple[Any, str, Any]]) -> None:
    if not hasattr(config, name):
        return
    changes.append((config, name, getattr(config, name)))
    setattr(config, name, value)


def _processor_config_summary(config: Any) -> Dict[str, Any]:
    return {
        "class": type(config).__name__,
        "messages_threshold": _config_value(config, "messages_threshold"),
        "tokens_threshold": _config_value(config, "tokens_threshold"),
        "messages_to_keep": _config_value(config, "messages_to_keep"),
        "keep_last_round": _config_value(config, "keep_last_round"),
        "compression_target_tokens": _config_value(config, "compression_target_tokens"),
        "summary_max_tokens": _config_value(config, "summary_max_tokens"),
        "trigger_total_tokens": _config_value(config, "trigger_total_tokens"),
        "target_total_tokens": _config_value(config, "target_total_tokens"),
        "offload_writeback_enabled": _config_value(config, "offload_writeback_enabled"),
    }


def _normalize_provider(provider: str) -> str:
    mapping = {
        "openrouter": "OpenRouter",
        "openai": "OpenAI",
        "siliconflow": "SiliconFlow",
        "dashscope": "DashScope",
    }
    return mapping.get((provider or "").lower(), "OpenAI")


def _infer_provider(provider: str, model: str, base_url: str) -> str:
    """Infer provider when Hermes runtime does not pass one explicitly."""
    p = (provider or "").strip().lower()
    if p:
        return p
    m = (model or "").strip().lower()
    if "/" in m:
        return m.split("/", 1)[0]
    b = (base_url or "").strip().lower()
    if "openrouter.ai" in b:
        return "openrouter"
    if "api.openai.com" in b:
        return "openai"
    return ""


class _SessionShim:
    """Minimal session object expected by OpenJiuwen's ContextEngine."""

    def __init__(self, session_id: str) -> None:
        self._session_id = session_id

    def get_session_id(self) -> str:
        return self._session_id


def _bootstrap_auxiliary_env(provider: str, model: str, api_key: str, base_url: str) -> None:
    """Best-effort bridge so Hermes auxiliary-compression client can resolve.

    We only set missing env vars and never override user-provided values.
    This keeps behavior predictable while allowing plugin-only integration.
    """
    p = _infer_provider(provider=provider, model=model, base_url=base_url)
    key = (api_key or "").strip()
    base = (base_url or "").strip()

    if p == "openrouter" and key and not os.getenv("OPENROUTER_API_KEY"):
        os.environ["OPENROUTER_API_KEY"] = key
        logger.info("openjiuwen: bootstrapped OPENROUTER_API_KEY for auxiliary compression checks")

    # For OpenAI/custom-compatible endpoints, auxiliary resolver uses OPENAI_*.
    if p in {"openai", "custom"}:
        if key and not os.getenv("OPENAI_API_KEY"):
            os.environ["OPENAI_API_KEY"] = key
            logger.info("openjiuwen: bootstrapped OPENAI_API_KEY for auxiliary compression checks")
        if base and not os.getenv("OPENAI_BASE_URL"):
            os.environ["OPENAI_BASE_URL"] = base
            logger.info("openjiuwen: bootstrapped OPENAI_BASE_URL for auxiliary compression checks")


class OpenJiuwenContextEngine(ContextEngine):
    """Hermes adapter for OpenJiuwen's native ContextEngine."""

    def __init__(
        self,
        threshold_percent: float | None = None,
        protect_first_n: int = 3,
        protect_last_n: int | None = None,
        summary_target_ratio: float | None = None,
    ):
        self._config = _load_config()
        compression_defaults = _load_hermes_compression_defaults()
        self.threshold_percent = (
            compression_defaults["threshold_percent"]
            if threshold_percent is None
            else threshold_percent
        )
        self.protect_first_n = protect_first_n
        self.protect_last_n = (
            compression_defaults["protect_last_n"]
            if protect_last_n is None
            else protect_last_n
        )
        summary_ratio = (
            compression_defaults["summary_target_ratio"]
            if summary_target_ratio is None
            else summary_target_ratio
        )
        self.summary_target_ratio = max(0.10, min(summary_ratio, 0.80))

        self.context_length = 0
        self.threshold_tokens = 0
        self.compression_count = 0
        self.last_prompt_tokens = 0
        self.last_completion_tokens = 0
        self.last_total_tokens = 0
        self._compression_model_name = "gpt-4"
        self._token_counter = None

        self._bindings = _load_bindings()
        self._runtime = self._bindings.ContextEngine(
            self._bindings.ContextEngineConfig(
                max_context_message_num=self._config["max_context_message_num"],
                default_window_message_num=self._config["default_window_message_num"],
                default_window_round_num=self._config["default_window_round_num"],
                enable_reload=True,
                enable_kv_cache_release=False,
            )
        )

        self._processors: List[tuple[str, Dict[str, Any]]] = []
        self._context_id = "hermes-openjiuwen-context"
        self._session_id = "default-session"

        logger.info(
            "openjiuwen context engine initialized: default_window_round_num=%s "
            "max_context_message_num=%s default_window_message_num=%s "
            "offload_message_type=%s protected_tool_names=%d content_max_chars_for_compression=%d",
            self._config["default_window_round_num"],
            self._config["max_context_message_num"],
            self._config["default_window_message_num"],
            self._config["offload_message_type"],
            len(self._config["protected_tool_names"]),
            self._config["content_max_chars_for_compression"],
        )

    @property
    def name(self) -> str:
        return "openjiuwen"

    def is_available(self) -> bool:
        return self._runtime is not None

    def _get_context(self):
        try:
            return self._runtime.get_context(context_id=self._context_id, session_id=self._session_id)
        except TypeError:
            try:
                return self._runtime.get_context(self._context_id, self._session_id)
            except TypeError:
                return self._runtime.get_context(self._context_id)

    def _build_token_counter(self, model: str):
        """OpenJiuwen processors read token totals via context.token_counter(); prefer TiktokenCounter."""
        try:
            from openjiuwen.core.context_engine.token.tiktoken_counter import TiktokenCounter

            name = (model or "").strip() or "gpt-4"
            return TiktokenCounter(model=name)
        except Exception as exc:
            logger.info("openjiuwen: could not construct TiktokenCounter (%s); using runtime default", exc)
            return None

    def _clear_context(self) -> None:
        try:
            result = self._runtime.clear_context(context_id=self._context_id, session_id=self._session_id)
        except TypeError:
            result = self._runtime.clear_context(self._context_id, self._session_id)
        _resolve_maybe_awaitable(result)

    def _create_context(self, history_messages: List[Any] | None = None):
        processor_names = [name for name, _ in self._processors]
        logger.info(
            "openjiuwen create_context start: session=%s context=%s processors=%s history_messages=%d",
            self._session_id,
            self._context_id,
            processor_names,
            len(history_messages or []),
        )
        kwargs = {
            "context_id": self._context_id,
            "session": _SessionShim(self._session_id),
            "processors": self._processors or None,
        }
        if history_messages is not None:
            kwargs["history_messages"] = history_messages
        if self._token_counter is not None:
            kwargs["token_counter"] = self._token_counter
        try:
            created = self._runtime.create_context(**kwargs)
            ctx = _resolve_maybe_awaitable(created)
            logger.info(
                "openjiuwen create_context complete: session=%s context=%s context_messages=%s",
                self._session_id,
                self._context_id,
                _message_count_from_context(ctx),
            )
            return ctx
        except TypeError:
            kwargs.pop("token_counter", None)
            try:
                created = self._runtime.create_context(**kwargs)
                ctx = _resolve_maybe_awaitable(created)
                logger.info(
                    "openjiuwen create_context complete: session=%s context=%s context_messages=%s token_counter_fallback=true",
                    self._session_id,
                    self._context_id,
                    _message_count_from_context(ctx),
                )
                return ctx
            except TypeError:
                # Older/fake runtimes may not support session/history_messages.
                created = self._runtime.create_context(
                    context_id=self._context_id,
                    processors=self._processors or None,
                )
                ctx = _resolve_maybe_awaitable(created)
                if history_messages:
                    self._add_messages(ctx, history_messages)
                logger.info(
                    "openjiuwen create_context complete: session=%s context=%s context_messages=%s compat_mode=true",
                    self._session_id,
                    self._context_id,
                    _message_count_from_context(ctx),
                )
                return ctx
        except Exception:
            logger.exception(
                "openjiuwen create_context failed: session=%s context=%s processors=%s",
                self._session_id,
                self._context_id,
                [
                    {"name": name, "config": _processor_config_summary(config)}
                    for name, config in self._processors
                ],
            )
            self._diagnose_processor_initialization()
            raise

    def _diagnose_processor_initialization(self) -> None:
        for index, (name, config) in enumerate(self._processors):
            diagnostic_context_id = f"{self._context_id}-diagnostic-{index}"
            logger.info(
                "openjiuwen processor init diagnostic start: processor=%s config=%s",
                name,
                _processor_config_summary(config),
            )
            try:
                diag_kwargs = {
                    "context_id": diagnostic_context_id,
                    "session": _SessionShim(self._session_id),
                    "processors": [(name, config)],
                }
                if self._token_counter is not None:
                    diag_kwargs["token_counter"] = self._token_counter
                try:
                    created = self._runtime.create_context(**diag_kwargs)
                except TypeError:
                    diag_kwargs.pop("token_counter", None)
                    created = self._runtime.create_context(**diag_kwargs)
                _resolve_maybe_awaitable(created)
                logger.info("openjiuwen processor init diagnostic ok: processor=%s", name)
            except Exception:
                logger.exception("openjiuwen processor init diagnostic failed: processor=%s", name)
            finally:
                try:
                    result = self._runtime.clear_context(
                        context_id=diagnostic_context_id,
                        session_id=self._session_id,
                    )
                    _resolve_maybe_awaitable(result)
                except Exception:
                    pass

    def _ensure_context(self):
        ctx = self._get_context()
        if ctx is not None:
            return ctx
        return self._create_context()

    def _prepare_context_for_compress(self, history_messages: List[Any], **kwargs):
        """Reuse pooled ModelContext so processor instances and buffers survive across compress calls.

        Hermes passes the full transcript each time; we clear the buffer then ``add_messages`` so
        ADD-phase processors still run, without ``clear_context`` destroying the pooled context.
        """
        ctx = self._get_context()
        if ctx is None:
            logger.info(
                "openjiuwen prepare_context: creating pooled context session=%s context=%s",
                self._session_id,
                self._context_id,
            )
            ctx = self._create_context(history_messages=None)
        else:
            logger.info(
                "openjiuwen prepare_context: reusing pooled context session=%s context=%s",
                self._session_id,
                self._context_id,
            )
        clear_m = getattr(ctx, "clear_messages", None)
        if clear_m is not None:
            try:
                _resolve_maybe_awaitable(clear_m())
            except TypeError:
                _resolve_maybe_awaitable(clear_m(with_history=True))
        else:
            set_m = getattr(ctx, "set_messages", None)
            if callable(set_m):
                set_m([], with_history=True)
            else:
                logger.warning("openjiuwen prepare_context: context has no clear_messages/set_messages; skipping buffer reset")
        self._add_messages(ctx, history_messages, **kwargs)
        return ctx

    def _add_messages(self, ctx, messages: List[Any], **kwargs) -> None:
        if not messages:
            return
        before_count = _message_count_from_context(ctx)
        logger.info(
            "openjiuwen processor phase=add_messages start: processors=%s incoming_messages=%d context_messages_before=%s",
            [name for name, _ in self._processors],
            len(messages),
            before_count,
        )
        add_messages = getattr(ctx, "add_messages", None)
        if add_messages is None:
            ctx.set_messages(messages, with_history=True)
            logger.info(
                "openjiuwen processor phase=add_messages complete: context_messages_before=%s context_messages_after=%s compat_set_messages=true",
                before_count,
                _message_count_from_context(ctx),
            )
            return
        try:
            try:
                result = add_messages(messages, **kwargs)
                _resolve_maybe_awaitable(result)
            except TypeError:
                result = add_messages(messages)
                _resolve_maybe_awaitable(result)
        except TypeError:
            for msg in messages:
                try:
                    result = add_messages(msg, **kwargs)
                    _resolve_maybe_awaitable(result)
                except TypeError:
                    result = add_messages(msg)
                    _resolve_maybe_awaitable(result)
        logger.info(
            "openjiuwen processor phase=add_messages complete: context_messages_before=%s context_messages_after=%s",
            before_count,
            _message_count_from_context(ctx),
        )

    def _apply_compress_threshold_tuning(self, current_tokens: int | None):
        """Lower processor token bars so OpenJiuwen triggers match Hermes/API usage.

        Provider-reported prompt tokens are often much higher than OpenJiuwen's local
        tiktoken-based counts. If we only set ``tokens_threshold`` from context
        length × threshold %, processors may never fire while Hermes already asked
        for compression. Optional env ``OPENJIUWEN_HERMES_TOKEN_ALIGN_RATIO``
        (default ``0.62``) scales Hermes ``current_tokens`` into a conservative
        ceiling for processor thresholds when above the auto threshold.
        """
        if not current_tokens or current_tokens <= 0 or self.threshold_tokens <= 0:
            return None
        align_ratio = max(0.05, min(_as_float(os.environ.get("OPENJIUWEN_HERMES_TOKEN_ALIGN_RATIO"), 0.62), 0.95))
        hermes_aligned = max(2000, int(current_tokens * align_ratio))

        if current_tokens < self.threshold_tokens:
            effective_threshold = max(1, int(current_tokens * 0.80))
            tune_reason = "below_auto_threshold"
        else:
            effective_threshold = max(2000, min(self.threshold_tokens, hermes_aligned))
            tune_reason = "align_to_hermes_reported_tokens"

        compression_target_tokens = max(600, int(effective_threshold * self.summary_target_ratio))
        changes: List[tuple[Any, str, Any]] = []
        for name, cfg in self._processors:
            _set_config_value(cfg, "tokens_threshold", effective_threshold, changes)
            _set_config_value(cfg, "large_message_threshold", effective_threshold, changes)
            _set_config_value(cfg, "trigger_total_tokens", effective_threshold, changes)
            _set_config_value(cfg, "target_total_tokens", max(1000, int(effective_threshold * 0.70)), changes)
            _set_config_value(cfg, "compression_call_max_tokens", max(2000, current_tokens), changes)
            _set_config_value(cfg, "compression_target_tokens", compression_target_tokens, changes)
            _set_config_value(cfg, "summary_max_tokens", max(600, compression_target_tokens // 2), changes)
            _set_config_value(
                cfg,
                "min_selected_tokens_for_compression",
                max(1000, effective_threshold // 5),
                changes,
            )
            _set_config_value(cfg, "summary_merge_target_tokens", compression_target_tokens, changes)
            _set_config_value(cfg, "accumulated_summary_token_limit", max(2000, compression_target_tokens * 5), changes)
            _set_config_value(cfg, "first_pass_target_tokens", max(600, compression_target_tokens * 3), changes)
            _set_config_value(cfg, "second_pass_target_tokens", max(600, compression_target_tokens * 2), changes)
            _set_config_value(cfg, "third_pass_target_tokens", compression_target_tokens, changes)

        logger.info(
            "openjiuwen compress threshold tuning: reason=%s current_tokens=%d hermes_auto_threshold=%d "
            "align_ratio=%.2f effective_threshold=%d processors=%s",
            tune_reason,
            current_tokens,
            self.threshold_tokens,
            align_ratio,
            effective_threshold,
            [
                {"name": name, "config": _processor_config_summary(config)}
                for name, config in self._processors
            ],
        )

        def restore() -> None:
            for config, attr, old_value in changes:
                setattr(config, attr, old_value)

        return restore

    def _to_oj_message(self, msg: Dict[str, Any]):
        role = _hermes_message_role(msg)
        content = msg.get("content", "")
        if role == "system":
            return self._bindings.SystemMessage(content=content)
        if role == "assistant":
            tool_calls = msg.get("tool_calls") or None
            return self._bindings.AssistantMessage(content=content, tool_calls=tool_calls)
        if role == "tool":
            tid = msg.get("tool_call_id") or "unknown"
            try:
                return self._bindings.ToolMessage(
                    content=content,
                    name=msg.get("name"),
                    tool_call_id=tid,
                )
            except TypeError:
                return self._bindings.ToolMessage(content=content, tool_call_id=tid)
        if role == "user":
            return self._bindings.UserMessage(content=content)
        return self._bindings.BaseMessage(role=role, content=content)

    def _from_oj_message(self, oj_msg) -> Dict[str, Any]:
        if hasattr(oj_msg, "model_dump"):
            return oj_msg.model_dump(exclude_none=True)
        if isinstance(oj_msg, dict):
            return oj_msg
        return {"role": "user", "content": str(oj_msg)}

    def update_from_response(self, usage: Dict[str, Any]) -> None:
        self.last_prompt_tokens = usage.get("prompt_tokens", 0)
        self.last_completion_tokens = usage.get("completion_tokens", 0)
        self.last_total_tokens = usage.get("total_tokens", 0)

    def should_compress(self, prompt_tokens: int = None) -> bool:
        tokens = prompt_tokens if prompt_tokens is not None else self.last_prompt_tokens
        if self.threshold_tokens <= 0:
            return False
        return tokens >= self.threshold_tokens

    def compress(
        self,
        messages: List[Dict[str, Any]],
        current_tokens: int = None,
        focus_topic: str = None,
        **kwargs,
    ) -> List[Dict[str, Any]]:
        _ = focus_topic
        _ = kwargs
        if not messages:
            logger.info("openjiuwen compress skipped: empty message list")
            return messages
        try:
            logger.info(
                "openjiuwen compress start: messages=%d current_tokens=%s threshold_tokens=%d session=%s context=%s",
                len(messages),
                current_tokens if current_tokens is not None else "unknown",
                self.threshold_tokens,
                self._session_id,
                self._context_id,
            )
            system_messages = []
            history_messages = []
            for msg in messages:
                oj_message = self._to_oj_message(msg)
                if _hermes_message_role(msg) == "system":
                    system_messages.append(oj_message)
                else:
                    history_messages.append(oj_message)
            restore_processor_thresholds = self._apply_compress_threshold_tuning(current_tokens)
            ctx = self._prepare_context_for_compress(
                history_messages,
                system_messages=system_messages,
                tools=[],
            )
            before_window_count = _message_count_from_context(ctx)
            logger.info(
                "openjiuwen processor phase=get_context_window start: processors=%s system_messages=%d history_messages=%d context_messages_before=%s",
                [name for name, _ in self._processors],
                len(system_messages),
                len(history_messages),
                before_window_count,
            )
            try:
                window_result = ctx.get_context_window(system_messages=system_messages, tools=[])
            except TypeError:
                window_result = ctx.get_context_window()
            window = _resolve_maybe_awaitable(window_result)
            if window is None:
                logger.info("openjiuwen compress skipped: runtime returned no context window")
                return messages
            window_system_messages = list(window.system_messages or [])
            window_context_messages = list(window.context_messages or [])
            merged = window_system_messages + window_context_messages
            stat = getattr(window, "statistic", None)
            logger.info(
                "openjiuwen processor phase=get_context_window complete: system_messages=%d context_messages=%d merged=%d "
                "context_messages_before=%s context_messages_after=%s stat_total_messages=%s stat_total_tokens=%s",
                len(window_system_messages),
                len(window_context_messages),
                len(merged),
                before_window_count,
                _message_count_from_context(ctx),
                _stat_value(stat, "total_messages"),
                _stat_value(stat, "total_tokens"),
            )
            out = [self._from_oj_message(m) for m in merged]
            if not out:
                logger.info("openjiuwen compress skipped: runtime produced empty output")
                return messages
            if stat is not None:
                self.last_total_tokens = getattr(stat, "total_tokens", self.last_total_tokens)
            stat_tokens = _as_int(_stat_value(stat, "total_tokens"), 0)
            msg_reduced = len(out) < len(messages)
            token_reduced = bool(
                current_tokens and stat_tokens > 0 and stat_tokens < max(1, int(current_tokens * 0.93))
            )
            if msg_reduced or token_reduced:
                self.compression_count += 1
                logger.info(
                    "openjiuwen compress complete: messages before=%d after=%d saved=%d "
                    "compression_count=%d stat_total_tokens=%s",
                    len(messages),
                    len(out),
                    len(messages) - len(out),
                    self.compression_count,
                    stat_tokens or "n/a",
                )
            else:
                logger.info(
                    "openjiuwen compress no effective reduction: messages before=%d after=%d "
                    "stat_total_tokens=%s note=\"OpenJiuwen may inject system prompts; "
                    "if stat_total_tokens is 0, watch OpenJiuwen logs for "
                    "'trigger context processor' on ADD/GET\"",
                    len(messages),
                    len(out),
                    stat_tokens or "n/a",
                )
            if current_tokens and current_tokens > 0:
                self.last_prompt_tokens = min(self.last_prompt_tokens or current_tokens, current_tokens)
            return out
        except Exception as exc:
            logger.warning("openjiuwen compress failed, falling back to original messages: %s", exc)
            return messages
        finally:
            if "restore_processor_thresholds" in locals() and restore_processor_thresholds is not None:
                restore_processor_thresholds()

    def update_model(
        self,
        model: str,
        context_length: int,
        base_url: str = "",
        api_key: str = "",
        provider: str = "",
        api_mode: str = "",
        **kwargs,
    ) -> None:
        _ = api_mode
        self._config = _load_config()
        self.threshold_percent = _as_float(kwargs.get("threshold_percent"), self.threshold_percent)
        self.protect_last_n = _as_int(kwargs.get("protect_last_n"), self.protect_last_n)
        self.summary_target_ratio = _as_float(
            kwargs.get("summary_target_ratio"),
            self.summary_target_ratio,
        )
        self.summary_target_ratio = max(0.10, min(self.summary_target_ratio, 0.80))
        self.context_length = context_length
        self.threshold_tokens = int(context_length * self.threshold_percent)
        self._compression_model_name = (model or "").strip() or "gpt-4"
        self._token_counter = self._build_token_counter(self._compression_model_name)
        try:
            self._clear_context()
        except Exception:
            pass
        _bootstrap_auxiliary_env(
            provider=provider,
            model=model,
            api_key=api_key,
            base_url=base_url,
        )

        model_cfg = self._bindings.ModelRequestConfig(
            model=model,
            max_tokens=max(512, int(self.threshold_tokens * 0.25)),
        )
        client_cfg = self._bindings.ModelClientConfig(
            client_provider=_normalize_provider(provider),
            api_key=api_key or "empty",
            api_base=base_url or "https://api.openai.com/v1",
            verify_ssl=False,
        )
        compression_target_tokens = max(600, int(self.threshold_tokens * self.summary_target_ratio))
        recent_messages = max(6, self.protect_last_n)
        processor_base = {
            "model": model_cfg,
            "model_client": client_cfg,
        }
        self._processors = [
            (
                "MessageSummaryOffloader",
                self._bindings.MessageSummaryOffloaderConfig(
                    messages_threshold=None,
                    tokens_threshold=max(2000, self.threshold_tokens),
                    large_message_threshold=max(2000, self.threshold_tokens),
                    offload_message_type=self._config["offload_message_type"],
                    protected_tool_names=self._config["protected_tool_names"],
                    messages_to_keep=None,
                    keep_last_round=False,
                    customized_summary_prompt=None,
                    enable_adaptive_compression=True,
                    summary_max_tokens=max(600, compression_target_tokens // 2),
                    enable_precise_step=False,
                    step_summary_max_context_messages=8,
                    content_max_chars_for_compression=self._config["content_max_chars_for_compression"],
                    **processor_base,
                ),
            ),
            (
                "DialogueCompressor",
                self._bindings.DialogueCompressorConfig(
                    messages_threshold=None,
                    tokens_threshold=max(2000, self.threshold_tokens),
                    messages_to_keep=recent_messages,
                    keep_last_round=False,
                    compression_target_tokens=compression_target_tokens,
                    offload_writeback_enabled=False,
                    **processor_base,
                ),
            ),
            (
                "CurrentRoundCompressor",
                self._bindings.CurrentRoundCompressorConfig(
                    tokens_threshold=max(2000, self.threshold_tokens),
                    messages_to_keep=max(3, min(recent_messages, 6)),
                    min_selected_tokens_for_compression=max(1000, self.threshold_tokens // 5),
                    compression_target_tokens=max(600, compression_target_tokens),
                    summary_merge_target_tokens=max(600, compression_target_tokens),
                    accumulated_summary_token_limit=max(2000, compression_target_tokens * 5),
                    summary_merge_min_blocks=3,
                    prior_context_window_size=10,
                    offload_writeback_enabled=False,
                    **processor_base,
                ),
            ),
            (
                "RoundLevelCompressor",
                self._bindings.RoundLevelCompressorConfig(
                    rounds_threshold=2,
                    tokens_threshold=max(2000, self.threshold_tokens),
                    trigger_total_tokens=max(2000, self.threshold_tokens),
                    target_total_tokens=max(1000, int(self.threshold_tokens * 0.7)),
                    compression_call_max_tokens=max(2000, self.context_length),
                    keep_last_round=True,
                    keep_recent_messages=recent_messages,
                    messages_to_keep=recent_messages,
                    first_pass_target_tokens=max(600, compression_target_tokens * 3),
                    second_pass_target_tokens=max(600, compression_target_tokens * 2),
                    third_pass_target_tokens=max(600, compression_target_tokens),
                    truncate_head_ratio=0.2,
                    offload_writeback_enabled=False,
                    **processor_base,
                ),
            ),
        ]
        processor_summaries = []
        for name, cfg in self._processors:
            processor_summaries.append(
                {
                    "name": name,
                    "messages_threshold": _config_value(cfg, "messages_threshold"),
                    "tokens_threshold": _config_value(cfg, "tokens_threshold"),
                    "messages_to_keep": _config_value(cfg, "messages_to_keep"),
                    "keep_last_round": _config_value(cfg, "keep_last_round"),
                    "compression_target_tokens": _config_value(cfg, "compression_target_tokens"),
                    "summary_max_tokens": _config_value(cfg, "summary_max_tokens"),
                    "trigger_total_tokens": _config_value(cfg, "trigger_total_tokens"),
                    "target_total_tokens": _config_value(cfg, "target_total_tokens"),
                    "offload_writeback_enabled": _config_value(cfg, "offload_writeback_enabled"),
                }
            )
        logger.info(
            "openjiuwen processors configured: context_length=%d threshold_tokens=%d processors=%s",
            self.context_length,
            self.threshold_tokens,
            processor_summaries,
        )

    def on_session_start(self, session_id: str, **kwargs) -> None:
        _ = kwargs
        self._session_id = session_id or "default-session"
        self._context_id = f"hermes-openjiuwen-{self._session_id}"
        try:
            self._ensure_context()
        except Exception as exc:
            logger.info("openjiuwen on_session_start failed to create context: %s", exc, exc_info=True)

    def on_session_end(self, session_id: str, messages: List[Dict[str, Any]]) -> None:
        _ = session_id
        _ = messages
        try:
            self._clear_context()
        except Exception:
            pass

    def on_session_reset(self) -> None:
        super().on_session_reset()
        try:
            self._clear_context()
        except Exception:
            pass

    def get_tool_schemas(self) -> List[Dict[str, Any]]:
        return []

    def handle_tool_call(self, name: str, args: Dict[str, Any], **kwargs) -> str:
        _ = kwargs
        return json.dumps({"error": f"OpenJiuwen context engine does not expose tools: {name}"})


def register(ctx) -> None:
    """Register the OpenJiuwen context engine when dependency is available."""
    try:
        engine = OpenJiuwenContextEngine()
    except ImportError:
        logger.info("openjiuwen dependency missing; context engine plugin unavailable")
        return
    except Exception as exc:
        logger.warning("openjiuwen context engine init failed: %s", exc)
        return

    if hasattr(engine, "is_available") and not engine.is_available():
        return
    ctx.register_context_engine(engine)
