"""Helpers for Hermes message content blocks.

Hermes historically treated ``message["content"]`` as plain text. Modern chat
APIs also allow structured content arrays that mix text and images. This module
provides a small set of helpers so the rest of the codebase can preserve
multimodal payloads internally while still deriving text for logging, search,
and rough token estimation.
"""

from __future__ import annotations

import json
import base64
import mimetypes
from pathlib import Path
from typing import Any, Dict, List, Optional

_TEXT_PART_TYPES = frozenset({"text", "input_text", "output_text"})
_IMAGE_PART_TYPES = frozenset({"image_url", "input_image", "image"})


def image_path_to_data_url(path: str, media_type: str = "") -> Optional[str]:
    """Read a local image file and return a data URL."""
    try:
        raw = Path(path).read_bytes()
    except OSError:
        return None
    mime = media_type if isinstance(media_type, str) and media_type.startswith("image/") else ""
    if not mime:
        mime = mimetypes.guess_type(path)[0] or "image/jpeg"
    encoded = base64.b64encode(raw).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def content_has_image_parts(content: Any) -> bool:
    if not isinstance(content, list):
        return False
    for part in content:
        if isinstance(part, dict) and part.get("type") in _IMAGE_PART_TYPES:
            return True
    return False


def content_to_text(
    content: Any,
    *,
    image_placeholder: str = "[image]",
    fallback_json: bool = False,
) -> str:
    """Extract human-readable text from a structured content payload."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: List[str] = []
        for part in content:
            if isinstance(part, str):
                if part:
                    parts.append(part)
                continue
            if not isinstance(part, dict):
                if fallback_json:
                    parts.append(str(part))
                continue
            ptype = part.get("type")
            if ptype in _TEXT_PART_TYPES:
                text = part.get("text")
                if text is None and ptype == "output_text":
                    text = part.get("content")
                if isinstance(text, str) and text:
                    parts.append(text)
            elif ptype in _IMAGE_PART_TYPES and image_placeholder:
                parts.append(image_placeholder)
            elif fallback_json:
                text = part.get("text")
                if isinstance(text, str) and text:
                    parts.append(text)
                else:
                    parts.append(json.dumps(part, ensure_ascii=False))
        return "\n".join(p for p in parts if p)
    if isinstance(content, dict):
        text = content.get("text")
        if isinstance(text, str):
            return text
        nested = content.get("content")
        if nested is not None:
            return content_to_text(
                nested,
                image_placeholder=image_placeholder,
                fallback_json=fallback_json,
            )
        return json.dumps(content, ensure_ascii=False) if fallback_json else ""
    return str(content)


def serialize_message_content(content: Any) -> tuple[Optional[str], Optional[str]]:
    """Return ``(content_text, content_json)`` for state.db storage."""
    if content is None:
        return None, None
    if isinstance(content, str):
        return content, None
    return (
        content_to_text(content, image_placeholder="[image]", fallback_json=False),
        json.dumps(content, ensure_ascii=False),
    )


def deserialize_message_content(content_text: Any, content_json: Any) -> Any:
    """Restore structured content from state.db columns."""
    if isinstance(content_json, str) and content_json:
        try:
            return json.loads(content_json)
        except (json.JSONDecodeError, TypeError):
            pass
    return content_text


def convert_content_to_responses_input(content: Any) -> Any:
    """Convert OpenAI chat-style content blocks to Responses input blocks."""
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return content_to_text(content, image_placeholder="", fallback_json=True)

    converted: List[Dict[str, Any]] = []
    for part in content:
        if isinstance(part, str):
            if part:
                converted.append({"type": "input_text", "text": part})
            continue
        if not isinstance(part, dict):
            continue
        ptype = part.get("type", "")
        if ptype == "text":
            converted.append({"type": "input_text", "text": part.get("text", "")})
        elif ptype == "image_url":
            image_data = part.get("image_url", {})
            url = image_data.get("url", "") if isinstance(image_data, dict) else str(image_data or "")
            entry: Dict[str, Any] = {"type": "input_image", "image_url": url}
            if isinstance(image_data, dict) and image_data.get("detail"):
                entry["detail"] = image_data["detail"]
            converted.append(entry)
        elif ptype in {"input_text", "input_image"}:
            converted.append(dict(part))
        else:
            text = part.get("text", "")
            if text:
                converted.append({"type": "input_text", "text": text})

    return converted or ""
