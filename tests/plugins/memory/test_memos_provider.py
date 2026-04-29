"""Tests for MemOS Platform memory provider."""

import json
import pytest
import threading

from plugins.memory.memos import MemosMemoryProvider

class FakeMemosClient:
    """Fake MemOS client for testing."""
    def __init__(self, search_results=None, add_result=None):
        self._search_results = search_results or {"code": 0, "data": {"memory_detail_list": []}}
        self._add_result = add_result or {"code": 0, "message": "success"}
        self.captured_search = []
        self.captured_add = []

    def search_memory(self, **kwargs):
        self.captured_search.append(kwargs)
        return self._search_results

    def add_message(self, **kwargs):
        self.captured_add.append(kwargs)
        return self._add_result

def test_memos_is_available(monkeypatch):
    provider = MemosMemoryProvider()
    monkeypatch.setenv("MEMOS_API_KEY", "test_key")
    assert provider.is_available() is True
    monkeypatch.delenv("MEMOS_API_KEY", raising=False)

def test_memos_prefetch_success(monkeypatch):
    client = FakeMemosClient(search_results={
        "code": 0,
        "data": {
            "memory_detail_list": [
                {"memory_value": "User likes Python"}
            ],
            "preference_detail_list": [
                {"preference": "Dark mode"}
            ]
        }
    })
    
    provider = MemosMemoryProvider()
    provider.initialize("test-session")
    provider._user_id = "test-user"
    monkeypatch.setattr(provider, "_get_client", lambda: client)

    result = provider.prefetch("What does user like?")
    
    assert "User likes Python" in result
    assert "Dark mode" in result
    assert len(client.captured_search) == 1
    assert client.captured_search[0]["query"] == "What does user like?"
    assert client.captured_search[0]["user_id"] == "test-user"

def test_memos_sync_turn(monkeypatch):
    client = FakeMemosClient()
    
    provider = MemosMemoryProvider()
    provider.initialize("test-session")
    provider._user_id = "test-user"
    monkeypatch.setattr(provider, "_get_client", lambda: client)

    provider.sync_turn("hello", "hi there", session_id="test-session")
    provider._sync_thread.join(timeout=2)

    assert len(client.captured_add) == 1
    assert client.captured_add[0]["user_id"] == "test-user"
    assert client.captured_add[0]["conversation_id"] == "test-session"
    assert len(client.captured_add[0]["messages"]) == 2

def test_memos_search_tool(monkeypatch):
    client = FakeMemosClient(search_results={
        "code": 0,
        "data": {
            "memory_detail_list": [
                {"memory_value": "User uses macOS"}
            ]
        }
    })
    
    provider = MemosMemoryProvider()
    provider.initialize("test-session")
    monkeypatch.setattr(provider, "_get_client", lambda: client)

    result_json = provider.handle_tool_call("memos_search", {"query": "macOS"})
    result = json.loads(result_json)
    
    assert result["count"] == 1
    assert "User uses macOS" in result["results"]

def test_memos_config_methods(tmp_path):
    provider = MemosMemoryProvider()
    schema = provider.get_config_schema()
    assert isinstance(schema, list)
    assert any(item["key"] == "api_key" for item in schema)
    
    # Test save_config
    hermes_home = str(tmp_path)
    provider.save_config({"api_key": "new_key", "user_id": "test_user"}, hermes_home)
    
    config_path = tmp_path / "memos.json"
    assert config_path.exists()
    saved_cfg = json.loads(config_path.read_text())
    assert saved_cfg["api_key"] == "new_key"
    assert saved_cfg["user_id"] == "test_user"

    # Save again with new values to test update
    provider.save_config({"knowledgebase": "kb-1"}, hermes_home)
    saved_cfg2 = json.loads(config_path.read_text())
    assert saved_cfg2["api_key"] == "new_key"
    assert saved_cfg2["knowledgebase"] == "kb-1"

