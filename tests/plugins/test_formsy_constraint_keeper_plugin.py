from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def _load_plugin_module():
    root = Path(__file__).resolve().parents[2]
    init_path = root / "plugins" / "formsy-constraint-keeper" / "__init__.py"
    spec = importlib.util.spec_from_file_location("formsy_constraint_keeper_test_plugin", init_path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class FakeContext:
    def __init__(self):
        self.hooks = []
        self.tools = []

    def register_hook(self, name, callback):
        self.hooks.append((name, callback))

    def register_tool(self, **kwargs):
        self.tools.append(kwargs)


def test_register_adds_expected_hooks_and_tools():
    module = _load_plugin_module()
    ctx = FakeContext()

    module.register(ctx)

    assert [name for name, _callback in ctx.hooks] == [
        "on_session_start",
        "pre_llm_call",
        "post_llm_call",
        "pre_tool_call",
        "post_tool_call",
        "transform_tool_result",
        "on_session_end",
        "on_session_reset",
    ]
    assert [tool["name"] for tool in ctx.tools] == [
        "formsy_constraint_status",
        "formsy_recover",
        "formsy_verify_completion",
        "formsy_request_human_review",
    ]
    assert "formsy_human_override" not in [tool["name"] for tool in ctx.tools]


def test_pre_tool_call_wraps_block_message_as_hermes_directive(monkeypatch):
    module = _load_plugin_module()

    class Coordinator:
        def pre_tool_call_block_message(self, *args, **kwargs):
            return "blocked by verifier"

    monkeypatch.setattr(module, "_coordinator", Coordinator())

    assert module._on_pre_tool_call(tool_name="terminal", args={}, session_id="s") == {
        "action": "block",
        "message": "blocked by verifier",
    }


def test_post_llm_call_returns_final_response_directive(monkeypatch):
    module = _load_plugin_module()

    class Coordinator:
        def post_llm_call_final_response_directive(self, *args, **kwargs):
            return {
                "action": "replace_final_response",
                "final_response": "Finish Gate was not called.",
            }

    monkeypatch.setattr(module, "_coordinator", Coordinator())

    assert module._on_post_llm_call(
        session_id="s",
        assistant_response="Completion Verifier: ACCEPT_DONE",
    ) == {
        "action": "replace_final_response",
        "final_response": "Finish Gate was not called.",
    }


def test_tool_hooks_do_not_forward_hermes_task_id_as_formsy_task_id(monkeypatch):
    module = _load_plugin_module()
    calls = []

    class Coordinator:
        def pre_tool_call_block_message(self, *args, **kwargs):
            calls.append(("pre", kwargs))
            return None

        def observe_tool_result(self, *args, **kwargs):
            calls.append(("post", kwargs))

        def transform_tool_result(self, *args, **kwargs):
            calls.append(("transform", kwargs))
            return None

    monkeypatch.setattr(module, "_coordinator", Coordinator())

    module._on_pre_tool_call(
        tool_name="terminal",
        args={},
        session_id="sess-1",
        task_id="hermes-internal-task",
    )
    module._on_post_tool_call(
        tool_name="terminal",
        args={},
        result="ok",
        session_id="sess-1",
        task_id="hermes-internal-task",
    )
    module._on_transform_tool_result(
        tool_name="terminal",
        args={},
        result="ok",
        session_id="sess-1",
        task_id="hermes-internal-task",
    )

    assert calls == [
        ("pre", {"session_id": "sess-1"}),
        ("post", {"session_id": "sess-1"}),
        ("transform", {"session_id": "sess-1"}),
    ]


def test_request_human_review_tool_routes_through_coordinator(monkeypatch):
    module = _load_plugin_module()
    calls = []

    class Coordinator:
        def request_human_review(
            self, *, reason: str, session_id: str = "", task_id: str = ""
        ):
            calls.append(
                {"reason": reason, "session_id": session_id, "task_id": task_id}
            )
            return {"decision": "NEED_HUMAN_REVIEW"}

    monkeypatch.setattr(module, "_coordinator", Coordinator())

    result = module._tool_request_human_review(
        {"reason": "Focused validation cannot be produced safely."},
        session_id="sess-1",
    )

    assert '"NEED_HUMAN_REVIEW"' in result
    assert calls == [
        {
            "reason": "Focused validation cannot be produced safely.",
            "session_id": "sess-1",
            "task_id": "",
        }
    ]


def test_transform_tool_result_returns_only_string_replacements(monkeypatch):
    module = _load_plugin_module()

    class Coordinator:
        def transform_tool_result(self, *args, **kwargs):
            return "rewritten"

    monkeypatch.setattr(module, "_coordinator", Coordinator())

    assert module._on_transform_tool_result(
        tool_name="terminal",
        args={},
        result="original",
        session_id="s",
    ) == "rewritten"


def test_pre_llm_call_returns_recovery_context(monkeypatch):
    module = _load_plugin_module()

    class Coordinator:
        def on_user_turn(self, *args, **kwargs):
            return None

        def pre_llm_call_context(self, *args, **kwargs):
            return {"context": "short recovery reminder"}

    monkeypatch.setattr(module, "_coordinator", Coordinator())

    assert module._on_pre_llm_call(session_id="s", user_message="continue") == {
        "context": "short recovery reminder"
    }
