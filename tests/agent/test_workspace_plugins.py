from __future__ import annotations

from pathlib import Path


def _plugin_config(tmp_path: Path) -> dict:
    return {
        "workspace": {
            "enabled": True,
            "path": str(tmp_path / "workspace"),
        },
        "knowledgebase": {
            "path": str(tmp_path / "knowledgebase"),
            "parsers": {"active": "builtin_text", "builtin_text": {}},
            "chunkers": {"active": "builtin_structural", "builtin_structural": {}},
            "embedders": {
                "active": "builtin_hash",
                "builtin_hash": {"dimensions": 64},
            },
            "rerankers": {"active": "disabled", "disabled": {}},
            "retrievers": {
                "active": "builtin_hybrid_rrf",
                "builtin_hybrid_rrf": {"dense_top_k": 5, "sparse_top_k": 5, "fused_top_k": 5},
            },
            "index_stores": {"active": "builtin_sqlite", "builtin_sqlite": {}},
        },
    }


def test_workspace_plugin_manager_resolves_builtin_ids_without_false_fallback(tmp_path):
    from agent.workspace import _workspace_plugin_context
    from agent.workspace_plugin_manager import WorkspacePluginManager
    from agent.workspace import get_workspace_paths

    cfg = _plugin_config(tmp_path)
    paths = get_workspace_paths(cfg, ensure=True)
    manager = WorkspacePluginManager(cfg, _workspace_plugin_context(paths))

    status = manager.status_report()

    assert status["categories"]["parsers"]["resolved"] == "builtin_text"
    assert status["categories"]["parsers"]["fallback"] is False
    assert status["categories"]["embedders"]["resolved"] == "builtin_hash"
    assert status["categories"]["embedders"]["fallback"] is False


def test_workspace_retrieve_uses_embedder_plugin_config_dimensions(tmp_path):
    from agent.workspace import get_workspace_paths, _workspace_plugin_context
    from agent.workspace_plugin_manager import WorkspacePluginManager

    cfg = _plugin_config(tmp_path)
    paths = get_workspace_paths(cfg, ensure=True)
    context = _workspace_plugin_context(paths)
    manager = WorkspacePluginManager(cfg, context)
    embedder = manager.resolve_embedder()
    retriever = manager.resolve_retriever()
    assert embedder is not None
    assert retriever is not None

    embedder_cfg = manager.resolved_config("embedders")
    retriever_cfg = manager.resolved_config("retrievers")
    context.runtime_metadata["workspace_plugin_configs"] = {"embedders": embedder_cfg}

    class FakeIndexSession:
        def __init__(self):
            self.vector_length = None

        def sparse_search(self, query: str, limit: int):
            return []

        def dense_search(self, query_embedding: list[float], limit: int):
            self.vector_length = len(query_embedding)
            return []

    session = FakeIndexSession()
    retriever.retrieve(
        "deployment plan",
        index_session=session,
        embedder=embedder,
        config=retriever_cfg,
        context=context,
    )

    assert session.vector_length == 64
