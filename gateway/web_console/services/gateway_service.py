"""Gateway admin service helpers for the Hermes Web Console backend."""

from __future__ import annotations

import os
from typing import Any, Callable

from gateway.config import GatewayConfig, Platform, PlatformConfig, load_gateway_config
from gateway.pairing import PairingStore
from gateway.status import get_running_pid, read_runtime_status

_ALLOW_ALL_ENV_BY_PLATFORM: dict[Platform, str] = {
    Platform.TELEGRAM: "TELEGRAM_ALLOW_ALL_USERS",
    Platform.DISCORD: "DISCORD_ALLOW_ALL_USERS",
    Platform.WHATSAPP: "WHATSAPP_ALLOW_ALL_USERS",
    Platform.SLACK: "SLACK_ALLOW_ALL_USERS",
    Platform.SIGNAL: "SIGNAL_ALLOW_ALL_USERS",
    Platform.EMAIL: "EMAIL_ALLOW_ALL_USERS",
    Platform.SMS: "SMS_ALLOW_ALL_USERS",
    Platform.MATTERMOST: "MATTERMOST_ALLOW_ALL_USERS",
    Platform.MATRIX: "MATRIX_ALLOW_ALL_USERS",
    Platform.DINGTALK: "DINGTALK_ALLOW_ALL_USERS",
}

_ALLOWLIST_ENV_BY_PLATFORM: dict[Platform, str] = {
    Platform.TELEGRAM: "TELEGRAM_ALLOWED_USERS",
    Platform.DISCORD: "DISCORD_ALLOWED_USERS",
    Platform.WHATSAPP: "WHATSAPP_ALLOWED_USERS",
    Platform.SLACK: "SLACK_ALLOWED_USERS",
    Platform.SIGNAL: "SIGNAL_ALLOWED_USERS",
    Platform.EMAIL: "EMAIL_ALLOWED_USERS",
    Platform.SMS: "SMS_ALLOWED_USERS",
    Platform.MATTERMOST: "MATTERMOST_ALLOWED_USERS",
    Platform.MATRIX: "MATRIX_ALLOWED_USERS",
    Platform.DINGTALK: "DINGTALK_ALLOWED_USERS",
}

_EXEMPT_AUTH_PLATFORMS = {Platform.HOMEASSISTANT, Platform.WEBHOOK}


