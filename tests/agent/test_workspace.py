from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

from tests.conftest import make_workspace_config


def _config(tmp_path: Path) -> dict:
    return make_workspace_config(tmp_path)


class TestWorkspacePaths:
    def test_get_workspace_paths_creates_expected_directories(self, tmp_path):
        from agent.workspace import get_workspace_paths

        paths = get_workspace_paths(_config(tmp_path), ensure=True)

        assert paths.workspace_root == tmp_path / "workspace"
        assert paths.knowledgebase_root == tmp_path / "knowledgebase"
        for subdir in ("docs", "notes", "data", "code", "uploads", "media"):
            assert (paths.workspace_root / subdir).is_dir()
        assert paths.indexes_dir.is_dir()
        assert paths.manifests_dir.is_dir()
        assert paths.cache_dir.is_dir()


class TestWorkspaceManifest:
    def test_build_workspace_manifest_writes_summary(self, tmp_path):
        from agent.workspace import build_workspace_manifest

        cfg = _config(tmp_path)
        workspace = Path(cfg["workspace"]["path"])
        (workspace / "docs").mkdir(parents=True)
        (workspace / "notes").mkdir(parents=True)
        (workspace / "docs" / "a.md").write_text("alpha\n", encoding="utf-8")
        (workspace / "notes" / "b.txt").write_text("beta\n", encoding="utf-8")

        manifest = build_workspace_manifest(cfg)

        assert manifest["success"] is True
        assert manifest["file_count"] == 2
        assert manifest["manifest_path"].endswith("workspace.json")
        assert Path(manifest["manifest_path"]).exists()
        paths = {entry["relative_path"] for entry in manifest["files"]}
        assert paths == {"docs/a.md", "notes/b.txt"}

        saved = json.loads(Path(manifest["manifest_path"]).read_text(encoding="utf-8"))
        assert saved["file_count"] == 2


class TestWorkspaceSearch:
    def test_workspace_search_finds_text_matches_and_respects_ignore(self, tmp_path):
        from agent.workspace import workspace_search

        cfg = _config(tmp_path)
        workspace = Path(cfg["workspace"]["path"])
        (workspace / "docs").mkdir(parents=True)
        (workspace / "docs" / "keep.md").write_text("Hermes likes retrieval\n", encoding="utf-8")
        (workspace / "docs" / "skip.md").write_text("Hermes hidden\n", encoding="utf-8")
        (workspace / ".hermesignore").write_text("docs/skip.md\n", encoding="utf-8")
        (workspace / "docs" / "blob.bin").write_bytes(b"\x00\x01\x02Hermes")

        result = workspace_search("Hermes", config=cfg)

        assert result["success"] is True
        assert result["count"] == 1
        match = result["matches"][0]
        assert match["relative_path"] == "docs/keep.md"
        assert match["line"] == 1

    def test_workspace_search_supports_file_glob(self, tmp_path):
        from agent.workspace import workspace_search

        cfg = _config(tmp_path)
        workspace = Path(cfg["workspace"]["path"])
        (workspace / "docs").mkdir(parents=True)
        (workspace / "docs" / "a.md").write_text("deploy target\n", encoding="utf-8")
        (workspace / "docs" / "a.txt").write_text("deploy target\n", encoding="utf-8")

        result = workspace_search("deploy", config=cfg, file_glob="*.md")

        assert result["success"] is True
        assert result["count"] == 1
        assert result["matches"][0]["relative_path"] == "docs/a.md"


