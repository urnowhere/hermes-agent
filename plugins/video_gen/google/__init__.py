"""Google Veo video generation plugin.

Registers a single ``video_generate`` tool that wraps the Gemini API's
``models/{veo}:predictLongRunning`` endpoint:

  1. Submit prompt → get an operation name (long-running op)
  2. Poll the op until ``done: true``
  3. Follow the signed download URI to fetch the MP4 bytes
  4. Save under ``$HERMES_HOME/cache/videos/`` and return the path

Auth: ``GEMINI_API_KEY`` (preferred) or ``GOOGLE_API_KEY``. Veo requires
Tier 1 (paid) on the Gemini API project; free-tier sees zero quota.

Selection precedence (first hit wins):
1. ``GOOGLE_VIDEO_MODEL`` env var
2. ``video_gen.google.model`` in ``config.yaml``
3. :data:`DEFAULT_MODEL` (``veo-3.0-fast-generate-001``)
"""

from __future__ import annotations

import datetime
import logging
import os
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import requests

from tools.registry import tool_error, tool_result

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Model catalog
# ---------------------------------------------------------------------------

_MODELS: Dict[str, Dict[str, Any]] = {
    "veo-3.0-fast-generate-001": {
        "display": "Veo 3 Fast",
        "speed": "~30-60s for 4s video",
        "strengths": "Fast iteration, good quality",
    },
    "veo-3.0-generate-001": {
        "display": "Veo 3",
        "speed": "~60-120s for 8s video",
        "strengths": "Default Veo 3 — balanced quality/cost",
    },
    "veo-2.0-generate-001": {
        "display": "Veo 2",
        "speed": "~60-120s",
        "strengths": "Older generation, sometimes cheaper",
    },
    "veo-3.1-generate-preview": {
        "display": "Veo 3.1 (preview)",
        "speed": "~90s",
        "strengths": "Newer preview, improved temporal coherence",
    },
    "veo-3.1-fast-generate-preview": {
        "display": "Veo 3.1 Fast (preview)",
        "speed": "~30-60s",
        "strengths": "Newer preview, fast tier",
    },
    "veo-3.1-lite-generate-preview": {
        "display": "Veo 3.1 Lite (preview)",
        "speed": "~20-40s",
        "strengths": "Newer preview, cheapest tier",
    },
}

DEFAULT_MODEL = "veo-3.0-fast-generate-001"

API_BASE = "https://generativelanguage.googleapis.com/v1beta"

VALID_ASPECT_RATIOS: Tuple[str, ...] = ("16:9", "9:16", "1:1")
DEFAULT_ASPECT_RATIO = "16:9"

# Cap polling so the tool never blocks forever even if Google hangs the op.
POLL_TIMEOUT_SECONDS = 600
POLL_INTERVAL_SECONDS = 5


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _resolve_api_key() -> str:
    return (
        os.getenv("GEMINI_API_KEY", "").strip()
        or os.getenv("GOOGLE_API_KEY", "").strip()
    )


def _load_video_config() -> Dict[str, Any]:
    """Read ``video_gen.google`` from config.yaml (returns {} on failure)."""
    try:
        from hermes_cli.config import load_config

        cfg = load_config()
        section = cfg.get("video_gen") if isinstance(cfg, dict) else None
        google_section = section.get("google") if isinstance(section, dict) else None
        return google_section if isinstance(google_section, dict) else {}
    except Exception as exc:
        logger.debug("Could not load video_gen.google config: %s", exc)
        return {}


def _resolve_model() -> Tuple[str, Dict[str, Any]]:
    env_override = os.environ.get("GOOGLE_VIDEO_MODEL")
    if env_override and env_override in _MODELS:
        return env_override, _MODELS[env_override]

    cfg = _load_video_config()
    candidate = cfg.get("model") if isinstance(cfg.get("model"), str) else None
    if candidate and candidate in _MODELS:
        return candidate, _MODELS[candidate]

    return DEFAULT_MODEL, _MODELS[DEFAULT_MODEL]


def _videos_cache_dir() -> Path:
    """Return ``$HERMES_HOME/cache/videos/``, creating parents as needed."""
    from hermes_constants import get_hermes_home

    path = get_hermes_home() / "cache" / "videos"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _save_mp4(content: bytes, *, prefix: str = "video") -> Path:
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    short = uuid.uuid4().hex[:8]
    path = _videos_cache_dir() / f"{prefix}_{ts}_{short}.mp4"
    path.write_bytes(content)
    return path


