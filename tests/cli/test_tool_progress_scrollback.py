"""Tests for stacked tool progress scrollback lines in the CLI TUI.

When tool_progress_mode is "all" or "new", _on_tool_progress should print
persistent lines to scrollback on tool.completed, restoring the stacked
tool history that was lost when the TUI switched to a single-line spinner.
"""

import os
import sys
import importlib
import json
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Module-level reference to the cli module (set by _make_cli on first call)
_cli_mod = None


def _make_cli(tool_progress="all"):
    """Create a HermesCLI instance with minimal mocking."""
    global _cli_mod
    _clean_config = {
        "model": {
            "default": "anthropic/claude-opus-4.6",
            "base_url": "https://openrouter.ai/api/v1",
            "provider": "auto",
        },
        "display": {"compact": False, "tool_progress": tool_progress},
        "agent": {},
        "terminal": {"env_type": "local"},
    }
    clean_env = {"LLM_MODEL": "", "HERMES_MAX_ITERATIONS": ""}
    prompt_toolkit_stubs = {
        "prompt_toolkit": MagicMock(),
        "prompt_toolkit.history": MagicMock(),
        "prompt_toolkit.styles": MagicMock(),
        "prompt_toolkit.patch_stdout": MagicMock(),
        "prompt_toolkit.application": MagicMock(),
        "prompt_toolkit.layout": MagicMock(),
        "prompt_toolkit.layout.processors": MagicMock(),
        "prompt_toolkit.filters": MagicMock(),
        "prompt_toolkit.layout.dimension": MagicMock(),
        "prompt_toolkit.layout.menus": MagicMock(),
        "prompt_toolkit.widgets": MagicMock(),
        "prompt_toolkit.key_binding": MagicMock(),
        "prompt_toolkit.completion": MagicMock(),
        "prompt_toolkit.formatted_text": MagicMock(),
        "prompt_toolkit.auto_suggest": MagicMock(),
    }
    with patch.dict(sys.modules, prompt_toolkit_stubs), \
         patch.dict("os.environ", clean_env, clear=False):
        import cli as mod
        mod = importlib.reload(mod)
        _cli_mod = mod
        with patch.object(mod, "get_tool_definitions", return_value=[]), \
             patch.dict(mod.__dict__, {"CLI_CONFIG": _clean_config}):
            return mod.HermesCLI()


def _formsy_cards(
    name,
    args,
    result,
    *,
    tool_call_id="tool-1",
    fs_console_base_url="",
    fallback_code_plan_url="",
):
    from hermes_cli.formsy_status import formsy_tool_status_cards_from_tool_result

    return formsy_tool_status_cards_from_tool_result(
        name,
        args,
        result,
        fs_console_base_url=fs_console_base_url,
        fallback_code_plan_url=fallback_code_plan_url,
        tool_call_id=tool_call_id,
    )


def _drain_from(cards_by_tool_call_id):
    return lambda tool_call_id: cards_by_tool_call_id.get(tool_call_id, [])


