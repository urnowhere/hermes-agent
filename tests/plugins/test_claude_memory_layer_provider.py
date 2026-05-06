import json

import pytest

from tools.registry import registry


@pytest.fixture(autouse=True)
def isolated_claude_memory_layer_config(monkeypatch):
    for env_name in [
        "CLAUDE_MEMORY_LAYER_CONTEXT_TOOL",
        "CLAUDE_MEMORY_LAYER_PROJECT_PATH",
        "CLAUDE_MEMORY_LAYER_TOP_K",
        "CLAUDE_MEMORY_LAYER_RECENT_LIMIT",
        "CLAUDE_MEMORY_LAYER_SESSION_LIMIT",
        "CLAUDE_MEMORY_LAYER_MAX_CHARS",
        "CLAUDE_MEMORY_LAYER_SESSION_ID",
        "TERMINAL_CWD",
    ]:
        monkeypatch.delenv(env_name, raising=False)

    import plugins.memory.claude_memory_layer as cml

    monkeypatch.setattr(cml, "_load_hermes_config", lambda: {})


def _register_fake_tool(name, handler):
    registry.deregister(name)
    registry.register(
        name=name,
        toolset="mcp-claude-memory-layer-test",
        schema={
            "description": "fake claude-memory-layer mem-context-pack",
            "parameters": {"type": "object", "properties": {}},
        },
        handler=handler,
    )


def test_prefetch_calls_project_context_pack_tool(monkeypatch):
    from plugins.memory.claude_memory_layer import ClaudeMemoryLayerProvider

    tool_name = "fake_cml_context_pack_prefetch"
    calls = []

    def handler(args, **kwargs):
        calls.append((args, kwargs))
        return json.dumps({"result": "Project Context Pack\n- relevant decision"})

    _register_fake_tool(tool_name, handler)
    monkeypatch.setenv("CLAUDE_MEMORY_LAYER_CONTEXT_TOOL", tool_name)
    monkeypatch.setenv("CLAUDE_MEMORY_LAYER_TOP_K", "7")
    monkeypatch.setenv("CLAUDE_MEMORY_LAYER_RECENT_LIMIT", "44")
    monkeypatch.setenv("CLAUDE_MEMORY_LAYER_SESSION_LIMIT", "6")
    monkeypatch.setenv("TERMINAL_CWD", "/tmp/example-project")

    try:
        provider = ClaudeMemoryLayerProvider()
        assert provider.name == "claude_memory_layer"
        assert provider.is_available()
        provider.initialize("hermes-session")

        context = provider.prefetch("continue the refactor", session_id="hermes-session")

        assert "## Claude Memory Layer Project Context" in context
        assert "relevant decision" in context
        assert len(calls) == 1
        args, kwargs = calls[0]
        assert kwargs == {}
        assert args == {
            "query": "continue the refactor",
            "projectPath": "/tmp/example-project",
            "topK": 7,
            "recentLimit": 44,
            "sessionLimit": 6,
        }
        assert "sessionId" not in args
    finally:
        registry.deregister(tool_name)


def test_prefetch_uses_configured_project_path_and_truncates(monkeypatch):
    from plugins.memory.claude_memory_layer import ClaudeMemoryLayerProvider

    tool_name = "fake_cml_context_pack_truncate"

    def handler(args, **kwargs):
        return json.dumps({"result": "0123456789" * 20})

    _register_fake_tool(tool_name, handler)
    monkeypatch.setenv("CLAUDE_MEMORY_LAYER_CONTEXT_TOOL", tool_name)
    monkeypatch.setenv("CLAUDE_MEMORY_LAYER_PROJECT_PATH", "/workspace/override")
    monkeypatch.setenv("CLAUDE_MEMORY_LAYER_MAX_CHARS", "40")

    try:
        provider = ClaudeMemoryLayerProvider()
        provider.initialize("session")
        context = provider.prefetch("task")

        assert "/workspace/override" not in context  # path is an input, not echoed by provider
        assert len(context) < 120
        assert "[truncated]" in context
    finally:
        registry.deregister(tool_name)


def test_project_path_is_inferred_from_gateway_thread_title(monkeypatch, tmp_path):
    from plugins.memory.claude_memory_layer import ClaudeMemoryLayerProvider

    tool_name = "fake_cml_context_pack_thread_path"
    calls = []
    project_dir = tmp_path / "claude-memory-layer"
    project_dir.mkdir()
    hermes_install = tmp_path / "hermes-agent"
    hermes_install.mkdir()

    def handler(args, **kwargs):
        calls.append(args)
        return json.dumps({"result": "Project Context Pack\nRecent project events: 2"})

    _register_fake_tool(tool_name, handler)
    monkeypatch.setenv("CLAUDE_MEMORY_LAYER_CONTEXT_TOOL", tool_name)
    monkeypatch.chdir(hermes_install)

    try:
        provider = ClaudeMemoryLayerProvider()
        provider.initialize(
            "session",
            chat_name=f"server / #hermes / {project_dir} 이 프로젝트를 Codex 나 hermes 에서도 이어서",
        )
        context = provider.prefetch("continue")

        assert "Recent project events: 2" in context
        assert calls[0]["projectPath"] == str(project_dir)
    finally:
        registry.deregister(tool_name)


