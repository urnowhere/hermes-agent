"""Tests for the LanceDB memory provider."""

from __future__ import annotations

import json
import sys
import types
from pathlib import Path

import pytest


class FakeSentenceTransformer:
    def __init__(self, model_name: str):
        self.model_name = model_name

    def get_sentence_embedding_dimension(self):
        return 4

    def encode(self, texts, normalize_embeddings=True):
        vectors = []
        for text in texts:
            text = str(text)
            vectors.append([
                float(len(text)),
                float(sum(ord(ch) for ch in text) % 97),
                float(text.lower().count("user") + 1),
                float(text.lower().count("project") + 1),
            ])
        return vectors


class FakeSearch:
    def __init__(self, rows):
        self._rows = list(rows)
        self._limit = len(self._rows)

    def limit(self, value):
        self._limit = value
        return self

    def to_list(self):
        return list(self._rows[: self._limit])


class FakeTable:
    def __init__(self):
        self.rows = []

    def add(self, rows):
        self.rows.extend(rows)

    def search(self, vector):
        def score(row):
            row_vec = row.get("vector", [])
            if not row_vec:
                return 0.0
            length = min(len(vector), len(row_vec))
            diff = sum(abs(float(vector[i]) - float(row_vec[i])) for i in range(length))
            return 1.0 / (1.0 + diff)

        ranked = []
        for row in self.rows:
            item = dict(row)
            item["_score"] = score(item)
            ranked.append(item)
        ranked.sort(key=lambda item: item["_score"], reverse=True)
        return FakeSearch(ranked)

    def delete(self, condition: str):
        ids = []
        for chunk in condition.split("OR"):
            if "=" not in chunk:
                continue
            ids.append(chunk.split("=", 1)[1].strip().strip("'"))
        self.rows = [row for row in self.rows if row.get("id") not in ids]


class FakeDB:
    def __init__(self):
        self.tables = {}

    def table_names(self):
        return list(self.tables.keys())

    def open_table(self, name):
        return self.tables[name]

    def create_table(self, name, schema=None):
        table = FakeTable()
        self.tables[name] = table
        return table


class FakeOpenAIEmbeddingsClient:
    def create(self, model, input):
        data = []
        for text in input:
            data.append(types.SimpleNamespace(embedding=[float(len(str(text))), 1.0, 2.0, 3.0]))
        return types.SimpleNamespace(data=data)


class FakeOpenAI:
    def __init__(self, api_key=""):
        self.api_key = api_key
        self.embeddings = FakeOpenAIEmbeddingsClient()


@pytest.fixture(autouse=True)
def _fake_modules(monkeypatch, tmp_path):
    hermes_home = tmp_path / ".hermes"
    hermes_home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    fake_lancedb = types.ModuleType("lancedb")
    fake_db = FakeDB()
    fake_lancedb.connect = lambda path: fake_db
    monkeypatch.setitem(sys.modules, "lancedb", fake_lancedb)

    fake_pa = types.ModuleType("pyarrow")
    fake_pa.field = lambda *args, **kwargs: ("field", args, kwargs)
    fake_pa.float32 = lambda: "float32"
    fake_pa.string = lambda: "string"
    fake_pa.list_ = lambda inner, size=None: ("list", inner, size)
    fake_pa.schema = lambda fields: fields
    monkeypatch.setitem(sys.modules, "pyarrow", fake_pa)

    fake_st = types.ModuleType("sentence_transformers")
    fake_st.SentenceTransformer = FakeSentenceTransformer
    monkeypatch.setitem(sys.modules, "sentence_transformers", fake_st)

    fake_openai = types.ModuleType("openai")
    fake_openai.OpenAI = FakeOpenAI
    monkeypatch.setitem(sys.modules, "openai", fake_openai)


def test_provider_is_available_with_sentence_transformers():
    from plugins.memory.lancedb import LanceDBMemoryProvider

    import os
    os.environ["OPENAI_API_KEY"] = "test-key"
    provider = LanceDBMemoryProvider()
    assert provider.is_available()


def test_provider_loads_and_registers_tools(tmp_path):
    from plugins.memory.lancedb import LanceDBMemoryProvider

    import os
    os.environ["OPENAI_API_KEY"] = "test-key"
    provider = LanceDBMemoryProvider()
    provider.initialize("session-1", hermes_home=str(tmp_path / ".hermes"), user_id="u1", agent_identity="coder")
    names = {schema["name"] for schema in provider.get_tool_schemas()}
    assert names == {"lancedb_store", "lancedb_search", "lancedb_forget", "lancedb_profile"}


