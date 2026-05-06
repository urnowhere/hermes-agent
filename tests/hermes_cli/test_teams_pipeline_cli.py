"""Tests for hermes_cli/teams_pipeline.py."""

import json
from argparse import Namespace
from types import SimpleNamespace

import pytest

from hermes_cli.teams_pipeline import teams_pipeline_command, _graph_setup_hint
from tools.teams_pipeline_store import TeamsPipelineStore


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))


def _make_args(**kwargs):
    defaults = {
        "teams_pipeline_action": None,
        "store_path": "",
        "status": "",
        "limit": 20,
        "job_id": "",
        "meeting_id": "",
        "join_web_url": "",
        "tenant_id": "",
        "call_record_id": "",
        "resource": "",
        "notification_url": "",
        "change_type": "updated",
        "expiration": "",
        "client_state": "",
        "lifecycle_notification_url": "",
        "latest_supported_tls_version": "v1_2",
        "subscription_id": "",
        "force_refresh": False,
        "skip_remote": False,
        "renew_within_hours": 24,
        "extend_hours": 24,
        "dry_run": False,
    }
    defaults.update(kwargs)
    return Namespace(**defaults)


def test_list_prints_recent_jobs(capsys, tmp_path):
    store = TeamsPipelineStore(tmp_path / "teams_pipeline_store.json")
    store.upsert_job(
        "job-1",
        {
            "event_id": "evt-1",
            "source_event_type": "updated",
            "dedupe_key": "evt-1",
            "status": "completed",
            "meeting_ref": {"meeting_id": "meeting-1"},
        },
    )

    teams_pipeline_command(
        _make_args(
            teams_pipeline_action="list",
            store_path=str(tmp_path / "teams_pipeline_store.json"),
        )
    )
    out = capsys.readouterr().out
    assert "job-1" in out
    assert "meeting-1" in out


def test_show_prints_job_json(capsys, tmp_path):
    store = TeamsPipelineStore(tmp_path / "teams_pipeline_store.json")
    store.upsert_job(
        "job-1",
        {
            "event_id": "evt-1",
            "source_event_type": "updated",
            "dedupe_key": "evt-1",
            "status": "completed",
            "meeting_ref": {"meeting_id": "meeting-1"},
        },
    )

    teams_pipeline_command(
        _make_args(
            teams_pipeline_action="show",
            job_id="job-1",
            store_path=str(tmp_path / "teams_pipeline_store.json"),
        )
    )
    out = capsys.readouterr().out
    payload = json.loads(out)
    assert payload["job_id"] == "job-1"
    assert payload["meeting_ref"]["meeting_id"] == "meeting-1"


def test_fetch_requires_meeting_identifier(capsys):
    teams_pipeline_command(_make_args(teams_pipeline_action="fetch"))
    out = capsys.readouterr().out
    assert "meeting_id or join_web_url is required" in out


def test_subscriptions_lists_graph_subscriptions(monkeypatch, capsys):
    class FakeClient:
        async def collect_paginated(self, path):
            assert path == "/subscriptions"
            return [
                {
                    "id": "sub-1",
                    "resource": "communications/onlineMeetings/getAllTranscripts",
                    "changeType": "updated",
                    "expirationDateTime": "2026-05-05T00:00:00Z",
                }
            ]

    monkeypatch.setattr("hermes_cli.teams_pipeline._build_graph_client", lambda: FakeClient())
    teams_pipeline_command(_make_args(teams_pipeline_action="subscriptions"))
    out = capsys.readouterr().out
    assert "sub-1" in out
    assert "getAllTranscripts" in out


def test_subscribe_defaults_to_created_for_transcript_resources(monkeypatch, capsys):
    captured = {}

    class FakeClient:
        async def post_json(self, path, json_body=None, headers=None):
            captured["path"] = path
            captured["json_body"] = json_body
            return {
                "id": "sub-transcript",
                "resource": json_body["resource"],
                "changeType": json_body["changeType"],
                "notificationUrl": json_body["notificationUrl"],
                "expirationDateTime": json_body["expirationDateTime"],
            }

    monkeypatch.setattr("hermes_cli.teams_pipeline._build_graph_client", lambda: FakeClient())
    teams_pipeline_command(
        _make_args(
            teams_pipeline_action="subscribe",
            resource="communications/onlineMeetings/getAllTranscripts",
            notification_url="https://example.com/webhooks/msgraph",
            change_type="",
        )
    )
    payload = json.loads(capsys.readouterr().out)
    assert captured["path"] == "/subscriptions"
    assert captured["json_body"]["changeType"] == "created"
    assert payload["changeType"] == "created"