class TestWorkspacePlugins:
    def test_local_embeddinggemma_plugin_uses_sentence_transformers_when_available(self, tmp_path, monkeypatch):
        from agent.workspace import _workspace_plugin_compatible_config, _workspace_plugin_context, get_workspace_paths
        from agent.workspace_plugin_manager import WorkspacePluginManager

        calls = {}

        class FakeVector(list):
            def tolist(self):
                return list(self)

        class FakeModel:
            def __init__(self, model_id, **kwargs):
                calls["model_id"] = model_id
                calls["kwargs"] = kwargs

            def encode_query(self, text, **kwargs):
                calls["query"] = (text, kwargs)
                return FakeVector([0.1, 0.2, 0.3])

            def encode_document(self, texts, **kwargs):
                calls["documents"] = (list(texts), kwargs)
                return [FakeVector([0.4, 0.5, 0.6]) for _ in texts]

        fake_torch = SimpleNamespace(
            cuda=SimpleNamespace(is_available=lambda: False),
            backends=SimpleNamespace(mps=SimpleNamespace(is_available=lambda: False)),
        )
        monkeypatch.setitem(sys.modules, "torch", fake_torch)
        monkeypatch.setitem(sys.modules, "sentence_transformers", SimpleNamespace(SentenceTransformer=FakeModel))

        cfg = _config(tmp_path)
        plugin_cfg = _workspace_plugin_compatible_config(cfg)
        paths = get_workspace_paths(cfg, ensure=True)
        manager = WorkspacePluginManager(plugin_cfg, _workspace_plugin_context(paths))
        embedder = manager.resolve_embedder()

        docs = embedder.embed_documents(["alpha doc"], config=manager.resolved_config("embedders"), context=manager.context)
        query = embedder.embed_query("alpha query", config=manager.resolved_config("embedders"), context=manager.context)

        assert calls["model_id"] == "google/embeddinggemma-300m"
        assert calls["documents"][0] == ["alpha doc"]
        assert calls["query"][0] == "alpha query"
        assert docs == [[0.4, 0.5, 0.6]]
        assert query == [0.1, 0.2, 0.3]

    def test_markdown_chunking_prefers_headings(self, tmp_path):
        from agent.workspace import _workspace_plugin_compatible_config, _workspace_plugin_context, get_workspace_paths
        from agent.workspace_plugin_manager import WorkspacePluginManager
        from agent.workspace_types import WorkspaceDocument

        cfg = _config(tmp_path)
        plugin_cfg = _workspace_plugin_compatible_config(cfg)
        paths = get_workspace_paths(cfg, ensure=True)
        manager = WorkspacePluginManager(plugin_cfg, _workspace_plugin_context(paths))
        chunker = manager.resolve_chunker()

        text = "# Intro\n\nAlpha overview.\n\n## Deploy\n\nBlue green rollout plan.\n\n## Rollback\n\nRollback steps.\n"
        document = WorkspaceDocument(
            source_path="docs/plan.md",
            relative_path="docs/plan.md",
            media_type="text/markdown",
            text=text,
        )
        chunks = chunker.chunk(document, path=Path("docs/plan.md"), config=manager.resolved_config("chunkers"), context=manager.context)

        assert len(chunks) >= 3
        assert any("deploy" in chunk.content.lower() for chunk in chunks)
        assert any("rollback" in chunk.content.lower() for chunk in chunks)

    def test_code_chunking_prefers_symbol_boundaries(self, tmp_path):
        from agent.workspace import _workspace_plugin_compatible_config, _workspace_plugin_context, get_workspace_paths
        from agent.workspace_plugin_manager import WorkspacePluginManager
        from agent.workspace_types import WorkspaceDocument

        cfg = _config(tmp_path)
        plugin_cfg = _workspace_plugin_compatible_config(cfg)
        paths = get_workspace_paths(cfg, ensure=True)
        manager = WorkspacePluginManager(plugin_cfg, _workspace_plugin_context(paths))
        chunker = manager.resolve_chunker()

        text = "def alpha():\n    return 'a'\n\n\ndef beta():\n    return 'b'\n"
        document = WorkspaceDocument(
            source_path="code/example.py",
            relative_path="code/example.py",
            media_type="text/x-code",
            text=text,
        )
        chunks = chunker.chunk(document, path=Path("code/example.py"), config=manager.resolved_config("chunkers"), context=manager.context)

        assert len(chunks) >= 2
        assert any("def alpha" in chunk.content for chunk in chunks)
        assert any("def beta" in chunk.content for chunk in chunks)

    def test_local_cross_encoder_plugin_reorders_candidates(self, tmp_path, monkeypatch):
        from agent.workspace import _workspace_plugin_compatible_config, _workspace_plugin_context, get_workspace_paths
        from agent.workspace_plugin_manager import WorkspacePluginManager
        from agent.workspace_types import WorkspaceHit

        calls = {}

        class FakeCrossEncoder:
            def __init__(self, model_name, **kwargs):
                calls["model_name"] = model_name
                calls["kwargs"] = kwargs

            def predict(self, pairs, **kwargs):
                calls["pairs"] = pairs
                calls["predict_kwargs"] = kwargs
                return [0.1, 0.9]

        fake_torch = SimpleNamespace(
            cuda=SimpleNamespace(is_available=lambda: False),
            backends=SimpleNamespace(mps=SimpleNamespace(is_available=lambda: False)),
        )
        monkeypatch.setitem(sys.modules, "torch", fake_torch)
        monkeypatch.setitem(sys.modules, "sentence_transformers", SimpleNamespace(CrossEncoder=FakeCrossEncoder))

        cfg = _config(tmp_path)
        cfg["knowledgebase"]["reranker"]["enabled"] = True
        cfg["knowledgebase"]["reranker"]["provider"] = "local"
        cfg["knowledgebase"]["reranker"]["model"] = "cross-encoder/ms-marco-MiniLM-L6-v2"
        plugin_cfg = _workspace_plugin_compatible_config(cfg)
        paths = get_workspace_paths(cfg, ensure=True)
        manager = WorkspacePluginManager(plugin_cfg, _workspace_plugin_context(paths))
        reranker = manager.resolve_reranker()

        ranked = reranker.rerank(
            "rollback plan",
            [
                WorkspaceHit(chunk_id="a", relative_path="docs/a.md", content="deployment overview", metadata={}, dense_score=0.9, fusion_score=0.9),
                WorkspaceHit(chunk_id="b", relative_path="docs/b.md", content="rollback plan details", metadata={}, dense_score=0.2, fusion_score=0.3),
            ],
            config=manager.resolved_config("rerankers"),
            context=manager.context,
        )

        assert calls["model_name"] == "cross-encoder/ms-marco-MiniLM-L6-v2"
        assert ranked[0].content == "rollback plan details"


