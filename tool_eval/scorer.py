"""Scoring engine for tool_eval harness.

Scoring: 40 pts tool name(s) correct + 60 pts argument quality.
All scoring is structural/parsing-based — no LLM-as-judge.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple


@dataclass
class TestResult:
    """Result of a single test evaluation."""

    test_id: str
    category: str
    description: str
    score: int
    passed: bool
    details: Dict[str, Any]
    model_calls: int
    raw_response: Dict
    error: Optional[str] = None
    retries: int = 0
    is_infra_error: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "test_id": self.test_id,
            "category": self.category,
            "description": self.description,
            "score": self.score,
            "passed": self.passed,
            "details": self.details,
            "model_calls": self.model_calls,
            "error": self.error,
            "retries": self.retries,
            "is_infra_error": self.is_infra_error,
            "raw_response": self._trunc(str(self.raw_response), 500),
        }

    @staticmethod
    def _trunc(text: str, max_len: int = 200) -> str:
        return text if len(text) <= max_len else text[:max_len] + "..."


def _safe_first_choice(raw: Any) -> Optional[Dict]:
    """Safely extract the first choice from a model response, handling None/empty lists."""
    if not isinstance(raw, dict):
        return None
    choices = raw.get("choices")
    if not isinstance(choices, list) or len(choices) == 0:
        return None
    first = choices[0]
    if first is None or not isinstance(first, dict):
        return None
    return first


def _extract_tool_calls(raw: Any) -> List[Dict]:
    """Extract tool call list from a model response dict.

    Handles both OpenAI chat.completion shape and raw dict shapes.
    Returns list of dicts with 'function' key containing name + arguments.
    """
    if not isinstance(raw, dict):
        return []

    choice = _safe_first_choice(raw)
    if choice:
        message = choice.get("message") or {}
        tool_calls = message.get("tool_calls")
        if isinstance(tool_calls, list):
            return _parse_tool_calls_list(tool_calls)

    # Fallback: top-level tool_calls
    tool_calls = raw.get("tool_calls")
    if isinstance(tool_calls, list):
        return _parse_tool_calls_list(tool_calls)

    return []


def _parse_tool_calls_list(tool_calls: list) -> List[Dict]:
    result = []
    for tc in tool_calls:
        if not isinstance(tc, dict):
            continue
        func = tc.get("function") or {}
        name = func.get("name", "")
        args = func.get("arguments", "{}")
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except (json.JSONDecodeError, TypeError):
                args = {}
        result.append({"function": {"name": name, "arguments": args}})
    return result


def _has_text_content(raw: Any) -> bool:
    """Check if model produced any textual content (not tool calls or refusals)."""
    return bool(_text_content(raw).strip())


def _text_content(raw: Any) -> str:
    """Extract text content from model response."""
    if not isinstance(raw, dict):
        return ""
    choice = _safe_first_choice(raw)
    if not choice:
        return ""
    message = choice.get("message") or {}
    content = message.get("content")
    return content if isinstance(content, str) else ""


def _check_arg_values(actual: Dict, expected: Dict) -> Tuple[bool, Dict]:
    """Check if specific arg values match (with type awareness)."""
    details: Dict = {}
    all_passed = True
    for key, exp_val in expected.items():
        act_val = actual.get(key)
        if isinstance(exp_val, str) and isinstance(act_val, str):
            passed = act_val.lower() == exp_val.lower()
        else:
            passed = act_val == exp_val
        details[key] = {"expected": exp_val, "actual": act_val, "passed": passed}
        if not passed:
            all_passed = False
    return all_passed, details


def _check_type_compliance(actual: Dict, expected: Dict) -> Tuple[bool, Dict]:
    """Verify argument types match expected."""
    type_map = {
        "int": int, "str": str, "bool": bool,
        "float": float, "list": list, "dict": dict,
    }
    details: Dict = {}
    all_passed = True
    for key, type_name in expected.items():
        act_val = actual.get(key)
        expected_type = type_map.get(type_name)
        if expected_type is None:
            continue
        passed = isinstance(act_val, expected_type)
        details[key] = {
            "expected_type": type_name,
            "actual_type": type(act_val).__name__,
            "passed": passed,
        }
        if not passed:
            all_passed = False
    return all_passed, details


def _check_no_extra_params(actual: Dict, schema: Dict) -> Tuple[bool, str]:
    """Check that model didn't invent parameters not in schema."""
    allowed = set(schema.get("parameters", {}).get("properties", {}).keys())
    allowed.discard("_raw_string")
    allowed.discard("_parse_error")
    extra = set(actual.keys()) - allowed
    if extra:
        return False, f"Hallucinated args: {sorted(extra)}"
    return True, ""


