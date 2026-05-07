"""Tests for the bundled OpenJiuwen context engine plugin."""

from __future__ import annotations

import json
from types import SimpleNamespace


class _Collector:
    def __init__(self):
        self.engine = None

    def register_context_engine(self, engine):
        self.engine = engine


class _FakeMessage:
    def __init__(self, role="user", content="", tool_calls=None, tool_call_id=None):
        self.role = role
        self.content = content
        self.tool_calls = tool_calls
        self.tool_call_id = tool_call_id

    def model_dump(self, exclude_none=True):
        data = {"role": self.role, "content": self.content}
        if self.tool_calls is not None:
            data["tool_calls"] = self.tool_calls
        if self.tool_call_id is not None:
            data["tool_call_id"] = self.tool_call_id
        return data


class _FakeContext:
    def __init__(self, history_messages=None):
        self.messages = list(history_messages or [])
        self.add_calls = []
        self.clear_count = 0
        self.window_calls = []

    async def clear_messages(self, with_history=True):
        self.clear_count += 1
        self.messages.clear()

    async def add_messages(self, messages, **kwargs):
        self.add_calls.append(kwargs)
        if isinstance(messages, list):
            self.messages.extend(messages)
        else:
            self.messages.append(messages)

    async def get_context_window(self, system_messages=None, tools=None):
        self.window_calls.append({"system_messages": system_messages or [], "tools": tools or []})
        context_messages = [_FakeMessage(role="assistant", content="compressed")]
        return SimpleNamespace(
            system_messages=system_messages or [],
            context_messages=context_messages,
            statistic=SimpleNamespace(total_tokens=42),
        )


class _FakeOJContextEngine:
    def __init__(self, config):
        self.config = config
        self.contexts = {}
        self.created_contexts = []

    def _key(self, context_id, session_id="default-session"):
        return (context_id, session_id)

    def get_context(self, context_id="ctx", session_id="default-session"):
        return self.contexts.get(self._key(context_id, session_id))

    async def create_context(self, context_id="ctx", session=None, processors=None, history_messages=None, **kwargs):
        session_id = session.get_session_id() if session is not None else "default-session"
        ctx = _FakeContext(history_messages=history_messages)
        self.contexts[self._key(context_id, session_id)] = ctx
        self.created_contexts.append(
            {
                "context_id": context_id,
                "session_id": session_id,
                "processors": processors,
                "processor_snapshot": [
                    {
                        "name": name,
                        "tokens_threshold": getattr(config, "tokens_threshold", None),
                        "compression_target_tokens": getattr(config, "compression_target_tokens", None),
                    }
                    for name, config in (processors or [])
                ],
                "history_messages": history_messages,
            }
        )
        return ctx

    def clear_context(self, context_id, session_id):
        self.contexts.pop(self._key(context_id, session_id), None)


def _fake_bindings():
    return SimpleNamespace(
        ContextEngineConfig=lambda *args, **kwargs: {"args": args, "kwargs": kwargs},
        ContextEngine=_FakeOJContextEngine,
        MessageSummaryOffloaderConfig=lambda **kwargs: SimpleNamespace(**kwargs),
        DialogueCompressorConfig=lambda **kwargs: SimpleNamespace(**kwargs),
        CurrentRoundCompressorConfig=lambda **kwargs: SimpleNamespace(**kwargs),
        RoundLevelCompressorConfig=lambda **kwargs: SimpleNamespace(**kwargs),
        ModelRequestConfig=lambda **kwargs: kwargs,
        ModelClientConfig=lambda **kwargs: kwargs,
        BaseMessage=lambda role="user", content="": _FakeMessage(role=role, content=content),
        SystemMessage=lambda content="": _FakeMessage(role="system", content=content),
        UserMessage=lambda content="": _FakeMessage(role="user", content=content),
        AssistantMessage=lambda content="", tool_calls=None: _FakeMessage(
            role="assistant", content=content, tool_calls=tool_calls
        ),
        ToolMessage=lambda content="", name=None, tool_call_id="unknown": _FakeMessage(
            role="tool", content=content, tool_call_id=tool_call_id
        ),
    )


def _fake_config():
    return {
        "max_context_message_num": None,
        "default_window_message_num": None,
        "default_window_round_num": 7,
        "offload_message_type": ["tool"],
        "protected_tool_names": ["reload_original_context_messages"],
        "content_max_chars_for_compression": 12345,
    }


def _fake_compression_defaults():
    return {
        "threshold_percent": 0.25,
        "protect_last_n": 6,
        "summary_target_ratio": 0.08,
    }