def test_subscriptions_sync_local_store(monkeypatch, capsys, tmp_path):
    class FakeClient:
        async def collect_paginated(self, path):
            assert path == "/subscriptions"
            return [
                {
                    "id": "sub-sync",
                    "resource": "communications/onlineMeetings/getAllTranscripts",
                    "changeType": "updated",
                    "notificationUrl": "https://example.com/webhooks/msgraph",
                    "expirationDateTime": "2026-05-05T00:00:00Z",
                    "clientState": "sync-state",
                }
            ]

    monkeypatch.setattr("hermes_cli.teams_pipeline._build_graph_client", lambda: FakeClient())
    store_path = tmp_path / "teams_pipeline_store.json"
    teams_pipeline_command(
        _make_args(
            teams_pipeline_action="subscriptions",
            store_path=str(store_path),
        )
    )
    capsys.readouterr()
    store = TeamsPipelineStore(store_path)
    assert store.get_subscription("sub-sync")["client_state"] == "sync-state"


def test_subscribe_creates_graph_subscription(monkeypatch, capsys):
    class FakeClient:
        async def post_json(self, path, json_body):
            assert path == "/subscriptions"
            assert json_body["resource"] == "communications/onlineMeetings/getAllRecordings"
            assert json_body["notificationUrl"] == "https://example.com/webhooks/msgraph"
            return {"id": "sub-2", **json_body}

    monkeypatch.setattr("hermes_cli.teams_pipeline._build_graph_client", lambda: FakeClient())
    teams_pipeline_command(
        _make_args(
            teams_pipeline_action="subscribe",
            resource="communications/onlineMeetings/getAllRecordings",
            notification_url="https://example.com/webhooks/msgraph",
            client_state="state-123",
            expiration="2026-05-05T00:00:00Z",
        )
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["id"] == "sub-2"
    assert payload["clientState"] == "state-123"


def test_subscribe_persists_local_store(monkeypatch, capsys, tmp_path):
    class FakeClient:
        async def post_json(self, path, json_body):
            return {
                "id": "sub-store",
                **json_body,
            }

    monkeypatch.setattr("hermes_cli.teams_pipeline._build_graph_client", lambda: FakeClient())
    store_path = tmp_path / "teams_pipeline_store.json"
    teams_pipeline_command(
        _make_args(
            teams_pipeline_action="subscribe",
            resource="communications/onlineMeetings/getAllTranscripts",
            notification_url="https://example.com/webhooks/msgraph",
            expiration="2026-05-05T00:00:00Z",
            store_path=str(store_path),
        )
    )
    capsys.readouterr()
    store = TeamsPipelineStore(store_path)
    assert store.get_subscription("sub-store")["status"] == "active"


def test_token_health_force_refresh(monkeypatch, capsys):
    class FakeProvider:
        def inspect_token_health(self):
            return {"configured": True, "cache_state": "warm"}

        async def get_access_token(self, force_refresh=False):
            assert force_refresh is True
            return "token-123"

    monkeypatch.setattr(
        "hermes_cli.teams_pipeline.MicrosoftGraphTokenProvider",
        SimpleNamespace(from_env=lambda: FakeProvider()),
    )
    teams_pipeline_command(
        _make_args(teams_pipeline_action="token-health", force_refresh=True)
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["configured"] is True
    assert payload["last_refresh_succeeded"] is True
    assert payload["access_token_length"] == len("token-123")


def test_renew_subscription_updates_expiration(monkeypatch, capsys):
    class FakeClient:
        async def patch_json(self, path, json_body):
            assert path == "/subscriptions/sub-3"
            assert json_body == {"expirationDateTime": "2026-05-06T00:00:00Z"}
            return {"id": "sub-3", **json_body}

    monkeypatch.setattr("hermes_cli.teams_pipeline._build_graph_client", lambda: FakeClient())
    teams_pipeline_command(
        _make_args(
            teams_pipeline_action="renew-subscription",
            subscription_id="sub-3",
            expiration="2026-05-06T00:00:00Z",
        )
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["id"] == "sub-3"


def test_renew_subscription_updates_local_store(monkeypatch, capsys, tmp_path):
    class FakeClient:
        async def patch_json(self, path, json_body):
            return {"id": "sub-3", **json_body}

    monkeypatch.setattr("hermes_cli.teams_pipeline._build_graph_client", lambda: FakeClient())
    store_path = tmp_path / "teams_pipeline_store.json"
    store = TeamsPipelineStore(store_path)
    store.upsert_subscription(
        "sub-3",
        {
            "subscription_id": "sub-3",
            "resource": "communications/onlineMeetings/getAllTranscripts",
            "change_type": "updated",
            "notification_url": "https://example.com/webhooks/msgraph",
            "expiration_datetime": "2026-05-05T00:00:00Z",
            "client_state": "state-1",
        },
    )
    teams_pipeline_command(
        _make_args(
            teams_pipeline_action="renew-subscription",
            subscription_id="sub-3",
            expiration="2026-05-06T00:00:00Z",
            store_path=str(store_path),
        )
    )
    capsys.readouterr()
    reloaded = TeamsPipelineStore(store_path)
    record = reloaded.get_subscription("sub-3")
    assert record["expiration_datetime"] == "2026-05-06T00:00:00Z"
    assert record["latest_renewal_at"]


def test_delete_subscription_calls_graph(monkeypatch, capsys):
    class FakeClient:
        async def delete(self, path):
            assert path == "/subscriptions/sub-4"
            return {"deleted": True}

    monkeypatch.setattr("hermes_cli.teams_pipeline._build_graph_client", lambda: FakeClient())
    teams_pipeline_command(
        _make_args(
            teams_pipeline_action="delete-subscription",
            subscription_id="sub-4",
        )
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["subscription_id"] == "sub-4"
    assert payload["result"] == {"deleted": True}


def test_delete_subscription_removes_local_store(monkeypatch, capsys, tmp_path):
    class FakeClient:
        async def delete(self, path):
            return {"deleted": True}

    monkeypatch.setattr("hermes_cli.teams_pipeline._build_graph_client", lambda: FakeClient())
    store_path = tmp_path / "teams_pipeline_store.json"
    store = TeamsPipelineStore(store_path)
    store.upsert_subscription(
        "sub-4",
        {
            "subscription_id": "sub-4",
            "resource": "communications/onlineMeetings/getAllTranscripts",
            "change_type": "updated",
            "notification_url": "https://example.com/webhooks/msgraph",
            "expiration_datetime": "2026-05-05T00:00:00Z",
        },
    )
    teams_pipeline_command(
        _make_args(
            teams_pipeline_action="delete-subscription",
            subscription_id="sub-4",
            store_path=str(store_path),
        )
    )
    capsys.readouterr()
    assert TeamsPipelineStore(store_path).get_subscription("sub-4") is None


def test_graph_setup_hint_printed_on_config_error(monkeypatch, capsys):
    from tools.microsoft_graph_auth import MicrosoftGraphConfigError

    monkeypatch.setattr(
        "hermes_cli.teams_pipeline._build_graph_client",
        lambda: (_ for _ in ()).throw(MicrosoftGraphConfigError("missing credentials")),
    )
    teams_pipeline_command(_make_args(teams_pipeline_action="subscriptions"))
    out = capsys.readouterr().out
    assert out == _graph_setup_hint() + "\n"


def test_validate_reports_local_config(monkeypatch, capsys, tmp_path):
    monkeypatch.setenv("MSGRAPH_TENANT_ID", "tenant-1")
    monkeypatch.setenv("MSGRAPH_CLIENT_ID", "client-1")
    monkeypatch.setenv("MSGRAPH_CLIENT_SECRET", "secret-1")
    monkeypatch.setenv("MSGRAPH_WEBHOOK_ENABLED", "true")
    monkeypatch.setenv("TEAMS_ENABLED", "true")
    monkeypatch.setenv("TEAMS_DELIVERY_MODE", "incoming_webhook")
    monkeypatch.setenv("TEAMS_INCOMING_WEBHOOK_URL", "https://example.com/teams-webhook")

    store_path = tmp_path / "teams_pipeline_store.json"
    teams_pipeline_command(
        _make_args(
            teams_pipeline_action="validate",
            store_path=str(store_path),
            skip_remote=True,
        )
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["store_path"] == str(store_path)
    assert payload["webhook_enabled"] is True


def test_validate_uses_runtime_config_yaml(monkeypatch, capsys, tmp_path):
    monkeypatch.setenv("MSGRAPH_TENANT_ID", "tenant-1")
    monkeypatch.setenv("MSGRAPH_CLIENT_ID", "client-1")
    monkeypatch.setenv("MSGRAPH_CLIENT_SECRET", "secret-1")

    (tmp_path / "config.yaml").write_text(
        """
platforms:
  msgraph_webhook:
    enabled: true
  teams:
    enabled: true
    extra:
      delivery_mode: incoming_webhook
      incoming_webhook_url: https://example.com/teams-webhook
      channel_id: channel-1
""".strip()
        + "\n",
        encoding="utf-8",
    )

    store_path = tmp_path / "teams_pipeline_store.json"
    teams_pipeline_command(
        _make_args(
            teams_pipeline_action="validate",
            store_path=str(store_path),
            skip_remote=True,
        )
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["webhook_enabled"] is True
    assert payload["teams_enabled"] is True
    assert payload["teams_delivery_mode"] == "incoming_webhook"


def test_validate_syncs_remote_subscriptions(monkeypatch, capsys, tmp_path):
    monkeypatch.setenv("MSGRAPH_TENANT_ID", "tenant-1")
    monkeypatch.setenv("MSGRAPH_CLIENT_ID", "client-1")
    monkeypatch.setenv("MSGRAPH_CLIENT_SECRET", "secret-1")
    monkeypatch.setenv("MSGRAPH_WEBHOOK_ENABLED", "true")

    class FakeClient:
        async def collect_paginated(self, path):
            return [
                {
                    "id": "sub-validate",
                    "resource": "communications/onlineMeetings/getAllTranscripts",
                    "changeType": "updated",
                    "notificationUrl": "https://example.com/webhooks/msgraph",
                    "expirationDateTime": "2026-05-05T00:00:00Z",
                }
            ]

    monkeypatch.setattr("hermes_cli.teams_pipeline._build_graph_client", lambda: FakeClient())
    store_path = tmp_path / "teams_pipeline_store.json"
    teams_pipeline_command(
        _make_args(
            teams_pipeline_action="validate",
            store_path=str(store_path),
        )
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["remote_subscription_count"] == 1
    assert payload["synced_subscription_count"] == 1
    assert TeamsPipelineStore(store_path).get_subscription("sub-validate") is not None


def test_validate_uses_env_store_path_when_no_arg(monkeypatch, capsys, tmp_path):
    monkeypatch.setenv("MSGRAPH_TENANT_ID", "tenant-1")
    monkeypatch.setenv("MSGRAPH_CLIENT_ID", "client-1")
    monkeypatch.setenv("MSGRAPH_CLIENT_SECRET", "secret-1")
    monkeypatch.setenv("MSGRAPH_WEBHOOK_ENABLED", "true")
    env_store_path = tmp_path / "custom-store.json"
    monkeypatch.setenv("MSGRAPH_WEBHOOK_STORE_PATH", str(env_store_path))

    teams_pipeline_command(
        _make_args(
            teams_pipeline_action="validate",
            skip_remote=True,
        )
    )
    payload = json.loads(capsys.readouterr().out)

    assert payload["store_path"] == str(env_store_path)


def test_maintain_subscriptions_dry_run(monkeypatch, capsys):
    async def fake_maintain(args):
        assert args["renew_within_hours"] == 12
        assert args["extend_hours"] == 48
        assert args["dry_run"] is True
        return {
            "success": True,
            "dry_run": True,
            "candidate_count": 1,
            "candidates": [{"subscription_id": "sub-1"}],
            "renewed_count": 0,
        }

    monkeypatch.setattr("hermes_cli.teams_pipeline.maintain_graph_subscriptions", fake_maintain)
    teams_pipeline_command(
        _make_args(
            teams_pipeline_action="maintain-subscriptions",
            renew_within_hours=12,
            extend_hours=48,
            dry_run=True,
        )
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["dry_run"] is True
    assert payload["candidate_count"] == 1


def test_maintain_subscriptions_uses_store_path(monkeypatch, capsys, tmp_path):
    async def fake_maintain(args):
        return {
            "success": True,
            "store_path": args["store_path"],
            "renewed_count": 1,
        }

    monkeypatch.setattr("hermes_cli.teams_pipeline.maintain_graph_subscriptions", fake_maintain)
    store_path = tmp_path / "teams_pipeline_store.json"
    teams_pipeline_command(
        _make_args(
            teams_pipeline_action="maintain-subscriptions",
            store_path=str(store_path),
        )
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["store_path"] == str(store_path)
    assert payload["renewed_count"] == 1