class TestWorkspaceRoots:
    def test_index_respects_non_recursive_additional_root_by_default(self, tmp_path):
        from agent.workspace import index_workspace_knowledgebase, workspace_search

        cfg = _config(tmp_path)
        extra = tmp_path / "notes"
        (extra / "nested").mkdir(parents=True)
        (extra / "top.txt").write_text("release notes\n", encoding="utf-8")
        (extra / "nested" / "deep.txt").write_text("hidden release notes\n", encoding="utf-8")
        cfg["knowledgebase"]["roots"] = [{"path": str(extra), "recursive": False}]

        index_workspace_knowledgebase(cfg)
        result = workspace_search("release", config=cfg)

        paths = {match["relative_path"] for match in result["matches"]}
        assert "notes/top.txt" in paths
        assert "notes/nested/deep.txt" not in paths


class TestWorkspaceRetrieval:
    def test_index_workspace_builds_chunk_db_and_retrieves_ranked_chunks(self, tmp_path):
        from agent.workspace import index_workspace_knowledgebase, workspace_retrieve

        cfg = _config(tmp_path)
        workspace = Path(cfg["workspace"]["path"])
        (workspace / "docs").mkdir(parents=True)
        (workspace / "docs" / "arch.md").write_text(
            "# Deployment\n\nThe deployment architecture uses blue green rollout and staged health checks.\n",
            encoding="utf-8",
        )
        (workspace / "notes").mkdir(parents=True)
        (workspace / "notes" / "random.txt").write_text("buy groceries\n", encoding="utf-8")

        indexed = index_workspace_knowledgebase(cfg)
        assert indexed["success"] is True
        assert indexed["chunk_count"] >= 1
        assert Path(indexed["index_path"]).exists()

        retrieved = workspace_retrieve("deployment architecture", config=cfg, limit=3)
        assert retrieved["success"] is True
        assert retrieved["count"] >= 1
        assert retrieved["results"][0]["relative_path"] == "docs/arch.md"
        assert "blue green" in retrieved["results"][0]["content"].lower()

    def test_workspace_retrieve_reports_backend_metadata(self, tmp_path):
        from agent.workspace import index_workspace_knowledgebase, workspace_retrieve

        cfg = _config(tmp_path)
        workspace = Path(cfg["workspace"]["path"])
        (workspace / "docs").mkdir(parents=True)
        (workspace / "docs" / "plan.md").write_text("blue green rollout plan\n", encoding="utf-8")

        index_workspace_knowledgebase(cfg)
        retrieved = workspace_retrieve("blue green rollout", config=cfg, limit=2)

        assert "dense_backend" in retrieved
        assert "rerank_backend" in retrieved

    def test_workspace_context_for_turn_formats_sources_and_respects_gating(self, tmp_path):
        from agent.workspace import index_workspace_knowledgebase, workspace_context_for_turn

        cfg = _config(tmp_path)
        cfg["knowledgebase"]["retrieval_mode"] = "always"
        workspace = Path(cfg["workspace"]["path"])
        (workspace / "docs").mkdir(parents=True)
        (workspace / "docs" / "plan.md").write_text(
            "Deployment plan includes canary analysis and rollback checkpoints.\n",
            encoding="utf-8",
        )

        index_workspace_knowledgebase(cfg)
        context = workspace_context_for_turn("summarize the deployment plan", config=cfg)
        assert "workspace context was retrieved for this turn only" in context.lower()
        assert "[source: relative/path]" in context.lower()
        assert "docs/plan.md" in context

        cfg["knowledgebase"]["retrieval_mode"] = "gated"
        assert workspace_context_for_turn("thanks", config=cfg) == ""


