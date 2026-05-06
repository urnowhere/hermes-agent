
from pathlib import Path

import plugins.transcript_capture as plugin
from agent.transcript_capture.config import TranscriptCaptureConfig
from agent.transcript_capture.session_export import SessionFinalizeEntry


class FakeCtx:
    def __init__(self):
        self.hooks = {}
        self.commands = {}
    def register_hook(self, name, callback):
        self.hooks[name] = callback
    def register_command(self, name, handler, description="", args_hint=""):
        self.commands[name] = handler


def test_plugin_registers_finalize_hook_and_command():
    ctx = FakeCtx()
    plugin.register(ctx)
    assert "on_session_finalize" in ctx.hooks
    assert "transcript-capture" in ctx.commands


def test_finalize_noops_when_disabled(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(plugin, "_build_config", lambda: TranscriptCaptureConfig(active_dir=tmp_path/"a", corpus_dir=tmp_path/"c", state_dir=tmp_path/"s", capture_enabled=False))
    monkeypatch.setattr(plugin, "_export_finalized", lambda *a, **k: calls.append((a, k)))
    plugin._on_session_finalize(session_id="s", session_key="k", platform="discord", session_store=object())
    assert calls == []


def test_finalize_enforces_platform_allowlist(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(plugin, "_build_config", lambda: TranscriptCaptureConfig(active_dir=tmp_path/"a", corpus_dir=tmp_path/"c", state_dir=tmp_path/"s", capture_enabled=True, platform_allowlist=frozenset({"telegram"})))
    monkeypatch.setattr(plugin, "_export_finalized", lambda *a, **k: calls.append((a, k)))
    plugin._on_session_finalize(session_id="s", session_key="k", platform="discord", session_store=object())
    assert calls == []


def test_finalize_invokes_export_once_for_allowed_session(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(plugin, "_build_config", lambda: TranscriptCaptureConfig(active_dir=tmp_path/"a", corpus_dir=tmp_path/"c", state_dir=tmp_path/"s", capture_enabled=True, platform_allowlist=frozenset({"discord"})))
    monkeypatch.setattr(plugin, "_export_finalized", lambda store, cfg, entry: calls.append(entry) or Path("/tmp/final.txt"))
    plugin._on_session_finalize(session_id="s", session_key="k", platform="discord", source_type="gateway", session_store=object())
    assert calls == [SessionFinalizeEntry(session_id="s", session_key="k", platform="discord", source_type="gateway")]


def test_finalize_exceptions_are_logged_and_not_raised(monkeypatch, tmp_path, caplog):
    monkeypatch.setattr(plugin, "_build_config", lambda: TranscriptCaptureConfig(active_dir=tmp_path/"a", corpus_dir=tmp_path/"c", state_dir=tmp_path/"s", capture_enabled=True))
    def boom(*args, **kwargs):
        raise RuntimeError("boom")
    monkeypatch.setattr(plugin, "_export_finalized", boom)
    plugin._on_session_finalize(session_id="s", session_key="k", platform="discord", session_store=object())
    assert "failed" in caplog.text.lower() or "boom" in caplog.text.lower()


def test_register_runs_retention_cleanup_on_load(monkeypatch, tmp_path):
    ctx = FakeCtx()
    cfg = TranscriptCaptureConfig(active_dir=tmp_path/"a", corpus_dir=tmp_path/"c", state_dir=tmp_path/"s", capture_enabled=False)
    calls = []
    monkeypatch.setattr(plugin, "_build_config", lambda: cfg)
    monkeypatch.setattr(plugin, "cleanup_old_artifacts", lambda config: calls.append(config))
    plugin.register(ctx)
    assert calls == [cfg]