def test_memos_add_message_tool(monkeypatch):
    client = FakeMemosClient()
    
    provider = MemosMemoryProvider()
    provider.initialize("test-session")
    monkeypatch.setattr(provider, "_get_client", lambda: client)

    result_json = provider.handle_tool_call("memos_add_message", {"content": "Save this fact"})
    result = json.loads(result_json)
    
    assert "result" in result
    assert len(client.captured_add) == 1
    assert client.captured_add[0]["messages"][0]["content"] == "Save this fact"

def test_multi_agent_isolation(monkeypatch):
    client = FakeMemosClient()
    
    provider = MemosMemoryProvider()
    # Mock config to enable multi_agent_mode
    monkeypatch.setattr("plugins.memory.memos._load_config", lambda: {"multiAgentMode": True})
    provider.initialize("test-session", agent_id="agent-123")
    monkeypatch.setattr(provider, "_get_client", lambda: client)

    provider.prefetch("query")
    
    assert len(client.captured_search) == 1
    filters = client.captured_search[0].get("filter")
    assert filters == {"user": {"and": [{"agent_id": "agent-123"}]}}

def test_initialization_parsing(monkeypatch):
    provider = MemosMemoryProvider()
    
    # Test with string values
    monkeypatch.setattr("plugins.memory.memos._load_config", lambda: {
        "knowledgebase": '["kb-1", "kb-2"]',
        "allowedAgents": "agent-1",
        "multiAgentMode": "true"
    })
    provider.initialize("session")
    assert provider._knowledgebase == ["kb-1", "kb-2"]
    assert provider._allowed_agents == ["agent-1"]
    assert provider._multi_agent_mode is True

    # Test with boolean/list values directly
    monkeypatch.setattr("plugins.memory.memos._load_config", lambda: {
        "knowledgebase": ["kb-3"],
        "allowedAgents": ["agent-2", "agent-3"],
        "multiAgentMode": False
    })
    provider.initialize("session")
    assert provider._knowledgebase == ["kb-3"]
    assert provider._allowed_agents == ["agent-2", "agent-3"]
    assert provider._multi_agent_mode is False

    # Test invalid json strings
    monkeypatch.setattr("plugins.memory.memos._load_config", lambda: {
        "knowledgebase": "invalid-json",
        "multiAgentMode": "invalid"
    })
    provider.initialize("session")
    assert provider._knowledgebase == ["invalid-json"]
    assert provider._multi_agent_mode is False

def test_memory_enabled_and_system_prompt(monkeypatch):
    provider = MemosMemoryProvider()
    provider._user_id = "test-user"
    
    # No allowedAgents means enabled for all
    provider._allowed_agents = None
    assert provider._is_memory_enabled() is True
    prompt = provider.system_prompt_block()
    assert "Active. User: test-user" in prompt
    assert "Use memos_search" in prompt

    # With allowedAgents, matches agent
    provider._allowed_agents = ["agent-1", "agent-2"]
    provider._agent_id = "agent-1"
    assert provider._is_memory_enabled() is True

    # With allowedAgents, doesn't match
    provider._agent_id = "agent-3"
    assert provider._is_memory_enabled() is False
    assert provider.system_prompt_block() == ""

def test_prefetch_edge_cases(monkeypatch):
    provider = MemosMemoryProvider()
    provider.initialize("session")

    # Test disabled
    provider._allowed_agents = ["other"]
    provider._agent_id = "agent"
    assert provider.prefetch("query") == ""

    # Test short query
    provider._allowed_agents = None
    assert provider.prefetch("hi") == ""

    # Test API error
    def mock_client_error(**kwargs):
        raise RuntimeError("API Error")
    
    class ErrorClient:
        def search_memory(self, **kwargs):
            raise RuntimeError("API Error")

    monkeypatch.setattr(provider, "_get_client", lambda: ErrorClient())
    assert provider.prefetch("long query") == ""

    # Test empty result (no data key or code != 0)
    class EmptyClient:
        def search_memory(self, **kwargs):
            return {"code": 1, "message": "error"}
    monkeypatch.setattr(provider, "_get_client", lambda: EmptyClient())
    assert provider.prefetch("long query") == ""

    # Test empty detail lists
    class NoDataClient:
        def search_memory(self, **kwargs):
            return {"code": 0, "data": {"memory_detail_list": [], "preference_detail_list": []}}
    monkeypatch.setattr(provider, "_get_client", lambda: NoDataClient())
    assert provider.prefetch("long query") == ""

    # Test Pydantic model response
    class PydanticRes:
        def model_dump(self):
            return {
                "code": 0,
                "data": {
                    "memory_detail_list": [{"memory_value": "fact"}],
                    "preference_detail_list": [{"preference": "pref"}]
                }
            }
    class PydanticClient:
        def search_memory(self, **kwargs):
            return PydanticRes()
    monkeypatch.setattr(provider, "_get_client", lambda: PydanticClient())
    res = provider.prefetch("long query")
    assert "fact" in res
    assert "pref" in res

    # queue_prefetch does nothing
    provider.queue_prefetch("query")