def test_load_config_defaults_without_plugin_file(monkeypatch):
    import plugins.context_engine.openjiuwen as plugin_mod

    monkeypatch.delenv("OPENJIUWEN_DEFAULT_WINDOW_ROUND_NUM", raising=False)
    monkeypatch.delenv("OPENJIUWEN_CONTENT_MAX_CHARS_FOR_COMPRESSION", raising=False)

    cfg = plugin_mod._load_config()

    assert cfg == {
        "max_context_message_num": None,
        "default_window_message_num": None,
        "default_window_round_num": 10,
        "offload_message_type": ["tool"],
        "protected_tool_names": [
            "read_file:*SKILL.md",
            "reload_original_context_messages",
        ],
        "content_max_chars_for_compression": 200000,
    }


def test_load_config_normalizes_invalid_plugin_file(tmp_path, monkeypatch):
    import hermes_constants
    import plugins.context_engine.openjiuwen as plugin_mod

    monkeypatch.setattr(hermes_constants, "get_hermes_home", lambda: tmp_path)
    (tmp_path / "openjiuwen_context_engine.json").write_text(
        json.dumps(
            {
                "max_context_message_num": -1,
                "default_window_message_num": "bad",
                "default_window_round_num": 0,
                "offload_message_type": "tool",
                "protected_tool_names": "reload",
                "content_max_chars_for_compression": "bad",
            }
        ),
        encoding="utf-8",
    )

    cfg = plugin_mod._load_config()

    assert cfg["max_context_message_num"] is None
    assert cfg["default_window_message_num"] is None
    assert cfg["default_window_round_num"] == 10
    assert cfg["offload_message_type"] == ["tool"]
    assert cfg["protected_tool_names"] == []
    assert cfg["content_max_chars_for_compression"] == 200000


def test_register_registers_engine_when_openjiuwen_available(monkeypatch):
    import plugins.context_engine.openjiuwen as plugin_mod

    monkeypatch.setattr(plugin_mod, "_load_bindings", _fake_bindings)
    monkeypatch.setattr(plugin_mod, "_load_config", _fake_config)
    monkeypatch.setattr(plugin_mod, "_load_hermes_compression_defaults", _fake_compression_defaults)

    collector = _Collector()
    plugin_mod.register(collector)
    assert collector.engine is not None
    assert collector.engine.name == "openjiuwen"


def test_register_skips_when_openjiuwen_missing(monkeypatch):
    import plugins.context_engine.openjiuwen as plugin_mod

    def _raise_missing():
        raise ImportError("missing")

    monkeypatch.setattr(plugin_mod, "_load_bindings", _raise_missing)

    collector = _Collector()
    plugin_mod.register(collector)
    assert collector.engine is None


def test_engine_handles_focus_topic_and_api_mode(monkeypatch):
    import plugins.context_engine.openjiuwen as plugin_mod

    monkeypatch.setattr(plugin_mod, "_load_bindings", _fake_bindings)
    monkeypatch.setattr(plugin_mod, "_load_config", _fake_config)
    monkeypatch.setattr(plugin_mod, "_load_hermes_compression_defaults", _fake_compression_defaults)

    engine = plugin_mod.OpenJiuwenContextEngine(
        threshold_percent=0.25,
        protect_last_n=4,
        summary_target_ratio=0.08,
    )
    engine.update_model(
        model="openrouter/auto",
        context_length=200_000,
        base_url="https://example.com/v1",
        api_key="test-key",
        provider="openrouter",
        api_mode="responses",
    )
    assert engine.context_length == 200_000
    assert engine.threshold_percent == 0.25
    assert engine.protect_last_n == 4
    assert engine.summary_target_ratio == 0.10
    assert engine.threshold_tokens == 50_000
    assert engine._runtime.config["kwargs"]["default_window_round_num"] == 7
    assert engine._runtime.config["kwargs"]["enable_reload"] is True
    assert engine._runtime.config["kwargs"]["enable_kv_cache_release"] is False
    assert [name for name, _ in engine._processors] == [
        "MessageSummaryOffloader",
        "DialogueCompressor",
        "CurrentRoundCompressor",
        "RoundLevelCompressor",
    ]

    compressed = engine.compress(
        [
            {"role": "system", "content": "system prompt"},
            {"role": "user", "content": "hello"},
            {"role": "tool", "content": "tool output", "tool_call_id": "call-1"},
        ],
        current_tokens=1234,
        focus_topic="test-topic",
    )
    assert compressed == [
        {"role": "system", "content": "system prompt"},
        {"role": "assistant", "content": "compressed"},
    ]
    assert engine.compression_count == 1
    assert engine.last_total_tokens == 42

    runtime = engine._runtime
    created = runtime.created_contexts[-1]
    assert created["context_id"] == engine._context_id
    assert created["session_id"] == engine._session_id
    assert [name for name, _ in created["processors"]] == [
        "MessageSummaryOffloader",
        "DialogueCompressor",
        "CurrentRoundCompressor",
        "RoundLevelCompressor",
    ]
    assert not isinstance(created["processors"][0][1], dict)
    assert created["processors"][0][1].messages_threshold is None
    assert created["processors"][0][1].protected_tool_names == ["reload_original_context_messages"]
    assert created["history_messages"] is None
    ctx = runtime.get_context(context_id=engine._context_id, session_id=engine._session_id)
    assert [m.role for m in ctx.messages] == ["user", "tool"]
    assert len(ctx.add_calls) == 1
    assert [m.role for m in ctx.add_calls[0]["system_messages"]] == ["system"]
    assert ctx.add_calls[0]["tools"] == []
    assert len(ctx.window_calls) == 1
    assert [m.role for m in ctx.window_calls[0]["system_messages"]] == ["system"]
    assert created["processor_snapshot"][0]["tokens_threshold"] == int(1234 * 0.80)
    assert engine._processors[0][1].tokens_threshold == 50_000
    assert ctx.clear_count == 1

    engine.compress([{"role": "user", "content": "x"}], current_tokens=80_000)
    assert len(runtime.created_contexts) == 1
    assert ctx.clear_count == 2
    assert len(ctx.add_calls) == 2

    result = engine.handle_tool_call("openjiuwen_lookup", {"query": "abc"})
    parsed = json.loads(result)
    assert "does not expose tools" in parsed["error"]