def _check_video_gen_available() -> bool:
    """Tool gate — only advertised when an API key is present."""
    return bool(_resolve_api_key())


# ---------------------------------------------------------------------------
# Veo API calls
# ---------------------------------------------------------------------------


def _start_op(
    *, api_key: str, model_id: str, prompt: str,
    aspect_ratio: str, duration_seconds: int,
) -> Tuple[Optional[str], Optional[str]]:
    """Submit the long-running op. Returns (op_name, error_message)."""
    url = f"{API_BASE}/models/{model_id}:predictLongRunning"
    payload: Dict[str, Any] = {
        "instances": [{"prompt": prompt}],
        "parameters": {
            "aspectRatio": aspect_ratio,
            "durationSeconds": duration_seconds,
        },
    }
    try:
        resp = requests.post(
            url, params={"key": api_key}, json=payload, timeout=60
        )
    except requests.Timeout:
        return None, "Veo submit timed out (60s)"
    except requests.ConnectionError as exc:
        return None, f"Connection error: {exc}"

    if resp.status_code != 200:
        try:
            err_msg = resp.json().get("error", {}).get("message", resp.text[:300])
        except Exception:
            err_msg = resp.text[:300]
        return None, f"Veo submit failed ({resp.status_code}): {err_msg}"

    op_name = resp.json().get("name")
    if not op_name:
        return None, "Veo submit returned no operation name"
    return op_name, None


def _poll_op(
    *, api_key: str, op_name: str
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Poll until done or timeout. Returns (final_op_body, error_message)."""
    deadline = time.time() + POLL_TIMEOUT_SECONDS
    url = f"{API_BASE}/{op_name}"
    last_status_for_log = None

    while time.time() < deadline:
        try:
            resp = requests.get(url, params={"key": api_key}, timeout=30)
        except requests.RequestException as exc:
            return None, f"Polling failed: {exc}"

        if resp.status_code != 200:
            return None, f"Polling returned {resp.status_code}: {resp.text[:200]}"

        try:
            body = resp.json()
        except Exception as exc:
            return None, f"Polling returned invalid JSON: {exc}"

        if body.get("done"):
            return body, None

        # Optional progress log (Veo doesn't surface % progress today, but this
        # helps if Google adds it later)
        meta = body.get("metadata") or {}
        progress = meta.get("progressPercent") or meta.get("progress")
        if progress != last_status_for_log:
            logger.debug("Veo op %s progress: %s", op_name, progress)
            last_status_for_log = progress

        time.sleep(POLL_INTERVAL_SECONDS)

    return None, f"Veo op {op_name} did not complete within {POLL_TIMEOUT_SECONDS}s"


def _extract_video_uri(op_body: Dict[str, Any]) -> Optional[str]:
    """Pull the download URI out of a finished op body."""
    err = op_body.get("error")
    if err:
        return None
    resp = op_body.get("response") or {}
    samples = (resp.get("generateVideoResponse") or {}).get("generatedSamples") or []
    if not samples:
        samples = resp.get("generatedSamples") or []
    for s in samples:
        v = s.get("video") or {}
        uri = v.get("uri") or v.get("url")
        if uri:
            return uri
    return None


def _download_video(*, api_key: str, uri: str) -> Tuple[Optional[bytes], Optional[str]]:
    """Download the MP4. Veo's URI returns a 302 → follow with the same key."""
    try:
        resp = requests.get(
            uri,
            headers={"x-goog-api-key": api_key},
            timeout=120,
            allow_redirects=True,
        )
    except requests.Timeout:
        return None, "Video download timed out (120s)"
    except requests.ConnectionError as exc:
        return None, f"Connection error: {exc}"

    if resp.status_code != 200:
        return None, f"Video download failed ({resp.status_code}): {resp.text[:200]}"
    if len(resp.content) < 1000:
        # 95-byte JSON error masquerading as a download — guard against it.
        return None, f"Video download returned suspiciously small body ({len(resp.content)} bytes)"
    return resp.content, None


# ---------------------------------------------------------------------------
# Tool handler
# ---------------------------------------------------------------------------


VIDEO_GENERATE_SCHEMA = {
    "name": "video_generate",
    "description": (
        "Generate a short video from a text prompt using Google Veo. "
        "Long-running (~30s–2min). Returns an absolute path to a saved "
        "MP4 file in the `video` field. Display with markdown "
        "![description](path) and the gateway will deliver it. "
        "Backend model is user-configured via "
        "`video_gen.google.model` or `GOOGLE_VIDEO_MODEL` env var."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "prompt": {
                "type": "string",
                "description": (
                    "The text prompt describing the desired video. Be "
                    "detailed: subject, action, camera motion, style, "
                    "lighting. Veo follows cinematic prompt structure well."
                ),
            },
            "aspect_ratio": {
                "type": "string",
                "enum": list(VALID_ASPECT_RATIOS),
                "description": "Aspect ratio. 16:9 widescreen, 9:16 vertical, 1:1 square.",
                "default": DEFAULT_ASPECT_RATIO,
            },
            "duration_seconds": {
                "type": "integer",
                "description": "Length of the clip in seconds (Veo 3: 4 or 8; Veo 2: 5–8).",
                "default": 4,
                "minimum": 2,
                "maximum": 16,
            },
        },
        "required": ["prompt"],
    },
}


