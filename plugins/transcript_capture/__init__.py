from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Optional

from agent.transcript_capture.config import TranscriptCaptureConfig
from agent.transcript_capture.session_export import SessionFinalizeEntry, SessionTranscriptExporter
from agent.transcript_capture.gbrain_verify import verify_corpus_shape
from agent.transcript_capture.retention import cleanup_old_artifacts

logger = logging.getLogger(__name__)


def _build_config() -> TranscriptCaptureConfig:
    return TranscriptCaptureConfig.from_env()


def _export_finalized(session_store: Any, config: TranscriptCaptureConfig, entry: SessionFinalizeEntry) -> Path:
    return SessionTranscriptExporter(session_store, config).export_finalized(entry)


def _on_session_finalize(
    session_id: str = "",
    session_key: str = "",
    platform: str = "",
    source_type: str = "gateway",
    chat_id: Optional[str] = None,
    session_store: Any = None,
    **_: Any,
) -> Optional[Path]:
    try:
        cfg = _build_config()
        cleanup_old_artifacts(cfg)
        if not cfg.capture_enabled:
            return None
        if not session_id or not session_key or session_store is None:
            logger.info("transcript-capture skipped: missing session_id/session_key/session_store")
            return None
        if not cfg.platform_allowed(platform):
            return None
        if not cfg.session_allowed(session_id):
            return None
        if not cfg.chat_allowed(chat_id):
            return None
        if cfg.denied(platform, session_id, chat_id):
            return None
        entry = SessionFinalizeEntry(session_id=session_id, session_key=session_key, platform=platform, source_type=source_type, chat_id=chat_id)
        return _export_finalized(session_store, cfg, entry)
    except Exception as exc:  # defensive plugin boundary: never crash gateway/CLI
        logger.warning("transcript-capture failed during session finalization: %s", exc)
        return None


def _handle_slash(raw_args: str) -> str:
    cfg = _build_config()
    args = (raw_args or "").strip().lower()
    if args in {"verify", "status"}:
        result = verify_corpus_shape(cfg.corpus_dir)
        return (
            "transcript-capture corpus shape: "
            f"ok={result['ok']} flat_txt={result['flat_txt_count']} "
            f"nested_txt={result['nested_txt_count']} corpus_part={result['corpus_part_count']}"
        )
    return (
        "transcript-capture is " + ("enabled" if cfg.capture_enabled else "disabled") +
        f"; corpus={cfg.corpus_dir}; active={cfg.active_dir}; "
        "commands: /transcript-capture status"
    )


def register(ctx) -> None:
    try:
        cleanup_old_artifacts(_build_config())
    except Exception as exc:
        logger.warning("transcript-capture retention cleanup failed during plugin load: %s", exc)
    ctx.register_hook("on_session_finalize", _on_session_finalize)
    ctx.register_command(
        "transcript-capture",
        handler=_handle_slash,
        description="Inspect safe transcript capture status and corpus shape.",
    )
