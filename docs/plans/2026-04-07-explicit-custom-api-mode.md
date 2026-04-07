# Explicit Custom API Mode Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make Hermes honor explicitly configured `api_mode` for custom providers and preserve it across custom provider save/switch flows, without adding new heuristics.

**Architecture:** Keep runtime resolution behavior unchanged unless `api_mode` is explicitly configured. Persist explicit `api_mode` on named custom providers, sync it into the active `model` config when a named custom provider is activated, and avoid silently preserving stale transport when no explicit mode exists.

**Tech Stack:** Python, pytest, YAML-backed config persistence

---

### Task 1: Red Tests For Explicit Custom Provider API Mode

**Files:**
- Modify: `tests/test_model_provider_persistence.py`

**Step 1: Write the failing test**

Add tests that prove:
- `_save_custom_provider(..., api_mode="codex_responses")` persists the explicit mode on a new custom provider entry.
- `_model_flow_named_custom()` writes `model.api_mode` when the named provider includes an explicit `api_mode`.
- `_model_flow_named_custom()` clears stale `model.api_mode` when the named provider does not define one.

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_model_provider_persistence.py -k "custom_provider_api_mode or named_custom_provider_api_mode" -v`
Expected: FAIL because the helper does not yet accept/persist `api_mode`, and named custom activation does not synchronize it.

**Step 3: Write minimal implementation**

Update the custom provider save/switch code to persist and synchronize explicit `api_mode`.

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_model_provider_persistence.py -k "custom_provider_api_mode or named_custom_provider_api_mode" -v`
Expected: PASS

### Task 2: Runtime Resolution Coverage

**Files:**
- Modify: `tests/test_runtime_provider_resolution.py`
- Modify: `hermes_cli/runtime_provider.py`

**Step 1: Write the failing test**

Add a test showing that a named custom provider with `api_mode: codex_responses` resolves as `codex_responses` even for a localhost endpoint.

**Step 2: Run test to verify it fails or protects behavior**

Run: `pytest tests/test_runtime_provider_resolution.py -k "named_custom_provider_explicit_api_mode" -v`
Expected: PASS if current runtime already honors explicit config, otherwise FAIL and document the gap.

**Step 3: Write minimal implementation**

Only adjust runtime resolution if the red test reveals a real gap. Do not add new heuristics.

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_runtime_provider_resolution.py -k "named_custom_provider_explicit_api_mode" -v`
Expected: PASS

### Task 3: End-to-End Regression Verification

**Files:**
- Modify: `hermes_cli/main.py`

**Step 1: Run focused regression suite**

Run:
- `pytest tests/test_model_provider_persistence.py -k "custom_provider_api_mode or named_custom_provider_api_mode" -v`
- `pytest tests/test_runtime_provider_resolution.py -k "named_custom_provider_explicit_api_mode" -v`

**Step 2: Run broader safety net**

Run:
- `pytest tests/test_model_provider_persistence.py tests/test_runtime_provider_resolution.py tests/test_cli_provider_resolution.py -q`

**Step 3: Confirm no config drift**

Verify that explicit `api_mode` survives save/switch flows and that omitted `api_mode` keeps existing behavior.