def _handle_video_generate(args: Dict[str, Any], **_kw: Any) -> str:
    prompt = (args.get("prompt") or "").strip()
    if not prompt:
        return tool_error("prompt is required for video generation")

    aspect_ratio = args.get("aspect_ratio") or DEFAULT_ASPECT_RATIO
    if aspect_ratio not in VALID_ASPECT_RATIOS:
        aspect_ratio = DEFAULT_ASPECT_RATIO

    duration_seconds = int(args.get("duration_seconds") or 4)
    duration_seconds = max(2, min(16, duration_seconds))

    api_key = _resolve_api_key()
    if not api_key:
        return tool_error(
            "GEMINI_API_KEY (or GOOGLE_API_KEY) not set. Get a key at "
            "https://aistudio.google.com/apikey — Veo requires Tier 1 "
            "(paid) on the project.",
            error_type="auth_required",
            provider="google",
        )

    model_id, meta = _resolve_model()

    op_name, err = _start_op(
        api_key=api_key,
        model_id=model_id,
        prompt=prompt,
        aspect_ratio=aspect_ratio,
        duration_seconds=duration_seconds,
    )
    if err:
        return tool_error(err, error_type="api_error", provider="google",
                          model=model_id, prompt=prompt)

    op_body, err = _poll_op(api_key=api_key, op_name=op_name)
    if err:
        return tool_error(err, error_type="poll_error", provider="google",
                          model=model_id, operation=op_name)

    op_err = op_body.get("error") if op_body else None
    if op_err:
        return tool_error(
            f"Veo op finished with error: {op_err.get('message', op_err)}",
            error_type="api_error", provider="google",
            model=model_id, operation=op_name,
        )

    uri = _extract_video_uri(op_body or {})
    if not uri:
        return tool_error(
            "Veo op completed but no video URI in response",
            error_type="empty_response", provider="google",
            model=model_id, operation=op_name,
        )

    content, err = _download_video(api_key=api_key, uri=uri)
    if err:
        return tool_error(err, error_type="download_error", provider="google",
                          model=model_id, operation=op_name)

    try:
        saved_path = _save_mp4(content, prefix=f"google_{model_id}")
    except Exception as exc:
        return tool_error(
            f"Could not save video to cache: {exc}",
            error_type="io_error", provider="google", model=model_id,
        )

    return tool_result(
        success=True,
        video=str(saved_path),
        model=model_id,
        prompt=prompt,
        aspect_ratio=aspect_ratio,
        duration_seconds=duration_seconds,
        provider="google",
    )


# ---------------------------------------------------------------------------
# Plugin entry point
# ---------------------------------------------------------------------------


def register(ctx: Any) -> None:
    """Plugin entry point — register the video_generate tool."""
    ctx.register_tool(
        name="video_generate",
        toolset="video_gen",
        schema=VIDEO_GENERATE_SCHEMA,
        handler=_handle_video_generate,
        check_fn=_check_video_gen_available,
        requires_env=[],
        is_async=False,
        description="Generate a short video from a text prompt via Google Veo.",
        emoji="🎬",
    )
