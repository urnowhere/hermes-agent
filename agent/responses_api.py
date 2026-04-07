"""Shared helpers for OpenAI Responses API message conversion."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Callable, Dict, List, Optional, Tuple


def deterministic_call_id(fn_name: str, arguments: str, index: int = 0) -> str:
    seed = f"{fn_name}:{arguments}:{index}"
    digest = hashlib.sha256(seed.encode("utf-8", errors="replace")).hexdigest()[:12]
    return f"call_{digest}"


def split_responses_tool_id(raw_id: Any) -> tuple[Optional[str], Optional[str]]:
    if not isinstance(raw_id, str):
        return None, None
    value = raw_id.strip()
    if not value:
        return None, None
    if "|" in value:
        call_id, response_item_id = value.split("|", 1)
        return call_id.strip() or None, response_item_id.strip() or None
    if value.startswith("fc_"):
        return None, value
    return value, None


def convert_content_for_responses(content: Any) -> Any:
    if isinstance(content, str):
        return content

    parts = content if isinstance(content, list) else [content]
    converted: List[Dict[str, Any]] = []

    for part in parts:
        if isinstance(part, str):
            converted.append({"type": "input_text", "text": part})
            continue
        if not isinstance(part, dict):
            text = str(part) if part else ""
            if text:
                converted.append({"type": "input_text", "text": text})
            continue

        ptype = part.get("type", "")
        if ptype in {"text", "output_text", "input_text"}:
            text = part.get("text", "")
            if text is not None:
                converted.append({"type": "input_text", "text": str(text)})
            continue
        if ptype in {"image_url", "input_image"}:
            image_data = part.get("image_url", {})
            url = image_data.get("url", "") if isinstance(image_data, dict) else str(image_data or "")
            entry: Dict[str, Any] = {"type": "input_image", "image_url": url}
            detail = image_data.get("detail") if isinstance(image_data, dict) else part.get("detail")
            if detail:
                entry["detail"] = detail
            converted.append(entry)
            continue

        text = part.get("text", "")
        if text:
            converted.append({"type": "input_text", "text": str(text)})

    if not converted:
        return ""
    if len(converted) == 1 and converted[0].get("type") == "input_text":
        return converted[0]["text"]
    return converted


def stringify_tool_output(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, (dict, list)):
        return json.dumps(content, ensure_ascii=False)
    return str(content)


def convert_content_from_responses(content: Any) -> str:
    if isinstance(content, str):
        return content

    parts = content if isinstance(content, list) else [content]
    text_parts: List[str] = []
    for part in parts:
        if isinstance(part, str):
            text_parts.append(part)
            continue
        if not isinstance(part, dict):
            text = str(part) if part else ""
            if text:
                text_parts.append(text)
            continue

        ptype = part.get("type", "")
        if ptype in {"text", "input_text", "output_text"}:
            text = part.get("text", "")
            if text is not None:
                text_parts.append(str(text))
            continue

        text = part.get("text", "")
        if text:
            text_parts.append(str(text))

    return "\n".join(chunk for chunk in text_parts if chunk)


def chat_messages_to_responses_input(
    messages: List[Dict[str, Any]],
    *,
    reasoning_items_getter: Optional[Callable[[Dict[str, Any]], Optional[List[Dict[str, Any]]]]] = None,
    split_tool_id: Callable[[Any], Tuple[Optional[str], Optional[str]]] = split_responses_tool_id,
    deterministic_id: Callable[[str, str, int], str] = deterministic_call_id,
) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []

    for msg in messages:
        if not isinstance(msg, dict):
            continue

        role = msg.get("role")
        if role == "system":
            continue

        if role in {"user", "assistant"}:
            content = convert_content_for_responses(msg.get("content", ""))

            if role == "assistant":
                has_reasoning = False
                if reasoning_items_getter is not None:
                    for ri in reasoning_items_getter(msg) or []:
                        if isinstance(ri, dict) and ri.get("encrypted_content"):
                            items.append(ri)
                            has_reasoning = True

                has_visible_content = bool(content.strip()) if isinstance(content, str) else bool(content)
                if has_visible_content:
                    items.append({"role": "assistant", "content": content})
                elif has_reasoning:
                    items.append({"role": "assistant", "content": ""})

                tool_calls = msg.get("tool_calls")
                if isinstance(tool_calls, list):
                    for tc in tool_calls:
                        if not isinstance(tc, dict):
                            continue
                        fn = tc.get("function", {})
                        fn_name = fn.get("name")
                        if not isinstance(fn_name, str) or not fn_name.strip():
                            continue

                        embedded_call_id, embedded_response_item_id = split_tool_id(tc.get("id"))
                        call_id = tc.get("call_id")
                        if not isinstance(call_id, str) or not call_id.strip():
                            call_id = embedded_call_id
                        if not isinstance(call_id, str) or not call_id.strip():
                            if (
                                isinstance(embedded_response_item_id, str)
                                and embedded_response_item_id.startswith("fc_")
                                and len(embedded_response_item_id) > len("fc_")
                            ):
                                call_id = f"call_{embedded_response_item_id[len('fc_'):]}"
                            else:
                                raw_args = str(fn.get("arguments", "{}"))
                                call_id = deterministic_id(fn_name, raw_args, len(items))
                        call_id = call_id.strip()

                        arguments = fn.get("arguments", "{}")
                        if isinstance(arguments, dict):
                            arguments = json.dumps(arguments, ensure_ascii=False)
                        elif not isinstance(arguments, str):
                            arguments = str(arguments)
                        arguments = arguments.strip() or "{}"

                        items.append(
                            {
                                "type": "function_call",
                                "call_id": call_id,
                                "name": fn_name,
                                "arguments": arguments,
                            }
                        )
                continue

            items.append({"role": role, "content": content})
            continue

        if role == "tool":
            raw_tool_call_id = msg.get("tool_call_id")
            call_id, _ = split_tool_id(raw_tool_call_id)
            if not isinstance(call_id, str) or not call_id.strip():
                if isinstance(raw_tool_call_id, str) and raw_tool_call_id.strip():
                    call_id = raw_tool_call_id.strip()
            if not isinstance(call_id, str) or not call_id.strip():
                continue
            items.append(
                {
                    "type": "function_call_output",
                    "call_id": call_id,
                    "output": stringify_tool_output(msg.get("content", "")),
                }
            )

    return items


def responses_input_to_chat_messages(
    raw_items: Any,
    *,
    split_tool_id: Callable[[Any], Tuple[Optional[str], Optional[str]]] = split_responses_tool_id,
) -> List[Dict[str, Any]]:
    normalized_items = normalize_responses_input_items(raw_items)
    messages: List[Dict[str, Any]] = []
    current_assistant_index: Optional[int] = None

    def _ensure_assistant_message() -> Dict[str, Any]:
        nonlocal current_assistant_index
        if current_assistant_index is not None:
            return messages[current_assistant_index]

        assistant_message: Dict[str, Any] = {"role": "assistant", "content": ""}
        messages.append(assistant_message)
        current_assistant_index = len(messages) - 1
        return assistant_message

    for item in normalized_items:
        item_type = item.get("type")
        if item_type == "reasoning":
            assistant_message = _ensure_assistant_message()
            reasoning_items = assistant_message.setdefault("codex_reasoning_items", [])
            if isinstance(reasoning_items, list):
                reasoning_items.append(dict(item))
            continue

        if item_type == "function_call":
            assistant_message = _ensure_assistant_message()
            tool_calls = assistant_message.setdefault("tool_calls", [])
            if not isinstance(tool_calls, list):
                tool_calls = []
                assistant_message["tool_calls"] = tool_calls
            tool_calls.append(
                {
                    "id": item["call_id"],
                    "call_id": item["call_id"],
                    "type": "function",
                    "function": {
                        "name": item["name"],
                        "arguments": item["arguments"],
                    },
                }
            )
            continue

        if item_type == "function_call_output":
            call_id, _ = split_tool_id(item.get("call_id"))
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call_id or item["call_id"],
                    "content": stringify_tool_output(item.get("output", "")),
                }
            )
            current_assistant_index = None
            continue

        role = item.get("role")
        if role in {"user", "assistant"}:
            message = {
                "role": role,
                "content": convert_content_from_responses(item.get("content", "")),
            }
            messages.append(message)
            current_assistant_index = len(messages) - 1 if role == "assistant" else None

    return messages


def normalize_responses_input_items(raw_items: Any) -> List[Dict[str, Any]]:
    if not isinstance(raw_items, list):
        raise ValueError("Codex Responses input must be a list of input items.")

    normalized: List[Dict[str, Any]] = []
    for idx, item in enumerate(raw_items):
        if not isinstance(item, dict):
            raise ValueError(f"Codex Responses input[{idx}] must be an object.")

        item_type = item.get("type")
        if item_type == "function_call":
            call_id = item.get("call_id")
            name = item.get("name")
            if not isinstance(call_id, str) or not call_id.strip():
                raise ValueError(f"Codex Responses input[{idx}] function_call is missing call_id.")
            if not isinstance(name, str) or not name.strip():
                raise ValueError(f"Codex Responses input[{idx}] function_call is missing name.")

            arguments = item.get("arguments", "{}")
            if isinstance(arguments, dict):
                arguments = json.dumps(arguments, ensure_ascii=False)
            elif not isinstance(arguments, str):
                arguments = str(arguments)
            arguments = arguments.strip() or "{}"

            normalized.append(
                {
                    "type": "function_call",
                    "call_id": call_id.strip(),
                    "name": name.strip(),
                    "arguments": arguments,
                }
            )
            continue

        if item_type == "function_call_output":
            call_id = item.get("call_id")
            if not isinstance(call_id, str) or not call_id.strip():
                raise ValueError(f"Codex Responses input[{idx}] function_call_output is missing call_id.")
            normalized.append(
                {
                    "type": "function_call_output",
                    "call_id": call_id.strip(),
                    "output": stringify_tool_output(item.get("output", "")),
                }
            )
            continue

        if item_type == "reasoning":
            encrypted = item.get("encrypted_content")
            if isinstance(encrypted, str) and encrypted:
                reasoning_item = {"type": "reasoning", "encrypted_content": encrypted}
                item_id = item.get("id")
                if isinstance(item_id, str) and item_id:
                    reasoning_item["id"] = item_id
                summary = item.get("summary")
                reasoning_item["summary"] = summary if isinstance(summary, list) else []
                normalized.append(reasoning_item)
            continue

        role = item.get("role")
        if role in {"user", "assistant"}:
            normalized.append({"role": role, "content": convert_content_for_responses(item.get("content", ""))})
            continue

        raise ValueError(
            f"Codex Responses input[{idx}] has unsupported item shape (type={item_type!r}, role={role!r})."
        )

    return normalized