class GatewayService:
    """Thin wrapper around existing gateway runtime status and pairing behavior."""

    def __init__(
        self,
        *,
        config_loader: Callable[[], GatewayConfig] = load_gateway_config,
        runtime_status_reader: Callable[[], dict[str, Any] | None] = read_runtime_status,
        running_pid_getter: Callable[[], int | None] = get_running_pid,
        pairing_store_factory: Callable[[], PairingStore] = PairingStore,
    ) -> None:
        self._config_loader = config_loader
        self._runtime_status_reader = runtime_status_reader
        self._running_pid_getter = running_pid_getter
        self._pairing_store_factory = pairing_store_factory

    def get_overview(self) -> dict[str, Any]:
        config = self._config_loader()
        runtime_status = self._runtime_status_reader() or {}
        running_pid = self._running_pid_getter()
        pairing_state = self.get_pairing_state()
        platforms = self._build_platform_rows(config=config, runtime_status=runtime_status, pairing_state=pairing_state)

        return {
            "gateway": {
                "running": running_pid is not None,
                "pid": running_pid,
                "state": runtime_status.get("gateway_state") or ("running" if running_pid is not None else "stopped"),
                "exit_reason": runtime_status.get("exit_reason"),
                "updated_at": runtime_status.get("updated_at"),
            },
            "summary": {
                "platform_count": len(platforms),
                "enabled_platforms": sum(1 for platform in platforms if platform["enabled"]),
                "configured_platforms": sum(1 for platform in platforms if platform["configured"]),
                "connected_platforms": sum(1 for platform in platforms if platform["runtime_state"] == "connected"),
                "pending_pairings": pairing_state["summary"]["pending_count"],
                "approved_pairings": pairing_state["summary"]["approved_count"],
            },
        }

    def get_platforms(self) -> list[dict[str, Any]]:
        config = self._config_loader()
        runtime_status = self._runtime_status_reader() or {}
        pairing_state = self.get_pairing_state()
        return self._build_platform_rows(config=config, runtime_status=runtime_status, pairing_state=pairing_state)

    def get_pairing_state(self) -> dict[str, Any]:
        store = self._pairing_store_factory()
        pending = sorted(store.list_pending(), key=lambda item: (item.get("platform", ""), item.get("code", "")))
        approved = sorted(store.list_approved(), key=lambda item: (item.get("platform", ""), item.get("user_id", "")))
        return {
            "pending": pending,
            "approved": approved,
            "summary": {
                "pending_count": len(pending),
                "approved_count": len(approved),
                "platforms_with_pending": sorted({item["platform"] for item in pending}),
                "platforms_with_approved": sorted({item["platform"] for item in approved}),
            },
        }

    def approve_pairing(self, *, platform: str, code: str) -> dict[str, Any] | None:
        store = self._pairing_store_factory()
        approved = store.approve_code(platform.strip().lower(), code.strip().upper())
        if approved is None:
            return None
        return {
            "platform": platform.strip().lower(),
            "code": code.strip().upper(),
            "user": approved,
        }

    def revoke_pairing(self, *, platform: str, user_id: str) -> bool:
        store = self._pairing_store_factory()
        return store.revoke(platform.strip().lower(), user_id.strip())

    def _build_platform_rows(
        self,
        *,
        config: GatewayConfig,
        runtime_status: dict[str, Any],
        pairing_state: dict[str, Any],
    ) -> list[dict[str, Any]]:
        runtime_platforms = runtime_status.get("platforms") or {}
        connected_platforms = set(config.get_connected_platforms())
        pending_counts = self._count_by_platform(pairing_state.get("pending") or [])
        approved_counts = self._count_by_platform(pairing_state.get("approved") or [])

        rows: list[dict[str, Any]] = []
        for platform in sorted((item for item in Platform if item is not Platform.LOCAL), key=lambda item: item.value):
            platform_config = config.platforms.get(platform)
            runtime_platform = runtime_platforms.get(platform.value) or {}
            allowlist = self._parse_allowlist(os.getenv(_ALLOWLIST_ENV_BY_PLATFORM.get(platform, ""), ""))
            home_channel = platform_config.home_channel.to_dict() if platform_config and platform_config.home_channel else None
            auth_mode = self._auth_mode(platform=platform, config=config, allowlist=allowlist)
            rows.append(
                {
                    "key": platform.value,
                    "label": self._platform_label(platform),
                    "enabled": bool(platform_config and platform_config.enabled),
                    "configured": platform in connected_platforms,
                    "runtime_state": runtime_platform.get("state") or "unknown",
                    "error_code": runtime_platform.get("error_code"),
                    "error_message": runtime_platform.get("error_message"),
                    "updated_at": runtime_platform.get("updated_at"),
                    "home_channel": home_channel,
                    "auth": {
                        "mode": auth_mode,
                        "allow_all": self._platform_allow_all_enabled(platform),
                        "allowlist_count": len(allowlist),
                        "pairing_behavior": config.get_unauthorized_dm_behavior(platform),
                    },
                    "pairing": {
                        "pending_count": pending_counts.get(platform.value, 0),
                        "approved_count": approved_counts.get(platform.value, 0),
                    },
                    "extra": dict(platform_config.extra) if platform_config else {},
                }
            )
        return rows

    @staticmethod
    def _count_by_platform(entries: list[dict[str, Any]]) -> dict[str, int]:
        counts: dict[str, int] = {}
        for item in entries:
            platform = item.get("platform")
            if not isinstance(platform, str) or not platform:
                continue
            counts[platform] = counts.get(platform, 0) + 1
        return counts

    @staticmethod
    def _platform_label(platform: Platform) -> str:
        return platform.value.replace("_", " ").title()

    @staticmethod
    def _parse_allowlist(raw: str) -> list[str]:
        return [item.strip() for item in raw.split(",") if item.strip()]

    @staticmethod
    def _platform_allow_all_enabled(platform: Platform) -> bool:
        platform_var = _ALLOW_ALL_ENV_BY_PLATFORM.get(platform)
        if platform_var and os.getenv(platform_var, "").strip().lower() in {"1", "true", "yes", "on"}:
            return True
        return os.getenv("GATEWAY_ALLOW_ALL_USERS", "").strip().lower() in {"1", "true", "yes", "on"}

    def _auth_mode(self, *, platform: Platform, config: GatewayConfig, allowlist: list[str]) -> str:
        if platform in _EXEMPT_AUTH_PLATFORMS:
            return "external_auth"
        if self._platform_allow_all_enabled(platform):
            return "allow_all"
        if allowlist:
            return "allowlist"
        if config.get_unauthorized_dm_behavior(platform) == "pair":
            return "pairing"
        return "deny"
