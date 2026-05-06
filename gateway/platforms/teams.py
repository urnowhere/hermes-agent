"""Outbound Microsoft Teams channel delivery helpers.

This adapter is intentionally narrow in scope:
- send meeting summaries to a Teams channel via an incoming webhook, or
- send to a Teams channel via Microsoft Graph when an explicit bearer token is provided.

Inbound conversational Teams bot support is out of scope for this adapter.
"""

from __future__ import annotations

import html
import os
from dataclasses import dataclass
from typing import Any, Dict, Optional

import httpx

from gateway.config import Platform, PlatformConfig
from gateway.platforms.base import BasePlatformAdapter, SendResult
from tools.microsoft_graph_client import MicrosoftGraphAPIError, MicrosoftGraphClient
from tools.teams_pipeline_models import TeamsMeetingSummaryPayload


class StaticAccessTokenProvider:
    """Tiny Graph token-provider shim for explicitly supplied bearer tokens."""

    def __init__(self, access_token: str) -> None:
        self._access_token = str(access_token or "").strip()

    async def get_access_token(self, *, force_refresh: bool = False) -> str:
        if not self._access_token:
            raise RuntimeError("Microsoft Teams Graph access token is not configured.")
        return self._access_token

    def clear_cache(self) -> None:
        return None


@dataclass(frozen=True)
class TeamsDeliveryTarget:
    delivery_mode: str
    channel_id: str
    team_id: str | None = None
    incoming_webhook_url: str | None = None


def check_teams_requirements() -> bool:
    return True


def build_teams_summary_markdown(payload: TeamsMeetingSummaryPayload) -> str:
    title = payload.title or f"Meeting {payload.meeting_ref.meeting_id}"
    lines = [
        f"**{title}**",
        "",
        payload.summary or "No summary available.",
        "",
        "**Key decisions**",
        *([f"- {item}" for item in payload.key_decisions] or ["- None"]),
        "",
        "**Action items**",
        *([f"- {item}" for item in payload.action_items] or ["- None"]),
        "",
        "**Risks / blockers**",
        *([f"- {item}" for item in payload.risks] or ["- None"]),
        "",
        f"Confidence: {payload.confidence or 'unknown'}",
    ]
    if payload.confidence_notes:
        lines.append(payload.confidence_notes)
    artifact_lines = [
        f"- {artifact.artifact_type}: {artifact.display_name or artifact.artifact_id}"
        for artifact in payload.source_artifacts
    ]
    if artifact_lines:
        lines.extend(["", "**Artifacts**", *artifact_lines])
    return "\n".join(lines).strip()


def build_teams_summary_html(payload: TeamsMeetingSummaryPayload) -> str:
    def _list(items: list[str]) -> str:
        if not items:
            return "<ul><li>None</li></ul>"
        return "<ul>" + "".join(f"<li>{html.escape(item)}</li>" for item in items) + "</ul>"

    title = html.escape(payload.title or f"Meeting {payload.meeting_ref.meeting_id}")
    summary = html.escape(payload.summary or "No summary available.")
    confidence = html.escape(payload.confidence or "unknown")
    confidence_notes = (
        f"<p><i>{html.escape(payload.confidence_notes)}</i></p>"
        if payload.confidence_notes
        else ""
    )
    artifacts = _list(
        [f"{artifact.artifact_type}: {artifact.display_name or artifact.artifact_id}" for artifact in payload.source_artifacts]
    )
    return (
        f"<h2>{title}</h2>"
        f"<p>{summary}</p>"
        "<h3>Key decisions</h3>"
        f"{_list(payload.key_decisions)}"
        "<h3>Action items</h3>"
        f"{_list(payload.action_items)}"
        "<h3>Risks / blockers</h3>"
        f"{_list(payload.risks)}"
        f"<p><b>Confidence:</b> {confidence}</p>"
        f"{confidence_notes}"
        "<h3>Artifacts</h3>"
        f"{artifacts}"
    )


def build_teams_webhook_payload(payload: TeamsMeetingSummaryPayload) -> dict[str, Any]:
    def _fact(title: str, value: str) -> dict[str, str]:
        return {"title": title, "value": value}

    markdown = build_teams_summary_markdown(payload)
    facts = [
        _fact("Meeting ID", payload.meeting_ref.meeting_id),
        _fact("Confidence", payload.confidence or "unknown"),
        _fact("Action items", str(len(payload.action_items))),
        _fact("Decisions", str(len(payload.key_decisions))),
    ]
    return {
        "type": "message",
        "attachments": [
            {
                "contentType": "application/vnd.microsoft.card.adaptive",
                "contentUrl": None,
                "content": {
                    "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                    "type": "AdaptiveCard",
                    "version": "1.4",
                    "msteams": {"width": "Full"},
                    "body": [
                        {
                            "type": "TextBlock",
                            "size": "Medium",
                            "weight": "Bolder",
                            "text": payload.title or f"Meeting {payload.meeting_ref.meeting_id}",
                            "wrap": True,
                        },
                        {
                            "type": "FactSet",
                            "facts": facts,
                        },
                        {
                            "type": "TextBlock",
                            "text": markdown,
                            "wrap": True,
                        },
                    ],
                },
            }
        ],
    }


