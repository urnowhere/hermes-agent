# Tool Eval Rebuild — Design Spec

**Date:** 2026-04-13  
**Status:** Approved

## Overview

Rebuild the `tool_eval/` harness that was lost (never committed). The harness evaluates OpenAI-compatible models on tool-calling quality to determine which models are suitable as hermes-agent backends. All scoring is fully objective (structural/parsing-based — no LLM-as-judge).

---

## Files

```
tool_eval/
  scorer.py           # Scoring engine
  run_eval.py         # CLI runner
  test_cases.json     # ~51 test cases
  hermes_context.md   # Operational context (injected via --hermes-context)
  tool_primer.md      # OpenAI tool-call format cheatsheet (injected via --tool-primer)
  .env                # Optional: OPENROUTER_API_KEY (gitignored)
  tests/              # Empty dir (reserved)
```

---

## scorer.py

Reconstructed from cached bytecode (`__pycache__/scorer.cpython-312.pyc`).

### Data model

```python
@dataclass
class TestResult:
    test_id: str
    category: str
    description: str
    score: int           # 0–100
    passed: bool
    details: Dict[str, Any]
    model_calls: int
    raw_response: Dict
    error: Optional[str]
    retries: int
    is_infra_error: bool
```

### Scoring

- **40 pts** — correct tool name(s) called
- **60 pts** — argument quality (averaged across active criteria)

### Test criteria (in `expected` block of each test case)

| Key | Meaning |
|---|---|
| `no_tool_calls` | Assert model returns text, not a tool call |
| `has_text` | Assert model returns any textual content |
| `text_contains` | Assert response text contains substring |
| `unexpected_tool_calls` | Assert model does NOT call listed tools |
| `text_no_call` | Assert text response with no tool call |
| `function_name` / `function_names` | Assert exact tool name(s) called |
| `function_count` | Assert exact number of tool calls |
| `function_counts_at_least` | Assert at least N calls |
| `arguments` | Per-tool arg scoring block |

### Argument scoring criteria

| Key | Meaning |
|---|---|
| `required_args` | These arg keys must be present |
| `arg_values` | These arg key→value pairs must match (type-aware) |
| `optional_args` | Presence earns credit but absence doesn't penalize |
| `arg_substring_checks` | Arg value must contain substring |
| `arg_types` | Arg values must match type (str/int/bool/float/list/dict) |
| `no_extra_params` | Model must not hallucinate extra args |
| `list_field_check` | List field must exist with minimum item count |

### Helper functions

- `_safe_first_choice(raw)` — safely extracts `choices[0]`, returns None on null/empty/malformed
- `_extract_tool_calls(raw)` — extracts tool call list; handles OpenAI shape and raw dict shape; guards against null `choices` (rate-limit error pattern)
- `_has_text_content(raw)` — bool, whether model produced text (not just tool calls)
- `_text_content(raw)` — extracts text content string
- `_check_arg_values(actual, expected)` — type-aware value comparison (str lowercased)
- `_check_type_compliance(actual, expected)` — checks arg types match
- `_check_no_extra_params(actual, schema)` — detects hallucinated args
- `_check_list_field(actual, field, min_items)` — list field length check
- `_is_infra_error(raw)` — detects rate-limit/502 responses (`{"choices": null, "error": {...}}`)
- `score_test(test_case, raw_response)` → `TestResult`
- `_score_single_args_ratio(actual_args, expected_args_spec)` → `(float, details)`
- `score_debug_fixture(test_case)` — feeds `expected` back through scorer; asserts 100

---

## run_eval.py

CLI runner using `python-fire` or `argparse`.

### Flags

| Flag | Default | Description |
|---|---|---|
| `--model` | required | Model ID (e.g. `anthropic/claude-sonnet-4`) |
| `--base-url` | — | OpenAI-compatible base URL |
| `--api-key` | — | API key (fallback: env) |
| `--openrouter` | False | Shortcut: sets base_url + reads OPENROUTER_API_KEY |
| `--rate-limit` | 3 | Seconds between requests |
| `--hermes-context` | False | Inject hermes_context.md into system prompt |
| `--tool-primer` | False | Inject tool_primer.md into system prompt |
| `--vision` | False | Include vision_analyze tests |
| `--image` | False | Include image_generate tests |
| `--tts` | False | Include text_to_speech tests |
| `--debug` | False | Feed gold fixtures through scorer; assert all score 100; exit non-zero on failure |
| `--category` | — | Run only tests matching this category |
| `--test-id` | — | Run a single test by ID |

### .env loading

Auto-loads in order: `tool_eval/.env`, repo root `.env`, `~/.env`.

### Output

- Per-test: test_id, score, passed/failed, details
- Summary: total score, pass rate, category breakdown
- JSON output option (`--json`)

---

## test_cases.json

~51 test cases. Each test case:

```json
{
  "id": "todo_create_tasks",
  "category": "todo",
  "description": "Create multiple todo items from a self-contained prompt",
  "prompt": "...",
  "available_tools": ["todo"],
  "expected": {
    "function_name": "todo",
    "arguments": {
      "required_args": ["action", "todos"],
      "arg_values": {"action": "create"},
      "list_field_check": {"field": "todos", "min_items": 3}
    }
  }
}
```

### Category distribution

| Category | Tools | Tests |
|---|---|---|
| file | read_file, write_file, patch (replace), patch (patch mode), search_files | 10 |
| terminal | terminal | 4 |
| todo | todo create/update/complete/list | 7 |
| memory | memory create/update/delete | 5 |
| web | web_search, web_extract | 6 |
| cron | cronjob create/list/delete | 5 |
| messaging | send_message | 3 |
| skills | skill_manage create/edit/delete | 3 |
| vision* | vision_analyze | 3 |
| image* | image_generate | 3 |
| tts* | text_to_speech | 2 |

`*` = opt-in via `--vision` / `--image` / `--tts`

### Known issues (from hermes_tracking.md, to keep in mind)

- `file_patch_replace` / `file_patch_mode_patch`: models tend to call `read_file` first. Tests should either include file contents in the prompt or expand `available_tools` to include `read_file` and accept the 2-call sequence.
- `todo_create_tasks` and `todo_update_and_complete`: prompts must be fully self-contained (single-shot eval has no session state).
- Rate limits on free tier: recommend `--rate-limit 5` for full runs.

---

## hermes_context.md

Operational context injected per-test into system prompt. Describes hermes-agent's personality, tool use philosophy, and response style. Allows testing whether models behave better with explicit behavioral guidance.

## tool_primer.md

OpenAI tool-call format cheatsheet injected per-test. Explains the exact JSON shape expected for tool calls. Helps models that are borderline on format compliance.

---

## Constraints

- No LLM-as-judge. Every scoring criterion must be structural/parseable.
- `--debug` must exit non-zero if any gold fixture scores < 100 (CI-friendly).
- Tests use OpenAI chat completions wire format, compatible with any OpenAI-compatible endpoint.
- No session state between tests — each prompt must be fully self-contained.
