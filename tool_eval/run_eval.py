#!/usr/bin/env python3
"""Tool eval runner for hermes-agent.

Usage:
    python tool_eval/run_eval.py --model "anthropic/claude-sonnet-4" --openrouter
    python tool_eval/run_eval.py --model "gpt-4o" --base-url https://api.openai.com/v1 --api-key sk-...
    python tool_eval/run_eval.py --debug   # verify scorer correctness, no API calls
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Optional

# Resolve paths relative to this file
TOOL_EVAL_DIR = Path(__file__).parent
REPO_ROOT = TOOL_EVAL_DIR.parent
sys.path.insert(0, str(REPO_ROOT))

# Load .env before importing openai
def _load_dotenv() -> None:
    try:
        from dotenv import load_dotenv
        for candidate in [
            TOOL_EVAL_DIR / ".env",
            REPO_ROOT / ".env",
            Path.home() / ".env",
        ]:
            if candidate.exists():
                load_dotenv(candidate)
                break
    except ImportError:
        pass

_load_dotenv()

import openai
from tool_eval.scorer import TestResult, score_test, score_debug_fixture

try:
    from rich.console import Console
    from rich.table import Table
    _rich = True
    console = Console()
except ImportError:
    _rich = False
    console = None


def _load_test_cases(path: Path) -> list:
    with open(path) as f:
        return json.load(f)


def _build_tool_schemas_openai_format(tool_names: list) -> list:
    """Return tool schemas in OpenAI tools array format."""
    try:
        from tools.registry import registry
        schemas = []
        for name in tool_names:
            entry = registry.get(name)
            if entry and hasattr(entry, "schema"):
                schemas.append({"type": "function", "function": entry.schema})
                continue
            schemas.append({
                "type": "function",
                "function": {
                    "name": name,
                    "description": f"Tool: {name}",
                    "parameters": {"type": "object", "properties": {}, "required": []},
                },
            })
        return schemas
    except Exception:
        return [
            {
                "type": "function",
                "function": {
                    "name": name,
                    "description": f"Tool: {name}",
                    "parameters": {"type": "object", "properties": {}, "required": []},
                },
            }
            for name in tool_names
        ]


def _load_injection_file(path: Path) -> str:
    if path.exists():
        return path.read_text()
    return ""


def _run_single_test(
    client: openai.OpenAI,
    test_case: dict,
    model: str,
    hermes_context: str,
    tool_primer: str,
    rate_limit: float,
    max_retries: int = 3,
) -> TestResult:
    """Send a single test case to the model and score the response."""
    system_parts = []
    if hermes_context:
        system_parts.append(hermes_context)
    if tool_primer:
        system_parts.append(tool_primer)
    system_prompt = "\n\n---\n\n".join(system_parts) if system_parts else "You are a helpful assistant."

    tool_names = test_case.get("available_tools", [])
    tools = _build_tool_schemas_openai_format(tool_names) if tool_names else None

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": test_case["prompt"]},
    ]

    retries = 0
    raw_response: dict = {}

    while retries <= max_retries:
        try:
            kwargs: dict = {
                "model": model,
                "messages": messages,
                "temperature": 0,
            }
            if tools:
                kwargs["tools"] = tools
                kwargs["tool_choice"] = "auto"

            response = client.chat.completions.create(**kwargs)
            raw_response = response.model_dump()
            break
        except openai.RateLimitError as e:
            retries += 1
            if retries <= max_retries:
                time.sleep(rate_limit * 2)
            else:
                raw_response = {"choices": None, "error": {"message": str(e), "code": 429}}
        except Exception as e:
            raw_response = {"choices": None, "error": {"message": str(e)}}
            break

        time.sleep(rate_limit)

    result = score_test(test_case, raw_response)
    result.retries = retries
    return result


def _print_result(result: TestResult, verbose: bool = False) -> None:
    status = "PASS" if result.passed else ("INFRA" if result.is_infra_error else "FAIL")
    color = "green" if result.passed else ("yellow" if result.is_infra_error else "red")

    if _rich and console:
        console.print(
            f"  [{color}]{status:5}[/{color}] {result.score:3}/100  "
            f"[dim]{result.test_id}[/dim]"
            + (f"  [dim red]{result.error}[/dim red]" if result.error and not result.passed else "")
        )
    else:
        print(f"  {status:5} {result.score:3}/100  {result.test_id}"
              + (f"  -- {result.error}" if result.error and not result.passed else ""))

    if verbose and result.details:
        for k, v in result.details.items():
            print(f"         {k}: {v}")


def _print_summary(results: list, model: str) -> None:
    total = len(results)
    passed = sum(1 for r in results if r.passed)
    infra_errors = sum(1 for r in results if r.is_infra_error)
    avg_score = sum(r.score for r in results) / total if total else 0

    cats: dict = {}
    for r in results:
        if r.category not in cats:
            cats[r.category] = {"total": 0, "passed": 0, "score_sum": 0}
        cats[r.category]["total"] += 1
        if r.passed:
            cats[r.category]["passed"] += 1
        cats[r.category]["score_sum"] += r.score

    print()
    if _rich and console:
        table = Table(title=f"Results — {model}")
        table.add_column("Category", style="cyan")
        table.add_column("Pass", justify="right")
        table.add_column("Total", justify="right")
        table.add_column("Avg Score", justify="right")
        for cat, data in sorted(cats.items()):
            avg = data["score_sum"] / data["total"]
            color = "green" if data["passed"] == data["total"] else ("yellow" if data["passed"] > 0 else "red")
            table.add_row(cat, f"[{color}]{data['passed']}[/{color}]", str(data["total"]), f"{avg:.0f}")
        table.add_section()
        table.add_row("TOTAL", str(passed), str(total), f"{avg_score:.0f}")
        console.print(table)
    else:
        print(f"=== Results: {model} ===")
        for cat, data in sorted(cats.items()):
            avg = data["score_sum"] / data["total"]
            print(f"  {cat:15} {data['passed']}/{data['total']}  avg={avg:.0f}")
        print(f"  {'TOTAL':15} {passed}/{total}  avg={avg_score:.0f}")

    if infra_errors:
        print(f"\n  WARNING: {infra_errors} infra errors (rate limits/502s) — scores may be lower than actual")
    print()


def _run_debug_mode(test_cases: list, categories: Optional[set]) -> int:
    """Feed gold fixtures back through scorer — all must score 100."""
    print("=== DEBUG MODE: verifying scorer on gold fixtures ===\n")
    failures = []
    for tc in test_cases:
        if categories and tc.get("category") not in categories:
            continue
        result = score_debug_fixture(tc)
        if result.score != 100:
            failures.append(result)
            print(f"  FAIL  {result.test_id}: score={result.score}, details={result.details}")
        else:
            print(f"  OK    {result.test_id}")

    print()
    if failures:
        print(f"FAIL: {len(failures)} fixture(s) scored < 100. Scorer has a bug.")
        return 1
    print(f"OK: All {len(test_cases)} fixtures score 100.")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Tool eval runner for hermes-agent")
    parser.add_argument("--model", default="", help="Model ID (e.g. anthropic/claude-sonnet-4)")
    parser.add_argument("--base-url", default="", help="OpenAI-compatible base URL")
    parser.add_argument("--api-key", default="", help="API key (fallback: env vars)")
    parser.add_argument("--openrouter", action="store_true", help="Use OpenRouter (sets base-url, reads OPENROUTER_API_KEY)")
    parser.add_argument("--rate-limit", type=float, default=3.0, help="Seconds between requests (default: 3)")
    parser.add_argument("--hermes-context", action="store_true", help="Inject hermes_context.md into system prompt")
    parser.add_argument("--tool-primer", action="store_true", help="Inject tool_primer.md into system prompt")
    parser.add_argument("--vision", action="store_true", help="Include vision_analyze tests (opt-in)")
    parser.add_argument("--image", action="store_true", help="Include image_generate tests (opt-in)")
    parser.add_argument("--tts", action="store_true", help="Include text_to_speech tests (opt-in)")
    parser.add_argument("--debug", action="store_true", help="Debug mode: verify scorer on gold fixtures, no API calls")
    parser.add_argument("--category", default="", help="Run only tests in this category")
    parser.add_argument("--test-id", default="", help="Run a single test by ID")
    parser.add_argument("--verbose", action="store_true", help="Print per-test scoring details")
    parser.add_argument("--json-output", default="", help="Write results JSON to this file path")
    args = parser.parse_args()

    test_cases = _load_test_cases(TOOL_EVAL_DIR / "test_cases.json")

    # Filter opt-in categories (off by default for text-only model compat)
    excluded_by_default = set()
    if not args.vision:
        excluded_by_default.add("vision")
    if not args.image:
        excluded_by_default.add("image")
    if not args.tts:
        excluded_by_default.add("tts")

    test_cases = [tc for tc in test_cases if tc.get("category") not in excluded_by_default]

    # Filter by --category or --test-id
    if args.test_id:
        test_cases = [tc for tc in test_cases if tc["id"] == args.test_id]
    elif args.category:
        test_cases = [tc for tc in test_cases if tc.get("category") == args.category]

    if not test_cases:
        print("No test cases matched the given filters.")
        sys.exit(1)

    # Debug mode — no API calls needed
    if args.debug:
        categories = {args.category} if args.category else None
        sys.exit(_run_debug_mode(test_cases, categories))

    # Require model for live runs
    if not args.model:
        parser.error("--model is required (unless using --debug)")

    # Configure OpenAI client
    base_url = args.base_url
    api_key = args.api_key

    if args.openrouter:
        base_url = "https://openrouter.ai/api/v1"
        api_key = api_key or os.environ.get("OPENROUTER_API_KEY", "")

    if not api_key:
        api_key = (
            os.environ.get("OPENAI_API_KEY")
            or os.environ.get("ANTHROPIC_API_KEY")
            or "no-key"
        )

    client_kwargs: dict = {"api_key": api_key}
    if base_url:
        client_kwargs["base_url"] = base_url
    client = openai.OpenAI(**client_kwargs)

    # Load injection files
    hermes_context = _load_injection_file(TOOL_EVAL_DIR / "hermes_context.md") if args.hermes_context else ""
    tool_primer_text = _load_injection_file(TOOL_EVAL_DIR / "tool_primer.md") if args.tool_primer else ""

    # Run
    print(f"\nRunning {len(test_cases)} tests against {args.model}\n")
    results: list = []

    for tc in test_cases:
        result = _run_single_test(
            client=client,
            test_case=tc,
            model=args.model,
            hermes_context=hermes_context,
            tool_primer=tool_primer_text,
            rate_limit=args.rate_limit,
        )
        results.append(result)
        _print_result(result, verbose=args.verbose)
        time.sleep(args.rate_limit)

    _print_summary(results, args.model)

    if args.json_output:
        out_path = Path(args.json_output)
        out_path.write_text(json.dumps([r.to_dict() for r in results], indent=2))
        print(f"Results written to {out_path}")


if __name__ == "__main__":
    main()