def _resolve_teams_target(
    config: PlatformConfig | dict[str, Any],
    chat_id: str,
) -> TeamsDeliveryTarget:
    if isinstance(config, PlatformConfig):
        platform_config = config
        extra = dict(config.extra or {})
    else:
        extra = dict(config or {})
        platform_config = PlatformConfig(enabled=True, extra=extra)

    normalized_chat_id = str(chat_id or extra.get("channel_id") or extra.get("chat_id") or "").strip()
    if not normalized_chat_id:
        home_channel = platform_config.home_channel.chat_id if platform_config.home_channel else ""
        normalized_chat_id = str(home_channel or "").strip()

    webhook_url = str(
        extra.get("incoming_webhook_url")
        or os.getenv("TEAMS_INCOMING_WEBHOOK_URL", "")
    ).strip()
    graph_team_id = str(extra.get("team_id") or os.getenv("TEAMS_TEAM_ID", "")).strip() or None
    graph_channel_id = normalized_chat_id or str(extra.get("channel_id") or "").strip()
    delivery_mode = str(extra.get("delivery_mode") or os.getenv("TEAMS_DELIVERY_MODE") or "auto").strip().lower()
    if delivery_mode == "auto":
        delivery_mode = "incoming_webhook" if webhook_url else "graph"

    if delivery_mode == "incoming_webhook":
        if not webhook_url:
            raise RuntimeError("Teams incoming webhook delivery requires incoming_webhook_url.")
        if not graph_channel_id:
            graph_channel_id = "incoming-webhook"
        return TeamsDeliveryTarget(
            delivery_mode=delivery_mode,
            channel_id=graph_channel_id,
            incoming_webhook_url=webhook_url,
        )

    if delivery_mode != "graph":
        raise RuntimeError(f"Unsupported Teams delivery mode: {delivery_mode}")
    if not graph_team_id:
        raise RuntimeError("Teams Graph delivery requires team_id.")
    if not graph_channel_id:
        raise RuntimeError("Teams Graph delivery requires channel_id.")
    return TeamsDeliveryTarget(
        delivery_mode=delivery_mode,
        channel_id=graph_channel_id,
        team_id=graph_team_id,
    )


