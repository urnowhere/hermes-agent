"""Tests for outbound Microsoft Teams delivery."""

from __future__ import annotations

import httpx
import pytest

from gateway.config import GatewayConfig, Platform, PlatformConfig
from gateway.platforms.teams import TeamsAdapter, TeamsSummarySender
from tools.teams_pipeline_models import TeamsMeetingRef, TeamsMeetingSummaryPayload


def _summary_payload() -> TeamsMeetingSummaryPayload:
    return TeamsMeetingSummaryPayload(
        meeting_ref=TeamsMeetingRef(meeting_id="meeting-1"),
        title="Weekly Sync",
        summary="Reviewed launch readiness.",
        key_decisions=["Ship on Tuesday."],
        action_items=["Ada to update the runbook."],
        risks=["Legal sign-off pending."],
        confidence="high",
        confidence_notes="Primary transcript was available.",
    )


def test_gateway_config_accepts_teams_platform():
    config = GatewayConfig.from_dict(
        {
            "platforms": {
                "teams": {
                    "enabled": True,
                    "extra": {
                        "incoming_webhook_url": "https://outlook.office.com/webhook/abc",
                    },
                }
            }
        }
    )

    assert Platform.TEAMS in config.platforms
    assert Platform.TEAMS in config.get_connected_platforms()


@pytest.mark.anyio
async def test_teams_adapter_sends_incoming_webhook_summary():
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"id": "webhook-msg-1"}, request=request)

    adapter = TeamsAdapter(
        PlatformConfig(
            enabled=True,
            extra={
                "delivery_mode": "incoming_webhook",
                "incoming_webhook_url": "https://outlook.office.com/webhook/abc",
                "channel_id": "channel-1",
            },
        ),
        transport=httpx.MockTransport(handler),
    )

    result = await adapter.send_summary(_summary_payload())

    assert result.success is True
    assert result.message_id == "webhook-msg-1"
    assert len(requests) == 1
    request = requests[0]
    assert str(request.url) == "https://outlook.office.com/webhook/abc"
    body = request.content.decode("utf-8")
    assert "AdaptiveCard" in body
    assert "Weekly Sync" in body
    assert "Ship on Tuesday." in body


@pytest.mark.anyio
async def test_teams_adapter_sends_graph_reply_message():
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.headers["Authorization"] == "Bearer delegated-token"
        return httpx.Response(201, json={"id": "graph-msg-1"}, request=request)

    adapter = TeamsAdapter(
        PlatformConfig(
            enabled=True,
            token="delegated-token",
            extra={
                "delivery_mode": "graph",
                "team_id": "team-1",
                "channel_id": "channel-1",
            },
        ),
        transport=httpx.MockTransport(handler),
    )

    result = await adapter.send_summary(_summary_payload(), reply_to="parent-1")

    assert result.success is True
    assert result.message_id == "graph-msg-1"
    assert len(requests) == 1
    request = requests[0]
    assert request.url.path == "/v1.0/teams/team-1/channels/channel-1/messages/parent-1/replies"
    assert b'"contentType":"html"' in request.content
    assert b"<h2>Weekly Sync</h2>" in request.content


@pytest.mark.anyio
async def test_teams_adapter_returns_clear_permission_error_for_graph_failures():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, text="Forbidden", request=request)

    adapter = TeamsAdapter(
        PlatformConfig(
            enabled=True,
            token="delegated-token",
            extra={
                "delivery_mode": "graph",
                "team_id": "team-1",
                "channel_id": "channel-1",
            },
        ),
        transport=httpx.MockTransport(handler),
    )

    result = await adapter.send("channel-1", "summary")

    assert result.success is False
    assert result.retryable is False
    assert "HTTP 403" in (result.error or "")


@pytest.mark.anyio
async def test_teams_summary_sender_reuses_existing_message_id_for_replies():
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(201, json={"id": "reply-2"}, request=request)

    sender = TeamsSummarySender(transport=httpx.MockTransport(handler))
    result = await sender.write_summary(
        _summary_payload(),
        {
            "delivery_mode": "graph",
            "team_id": "team-1",
            "channel_id": "channel-1",
            "access_token": "delegated-token",
        },
        existing_record={"message_id": "parent-1"},
    )

    assert result["message_id"] == "reply-2"
    assert len(requests) == 1
    assert requests[0].url.path.endswith("/messages/parent-1/replies")