def _check_list_field(actual: Dict, field: str, min_items: int) -> Tuple[bool, str]:
    """Check that a list field exists and has the minimum number of items."""
    val = actual.get(field)
    if not isinstance(val, list):
        return False, f"field='{field}' missing or actual_type={type(val).__name__}"
    actual_count = len(val)
    passed = actual_count >= min_items
    return passed, f"field='{field}' min_items={min_items} actual_count={actual_count}"


def _is_infra_error(raw: Any) -> bool:
    """Detect upstream infrastructure errors (rate limits, 502s, null responses)."""
    if not isinstance(raw, dict):
        return False
    choices = raw.get("choices")
    error = raw.get("error")

    if (not choices) and isinstance(error, dict):
        message = str(error.get("message", "")).lower()
        code = str(error.get("code", ""))
        if "rate" in message or "502" in message or code == "502":
            return True

    if not any([raw.get("id"), raw.get("choices"), raw.get("model")]):
        return True

    return False


def _score_single_args_ratio(actual_args: Dict, spec: Dict) -> Tuple[float, Dict]:
    """Score arguments for a single matched tool call.

    Returns (ratio 0.0-1.0, details). All active criteria equally weighted.
    """
    criteria_scores: List[float] = []
    details: Dict = {}

    required_args = spec.get("required_args", [])
    if required_args:
        present = [k for k in required_args if k in actual_args]
        ratio = len(present) / len(required_args)
        criteria_scores.append(ratio)
        details["required_args"] = {"required": required_args, "present": present, "ratio": ratio}

    arg_values = spec.get("arg_values", {})
    if arg_values:
        _, val_details = _check_arg_values(actual_args, arg_values)
        ratio = sum(1 for v in val_details.values() if v["passed"]) / len(val_details)
        criteria_scores.append(ratio)
        details["arg_values"] = val_details

    optional_args = spec.get("optional_args", [])
    if optional_args:
        present = [k for k in optional_args if k in actual_args]
        ratio = len(present) / len(optional_args)
        criteria_scores.append(ratio)
        details["optional_args"] = {"optional": optional_args, "present": present, "ratio": ratio}

    arg_substring_checks = spec.get("arg_substring_checks", {})
    if arg_substring_checks:
        results = {}
        for key, substr in arg_substring_checks.items():
            val = str(actual_args.get(key, "")).lower()
            passed = substr.lower() in val
            results[key] = {"substring": substr, "passed": passed}
        ratio = sum(1 for v in results.values() if v["passed"]) / len(results)
        criteria_scores.append(ratio)
        details["arg_substring_checks"] = results

    arg_types = spec.get("arg_types", {})
    if arg_types:
        _, type_details = _check_type_compliance(actual_args, arg_types)
        if type_details:
            ratio = sum(1 for v in type_details.values() if v["passed"]) / len(type_details)
            criteria_scores.append(ratio)
            details["arg_types"] = type_details

    no_extra_params = spec.get("no_extra_params")
    if no_extra_params:
        passed, msg = _check_no_extra_params(actual_args, no_extra_params)
        criteria_scores.append(1.0 if passed else 0.0)
        details["no_extra_params"] = {"passed": passed, "message": msg}

    list_field_check = spec.get("list_field_check")
    if list_field_check:
        field = list_field_check.get("field", "")
        min_items = list_field_check.get("min_items", 1)
        passed, msg = _check_list_field(actual_args, field, min_items)
        criteria_scores.append(1.0 if passed else 0.0)
        details["list_field_check"] = {"passed": passed, "message": msg}

    if not criteria_scores:
        details["_criteria_summary"] = "No detailed arg scoring criteria — full credit"
        return 1.0, details

    return round(sum(criteria_scores) / len(criteria_scores), 4), details