class TestQueryEnrichment:
    """Tests for _enrich_query_from_history conversation-aware query enrichment."""

    def test_enrich_query_short_query_with_history(self):
        from agent.workspace import _enrich_query_from_history

        history = [
            {"role": "user", "content": "Tell me about the deployment pipeline"},
            {"role": "assistant", "content": "The deployment pipeline uses..."},
            {"role": "user", "content": "What about the rollback strategy"},
        ]
        result = _enrich_query_from_history("show me", history)
        # Short query (2 words) should be enriched with history
        assert "show me" in result
        assert "rollback strategy" in result
        assert "deployment pipeline" in result

    def test_enrich_query_pronoun_triggers_enrichment(self):
        from agent.workspace import _enrich_query_from_history

        history = [
            {"role": "user", "content": "Explain the caching layer"},
            {"role": "assistant", "content": "The caching layer works by..."},
            {"role": "user", "content": "How does invalidation work"},
        ]
        # 5+ words but contains pronoun "this"
        result = _enrich_query_from_history("Can you explain this in more detail", history)
        assert "Can you explain this in more detail" in result
        assert "invalidation" in result

    def test_enrich_query_long_query_no_enrichment(self):
        from agent.workspace import _enrich_query_from_history

        history = [
            {"role": "user", "content": "Tell me about the deployment pipeline"},
            {"role": "assistant", "content": "Sure..."},
        ]
        # 6 words, no pronouns — should NOT be enriched
        query = "Explain the database migration rollback procedure"
        result = _enrich_query_from_history(query, history)
        assert result == query

    def test_enrich_query_no_history(self):
        from agent.workspace import _enrich_query_from_history

        result_none = _enrich_query_from_history("fix it", None)
        assert result_none == "fix it"

        result_empty = _enrich_query_from_history("fix it", [])
        assert result_empty == "fix it"

    def test_enrich_query_truncates_to_500_chars(self):
        from agent.workspace import _enrich_query_from_history

        long_text = "x" * 300
        history = [
            {"role": "user", "content": long_text},
            {"role": "assistant", "content": "ok"},
            {"role": "user", "content": long_text},
        ]
        result = _enrich_query_from_history("show", history)
        assert len(result) <= 500


class TestIndexProgress:
    def test_index_progress_callback_reports_files(self, tmp_path):
        from agent.workspace import index_workspace_knowledgebase

        cfg = _config(tmp_path)
        workspace = Path(cfg["workspace"]["path"])
        (workspace / "docs").mkdir(parents=True)
        (workspace / "docs" / "a.md").write_text("alpha content\n", encoding="utf-8")
        (workspace / "docs" / "b.md").write_text("beta content\n", encoding="utf-8")
        (workspace / "docs" / "c.md").write_text("gamma content\n", encoding="utf-8")

        progress_calls: list[tuple[int, int, str]] = []

        def _cb(current: int, total: int, path: str) -> None:
            progress_calls.append((current, total, path))

        result = index_workspace_knowledgebase(cfg, progress_callback=_cb)

        assert result["success"] is True
        assert len(progress_calls) == 3
        # All calls should report total=3
        for current, total, path in progress_calls:
            assert total == 3
        # current values should be 1, 2, 3 (order may vary by file iteration)
        currents = sorted(c for c, _, _ in progress_calls)
        assert currents == [1, 2, 3]
        # All paths should be non-empty strings
        for _, _, path in progress_calls:
            assert isinstance(path, str) and len(path) > 0

    def test_index_progress_callback_none_is_safe(self, tmp_path):
        from agent.workspace import index_workspace_knowledgebase

        cfg = _config(tmp_path)
        workspace = Path(cfg["workspace"]["path"])
        (workspace / "docs").mkdir(parents=True)
        (workspace / "docs" / "a.md").write_text("alpha content\n", encoding="utf-8")

        # progress_callback=None is the default — must not raise
        result = index_workspace_knowledgebase(cfg, progress_callback=None)
        assert result["success"] is True

        # Also test without passing the argument at all (backward compat)
        result2 = index_workspace_knowledgebase(cfg)
        assert result2["success"] is True
