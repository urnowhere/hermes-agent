"""Operator-facing Microsoft Graph subscription management tools."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from tools.microsoft_graph_auth import (
    GraphCredentials,
    MicrosoftGraphConfigError,
    MicrosoftGraphTokenProvider,
)
from tools.microsoft_graph_client import MicrosoftGraphClient
from tools.teams_pipeline_models import GraphSubscription
from tools.teams_pipeline_store import TeamsPipelineStore, resolve_teams_pipeline_store_path
from tools.registry import registry, tool_error, tool_result


def _check_graph_requirements() -> bool:
    return GraphCredentials.from_env(required=False) is not None


def _build_graph_client() -> MicrosoftGraphClient:
    provider = MicrosoftGraphTokenProvider.from_env()
    return MicrosoftGraphClient(provider)


def _parse_bool(value: Any, *, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "on"}:
            return True
        if lowered in {"0", "false", "no", "off"}:
            return False
    return default


def _parse_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _utc_now_iso() -> str:
    return _utc_now().replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _parse_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _resolve_store_path(args: dict[str, Any]) -> str:
    return str(resolve_teams_pipeline_store_path(args.get("store_path")))


def _build_store(args: dict[str, Any]) -> TeamsPipelineStore:
    return TeamsPipelineStore(_resolve_store_path(args))


def sync_graph_subscription_record(
    store: TeamsPipelineStore,
    subscription_payload: dict[str, Any],
    *,
    status: str | None = None,
    renewed: bool = False,
) -> dict[str, Any]:
    normalized = GraphSubscription.from_dict(subscription_payload).to_dict()
    expiration = _parse_datetime(normalized.get("expiration_datetime"))
    effective_status = status
    if effective_status is None:
        effective_status = "expired" if expiration and expiration <= _utc_now() else "active"
    normalized["status"] = effective_status
    if renewed:
        normalized["latest_renewal_at"] = _utc_now_iso()
    return store.upsert_subscription(normalized["subscription_id"], normalized)


def _expected_client_state(args: dict[str, Any]) -> str | None:
    raw = args.get("client_state")
    if raw is None:
        from os import getenv

        raw = getenv("MSGRAPH_WEBHOOK_CLIENT_STATE", "")
    value = str(raw or "").strip()
    return value or None


def _is_managed_subscription(
    store: TeamsPipelineStore,
    subscription_payload: dict[str, Any],
    *,
    expected_client_state: str | None,
) -> bool:
    subscription_id = str(
        subscription_payload.get("subscription_id") or subscription_payload.get("id") or ""
    ).strip()
    if subscription_id and store.get_subscription(subscription_id):
        return True

    if expected_client_state:
        candidate_state = str(
            subscription_payload.get("client_state") or subscription_payload.get("clientState") or ""
        ).strip()
        if candidate_state and candidate_state == expected_client_state:
            return True

    return False


async def maintain_graph_subscriptions(
    args: dict[str, Any],
    *,
    client: MicrosoftGraphClient | None = None,
    store: TeamsPipelineStore | None = None,
) -> dict[str, Any]:
    threshold_hours = max(1, _parse_int(args.get("renew_within_hours"), 24))
    extend_hours = max(1, _parse_int(args.get("extend_hours"), 24))
    dry_run = _parse_bool(args.get("dry_run"), default=False)

    graph_client = client or _build_graph_client()
    local_store = store or _build_store(args)
    expected_client_state = _expected_client_state(args)
    now = _utc_now()

    remote_subscriptions = await graph_client.collect_paginated("/subscriptions")
    remote_ids: set[str] = set()
    synced = 0
    renewed: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []

    for raw in remote_subscriptions:
        if not isinstance(raw, dict):
            continue
        subscription_id = str(raw.get("id") or "").strip()
        if not subscription_id:
            continue
        is_managed = _is_managed_subscription(
            local_store,
            raw,
            expected_client_state=expected_client_state,
        )
        if not is_managed:
            skipped.append(
                {
                    "subscription_id": subscription_id,
                    "reason": "not_managed_by_teams_pipeline",
                }
            )
            continue

        remote_ids.add(subscription_id)
        try:
            sync_graph_subscription_record(local_store, raw)
            synced += 1
        except Exception as exc:
            skipped.append(
                {
                    "subscription_id": subscription_id,
                    "reason": f"failed_to_sync_local_store: {exc}",
                }
            )
            continue

        expiration = _parse_datetime(raw.get("expirationDateTime"))
        if expiration is None:
            skipped.append(
                {
                    "subscription_id": subscription_id,
                    "reason": "missing_expiration",
                }
            )
            continue

        seconds_until_expiry = int((expiration - now).total_seconds())
        if seconds_until_expiry < 0:
            local_store.upsert_subscription(
                subscription_id,
                {"status": "expired", "expiration_datetime": expiration.isoformat().replace("+00:00", "Z")},
            )
            skipped.append(
                {
                    "subscription_id": subscription_id,
                    "reason": "already_expired",
                    "expiration_datetime": expiration.isoformat().replace("+00:00", "Z"),
                }
            )
            continue

        if seconds_until_expiry > threshold_hours * 3600:
            skipped.append(
                {
                    "subscription_id": subscription_id,
                    "reason": "not_due",
                    "expires_in_seconds": seconds_until_expiry,
                }
            )
            continue

        new_expiration = (max(now, expiration) + timedelta(hours=extend_hours)).replace(
            microsecond=0
        ).isoformat().replace("+00:00", "Z")
        candidate = {
            "subscription_id": subscription_id,
            "resource": raw.get("resource"),
            "current_expiration": expiration.isoformat().replace("+00:00", "Z"),
            "new_expiration": new_expiration,
        }
        candidates.append(candidate)
        if dry_run:
            continue

        patched = await graph_client.patch_json(
            f"/subscriptions/{subscription_id}",
            json_body={"expirationDateTime": new_expiration},
        )
        merged = {**raw, **(patched or {}), "id": subscription_id, "expirationDateTime": new_expiration}
        sync_graph_subscription_record(local_store, merged, status="active", renewed=True)
        renewed.append(
            {
                **candidate,
                "result": patched,
            }
        )

    for subscription_id, existing in local_store.list_subscriptions().items():
        if subscription_id in remote_ids:
            continue
        local_store.upsert_subscription(
            subscription_id,
            {
                "status": "missing_remote",
                "last_seen_missing_remote_at": _utc_now_iso(),
            },
        )

    return {
        "success": True,
        "dry_run": dry_run,
        "store_path": str(local_store.path),
        "remote_subscription_count": len(remote_subscriptions),
        "synced_subscription_count": synced,
        "candidate_count": len(candidates),
        "renewed_count": len(renewed),
        "threshold_hours": threshold_hours,
        "extend_hours": extend_hours,
        "candidates": candidates,
        "renewed": renewed,
        "skipped": skipped,
    }


def _subscription_payload_from_args(args: dict[str, Any]) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for key in (
        "changeType",
        "notificationUrl",
        "resource",
        "expirationDateTime",
        "clientState",
        "latestSupportedTlsVersion",
        "lifecycleNotificationUrl",
        "includeResourceData",
        "notificationContentType",
    ):
        value = args.get(key)
        if value is not None:
            payload[key] = value

    if "includeResourceData" in payload:
        payload["includeResourceData"] = _parse_bool(
            payload["includeResourceData"], default=False
        )

    encryption_certificate = args.get("encryptionCertificate")
    encryption_certificate_id = args.get("encryptionCertificateId")
    if encryption_certificate is not None:
        payload["encryptionCertificate"] = encryption_certificate
    if encryption_certificate_id is not None:
        payload["encryptionCertificateId"] = encryption_certificate_id
    return payload


async def _list_graph_subscriptions(args: dict[str, Any], **_kwargs: Any) -> str:
    try:
        client = _build_graph_client()
        subscriptions = await client.collect_paginated("/subscriptions")
        return tool_result(
            success=True,
            count=len(subscriptions),
            subscriptions=subscriptions,
        )
    except MicrosoftGraphConfigError as exc:
        return tool_error(str(exc))
    except Exception as exc:
        return tool_error(f"Failed to list Graph subscriptions: {exc}")


async def _create_graph_subscription(args: dict[str, Any], **_kwargs: Any) -> str:
    dry_run = _parse_bool(args.get("dry_run"), default=False)
    try:
        payload = _subscription_payload_from_args(args)
        missing = [key for key in ("changeType", "notificationUrl", "resource", "expirationDateTime") if not payload.get(key)]
        if missing:
            return tool_error(
                "Missing required subscription fields: " + ", ".join(missing)
            )
        if dry_run:
            return tool_result(success=True, dry_run=True, payload=payload)
        client = _build_graph_client()
        created = await client.post_json("/subscriptions", json_body=payload)
        return tool_result(success=True, subscription=created)
    except MicrosoftGraphConfigError as exc:
        return tool_error(str(exc))
    except Exception as exc:
        return tool_error(f"Failed to create Graph subscription: {exc}")


async def _renew_graph_subscription(args: dict[str, Any], **_kwargs: Any) -> str:
    subscription_id = str(args.get("subscription_id") or "").strip()
    expiration = str(args.get("expirationDateTime") or "").strip()
    dry_run = _parse_bool(args.get("dry_run"), default=False)
    if not subscription_id:
        return tool_error("subscription_id is required")
    if not expiration:
        return tool_error("expirationDateTime is required")
    payload = {"expirationDateTime": expiration}
    try:
        if dry_run:
            return tool_result(
                success=True,
                dry_run=True,
                subscription_id=subscription_id,
                payload=payload,
            )
        client = _build_graph_client()
        renewed = await client.patch_json(
            f"/subscriptions/{subscription_id}",
            json_body=payload,
        )
        return tool_result(success=True, subscription=renewed)
    except MicrosoftGraphConfigError as exc:
        return tool_error(str(exc))
    except Exception as exc:
        return tool_error(f"Failed to renew Graph subscription: {exc}")


async def _delete_graph_subscription(args: dict[str, Any], **_kwargs: Any) -> str:
    subscription_id = str(args.get("subscription_id") or "").strip()
    dry_run = _parse_bool(args.get("dry_run"), default=False)
    if not subscription_id:
        return tool_error("subscription_id is required")
    try:
        if dry_run:
            return tool_result(
                success=True,
                dry_run=True,
                subscription_id=subscription_id,
            )
        client = _build_graph_client()
        deleted = await client.delete(f"/subscriptions/{subscription_id}")
        return tool_result(success=True, subscription_id=subscription_id, result=deleted)
    except MicrosoftGraphConfigError as exc:
        return tool_error(str(exc))
    except Exception as exc:
        return tool_error(f"Failed to delete Graph subscription: {exc}")


async def _inspect_graph_token_health(args: dict[str, Any], **_kwargs: Any) -> str:
    force_refresh = _parse_bool(args.get("force_refresh"), default=False)
    try:
        provider = MicrosoftGraphTokenProvider.from_env()
        details = provider.inspect_token_health()
        if force_refresh:
            token = await provider.get_access_token(force_refresh=True)
            details = provider.inspect_token_health()
            details["last_refresh_succeeded"] = True
            details["access_token_length"] = len(token)
        return tool_result(details)
    except MicrosoftGraphConfigError as exc:
        return tool_error(str(exc))
    except Exception as exc:
        return tool_error(f"Failed to inspect Microsoft Graph token health: {exc}")


async def _maintain_graph_subscriptions(args: dict[str, Any], **_kwargs: Any) -> str:
    try:
        result = await maintain_graph_subscriptions(args)
        return tool_result(result)
    except MicrosoftGraphConfigError as exc:
        return tool_error(str(exc))
    except Exception as exc:
        return tool_error(f"Failed to maintain Microsoft Graph subscriptions: {exc}")


GRAPH_LIST_SUBSCRIPTIONS_SCHEMA = {
    "name": "microsoft_graph_list_subscriptions",
    "description": "List active Microsoft Graph subscriptions visible to the configured app.",
    "parameters": {
        "type": "object",
        "properties": {},
        "required": [],
    },
}

GRAPH_CREATE_SUBSCRIPTION_SCHEMA = {
    "name": "microsoft_graph_create_subscription",
    "description": "Create a Microsoft Graph change-notification subscription for webhook delivery.",
    "parameters": {
        "type": "object",
        "properties": {
            "changeType": {"type": "string"},
            "notificationUrl": {"type": "string"},
            "resource": {"type": "string"},
            "expirationDateTime": {"type": "string"},
            "clientState": {"type": "string"},
            "latestSupportedTlsVersion": {"type": "string"},
            "lifecycleNotificationUrl": {"type": "string"},
            "includeResourceData": {"type": "boolean"},
            "notificationContentType": {"type": "string"},
            "encryptionCertificate": {"type": "string"},
            "encryptionCertificateId": {"type": "string"},
            "dry_run": {
                "type": "boolean",
                "description": "When true, validate and return the outbound payload without calling Graph.",
            },
        },
        "required": ["changeType", "notificationUrl", "resource", "expirationDateTime"],
    },
}

GRAPH_RENEW_SUBSCRIPTION_SCHEMA = {
    "name": "microsoft_graph_renew_subscription",
    "description": "Renew an existing Microsoft Graph subscription by updating expirationDateTime.",
    "parameters": {
        "type": "object",
        "properties": {
            "subscription_id": {"type": "string"},
            "expirationDateTime": {"type": "string"},
            "dry_run": {"type": "boolean"},
        },
        "required": ["subscription_id", "expirationDateTime"],
    },
}

GRAPH_DELETE_SUBSCRIPTION_SCHEMA = {
    "name": "microsoft_graph_delete_subscription",
    "description": "Delete an existing Microsoft Graph subscription.",
    "parameters": {
        "type": "object",
        "properties": {
            "subscription_id": {"type": "string"},
            "dry_run": {"type": "boolean"},
        },
        "required": ["subscription_id"],
    },
}

GRAPH_INSPECT_TOKEN_SCHEMA = {
    "name": "microsoft_graph_inspect_token_health",
    "description": "Inspect Microsoft Graph app-only configuration and optionally force-refresh the cached access token.",
    "parameters": {
        "type": "object",
        "properties": {
            "force_refresh": {"type": "boolean"},
        },
        "required": [],
    },
}

GRAPH_MAINTAIN_SUBSCRIPTIONS_SCHEMA = {
    "name": "microsoft_graph_maintain_subscriptions",
    "description": "Sync Graph subscriptions into the Teams pipeline store and renew those expiring within a threshold window.",
    "parameters": {
        "type": "object",
        "properties": {
            "renew_within_hours": {"type": "integer"},
            "extend_hours": {"type": "integer"},
            "store_path": {"type": "string"},
            "dry_run": {"type": "boolean"},
        },
        "required": [],
    },
}


registry.register(
    name="microsoft_graph_list_subscriptions",
    toolset="microsoft_graph",
    schema=GRAPH_LIST_SUBSCRIPTIONS_SCHEMA,
    handler=_list_graph_subscriptions,
    check_fn=_check_graph_requirements,
    is_async=True,
    emoji="🪟",
)

registry.register(
    name="microsoft_graph_create_subscription",
    toolset="microsoft_graph",
    schema=GRAPH_CREATE_SUBSCRIPTION_SCHEMA,
    handler=_create_graph_subscription,
    check_fn=_check_graph_requirements,
    is_async=True,
    emoji="🪟",
)

registry.register(
    name="microsoft_graph_renew_subscription",
    toolset="microsoft_graph",
    schema=GRAPH_RENEW_SUBSCRIPTION_SCHEMA,
    handler=_renew_graph_subscription,
    check_fn=_check_graph_requirements,
    is_async=True,
    emoji="🪟",
)

registry.register(
    name="microsoft_graph_delete_subscription",
    toolset="microsoft_graph",
    schema=GRAPH_DELETE_SUBSCRIPTION_SCHEMA,
    handler=_delete_graph_subscription,
    check_fn=_check_graph_requirements,
    is_async=True,
    emoji="🪟",
)

registry.register(
    name="microsoft_graph_inspect_token_health",
    toolset="microsoft_graph",
    schema=GRAPH_INSPECT_TOKEN_SCHEMA,
    handler=_inspect_graph_token_health,
    check_fn=_check_graph_requirements,
    is_async=True,
    emoji="🪟",
)

registry.register(
    name="microsoft_graph_maintain_subscriptions",
    toolset="microsoft_graph",
    schema=GRAPH_MAINTAIN_SUBSCRIPTIONS_SCHEMA,
    handler=_maintain_graph_subscriptions,
    check_fn=_check_graph_requirements,
    is_async=True,
    emoji="🧰",
)