def test_project_path_inference_scans_session_title_and_chat_topic(monkeypatch, tmp_path):
    from plugins.memory.claude_memory_layer import ClaudeMemoryLayerProvider

    tool_name = "fake_cml_context_pack_topic_path"
    calls = []
    project_dir = tmp_path / "topic-project"
    project_dir.mkdir()

    def handler(args, **kwargs):
        calls.append(args)
        return json.dumps({"result": "context"})

    _register_fake_tool(tool_name, handler)
    monkeypatch.setenv("CLAUDE_MEMORY_LAYER_CONTEXT_TOOL", tool_name)

    try:
        provider = ClaudeMemoryLayerProvider()
        provider.initialize(
            "session",
            session_title="general discussion",
            chat_topic=f"Active repo: {project_dir}",
        )
        provider.prefetch("status")

        assert calls[0]["projectPath"] == str(project_dir)
    finally:
        registry.deregister(tool_name)


def test_project_path_inference_scans_thread_title(monkeypatch, tmp_path):
    from plugins.memory.claude_memory_layer import ClaudeMemoryLayerProvider

    tool_name = "fake_cml_context_pack_thread_title_path"
    calls = []
    project_dir = tmp_path / "thread-title-project"
    project_dir.mkdir()

    def handler(args, **kwargs):
        calls.append(args)
        return json.dumps({"result": "context"})

    _register_fake_tool(tool_name, handler)
    monkeypatch.setenv("CLAUDE_MEMORY_LAYER_CONTEXT_TOOL", tool_name)

    try:
        provider = ClaudeMemoryLayerProvider()
        provider.initialize("session", thread_title=f"Continue {project_dir}")
        provider.prefetch("continue")

        assert calls[0]["projectPath"] == str(project_dir)
    finally:
        registry.deregister(tool_name)


def test_prefetch_suppresses_mcp_errors(monkeypatch):
    from plugins.memory.claude_memory_layer import ClaudeMemoryLayerProvider

    tool_name = "fake_cml_context_pack_error"

    def handler(args, **kwargs):
        return json.dumps({"error": "server is unavailable"})

    _register_fake_tool(tool_name, handler)
    monkeypatch.setenv("CLAUDE_MEMORY_LAYER_CONTEXT_TOOL", tool_name)

    try:
        provider = ClaudeMemoryLayerProvider()
        provider.initialize("session")

        assert provider.prefetch("task") == ""
    finally:
        registry.deregister(tool_name)


def test_explicit_source_session_filter_is_forwarded(monkeypatch):
    from plugins.memory.claude_memory_layer import ClaudeMemoryLayerProvider

    tool_name = "fake_cml_context_pack_session_filter"
    calls = []

    def handler(args, **kwargs):
        calls.append(args)
        return json.dumps({"result": "context"})

    _register_fake_tool(tool_name, handler)
    monkeypatch.setenv("CLAUDE_MEMORY_LAYER_CONTEXT_TOOL", tool_name)
    monkeypatch.setenv("CLAUDE_MEMORY_LAYER_SESSION_ID", "cml-session-123")

    try:
        provider = ClaudeMemoryLayerProvider()
        provider.initialize("live-hermes-session")
        provider.prefetch("task", session_id="live-hermes-session")

        assert calls[0]["sessionId"] == "cml-session-123"
    finally:
        registry.deregister(tool_name)


def test_config_schema_documents_session_id(monkeypatch):
    from plugins.memory.claude_memory_layer import ClaudeMemoryLayerProvider

    keys = {item["key"] for item in ClaudeMemoryLayerProvider().get_config_schema()}

    assert "session_id" in keys


def test_is_available_requires_registered_context_tool(monkeypatch):
    from plugins.memory.claude_memory_layer import ClaudeMemoryLayerProvider

    tool_name = "fake_missing_cml_context_pack"
    registry.deregister(tool_name)
    monkeypatch.setenv("CLAUDE_MEMORY_LAYER_CONTEXT_TOOL", tool_name)

    provider = ClaudeMemoryLayerProvider()

    assert not provider.is_available()


def test_provider_can_be_loaded_by_memory_plugin_discovery(monkeypatch):
    from plugins.memory import load_memory_provider

    tool_name = "fake_cml_context_pack_discovery"

    def handler(args, **kwargs):
        return json.dumps({"result": "context"})

    _register_fake_tool(tool_name, handler)
    monkeypatch.setenv("CLAUDE_MEMORY_LAYER_CONTEXT_TOOL", tool_name)

    try:
        provider = load_memory_provider("claude_memory_layer")

        assert provider is not None
        assert provider.name == "claude_memory_layer"
        assert provider.is_available()
    finally:
        registry.deregister(tool_name)