class TestToolProgressScrollback:
    """Stacked scrollback lines for 'all' and 'new' modes."""

    def test_all_mode_prints_scrollback_on_completed(self):
        """In 'all' mode, tool.completed prints a stacked line."""
        cli = _make_cli(tool_progress="all")
        # Simulate tool.started
        cli._on_tool_progress("tool.started", "terminal", "git log", {"command": "git log"})
        # Simulate tool.completed
        with patch.object(_cli_mod, "_cprint") as mock_print:
            cli._on_tool_progress("tool.completed", "terminal", None, None, duration=1.5, is_error=False)

        mock_print.assert_called_once()
        line = mock_print.call_args[0][0]
        # Should contain tool info (the cute message format has "git log" for terminal)
        assert "git log" in line or "$" in line

    def test_all_mode_prints_every_call(self):
        """In 'all' mode, consecutive calls to the same tool each get a line."""
        cli = _make_cli(tool_progress="all")
        with patch.object(_cli_mod, "_cprint") as mock_print:
            # First call
            cli._on_tool_progress("tool.started", "read_file", "cli.py", {"path": "cli.py"})
            cli._on_tool_progress("tool.completed", "read_file", None, None, duration=0.1, is_error=False)
            # Second call (same tool)
            cli._on_tool_progress("tool.started", "read_file", "run_agent.py", {"path": "run_agent.py"})
            cli._on_tool_progress("tool.completed", "read_file", None, None, duration=0.2, is_error=False)

        assert mock_print.call_count == 2

    def test_new_mode_skips_consecutive_repeats(self):
        """In 'new' mode, consecutive calls to the same tool only print once."""
        cli = _make_cli(tool_progress="new")
        with patch.object(_cli_mod, "_cprint") as mock_print:
            cli._on_tool_progress("tool.started", "read_file", "cli.py", {"path": "cli.py"})
            cli._on_tool_progress("tool.completed", "read_file", None, None, duration=0.1, is_error=False)
            cli._on_tool_progress("tool.started", "read_file", "run_agent.py", {"path": "run_agent.py"})
            cli._on_tool_progress("tool.completed", "read_file", None, None, duration=0.2, is_error=False)

        assert mock_print.call_count == 1  # Only the first read_file

    def test_new_mode_prints_when_tool_changes(self):
        """In 'new' mode, a different tool name triggers a new line."""
        cli = _make_cli(tool_progress="new")
        with patch.object(_cli_mod, "_cprint") as mock_print:
            cli._on_tool_progress("tool.started", "read_file", "cli.py", {"path": "cli.py"})
            cli._on_tool_progress("tool.completed", "read_file", None, None, duration=0.1, is_error=False)
            cli._on_tool_progress("tool.started", "search_files", "pattern", {"pattern": "test"})
            cli._on_tool_progress("tool.completed", "search_files", None, None, duration=0.3, is_error=False)
            cli._on_tool_progress("tool.started", "read_file", "run_agent.py", {"path": "run_agent.py"})
            cli._on_tool_progress("tool.completed", "read_file", None, None, duration=0.2, is_error=False)

        # read_file, search_files, read_file (3rd prints because search_files broke the streak)
        assert mock_print.call_count == 3

    def test_off_mode_no_scrollback(self):
        """In 'off' mode, no stacked lines are printed."""
        cli = _make_cli(tool_progress="off")
        with patch.object(_cli_mod, "_cprint") as mock_print:
            cli._on_tool_progress("tool.started", "terminal", "ls", {"command": "ls"})
            cli._on_tool_progress("tool.completed", "terminal", None, None, duration=0.5, is_error=False)

        mock_print.assert_not_called()

    def test_error_suffix_on_failed_tool(self):
        """When is_error=True, the stacked line includes [error]."""
        cli = _make_cli(tool_progress="all")
        cli._on_tool_progress("tool.started", "terminal", "bad cmd", {"command": "bad cmd"})
        with patch.object(_cli_mod, "_cprint") as mock_print:
            cli._on_tool_progress("tool.completed", "terminal", None, None, duration=0.5, is_error=True)

        line = mock_print.call_args[0][0]
        assert "[error]" in line

    def test_spinner_still_updates_on_started(self):
        """tool.started still updates the spinner text for live display."""
        cli = _make_cli(tool_progress="all")
        cli._on_tool_progress("tool.started", "terminal", "git status", {"command": "git status"})
        assert "git status" in cli._spinner_text

    def test_spinner_timer_clears_on_completed(self):
        """tool.completed still clears the tool timer."""
        cli = _make_cli(tool_progress="all")
        cli._on_tool_progress("tool.started", "terminal", "git status", {"command": "git status"})
        assert cli._tool_start_time > 0
        with patch.object(_cli_mod, "_cprint"):
            cli._on_tool_progress("tool.completed", "terminal", None, None, duration=0.5, is_error=False)
        assert cli._tool_start_time == 0.0

    def test_concurrent_tools_produce_stacked_lines(self):
        """Multiple tool.started followed by multiple tool.completed all produce lines."""
        cli = _make_cli(tool_progress="all")
        with patch.object(_cli_mod, "_cprint") as mock_print:
            # All start first (concurrent pattern)
            cli._on_tool_progress("tool.started", "web_search", "query 1", {"query": "test 1"})
            cli._on_tool_progress("tool.started", "web_search", "query 2", {"query": "test 2"})
            # All complete
            cli._on_tool_progress("tool.completed", "web_search", None, None, duration=1.0, is_error=False)
            cli._on_tool_progress("tool.completed", "web_search", None, None, duration=1.5, is_error=False)

        assert mock_print.call_count == 2

    def test_verbose_mode_no_duplicate_scrollback(self):
        """In 'verbose' mode, scrollback lines are NOT printed (run_agent handles verbose output)."""
        cli = _make_cli(tool_progress="verbose")
        with patch.object(_cli_mod, "_cprint") as mock_print:
            cli._on_tool_progress("tool.started", "terminal", "ls", {"command": "ls"})
            cli._on_tool_progress("tool.completed", "terminal", None, None, duration=0.5, is_error=False)

        mock_print.assert_not_called()

    def test_pending_info_stores_on_started(self):
        """tool.started stores args for later use by tool.completed."""
        cli = _make_cli(tool_progress="all")
        cli._on_tool_progress("tool.started", "terminal", "ls", {"command": "ls"})
        assert "terminal" in cli._pending_tool_info
        assert len(cli._pending_tool_info["terminal"]) == 1
        assert cli._pending_tool_info["terminal"][0] == {"command": "ls"}

    def test_pending_info_consumed_on_completed(self):
        """tool.completed consumes stored args (FIFO for concurrent)."""
        cli = _make_cli(tool_progress="all")
        cli._on_tool_progress("tool.started", "terminal", "ls", {"command": "ls"})
        cli._on_tool_progress("tool.started", "terminal", "pwd", {"command": "pwd"})
        assert len(cli._pending_tool_info["terminal"]) == 2
        with patch.object(_cli_mod, "_cprint"):
            cli._on_tool_progress("tool.completed", "terminal", None, None, duration=0.1, is_error=False)
        # First entry consumed, second remains
        assert len(cli._pending_tool_info.get("terminal", [])) == 1
        assert cli._pending_tool_info["terminal"][0] == {"command": "pwd"}

    def test_cli_flush_prints_formsy_context_status(self):
        """FormSy context_search completion is printed from the CLI thread flush."""
        cli = _make_cli(tool_progress="all")
        result = json.dumps({
            "ok": True,
            "query": "PlayIterator public states",
            "coverage": "partial",
            "memory_status": "hit",
            "accepted_targets": ["lib/ansible/executor/play_iterator.py"],
        })
        cards = _formsy_cards(
            "context_search",
            {"query": "PlayIterator public states"},
            result,
            tool_call_id="tool-1",
        )

        with patch.object(_cli_mod, "drain_tool_status_cards", return_value=cards), \
             patch.object(_cli_mod, "_cprint_inline") as mock_print:
            cli._on_tool_complete(
                "tool-1",
                "context_search",
                {"query": "PlayIterator public states"},
                result,
            )
            cli._flush_tool_status_cards()

        printed = "\n".join(str(call.args[0]) for call in mock_print.call_args_list)
        assert "╭─ ◆ FormSy Context" in printed
        assert "[FormSy] Context Pack ready" in printed
        assert "Primary target: lib/ansible/executor/play_iterator.py" in printed
        assert "╰" in printed

    def test_cli_flush_prints_tool_status_card(self):
        """Generic ToolStatusCard entries are rendered from the CLI thread flush."""
        cli = _make_cli(tool_progress="all")
        cards = [
            {
                "source": "test_runner",
                "kind": "summary",
                "title": "Test Runner",
                "body": ["3 passed", "0 failed"],
                "severity": "success",
                "dedupe_key": "test-runner:summary:1",
                "link": {
                    "label": "Code plan",
                    "url": "http://localhost:3000/code-plans/cp_test",
                },
            }
        ]

        with patch.object(_cli_mod, "drain_tool_status_cards", return_value=cards), \
             patch.object(_cli_mod, "_cprint_inline") as mock_print:
            cli._on_tool_complete("tool-generic", "pytest", {}, "{}")
            assert len(cli._pending_tool_status_cards) == 1
            cli._flush_tool_status_cards()

        printed = "\n".join(str(call.args[0]) for call in mock_print.call_args_list)
        assert "╭─ ◆ Test Runner" in printed
        assert "3 passed" in printed
        assert "0 failed" in printed
        assert "Code plan: http://localhost:3000/code-plans/cp_test" in printed

    def test_cli_flush_keeps_only_latest_tool_status_card_by_group(self):
        """Generic latest cards collapse by group_key without FormSy-specific logic."""
        cli = _make_cli(tool_progress="all")
        old_card = {
            "source": "review",
            "kind": "gate",
            "title": "Review Gate",
            "body": ["Reason: old"],
            "severity": "warning",
            "dedupe_key": "review:gate:old",
            "group_key": "review:gate",
            "replace_policy": "latest",
        }
        new_card = {
            "source": "review",
            "kind": "gate",
            "title": "Review Gate",
            "body": ["Reason: new"],
            "severity": "warning",
            "dedupe_key": "review:gate:new",
            "group_key": "review:gate",
            "replace_policy": "latest",
        }

        def _drain(tool_call_id):
            return {"tool-old": [old_card], "tool-new": [new_card]}.get(tool_call_id, [])

        with patch.object(_cli_mod, "drain_tool_status_cards", side_effect=_drain), \
             patch.object(_cli_mod, "_cprint_inline") as mock_print:
            cli._on_tool_complete("tool-old", "review_tool", {}, "{}")
            cli._on_tool_complete("tool-new", "review_tool", {}, "{}")
            assert len(cli._pending_tool_status_cards) == 2
            cli._flush_tool_status_cards()

        printed = "\n".join(str(call.args[0]) for call in mock_print.call_args_list)
        assert "Reason: new" in printed
        assert "Reason: old" not in printed

    def test_cli_flush_prints_formsy_status_synchronously(self):
        """FormSy status flush must not use the async cross-thread print helper."""
        cli = _make_cli(tool_progress="all")
        result = json.dumps({
            "ok": True,
            "query": "PlayIterator public states",
            "coverage": "partial",
            "memory_status": "hit",
            "accepted_targets": ["lib/ansible/executor/play_iterator.py"],
        })
        cards = _formsy_cards(
            "context_search",
            {"query": "PlayIterator public states"},
            result,
            tool_call_id="tool-1",
        )

        with patch.object(_cli_mod, "drain_tool_status_cards", return_value=cards), \
             patch.object(_cli_mod, "_cprint") as mock_cprint, \
             patch.object(_cli_mod, "_pt_print") as mock_pt_print:
            cli._on_tool_complete(
                "tool-1",
                "context_search",
                {"query": "PlayIterator public states"},
                result,
            )
            cli._flush_tool_status_cards()

        mock_cprint.assert_not_called()
        assert mock_pt_print.call_count >= 1

    def test_cli_flush_merges_context_and_verified_recipe_in_one_card(self):
        """A context_search recipe hit is rendered as one FormSy Context card."""
        cli = _make_cli(tool_progress="all")
        result = json.dumps({
            "ok": True,
            "query": "PlayIterator public states",
            "coverage": "partial",
            "memory_status": "hit",
            "accepted_targets": ["lib/ansible/executor/play_iterator.py"],
            "verified_solution_recipes": [{
                "primary_edit_files": ["lib/ansible/executor/play_iterator.py"],
            }],
        })
        cards = _formsy_cards(
            "context_search",
            {"query": "PlayIterator public states"},
            result,
            tool_call_id="tool-1",
        )

        with patch.object(_cli_mod, "drain_tool_status_cards", return_value=cards), \
             patch.object(_cli_mod, "_cprint_inline") as mock_print:
            cli._on_tool_complete(
                "tool-1",
                "context_search",
                {"query": "PlayIterator public states"},
                result,
            )
            cli._flush_tool_status_cards()

        assert mock_print.call_count == 1
        printed = str(mock_print.call_args.args[0])
        assert printed.count("╭─ ◆ FormSy Context") == 1
        assert "[FormSy] Context Pack ready" in printed
        assert "[FormSy] Verified recipe available" in printed

    def test_cli_tool_complete_logs_formsy_projection_count(self):
        """FormSy status projection is observable even if terminal rendering hides it."""
        cli = _make_cli(tool_progress="all")
        result = json.dumps({
            "ok": True,
            "query": "PlayIterator public states",
            "coverage": "partial",
            "memory_status": "hit",
            "accepted_targets": ["lib/ansible/executor/play_iterator.py"],
        })
        cards = _formsy_cards(
            "context_search",
            {"query": "PlayIterator public states"},
            result,
            tool_call_id="tool-1",
        )

        with patch.object(_cli_mod, "drain_tool_status_cards", return_value=cards), \
             patch.object(_cli_mod, "_cprint_inline"), \
             patch.object(_cli_mod.logger, "info") as mock_info:
            cli._on_tool_complete(
                "tool-1",
                "context_search",
                {"query": "PlayIterator public states"},
                result,
            )

        assert any(
            call.args[:3] == (
                "Tool status card projection: tool=%s cards=%d",
                "context_search",
                1,
            )
            for call in mock_info.call_args_list
        )

    def test_cli_flush_prints_formsy_finish_gate_status(self):
        """Completion Verifier completion is printed from the CLI thread flush."""
        cli = _make_cli(tool_progress="all")
        result = json.dumps({
            "decision": "ACCEPT_DONE",
            "protocol": {
                "summary": "Completion proof satisfies P0 contracts.",
                "gate_decision": "ACCEPT_DONE",
            },
        })
        cards = _formsy_cards(
            "formsy_verify_completion",
            {},
            result,
            tool_call_id="tool-2",
        )

        with patch.object(_cli_mod, "drain_tool_status_cards", return_value=cards), \
             patch.object(_cli_mod, "_cprint_inline") as mock_print:
            cli._on_tool_complete(
                "tool-2",
                "formsy_verify_completion",
                {},
                result,
            )
            cli._flush_tool_status_cards()

        printed = "\n".join(str(call.args[0]) for call in mock_print.call_args_list)
        assert "╭─ ◆ FormSy Finish Gate" in printed
        assert "Decision: ACCEPT_DONE" in printed
        assert "Evidence: Completion proof satisfies P0 contracts." in printed
        assert "╰" in printed

    def test_cli_flush_prints_formsy_finish_gate_code_plan_link(self):
        """Finish Gate cards include the fs-console code plan link when configured."""
        cli = _make_cli(tool_progress="all")
        result = json.dumps({
            "decision": "NEED_MORE_VALIDATION",
            "code_plan_id": "cp_9c00c34d458e2e47",
            "protocol": {
                "summary": "Completion proof is incomplete.",
                "gate_decision": "NEED_MORE_VALIDATION",
            },
        })
        cards = _formsy_cards(
            "formsy_verify_completion",
            {},
            result,
            tool_call_id="tool-3",
            fs_console_base_url="http://localhost:5173",
        )

        with patch.object(_cli_mod, "drain_tool_status_cards", return_value=cards), \
             patch.object(_cli_mod, "_cprint_inline") as mock_print:
            cli._on_tool_complete(
                "tool-3",
                "formsy_verify_completion",
                {},
                result,
            )
            cli._flush_tool_status_cards()

        printed = "\n".join(str(call.args[0]) for call in mock_print.call_args_list)
        assert "Task Workflow: http://localhost:5173/code-plans/cp_9c00c34d458e2e47" in printed

    def test_cli_finish_gate_inherits_code_plan_link_from_context_search(self):
        """Finish Gate cards inherit the latest context_search code plan when verify lacks one."""
        cli = _make_cli(tool_progress="all")
        context_result = json.dumps({
            "ok": True,
            "query": "Request.open gzip Content-Encoding",
            "coverage": "partial",
            "accepted_targets": ["lib/ansible/module_utils/urls.py"],
            "guidance": {
                "code_plan_review": {
                    "code_plan_id": "cp_52359f4a1cb3e765",
                    "url": "http://localhost:3000/code-plans/cp_52359f4a1cb3e765",
                }
            },
        })
        finish_result = json.dumps({
            "decision": "NEED_MORE_VALIDATION",
            "protocol": {
                "summary": "Public interface evidence is incomplete.",
                "gate_decision": "NEED_MORE_VALIDATION",
            },
        })
        context_cards = _formsy_cards(
            "context_search",
            {},
            context_result,
            tool_call_id="tool-context",
            fs_console_base_url="http://localhost:5173",
        )
        finish_cards = _formsy_cards(
            "formsy_verify_completion",
            {},
            finish_result,
            tool_call_id="tool-finish",
            fallback_code_plan_url="http://localhost:5173/code-plans/cp_52359f4a1cb3e765",
        )

        with patch.object(_cli_mod, "drain_tool_status_cards", side_effect=_drain_from({
            "tool-context": context_cards,
            "tool-finish": finish_cards,
        })), patch.object(_cli_mod, "_cprint_inline") as mock_print:
            cli._on_tool_complete("tool-context", "context_search", {}, context_result)
            cli._on_tool_complete("tool-finish", "formsy_verify_completion", {}, finish_result)
            cli._flush_tool_status_cards()

        printed = "\n".join(str(call.args[0]) for call in mock_print.call_args_list)
        assert "Task Workflow: http://localhost:5173/code-plans/cp_52359f4a1cb3e765" in printed

    def test_cli_flush_keeps_only_latest_formsy_finish_gate(self):
        """A retry loop should not print stale Finish Gate cards with old code plan URLs."""
        cli = _make_cli(tool_progress="all")
        first_context = json.dumps({
            "ok": True,
            "query": "Request.open gzip Content-Encoding",
            "coverage": "partial",
            "accepted_targets": ["lib/ansible/module_utils/urls.py"],
            "guidance": {"code_plan_review": {"code_plan_id": "cp_first"}},
        })
        second_context = json.dumps({
            "ok": True,
            "query": "Request.open gzip tests",
            "coverage": "partial",
            "accepted_targets": ["lib/ansible/module_utils/urls.py"],
            "guidance": {"code_plan_review": {"code_plan_id": "cp_second"}},
        })
        first_finish = json.dumps({
            "decision": "NEED_MORE_VALIDATION",
            "protocol": {
                "summary": "Search coverage is insufficient.",
                "gate_decision": "NEED_MORE_VALIDATION",
            },
        })
        second_finish = json.dumps({
            "decision": "NEED_MORE_VALIDATION",
            "protocol": {
                "summary": "Public interface evidence is incomplete.",
                "gate_decision": "NEED_MORE_VALIDATION",
            },
        })
        cards_by_tool = {
            "tool-context-1": _formsy_cards(
                "context_search",
                {},
                first_context,
                tool_call_id="tool-context-1",
                fs_console_base_url="http://localhost:5173",
            ),
            "tool-finish-1": _formsy_cards(
                "formsy_verify_completion",
                {},
                first_finish,
                tool_call_id="tool-finish-1",
                fallback_code_plan_url="http://localhost:5173/code-plans/cp_first",
            ),
            "tool-context-2": _formsy_cards(
                "context_search",
                {},
                second_context,
                tool_call_id="tool-context-2",
                fs_console_base_url="http://localhost:5173",
            ),
            "tool-finish-2": _formsy_cards(
                "formsy_verify_completion",
                {},
                second_finish,
                tool_call_id="tool-finish-2",
                fallback_code_plan_url="http://localhost:5173/code-plans/cp_second",
            ),
        }

        with patch.object(_cli_mod, "drain_tool_status_cards", side_effect=_drain_from(cards_by_tool)), \
             patch.object(_cli_mod, "_cprint_inline") as mock_print:
            cli._on_tool_complete("tool-context-1", "context_search", {}, first_context)
            cli._on_tool_complete("tool-finish-1", "formsy_verify_completion", {}, first_finish)
            cli._on_tool_complete("tool-context-2", "context_search", {}, second_context)
            cli._on_tool_complete("tool-finish-2", "formsy_verify_completion", {}, second_finish)
            cli._flush_tool_status_cards()

        printed = "\n".join(str(call.args[0]) for call in mock_print.call_args_list)
        assert "Task Workflow: http://localhost:5173/code-plans/cp_second" in printed
        assert "Task Workflow: http://localhost:5173/code-plans/cp_first" not in printed
        assert "Reason: Public interface evidence is incomplete." in printed
        assert "Reason: Search coverage is insufficient." not in printed