def score_test(test_case: Dict, raw_response: Dict) -> TestResult:
    """Score a single test case against the model's response.

    Returns a TestResult with score 0-100.
    """
    test_id = test_case.get("id", "unknown")
    category = test_case.get("category", "Unknown")
    description = test_case.get("description", "")
    expected = test_case.get("expected", {})

    if _is_infra_error(raw_response):
        return TestResult(
            test_id=test_id, category=category, description=description,
            score=0, passed=False,
            details={"error": "infra_error"},
            model_calls=0, raw_response=raw_response,
            error="Infrastructure error (rate limit / 502)",
            is_infra_error=True,
        )

    tool_calls = _extract_tool_calls(raw_response)
    details: Dict = {}

    # --- no_tool_calls ---
    if expected.get("no_tool_calls"):
        hallucinated = [tc["function"]["name"] for tc in tool_calls]
        passed = len(tool_calls) == 0
        for name in hallucinated:
            details[f"hallucinated_{name}"] = {"passed": False}
        score = 100 if passed else 0
        return TestResult(
            test_id=test_id, category=category, description=description,
            score=score, passed=passed, details=details,
            model_calls=1, raw_response=raw_response,
            error=None if passed else f"unexpected_tool_calls: {hallucinated}",
        )

    # --- text_no_call ---
    if expected.get("text_no_call"):
        has_text = _has_text_content(raw_response)
        no_calls = len(tool_calls) == 0
        passed = has_text and no_calls
        details["text_no_call"] = {"has_text": has_text, "no_tool_calls": no_calls, "passed": passed}
        return TestResult(
            test_id=test_id, category=category, description=description,
            score=100 if passed else 0, passed=passed, details=details,
            model_calls=1, raw_response=raw_response,
            error=None if passed else "Expected text response with no tool calls",
        )

    # --- has_text / text_contains ---
    if expected.get("has_text") or expected.get("text_contains"):
        text = _text_content(raw_response)
        has_text = bool(text.strip())
        if not has_text:
            return TestResult(
                test_id=test_id, category=category, description=description,
                score=0, passed=False, details={"has_text": False},
                model_calls=1, raw_response=raw_response,
                error="Model did not return any text content",
            )
        substr = expected.get("text_contains", "")
        if substr:
            passed = substr.lower() in text.lower()
            details[f"text_contains_{substr[:20]}"] = {"passed": passed, "substring": substr}
            score = 100 if passed else 0
        else:
            passed = True
            score = 100
        return TestResult(
            test_id=test_id, category=category, description=description,
            score=score, passed=passed, details=details,
            model_calls=1, raw_response=raw_response,
            error=None if passed else f"Text did not contain: {substr!r}",
        )

    # --- unexpected_tool_calls ---
    unexpected_tools = expected.get("unexpected_tool_calls", [])
    if unexpected_tools:
        called_names = {tc["function"]["name"] for tc in tool_calls}
        violations = [n for n in unexpected_tools if n in called_names]
        passed = len(violations) == 0
        details["unexpected_tool_calls"] = {"forbidden": unexpected_tools, "violations": violations, "passed": passed}
        return TestResult(
            test_id=test_id, category=category, description=description,
            score=100 if passed else 0, passed=passed, details=details,
            model_calls=1, raw_response=raw_response,
            error=None if passed else f"Called forbidden tools: {violations}",
        )

    # --- Standard tool call scoring ---
    if not tool_calls:
        return TestResult(
            test_id=test_id, category=category, description=description,
            score=0, passed=False, details={"no_tool_calls_made": True},
            model_calls=1, raw_response=raw_response,
            error="Model did not call any tools",
        )

    tool_names = {tc["function"]["name"] for tc in tool_calls}
    fail_reason = None

    # function_count (exact)
    expected_count = expected.get("function_count")
    if expected_count is not None:
        actual_count = len(tool_calls)
        count_met = actual_count == expected_count
        details["function_count"] = {"expected": expected_count, "actual": actual_count, "passed": count_met}
        if not count_met:
            return TestResult(
                test_id=test_id, category=category, description=description,
                score=0, passed=False, details=details,
                model_calls=1, raw_response=raw_response,
                error=f"Expected exactly {expected_count} calls, got {actual_count}",
            )

    # function_counts_at_least
    min_count = expected.get("function_counts_at_least")
    if min_count is not None:
        actual_count = len(tool_calls)
        count_met = actual_count >= min_count
        details[f"at_least_{min_count}_calls"] = {
            "expected": f">= {min_count} calls, got {actual_count}",
            "passed": count_met,
        }
        if not count_met:
            return TestResult(
                test_id=test_id, category=category, description=description,
                score=0, passed=False, details=details,
                model_calls=1, raw_response=raw_response,
                error=f"Expected >= {min_count} calls, got {actual_count}",
            )

    # Resolve expected tool name(s)
    missing_tools: List[str] = []
    expected_names: List[str] = (
        expected.get("function_names")
        or ([expected["function_name"]] if expected.get("function_name") else [])
    )

    # --- Tool name score (40 pts) ---
    name_score = 40
    if expected_names:
        matched_tools = [n for n in expected_names if n in tool_names]
        missing_tools = [n for n in expected_names if n not in tool_names]
        extra_tools = sorted(tool_names - set(expected_names))
        name_ratio = len(matched_tools) / len(expected_names)
        name_score = int(round(name_ratio * 40))
        details["function_names"] = {
            "expected": expected_names,
            "actual": sorted(tool_names),
            "matched": matched_tools,
            "missing": missing_tools,
            "extra": extra_tools,
            "score": name_score,
        }
        if missing_tools:
            fail_reason = f"Tool '{missing_tools[0]}' not called"

    # --- Argument score (60 pts) ---
    args_spec = expected.get("arguments", {})
    args_score = 60

    if args_spec and expected_names:
        per_tool_ratios: List[float] = []
        for i, exp_name in enumerate(expected_names):
            found = next(
                (tc for tc in tool_calls if tc["function"]["name"] == exp_name),
                None,
            )
            if found is None:
                details[f"args_{exp_name}"] = {"passed": False, "error": f"Tool '{exp_name}' not called"}
                per_tool_ratios.append(0.0)
                continue

            spec = args_spec if len(expected_names) == 1 else args_spec.get(exp_name, {})
            actual_args = found["function"].get("arguments") or {}
            if not isinstance(actual_args, dict):
                actual_args = {}

            ratio, arg_details = _score_single_args_ratio(actual_args, spec)
            per_tool_ratios.append(ratio)
            details[f"call_{i}_{exp_name}"] = arg_details

        avg_ratio = sum(per_tool_ratios) / len(per_tool_ratios) if per_tool_ratios else 1.0
        args_score = int(round(avg_ratio * 60))

    score = name_score + args_score
    passed = score >= 60 and not (expected_names and missing_tools)

    return TestResult(
        test_id=test_id, category=category, description=description,
        score=score, passed=passed, details=details,
        model_calls=1, raw_response=raw_response,
        error=fail_reason,
    )