def test_sync_turn_edge_cases(monkeypatch):
    provider = MemosMemoryProvider()
    provider.initialize("session")

    # Test memory disabled
    provider._allowed_agents = ["other"]
    provider._agent_id = "agent"
    provider.sync_turn("hello", "hi")
    assert provider._sync_thread is None

    # Test API error in sync thread
    provider._allowed_agents = None
    class ErrorClient:
        def add_message(self, **kwargs):
            raise RuntimeError("API Error")
    monkeypatch.setattr(provider, "_get_client", lambda: ErrorClient())
    provider.sync_turn("hello", "hi")
    provider._sync_thread.join(timeout=2)
    # Should not crash the program

def test_handle_tool_call_edge_cases(monkeypatch):
    provider = MemosMemoryProvider()
    provider.initialize("session")

    # Disabled memory
    provider._allowed_agents = ["other"]
    provider._agent_id = "agent"
    res = provider.handle_tool_call("memos_search", {"query": "test"})
    assert "Memory is disabled" in res

    provider._allowed_agents = None
    
    # Missing query
    monkeypatch.setattr(provider, "_get_client", lambda: FakeMemosClient())
    res = provider.handle_tool_call("memos_search", {})
    assert "Missing required parameter: query" in res

    # Short query
    res = provider.handle_tool_call("memos_search", {"query": "hi"})
    assert "No relevant memories found" in json.loads(res)["result"]

    # Unknown tool
    res = provider.handle_tool_call("memos_unknown", {})
    assert "Unknown tool" in res

    # Missing content in add
    res = provider.handle_tool_call("memos_add_message", {})
    assert "Missing required parameter: content" in res

    # Search API error
    class ErrorClient:
        def search_memory(self, **kwargs):
            raise RuntimeError("API search error")
        def add_message(self, **kwargs):
            raise RuntimeError("API add error")

    monkeypatch.setattr(provider, "_get_client", lambda: ErrorClient())
    res = provider.handle_tool_call("memos_search", {"query": "test query"})
    assert "Search failed: API search error" in res

    # Add API error
    res = provider.handle_tool_call("memos_add_message", {"content": "test"})
    assert "Failed to store: API add error" in res

    # Add returns error code
    class FailClient:
        def add_message(self, **kwargs):
            return {"code": 1, "message": "Backend error"}
    monkeypatch.setattr(provider, "_get_client", lambda: FailClient())
    res = provider.handle_tool_call("memos_add_message", {"content": "test"})
    assert "API error: Backend error" in json.loads(res)["error"]

    # Add returns None
    class NoneClient:
        def add_message(self, **kwargs):
            return None
    monkeypatch.setattr(provider, "_get_client", lambda: NoneClient())
    res = provider.handle_tool_call("memos_add_message", {"content": "test"})
    assert "API error: No response" in json.loads(res)["error"]

def test_shutdown_and_schemas():
    provider = MemosMemoryProvider()
    
    schemas = provider.get_tool_schemas()
    assert isinstance(schemas, list)
    assert any(s["name"] == "memos_search" for s in schemas)
    assert any(s["name"] == "memos_add_message" for s in schemas)
    
    # Test shutdown handles thread and lock cleanly
    def mock_thread():
        pass
    provider._sync_thread = threading.Thread(target=mock_thread)
    provider._sync_thread.start()
    provider.shutdown()
    assert provider._client is None



