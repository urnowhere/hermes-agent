"""Transparent PII masking middleware for outgoing LLM requests.

Masks PII in messages before sending to OpenRouter, then restores original
values in the response so the agent sees real data throughout.
"""

import re
import uuid
from typing import Optional


class PIIFilter:
    # Matches placeholders like [private_email_a1b2c3] or [PERSON_a1b2c3]
    PLACEHOLDER_RE = re.compile(r'\[([A-Za-z_]+)_([0-9a-f]{8})\]')

    def __init__(self):
        self._classifier = None

    @property
    def classifier(self):
        if self._classifier is None:
            from transformers import pipeline
            import torch
            device = "mps" if torch.backends.mps.is_available() else "cpu"
            self._classifier = pipeline(
                "token-classification",
                model="openai/privacy-filter",
                aggregation_strategy="simple",
                device=device,
            )
        return self._classifier

    def mask_messages(self, messages: list, mask_roles: set = None) -> tuple[list, dict]:
        """Mask PII in messages. Returns (masked_messages, pii_map)."""
        if mask_roles is None:
            mask_roles = {"user", "tool", "assistant"}
        pii_map = {}
        result = []
        for msg in messages:
            msg = dict(msg)
            if msg.get("role") not in mask_roles:
                result.append(msg)
                continue
            content = msg.get("content")
            if isinstance(content, str):
                msg["content"] = self._mask_text(content, pii_map)
            elif isinstance(content, list):
                new_parts = []
                for part in content:
                    part = dict(part)
                    if part.get("type") == "text" and part.get("text"):
                        part["text"] = self._mask_text(part["text"], pii_map)
                    new_parts.append(part)
                msg["content"] = new_parts
            result.append(msg)
        return result, pii_map

    def restore_text(self, text: str, pii_map: dict) -> str:
        """Replace placeholders with original PII values."""
        if not pii_map or not text:
            return text

        def replace(m):
            return pii_map.get(m.group(0), m.group(0))

        return self.PLACEHOLDER_RE.sub(replace, text)

    def _mask_text(self, text: str, pii_map: dict) -> str:
        if not text or not text.strip():
            return text
        try:
            entities = self.classifier(text)
        except Exception:
            return text
        # Merge adjacent entities of same type (model often splits tokens)
        merged = self._merge_entities(entities, text)
        # Sort in reverse order so we don't shift offsets while replacing
        for start, end, group, word in sorted(merged, key=lambda e: e[0], reverse=True):
            uid = uuid.uuid4().hex[:8]
            mask = f"[{group}_{uid}]"
            pii_map[mask] = word
            text = text[:start] + mask + text[end:]
        return text

    @staticmethod
    def _merge_entities(entities: list, text: str) -> list[tuple]:
        """Merge adjacent entities of the same group. Returns (start, end, group, word) tuples."""
        if not entities:
            return []
        sorted_ents = sorted(entities, key=lambda e: e["start"])
        merged = []
        cur_start = sorted_ents[0]["start"]
        cur_end = sorted_ents[0]["end"]
        cur_group = sorted_ents[0]["entity_group"]
        for ent in sorted_ents[1:]:
            if ent["entity_group"] == cur_group and ent["start"] <= cur_end:
                cur_end = max(cur_end, ent["end"])
            else:
                merged.append((cur_start, cur_end, cur_group, text[cur_start:cur_end]))
                cur_start = ent["start"]
                cur_end = ent["end"]
                cur_group = ent["entity_group"]
        merged.append((cur_start, cur_end, cur_group, text[cur_start:cur_end]))
        return merged


_filter: Optional[PIIFilter] = None


def get_pii_filter() -> PIIFilter:
    global _filter
    if _filter is None:
        _filter = PIIFilter()
    return _filter
