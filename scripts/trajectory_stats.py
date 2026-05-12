#!/usr/bin/env python3
"""Summarize Hermes conversation trajectory files.

Supports a single trajectory file or a directory of trajectory files.
For each trajectory, reports message counts, API rounds, tool-call activity,
and token usage when the saved file contains usage metadata.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Dict, Iterable, List

from agent.model_metadata import estimate_messages_tokens_rough

ALLOWED_SUFFIXES = {".json", ".jsonl", ".ndjson"}
TOKENIZER_MODES = {"rough", "tiktoken"}


def collect_input_files(path: Path) -> List[Path]:
    """Return trajectory files from a file or directory."""
    if path.is_file():
        return [path]
    if path.is_dir():
        files = [p for p in path.rglob("*") if p.is_file() and p.suffix.lower() in ALLOWED_SUFFIXES]
        return sorted(files, key=lambda p: (p.name, str(p)))
    raise FileNotFoundError(path)


def _load_json_file(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _iter_trajectory_records(path: Path) -> Iterable[Dict[str, Any]]:
    if path.suffix.lower() in {".jsonl", ".ndjson"}:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            yield json.loads(line)
        return
    yield _load_json_file(path)


def _merge_usage(target: Counter, usage: Dict[str, Any]) -> None:
    for key in ("prompt_tokens", "completion_tokens", "total_tokens", "input_tokens", "output_tokens", "cached_tokens"):
        value = usage.get(key)
        if isinstance(value, int):
            target[key] += value


def _extract_usage(message: Dict[str, Any]) -> Dict[str, Any]:
    if isinstance(message.get("usage"), dict):
        return message["usage"]
    response = message.get("response")
    if isinstance(response, dict) and isinstance(response.get("usage"), dict):
        return response["usage"]
    metadata = message.get("metadata")
    if isinstance(metadata, dict) and isinstance(metadata.get("usage"), dict):
        return metadata["usage"]
    return {}


def _estimate_tokens(messages: List[Dict[str, Any]], model: str | None, tokenizer_mode: str) -> Dict[str, Any]:
    if tokenizer_mode == "tiktoken":
        try:
            import tiktoken

            try:
                encoding = tiktoken.encoding_for_model(model or "")
            except Exception:
                try:
                    encoding = tiktoken.get_encoding("o200k_base")
                except Exception:
                    encoding = tiktoken.get_encoding("cl100k_base")

            total = 0
            for msg in messages:
                payload = json.dumps(msg, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                total += len(encoding.encode(payload))
            return {"approx_tokens": total, "tokenizer": "tiktoken"}
        except Exception as exc:
            print(f"warning: tiktoken unavailable ({exc}); falling back to rough estimate", file=sys.stderr)

    return {
        "approx_tokens": estimate_messages_tokens_rough(messages),
        "tokenizer": "rough",
    }


def summarize_trajectory_data(
    data: Dict[str, Any],
    *,
    source: str | None = None,
    tokenizer_mode: str = "rough",
) -> Dict[str, Any]:
    if tokenizer_mode not in TOKENIZER_MODES:
        raise ValueError(f"Unsupported tokenizer mode: {tokenizer_mode}")

    messages = data.get("messages") or []
    role_counts = Counter(msg.get("role", "unknown") for msg in messages)
    finish_reasons = Counter()
    tool_names = Counter()
    tool_result_names = Counter()
    usage_totals: Counter = Counter()
    tool_call_turns = 0
    tool_calls_total = 0

    for msg in messages:
        if msg.get("role") == "assistant":
            finish_reason = msg.get("finish_reason")
            if finish_reason:
                finish_reasons[finish_reason] += 1
            tool_calls = msg.get("tool_calls") or []
            if tool_calls:
                tool_call_turns += 1
            for tool_call in tool_calls:
                function = tool_call.get("function") or {}
                name = function.get("name") or tool_call.get("name") or "unknown"
                tool_names[name] += 1
                tool_calls_total += 1
        elif msg.get("role") == "tool":
            tool_result_names[msg.get("name") or "unknown"] += 1

        usage = _extract_usage(msg)
        if usage:
            _merge_usage(usage_totals, usage)

    assistant_messages = role_counts.get("assistant", 0)
    tool_messages = role_counts.get("tool", 0)
    user_messages = role_counts.get("user", 0)
    total_tokens_exact = usage_totals.get("total_tokens")
    has_exact_usage = total_tokens_exact is not None and total_tokens_exact > 0
    estimated_token_info = None if has_exact_usage else _estimate_tokens(
        messages,
        data.get("model"),
        tokenizer_mode,
    )

    token_estimate = {
        "kind": "exact" if has_exact_usage else "estimated",
        "approx_tokens": total_tokens_exact if has_exact_usage else estimated_token_info["approx_tokens"],
        "tokenizer": "api_usage" if has_exact_usage else estimated_token_info["tokenizer"],
    }
    if has_exact_usage:
        token_estimate.update({
            "prompt_tokens": usage_totals.get("prompt_tokens"),
            "completion_tokens": usage_totals.get("completion_tokens"),
            "total_tokens": total_tokens_exact,
            "input_tokens": usage_totals.get("input_tokens"),
            "output_tokens": usage_totals.get("output_tokens"),
            "cached_tokens": usage_totals.get("cached_tokens"),
        })
    else:
        token_estimate.update({
            "prompt_tokens": None,
            "completion_tokens": None,
            "total_tokens": None,
            "input_tokens": None,
            "output_tokens": None,
            "cached_tokens": None,
        })

    return {
        "source": source,
        "model": data.get("model"),
        "session_id": data.get("session_id"),
        "session_start": data.get("session_start"),
        "message_count": len(messages),
        "user_messages": user_messages,
        "assistant_messages": assistant_messages,
        "tool_messages": tool_messages,
        "api_rounds": assistant_messages,
        "tool_call_turns": tool_call_turns,
        "tool_calls_total": tool_calls_total,
        "finish_reasons": dict(finish_reasons),
        "tool_calls_by_name": dict(tool_names.most_common()),
        "tool_results_by_name": dict(tool_result_names.most_common()),
        "token_estimate": token_estimate,
    }


def summarize_trajectory_file(path: Path, *, tokenizer_mode: str = "rough") -> Dict[str, Any]:
    records = list(_iter_trajectory_records(path))
    if not records:
        raise ValueError(f"No trajectory records found in {path}")
    if len(records) == 1:
        return summarize_trajectory_data(records[0], source=str(path), tokenizer_mode=tokenizer_mode)

    summaries = [
        summarize_trajectory_data(record, source=f"{path}#{idx + 1}", tokenizer_mode=tokenizer_mode)
        for idx, record in enumerate(records)
    ]
    aggregate = _aggregate_summaries(summaries)
    aggregate["records"] = summaries
    aggregate["source"] = str(path)
    return aggregate


def _aggregate_summaries(summaries: List[Dict[str, Any]]) -> Dict[str, Any]:
    finish_reasons = Counter()
    tool_calls_by_name = Counter()
    tool_results_by_name = Counter()
    token_totals = Counter()
    approximate_tokens = 0
    exact = True

    for summary in summaries:
        finish_reasons.update(summary.get("finish_reasons", {}))
        tool_calls_by_name.update(summary.get("tool_calls_by_name", {}))
        tool_results_by_name.update(summary.get("tool_results_by_name", {}))
        token_info = summary.get("token_estimate", {})
        approximate_tokens += int(token_info.get("approx_tokens") or 0)
        if token_info.get("kind") == "exact":
            for key in ("prompt_tokens", "completion_tokens", "total_tokens", "input_tokens", "output_tokens", "cached_tokens"):
                value = token_info.get(key)
                if isinstance(value, int):
                    token_totals[key] += value
        else:
            exact = False

    token_estimate = {
        "kind": "exact" if exact and token_totals.get("total_tokens", 0) > 0 else "estimated",
        "approx_tokens": approximate_tokens,
        "tokenizer": "api_usage" if exact and token_totals.get("total_tokens", 0) > 0 else "mixed_or_estimated",
    }
    if token_estimate["kind"] == "exact":
        token_estimate.update(dict(token_totals))
    else:
        token_estimate.update({
            "prompt_tokens": None,
            "completion_tokens": None,
            "total_tokens": None,
            "input_tokens": None,
            "output_tokens": None,
            "cached_tokens": None,
        })

    return {
        "file_count": len(summaries),
        "api_rounds": sum(s["api_rounds"] for s in summaries),
        "assistant_messages": sum(s["assistant_messages"] for s in summaries),
        "tool_messages": sum(s["tool_messages"] for s in summaries),
        "tool_call_turns": sum(s["tool_call_turns"] for s in summaries),
        "tool_calls_total": sum(s["tool_calls_total"] for s in summaries),
        "user_messages": sum(s["user_messages"] for s in summaries),
        "message_count": sum(s["message_count"] for s in summaries),
        "finish_reasons": dict(finish_reasons),
        "tool_calls_by_name": dict(tool_calls_by_name.most_common()),
        "tool_results_by_name": dict(tool_results_by_name.most_common()),
        "token_estimate": token_estimate,
    }


def _format_counter(counter: Dict[str, int], *, limit: int = 10) -> str:
    if not counter:
        return "  (none)"
    lines = []
    for idx, (name, count) in enumerate(sorted(counter.items(), key=lambda item: (-item[1], item[0]))[:limit], start=1):
        lines.append(f"  {idx}. {name}: {count}")
    return "\n".join(lines)


def format_summary(summary: Dict[str, Any]) -> str:
    token_info = summary["token_estimate"]
    lines = [
        f"Source: {summary.get('source')}",
        f"Messages: {summary.get('message_count')}  Users: {summary.get('user_messages')}  Assistants: {summary.get('assistant_messages')}  Tools: {summary.get('tool_messages')}",
        f"API rounds: {summary.get('api_rounds')}  Tool-call turns: {summary.get('tool_call_turns')}  Tool calls: {summary.get('tool_calls_total')}",
        f"Tokens ({token_info['kind']}, {token_info.get('tokenizer')}): {token_info['approx_tokens']}",
    ]
    if summary.get("model") is not None:
        lines.insert(1, f"Model: {summary.get('model')}")
    if summary.get("session_id") is not None or summary.get("session_start") is not None:
        lines.insert(2 if summary.get("model") is not None else 1,
                     f"Session: {summary.get('session_id')}  Start: {summary.get('session_start')}")
    if token_info.get("kind") == "exact":
        lines.append(
            "Exact usage: "
            f"prompt={token_info.get('prompt_tokens')} "
            f"completion={token_info.get('completion_tokens')} "
            f"total={token_info.get('total_tokens')}"
        )
    if summary.get("finish_reasons"):
        lines.append("Finish reasons:")
        lines.append(_format_counter(summary["finish_reasons"]))
    if summary.get("tool_calls_by_name"):
        lines.append("Tool calls:")
        lines.append(_format_counter(summary["tool_calls_by_name"]))
    if summary.get("tool_results_by_name"):
        lines.append("Tool results:")
        lines.append(_format_counter(summary["tool_results_by_name"]))
    return "\n".join(lines)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Summarize Hermes trajectory files.")
    parser.add_argument("path", type=Path, help="Trajectory file or directory")
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of text")
    parser.add_argument(
        "--tokenizer",
        choices=sorted(TOKENIZER_MODES),
        default="rough",
        help="Token counting backend for trajectories without API usage metadata",
    )
    return parser


def main(argv: List[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    paths = collect_input_files(args.path)
    summaries = [summarize_trajectory_file(path, tokenizer_mode=args.tokenizer) for path in paths]

    if args.json:
        payload: Dict[str, Any]
        if len(summaries) == 1:
            payload = summaries[0]
        else:
            payload = {"files": summaries, "aggregate": _aggregate_summaries(summaries)}
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        for idx, summary in enumerate(summaries):
            if idx:
                print()
                print("=" * 72)
            print(format_summary(summary))
        if len(summaries) > 1:
            print()
            print("Aggregate:")
            print(format_summary(_aggregate_summaries(summaries)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