class TeamsAdapter(BasePlatformAdapter):
    """Send-only Teams adapter for pipeline delivery."""

    def __init__(
        self,
        config: PlatformConfig,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        graph_client: MicrosoftGraphClient | None = None,
    ) -> None:
        super().__init__(config, Platform.TEAMS)
        self._transport = transport
        self._graph_client = graph_client

    async def connect(self) -> bool:
        try:
            _resolve_teams_target(self.config, "")
        except Exception as exc:
            self._set_fatal_error("teams_config", str(exc), retryable=False)
            return False
        self._mark_connected()
        return True

    async def disconnect(self) -> None:
        self._mark_disconnected()

    async def send(
        self,
        chat_id: str,
        content: str,
        reply_to: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> SendResult:
        metadata = dict(metadata or {})
        target = _resolve_teams_target(self.config, chat_id)
        try:
            if target.delivery_mode == "incoming_webhook":
                return await self._send_via_incoming_webhook(
                    target,
                    content,
                    payload_override=metadata.get("webhook_payload"),
                )
            return await self._send_via_graph(
                target,
                str(metadata.get("html") or content),
                reply_to=reply_to or metadata.get("reply_to_message_id"),
                content_is_html=bool(metadata.get("html")),
            )
        except httpx.HTTPStatusError as exc:
            error = f"Teams delivery failed with HTTP {exc.response.status_code}: {exc.response.text}"
            return SendResult(
                success=False,
                error=error,
                raw_response=exc.response,
                retryable=exc.response.status_code >= 500 or exc.response.status_code == 429,
            )
        except MicrosoftGraphAPIError as exc:
            error = f"Teams delivery failed with HTTP {exc.status_code}: {exc}"
            return SendResult(
                success=False,
                error=error,
                raw_response=exc.payload,
                retryable=exc.status_code >= 500 or exc.status_code == 429,
            )
        except Exception as exc:
            return SendResult(success=False, error=str(exc), retryable=False)

    async def get_chat_info(self, chat_id: str) -> Dict[str, Any]:
        target = _resolve_teams_target(self.config, chat_id)
        return {
            "name": target.channel_id,
            "type": "channel",
            "platform": self.platform.value,
            "delivery_mode": target.delivery_mode,
            "team_id": target.team_id,
        }

    async def send_summary(
        self,
        payload: TeamsMeetingSummaryPayload,
        *,
        reply_to: str | None = None,
    ) -> SendResult:
        return await self.send(
            payload.teams_target or "",
            build_teams_summary_markdown(payload),
            reply_to=reply_to,
            metadata={
                "html": build_teams_summary_html(payload),
                "webhook_payload": build_teams_webhook_payload(payload),
            },
        )

    async def _send_via_incoming_webhook(
        self,
        target: TeamsDeliveryTarget,
        content: str,
        *,
        payload_override: dict[str, Any] | None = None,
    ) -> SendResult:
        payload = payload_override or {"text": content}
        async with httpx.AsyncClient(timeout=30.0, transport=self._transport) as client:
            response = await client.post(target.incoming_webhook_url, json=payload)
            response.raise_for_status()
        response_payload: Any
        try:
            response_payload = response.json()
        except ValueError:
            response_payload = response.text
        return SendResult(
            success=True,
            message_id=_extract_response_message_id(response_payload) or response.headers.get("x-ms-activity-id"),
            raw_response=response_payload,
        )

    async def _send_via_graph(
        self,
        target: TeamsDeliveryTarget,
        content: str,
        *,
        reply_to: str | None = None,
        content_is_html: bool = False,
    ) -> SendResult:
        client = self._graph_client or self._build_graph_client()
        path = f"/teams/{target.team_id}/channels/{target.channel_id}/messages"
        if reply_to:
            path = f"{path}/{reply_to}/replies"
        response_payload = await client.post_json(
            path,
            json_body={
                "body": {
                    "contentType": "html",
                    "content": content if content_is_html else _content_to_html(content),
                }
            },
        )
        return SendResult(
            success=True,
            message_id=(response_payload.get("id") if isinstance(response_payload, dict) else None),
            raw_response=response_payload,
        )

    def _build_graph_client(self) -> MicrosoftGraphClient:
        access_token = str(
            self.config.extra.get("access_token")
            or self.config.token
            or os.getenv("TEAMS_GRAPH_ACCESS_TOKEN", "")
        ).strip()
        if not access_token:
            raise RuntimeError(
                "Teams Graph delivery requires TEAMS_GRAPH_ACCESS_TOKEN or platforms.teams.token."
            )
        provider = StaticAccessTokenProvider(access_token)
        return MicrosoftGraphClient(
            provider,  # type: ignore[arg-type]
            transport=self._transport,
            user_agent="Hermes-Agent/teams-delivery",
        )


class TeamsSummarySender:
    """Pipeline-facing sender that records Teams channel delivery results."""

    def __init__(
        self,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        graph_client: MicrosoftGraphClient | None = None,
    ) -> None:
        self._transport = transport
        self._graph_client = graph_client

    async def write_summary(
        self,
        payload: TeamsMeetingSummaryPayload,
        config: dict[str, Any],
        existing_record: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        adapter = TeamsAdapter(
            PlatformConfig(enabled=True, extra=dict(config or {})),
            transport=self._transport,
            graph_client=self._graph_client,
        )
        result = await adapter.send_summary(
            payload,
            reply_to=(existing_record or {}).get("message_id"),
        )
        if not result.success:
            raise RuntimeError(result.error or "Teams delivery failed.")
        return {
            "message_id": result.message_id,
            "delivery_mode": adapter.config.extra.get("delivery_mode", "auto"),
            "channel_id": str(config.get("channel_id") or payload.teams_target or ""),
        }

    async def __call__(
        self,
        payload: TeamsMeetingSummaryPayload,
        config: dict[str, Any],
        existing_record: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        return await self.write_summary(payload, config, existing_record)


def _content_to_html(content: str) -> str:
    escaped = html.escape(content or "")
    escaped = escaped.replace("\n", "<br/>")
    if not escaped.strip():
        escaped = "No summary available."
    return f"<p>{escaped}</p>"


def _extract_response_message_id(payload: Any) -> str | None:
    if isinstance(payload, dict):
        for key in ("id", "messageId", "message_id"):
            value = payload.get(key)
            if value:
                return str(value)
    if isinstance(payload, str) and payload.strip() and payload.strip() not in {"1", "ok"}:
        return payload.strip()
    return None


__all__ = [
    "TeamsAdapter",
    "TeamsSummarySender",
    "build_teams_summary_html",
    "build_teams_summary_markdown",
    "build_teams_webhook_payload",
    "check_teams_requirements",
]