def test_store_and_profile_round_trip(tmp_path):
    from plugins.memory.lancedb import LanceDBMemoryProvider

    import os
    os.environ["OPENAI_API_KEY"] = "test-key"
    provider = LanceDBMemoryProvider()
    provider.initialize("session-1", hermes_home=str(tmp_path / ".hermes"), user_id="u1", agent_identity="coder")

    result = json.loads(provider.handle_tool_call("lancedb_store", {
        "content": "User prefers concise release notes",
        "record_type": "profile",
        "category": "preference",
        "importance": 0.95,
    }))
    assert result["stored"] is True

    profile = json.loads(provider.handle_tool_call("lancedb_profile", {}))
    assert profile["count"] == 1
    assert profile["profile"][0]["record_type"] == "profile"
    assert "concise release notes" in profile["profile"][0]["content"]


def test_search_returns_semantic_match(tmp_path):
    from plugins.memory.lancedb import LanceDBMemoryProvider

    import os
    os.environ["OPENAI_API_KEY"] = "test-key"
    provider = LanceDBMemoryProvider()
    provider.initialize("session-1", hermes_home=str(tmp_path / ".hermes"), user_id="u1", agent_identity="coder")
    provider.handle_tool_call("lancedb_store", {
        "content": "Project Atlas uses Rust and Arrow for local analytics",
        "record_type": "memory",
        "category": "project",
        "importance": 0.8,
    })

    result = json.loads(provider.handle_tool_call("lancedb_search", {
        "query": "Which project uses Arrow?",
        "scope": "memory",
    }))
    assert result["count"] == 1
    assert result["results"][0]["category"] == "project"


def test_on_memory_write_mirrors_builtin_memory(tmp_path):
    from plugins.memory.lancedb import LanceDBMemoryProvider

    import os
    os.environ["OPENAI_API_KEY"] = "test-key"
    provider = LanceDBMemoryProvider()
    provider.initialize("session-1", hermes_home=str(tmp_path / ".hermes"), user_id="u1", agent_identity="coder")
    provider.on_memory_write("add", "user", "User likes deterministic tests")

    profile = json.loads(provider.handle_tool_call("lancedb_profile", {}))
    assert profile["count"] == 1
    assert profile["profile"][0]["category"] == "preference"


def test_sync_turn_creates_episode_and_prefetch_reads_it(tmp_path):
    from plugins.memory.lancedb import LanceDBMemoryProvider

    import os
    os.environ["OPENAI_API_KEY"] = "test-key"
    provider = LanceDBMemoryProvider()
    provider.initialize("session-1", hermes_home=str(tmp_path / ".hermes"), user_id="u1", agent_identity="coder")
    provider.sync_turn("We agreed to ship the plugin this week", "I will implement the LanceDB provider")
    provider.shutdown()

    provider = LanceDBMemoryProvider()
    provider.initialize("session-1", hermes_home=str(tmp_path / ".hermes"), user_id="u1", agent_identity="coder")
    context = provider.prefetch("What did we agree to ship?")
    assert "Relevant past context" in context
    assert "ship the plugin" in context


def test_forget_by_query_deletes_matches(tmp_path):
    from plugins.memory.lancedb import LanceDBMemoryProvider

    import os
    os.environ["OPENAI_API_KEY"] = "test-key"
    provider = LanceDBMemoryProvider()
    provider.initialize("session-1", hermes_home=str(tmp_path / ".hermes"), user_id="u1", agent_identity="coder")
    provider.handle_tool_call("lancedb_store", {
        "content": "Temporary memory to delete",
        "record_type": "memory",
        "category": "fact",
    })
    deleted = json.loads(provider.handle_tool_call("lancedb_forget", {
        "query": "temporary delete",
        "scope": "memory",
    }))
    assert deleted["deleted"] == 1
    search = json.loads(provider.handle_tool_call("lancedb_search", {
        "query": "temporary delete",
        "scope": "memory",
    }))
    assert search["count"] == 0


def test_openai_backend_requires_api_key(monkeypatch):
    from plugins.memory.lancedb import LanceDBMemoryProvider

    monkeypatch.setenv("LANCEDB_EMBEDDING_BACKEND", "openai")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    provider = LanceDBMemoryProvider()
    assert not provider.is_available()


def test_sentence_transformers_backend_still_supported(monkeypatch, tmp_path):
    from plugins.memory.lancedb import LanceDBMemoryProvider

    monkeypatch.setenv("LANCEDB_EMBEDDING_BACKEND", "sentence-transformers")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    provider = LanceDBMemoryProvider()
    assert provider.is_available()
    provider.initialize("session-1", hermes_home=str(tmp_path / ".hermes"), user_id="u1", agent_identity="coder")
    result = json.loads(provider.handle_tool_call("lancedb_store", {
        "content": "Local embeddings still work",
        "record_type": "memory",
    }))
    assert result["stored"] is True