def score_debug_fixture(test_case: Dict) -> TestResult:
    """Score a test case using its own 'expected' as the model response.

    This is for --debug mode: verifies the scoring engine gives 100 on
    a perfect response by feeding the expected output back through scorer.
    """
    expected = test_case.get("expected", {})

    if expected.get("no_tool_calls") or expected.get("text_no_call"):
        raw_response = {
            "id": "debug-fixture",
            "choices": [{
                "message": {
                    "role": "assistant",
                    "content": "I cannot do that — the requested tool is not available.",
                    "tool_calls": None,
                }
            }],
        }
        return score_test(test_case, raw_response)

    if expected.get("has_text") or expected.get("text_contains"):
        text = expected.get("text_contains", "Here is the information you requested.")
        raw_response = {
            "id": "debug-fixture",
            "choices": [{
                "message": {
                    "role": "assistant",
                    "content": text,
                    "tool_calls": None,
                }
            }],
        }
        return score_test(test_case, raw_response)

    if expected.get("unexpected_tool_calls"):
        raw_response = {
            "id": "debug-fixture",
            "choices": [{
                "message": {
                    "role": "assistant",
                    "content": "I'll handle this without using those tools.",
                    "tool_calls": None,
                }
            }],
        }
        return score_test(test_case, raw_response)

    expected_names: List[str] = (
        expected.get("function_names")
        or ([expected["function_name"]] if expected.get("function_name") else [])
    )
    args_spec = expected.get("arguments", {})

    tool_calls_raw = []
    for i, func_name in enumerate(expected_names):
        spec = args_spec if len(expected_names) == 1 else args_spec.get(func_name, {})
        args: Dict = {}

        # Determine which keys are handled by higher-priority checks so we
        # don't clobber them with a plain string fallback in the required_args pass.
        arg_substring_keys = set(spec.get("arg_substring_checks", {}).keys())
        lfc = spec.get("list_field_check")
        list_field_key = lfc["field"] if lfc else None
        arg_type_keys = set(spec.get("arg_types", {}).keys())

        # Type-default map for arg_types — produce a correctly-typed value.
        _type_defaults: Dict[str, Any] = {"int": 0, "str": "test_val", "bool": True, "float": 0.0, "list": [], "dict": {}}

        for k in spec.get("required_args", []):
            if k in arg_substring_keys or k == list_field_key:
                # Will be filled by the appropriate block below; skip fallback.
                continue
            if k in arg_type_keys:
                type_name = spec["arg_types"][k]
                args[k] = _type_defaults.get(type_name, 0)
            else:
                args[k] = spec.get("arg_values", {}).get(k, f"test_{k}")
        for k, v in spec.get("arg_values", {}).items():
            args[k] = v
        for k, substr in spec.get("arg_substring_checks", {}).items():
            args[k] = substr

        if lfc:
            field = lfc["field"]
            min_items = lfc.get("min_items", 1)
            args[field] = [
                {"id": f"t{j}", "content": f"task {j}", "status": "pending"}
                for j in range(min_items)
            ]

        tool_calls_raw.append({
            "id": f"call_{i}",
            "type": "function",
            "function": {"name": func_name, "arguments": json.dumps(args)},
        })

    raw_response = {
        "id": "debug-fixture",
        "choices": [{
            "message": {
                "role": "assistant",
                "content": None,
                "tool_calls": tool_calls_raw,
            }
        }],
    }
    return score_test(test_case, raw_response)
