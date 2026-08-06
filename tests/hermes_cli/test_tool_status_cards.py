from __future__ import annotations

from unittest.mock import patch


def test_queue_normalizes_and_drains_cards_by_tool_call_id():
    from hermes_cli.tool_status_cards import (
        drain_tool_status_cards,
        queue_tool_status_cards,
    )

    queue_tool_status_cards(
        [
            {
                "source": "test",
                "kind": "summary",
                "title": "Tool Summary",
                "body": ["first line", "second line"],
                "severity": "warning",
                "dedupe_key": "test:summary:1",
                "group_key": "test:summary",
                "replace_policy": "latest",
                "link": {"label": "Details", "url": "http://localhost/details/1"},
            }
        ],
        tool_call_id="call-1",
        session_id="session-1",
    )

    cards = drain_tool_status_cards("call-1")
    assert cards == [
        {
            "source": "test",
            "kind": "summary",
            "title": "Tool Summary",
            "body": ["first line", "second line"],
            "severity": "warning",
            "dedupe_key": "test:summary:1",
            "group_key": "test:summary",
            "replace_policy": "latest",
            "visibility": "developer",
            "tool_call_id": "call-1",
            "session_id": "session-1",
            "link": {"label": "Details", "url": "http://localhost/details/1"},
        }
    ]
    assert drain_tool_status_cards("call-1") == []


def test_display_event_from_card_is_compact_and_omits_body():
    from hermes_cli.tool_status_cards import display_event_from_card

    event = display_event_from_card(
        {
            "source": "test",
            "kind": "summary",
            "title": "Tool Summary",
            "body": ["this must not be persisted in display_events"],
            "severity": "info",
            "dedupe_key": "test:summary:1",
            "group_key": "test:summary",
            "tool_call_id": "call-1",
            "link": {"label": "Details", "url": "http://localhost/details/1"},
        }
    )

    assert event["version"] == 1
    assert event["tool_call_id"] == "call-1"
    assert event["source"] == "test"
    assert event["kind"] == "summary"
    assert event["title"] == "Tool Summary"
    assert event["severity"] == "info"
    assert event["link"] == {"label": "Details", "url": "http://localhost/details/1"}
    assert event["dedupe_key"] == "test:summary:1"
    assert event["group_key"] == "test:summary"
    assert event["rendered"] is False
    assert "body" not in event


def test_project_and_queue_invokes_status_card_hook_for_context_engine_result():
    """Context-engine tools must use the same display queue as registry tools."""
    from hermes_cli import tool_status_cards

    project_and_queue = getattr(
        tool_status_cards,
        "project_and_queue_tool_status_cards",
        None,
    )
    assert callable(project_and_queue)

    with patch(
        "hermes_cli.plugins.invoke_hook",
        return_value=[
            {
                "source": "formsy",
                "kind": "context_ready",
                "title": "FormSy Context",
                "body": ["Primary target: lib/ansible/modules/iptables.py"],
                "severity": "info",
                "dedupe_key": "formsy:context:call-context-1",
                "group_key": "formsy:context",
                "link": {
                    "label": "Code plan",
                    "url": "http://localhost:3000/code-plans/cp-context-1",
                },
            }
        ],
    ) as invoke_hook:
        project_and_queue(
            tool_name="context_search",
            args={"query": "iptables chain management"},
            result='{"guidance":{"fs_console":{"url":"http://localhost:3000/code-plans/cp-context-1"}}}',
            task_id="task-1",
            session_id="session-1",
            tool_call_id="call-context-1",
            duration_ms=12,
        )

    invoke_hook.assert_called_once_with(
        "project_tool_status_cards",
        tool_name="context_search",
        args={"query": "iptables chain management"},
        result='{"guidance":{"fs_console":{"url":"http://localhost:3000/code-plans/cp-context-1"}}}',
        task_id="task-1",
        session_id="session-1",
        tool_call_id="call-context-1",
        duration_ms=12,
    )
    cards = tool_status_cards.drain_tool_status_cards("call-context-1")
    assert cards[0]["title"] == "FormSy Context"
    assert cards[0]["link"] == {
        "label": "Code plan",
        "url": "http://localhost:3000/code-plans/cp-context-1",
    }
