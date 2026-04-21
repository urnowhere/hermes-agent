"""Official google-genai SDK bridge for Hermes' Google AI Studio provider."""

from __future__ import annotations

import base64
import binascii
import json
import time
import uuid
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

import httpx
from google import genai
from google.genai import types

DEFAULT_GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"


def normalize_google_model_name(model: str) -> str:
    normalized = str(model or "").strip()
    if normalized.startswith("models/"):
        normalized = normalized[len("models/") :]
    if normalized.startswith("google/"):
        normalized = normalized[len("google/") :]
    elif normalized.startswith("google:"):
        normalized = normalized[len("google:") :]
    return normalized


def _coerce_content_to_text(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: List[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict) and item.get("type") == "text":
                text = item.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return "\n".join(parts)
    return str(content)


def _extract_multimodal_parts(content: Any) -> List[Dict[str, Any]]:
    if not isinstance(content, list):
        text = _coerce_content_to_text(content)
        return [{"text": text}] if text else []

    parts: List[Dict[str, Any]] = []
    for item in content:
        if isinstance(item, str):
            parts.append({"text": item})
            continue
        if not isinstance(item, dict):
            continue
        if item.get("type") == "text":
            text = item.get("text")
            if isinstance(text, str) and text:
                parts.append({"text": text})
            continue
        if item.get("type") == "image_url":
            url = ((item.get("image_url") or {}).get("url") or "")
            if not isinstance(url, str) or not url.startswith("data:"):
                continue
            try:
                header, encoded = url.split(",", 1)
                mime = header.split(":", 1)[1].split(";", 1)[0]
                raw = base64.b64decode(encoded)
            except Exception:
                continue
            parts.append(
                {
                    "inlineData": {
                        "mimeType": mime,
                        "data": base64.b64encode(raw).decode("ascii"),
                    }
                }
            )
    return parts


def _tool_call_extra_signature(tool_call: Dict[str, Any]) -> Optional[str]:
    extra = tool_call.get("extra_content") or {}
    if not isinstance(extra, dict):
        return None
    google = extra.get("google") or extra.get("thought_signature")
    if isinstance(google, dict):
        signature = google.get("thought_signature") or google.get("thoughtSignature")
        return str(signature) if isinstance(signature, str) and signature else None
    if isinstance(google, str) and google:
        return google
    return None


def _translate_tool_call_to_gemini(tool_call: Dict[str, Any]) -> Dict[str, Any]:
    function = tool_call.get("function") or {}
    args_raw = function.get("arguments", "")
    try:
        args = json.loads(args_raw) if isinstance(args_raw, str) and args_raw else {}
    except json.JSONDecodeError:
        args = {"_raw": args_raw}
    if not isinstance(args, dict):
        args = {"_value": args}

    part: Dict[str, Any] = {
        "functionCall": {
            "name": str(function.get("name") or ""),
            "args": args,
        }
    }
    signature = _tool_call_extra_signature(tool_call)
    if signature:
        part["thoughtSignature"] = signature
    return part


def _translate_tool_result_to_gemini(
    message: Dict[str, Any],
    *,
    tool_name_by_call_id: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    tool_name_by_call_id = tool_name_by_call_id or {}
    tool_call_id = str(message.get("tool_call_id") or "")
    name = str(
        message.get("name")
        or tool_name_by_call_id.get(tool_call_id)
        or tool_call_id
        or "tool"
    )
    content = _coerce_content_to_text(message.get("content"))
    try:
        parsed = json.loads(content) if content.strip().startswith(("{", "[")) else None
    except json.JSONDecodeError:
        parsed = None
    response = parsed if isinstance(parsed, dict) else {"output": content}
    return {"functionResponse": {"name": name, "response": response}}


def build_gemini_request(
    *,
    messages: List[Dict[str, Any]],
    tools: Any = None,
    tool_choice: Any = None,
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
    top_p: Optional[float] = None,
    stop: Any = None,
    thinking_config: Any = None,
) -> Dict[str, Any]:
    system_parts: List[str] = []
    contents: List[Dict[str, Any]] = []
    tool_name_by_call_id: Dict[str, str] = {}

    for msg in messages:
        if not isinstance(msg, dict):
            continue
        role = str(msg.get("role") or "user")

        if role == "system":
            system_parts.append(_coerce_content_to_text(msg.get("content")))
            continue

        if role in {"tool", "function"}:
            contents.append(
                {
                    "role": "user",
                    "parts": [
                        _translate_tool_result_to_gemini(
                            msg,
                            tool_name_by_call_id=tool_name_by_call_id,
                        )
                    ],
                }
            )
            continue

        gemini_role = "model" if role == "assistant" else "user"
        parts = _extract_multimodal_parts(msg.get("content"))

        for tool_call in msg.get("tool_calls") or []:
            if not isinstance(tool_call, dict):
                continue
            tool_call_id = str(tool_call.get("id") or tool_call.get("call_id") or "")
            tool_name = str(((tool_call.get("function") or {}).get("name") or ""))
            if tool_call_id and tool_name:
                tool_name_by_call_id[tool_call_id] = tool_name
            parts.append(_translate_tool_call_to_gemini(tool_call))

        if parts:
            contents.append({"role": gemini_role, "parts": parts})

    request: Dict[str, Any] = {"contents": contents}
    system_instruction = "\n".join(part for part in system_parts if part).strip()
    if system_instruction:
        request["systemInstruction"] = {"parts": [{"text": system_instruction}]}

    declarations: List[Dict[str, Any]] = []
    for tool in tools or []:
        if not isinstance(tool, dict):
            continue
        function = tool.get("function") or {}
        name = function.get("name")
        if not isinstance(name, str) or not name:
            continue
        declaration: Dict[str, Any] = {"name": name}
        description = function.get("description")
        if isinstance(description, str) and description:
            declaration["description"] = description
        parameters = function.get("parameters")
        if isinstance(parameters, dict):
            declaration["parameters"] = parameters
        declarations.append(declaration)
    if declarations:
        request["tools"] = [{"functionDeclarations": declarations}]

    if tool_choice == "auto":
        request["toolConfig"] = {"functionCallingConfig": {"mode": "AUTO"}}
    elif tool_choice == "required":
        request["toolConfig"] = {"functionCallingConfig": {"mode": "ANY"}}
    elif tool_choice == "none":
        request["toolConfig"] = {"functionCallingConfig": {"mode": "NONE"}}
    elif isinstance(tool_choice, dict):
        function = tool_choice.get("function") or {}
        name = function.get("name")
        if isinstance(name, str) and name:
            request["toolConfig"] = {
                "functionCallingConfig": {"mode": "ANY", "allowedFunctionNames": [name]}
            }

    generation_config: Dict[str, Any] = {}
    if temperature is not None:
        generation_config["temperature"] = temperature
    if max_tokens is not None:
        generation_config["maxOutputTokens"] = max_tokens
    if top_p is not None:
        generation_config["topP"] = top_p
    if stop:
        generation_config["stopSequences"] = stop if isinstance(stop, list) else [str(stop)]
    if isinstance(thinking_config, dict):
        generation_config["thinkingConfig"] = thinking_config
    if generation_config:
        request["generationConfig"] = generation_config
    return request


def _decode_thought_signature(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    try:
        return base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError):
        return value.encode("utf-8")


def _sdk_content_list(request: Dict[str, Any]) -> List[types.Content]:
    contents: List[types.Content] = []
    for content in request.get("contents") or []:
        if not isinstance(content, dict):
            continue
        payload = dict(content)
        payload["parts"] = []
        for part in content.get("parts") or []:
            if not isinstance(part, dict):
                continue
            sdk_part = dict(part)
            if "thoughtSignature" in sdk_part:
                sdk_part["thoughtSignature"] = _decode_thought_signature(sdk_part["thoughtSignature"])
            payload["parts"].append(sdk_part)
        contents.append(types.Content.model_validate(payload))
    return contents


def _sdk_config(request: Dict[str, Any]) -> Optional[types.GenerateContentConfig]:
    payload: Dict[str, Any] = {}
    if request.get("systemInstruction"):
        payload["systemInstruction"] = request["systemInstruction"]
    if request.get("tools"):
        payload["tools"] = request["tools"]
    if request.get("toolConfig"):
        payload["toolConfig"] = request["toolConfig"]
    generation_config = request.get("generationConfig") or {}
    if isinstance(generation_config, dict):
        payload.update(generation_config)
    if not payload:
        return None
    return types.GenerateContentConfig.model_validate(payload)


def build_google_http_options(
    *,
    base_url: Optional[str] = None,
    default_headers: Optional[Dict[str, str]] = None,
    timeout: Any = None,
    http_client: Optional[httpx.Client] = None,
) -> types.HttpOptions:
    normalized_base = (base_url or DEFAULT_GEMINI_BASE_URL).rstrip("/")
    if normalized_base.endswith("/openai"):
        normalized_base = normalized_base[: -len("/openai")]

    timeout_ms: Optional[int] = None
    if isinstance(timeout, httpx.Timeout):
        values = [v for v in (timeout.connect, timeout.read, timeout.write, timeout.pool) if isinstance(v, (int, float))]
        if values:
            timeout_ms = int(max(values) * 1000)
    elif isinstance(timeout, (int, float)):
        timeout_ms = int(float(timeout) * 1000)

    return types.HttpOptions(
        base_url=normalized_base,
        headers=dict(default_headers or {}) or None,
        timeout=timeout_ms,
        httpx_client=http_client,
    )


def build_google_client(
    *,
    api_key: str,
    base_url: Optional[str] = None,
    default_headers: Optional[Dict[str, str]] = None,
    timeout: Any = None,
    http_client: Optional[httpx.Client] = None,
):
    return genai.Client(
        api_key=api_key,
        http_options=build_google_http_options(
            base_url=base_url,
            default_headers=default_headers,
            timeout=timeout,
            http_client=http_client,
        ),
    )


def build_google_generate_content_kwargs(
    *,
    model: str,
    messages: List[Dict[str, Any]],
    tools: Any = None,
    tool_choice: Any = None,
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
    top_p: Optional[float] = None,
    stop: Any = None,
    thinking_config: Any = None,
) -> Dict[str, Any]:
    request = build_gemini_request(
        messages=messages,
        tools=tools,
        tool_choice=tool_choice,
        temperature=temperature,
        max_tokens=max_tokens,
        top_p=top_p,
        stop=stop,
        thinking_config=thinking_config,
    )
    kwargs: Dict[str, Any] = {
        "model": normalize_google_model_name(model),
        "contents": _sdk_content_list(request),
    }
    config = _sdk_config(request)
    if config is not None:
        kwargs["config"] = config
    return kwargs


def build_google_kwargs(
    model: str,
    messages: List[Dict[str, Any]],
    tools: Any = None,
    max_tokens: Optional[int] = None,
) -> Dict[str, Any]:
    return build_google_generate_content_kwargs(
        model=model,
        messages=messages,
        tools=tools,
        max_tokens=max_tokens,
    )


def _google_response_payload(response: Any) -> Dict[str, Any]:
    if isinstance(response, dict):
        return response
    if hasattr(response, "model_dump"):
        payload = response.model_dump(mode="json", by_alias=True, exclude_none=True)
        if isinstance(payload, dict):
            return payload
    return {}


def _map_finish_reason(reason: str) -> str:
    mapping = {
        "STOP": "stop",
        "MAX_TOKENS": "length",
        "SAFETY": "content_filter",
        "RECITATION": "content_filter",
        "OTHER": "stop",
    }
    return mapping.get(str(reason or "").upper(), "stop")


def _normalize_google_response_object(response: Any, model: str) -> SimpleNamespace:
    payload = _google_response_payload(response)
    candidates = payload.get("candidates") or []
    if not candidates:
        message = SimpleNamespace(role="assistant", content="", tool_calls=None)
        return SimpleNamespace(
            id=f"chatcmpl-{uuid.uuid4().hex[:12]}",
            model=model,
            object="chat.completion",
            created=int(time.time()),
            choices=[SimpleNamespace(index=0, message=message, finish_reason="stop")],
            usage=SimpleNamespace(prompt_tokens=0, completion_tokens=0, total_tokens=0),
        )

    candidate = candidates[0] if isinstance(candidates[0], dict) else {}
    parts = ((candidate.get("content") or {}).get("parts") or []) if isinstance(candidate, dict) else []

    text_parts: List[str] = []
    tool_calls: List[SimpleNamespace] = []
    reasoning_parts: List[str] = []
    for index, part in enumerate(parts):
        if not isinstance(part, dict):
            continue
        if part.get("thought") is True and isinstance(part.get("text"), str):
            reasoning_parts.append(part["text"])
            continue
        if isinstance(part.get("text"), str):
            text_parts.append(part["text"])
            continue
        function_call = part.get("functionCall")
        if isinstance(function_call, dict) and function_call.get("name"):
            tool_calls.append(
                SimpleNamespace(
                    id=str(function_call.get("id") or f"call_{uuid.uuid4().hex[:12]}"),
                    type="function",
                    index=index,
                    function=SimpleNamespace(
                        name=str(function_call["name"]),
                        arguments=json.dumps(function_call.get("args") or {}, ensure_ascii=False),
                    ),
                )
            )

    finish_reason = "tool_calls" if tool_calls else _map_finish_reason(str(candidate.get("finishReason") or ""))
    message = SimpleNamespace(
        role="assistant",
        content="".join(text_parts) if text_parts else None,
        tool_calls=tool_calls or None,
        reasoning="".join(reasoning_parts) or None,
    )
    usage_meta = payload.get("usageMetadata") or {}
    return SimpleNamespace(
        id=f"chatcmpl-{uuid.uuid4().hex[:12]}",
        model=model,
        object="chat.completion",
        created=int(time.time()),
        choices=[SimpleNamespace(index=0, message=message, finish_reason=finish_reason)],
        usage=SimpleNamespace(
            prompt_tokens=int(usage_meta.get("promptTokenCount") or 0),
            completion_tokens=int(usage_meta.get("candidatesTokenCount") or 0),
            total_tokens=int(usage_meta.get("totalTokenCount") or 0),
        ),
    )


def normalize_google_response(response: Any, model: str):
    normalized = _normalize_google_response_object(response, model)
    return normalized, normalized.choices[0].finish_reason