def test_engine_uses_hermes_compression_defaults(monkeypatch):
    import plugins.context_engine.openjiuwen as plugin_mod

    monkeypatch.setattr(plugin_mod, "_load_bindings", _fake_bindings)
    monkeypatch.setattr(plugin_mod, "_load_config", _fake_config)
    monkeypatch.setattr(plugin_mod, "_load_hermes_compression_defaults", _fake_compression_defaults)

    engine = plugin_mod.OpenJiuwenContextEngine()
    engine.update_model(
        model="openrouter/auto",
        context_length=200_000,
        base_url="https://example.com/v1",
        api_key="test-key",
        provider="openrouter",
    )

    assert engine.threshold_percent == 0.25
    assert engine.protect_last_n == 6
    assert engine.summary_target_ratio == 0.10
    assert engine.threshold_tokens == 50_000


def test_compress_aligns_threshold_when_above_auto(monkeypatch):
    """Single compress: snapshot should reflect Hermes-aligned token ceiling."""
    import plugins.context_engine.openjiuwen as plugin_mod

    monkeypatch.setattr(plugin_mod, "_load_bindings", _fake_bindings)
    monkeypatch.setattr(plugin_mod, "_load_config", _fake_config)
    monkeypatch.setattr(plugin_mod, "_load_hermes_compression_defaults", _fake_compression_defaults)

    engine = plugin_mod.OpenJiuwenContextEngine(threshold_percent=0.25, protect_last_n=4, summary_target_ratio=0.08)
    engine.update_model(
        model="openrouter/auto",
        context_length=200_000,
        base_url="https://example.com/v1",
        api_key="test-key",
        provider="openrouter",
    )
    engine.compress([{"role": "user", "content": "x"}], current_tokens=80_000)
    snap = engine._runtime.created_contexts[0]["processor_snapshot"][0]
    expect_align = min(50_000, int(80_000 * 0.62))
    assert snap["tokens_threshold"] == expect_align


def test_hermes_message_role_normalizes_function_and_toolish_user(monkeypatch):
    import plugins.context_engine.openjiuwen as plugin_mod

    monkeypatch.setattr(plugin_mod, "_load_bindings", _fake_bindings)
    monkeypatch.setattr(plugin_mod, "_load_config", _fake_config)
    monkeypatch.setattr(plugin_mod, "_load_hermes_compression_defaults", _fake_compression_defaults)

    engine = plugin_mod.OpenJiuwenContextEngine()
    engine.update_model(
        model="openrouter/auto",
        context_length=200_000,
        base_url="https://example.com/v1",
        api_key="test-key",
        provider="openrouter",
    )
    m1 = engine._to_oj_message(
        {"role": "function", "content": "big output", "tool_call_id": "c1", "name": "read_file"}
    )
    assert getattr(m1, "role", None) == "tool"
    m2 = engine._to_oj_message({"role": "user", "content": "x", "tool_call_id": "c2"})
    assert getattr(m2, "role", None) == "tool"
