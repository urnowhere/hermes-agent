from datetime import datetime
from types import SimpleNamespace

import pytest

from gateway.config import GatewayConfig, Platform, PlatformConfig
from gateway.platforms.base import MessageType, resolve_topic_profile
from gateway.session import SessionSource, SessionStore, build_session_key
from hermes_constants import get_hermes_home, hermes_home_context


def _source(**overrides):
    data = {
        "platform": Platform.TELEGRAM,
        "chat_id": "-1001",
        "chat_type": "group",
        "thread_id": "101",
        "user_id": "42",
    }
    data.update(overrides)
    return SessionSource(**data)


def test_topic_profile_resolver_matches_exact_topic_and_home(tmp_path):
    profile_home = tmp_path / "profiles" / "cybrel-test"
    route = resolve_topic_profile(
        {
            "topic_profiles": [
                {
                    "match": {"chat_id": "-1001", "thread_id": 101},
                    "profile": "cybrel-test",
                    "profile_home": str(profile_home),
                }
            ]
        },
        "-1001",
        "101",
    )

    assert route == {"profile": "cybrel-test", "profile_home": str(profile_home)}


def test_topic_profile_resolver_handles_general_topic_and_absent_topic(tmp_path):
    general = resolve_topic_profile(
        {
            "topic_profiles": [
                {"match": {"chat_id": "-1001", "thread_id": "1"}, "profile": "general-test"},
                {"match": {"chat_id": "-1001"}, "profile": "no-topic-test"},
            ]
        },
        "-1001",
        "1",
    )
    no_topic = resolve_topic_profile(
        {
            "topic_profiles": [
                {"match": {"chat_id": "-1001", "thread_id": "1"}, "profile": "general-test"},
                {"match": {"chat_id": "-1001"}, "profile": "no-topic-test"},
            ]
        },
        "-1001",
        None,
    )

    assert general == {"profile": "general-test"}
    assert no_topic == {"profile": "no-topic-test"}


def test_topic_profile_resolver_rejects_invalid_profile_names(caplog):
    route = resolve_topic_profile(
        {
            "topic_profiles": [
                {
                    "match": {"chat_id": "-1001", "thread_id": "101"},
                    "profile": "bad:profile",
                }
            ]
        },
        "-1001",
        "101",
    )

    assert route is None
    assert "invalid topic profile" in caplog.text


def test_session_key_includes_valid_profile_and_ignores_invalid_profile():
    routed = build_session_key(_source(agent_profile="cybrel-test"))
    invalid = build_session_key(_source(agent_profile="bad:profile"))

    assert routed == "agent:cybrel-test:telegram:group:-1001:101"
    assert invalid == "agent:main:telegram:group:-1001:101"


def test_session_source_roundtrip_preserves_agent_profile_fields(tmp_path):
    source = _source(
        agent_profile="vault-test",
        agent_hermes_home=str(tmp_path / "vault-test"),
    )

    restored = SessionSource.from_dict(source.to_dict())

    assert restored.agent_profile == "vault-test"
    assert restored.agent_hermes_home == str(tmp_path / "vault-test")


def test_profile_session_store_uses_routed_home_without_changing_global_home(tmp_path):
    from gateway.run import GatewayRunner

    gateway_home = tmp_path / "gateway"
    profile_home = tmp_path / "profiles" / "cybrel-test"
    config = GatewayConfig()

    with hermes_home_context(gateway_home):
        runner = object.__new__(GatewayRunner)
        runner.config = config
        runner.session_store = SessionStore(gateway_home / "sessions", config)
        source = _source(
            agent_profile="cybrel-test",
            agent_hermes_home=str(profile_home),
        )

        profile_store = runner._session_store_for_source(source)

        assert profile_store.sessions_dir == profile_home / "sessions"
        assert profile_store._db.db_path == profile_home / "state.db"
        assert get_hermes_home() == gateway_home
        assert runner._session_key_for_source(source) == (
            "agent:cybrel-test:telegram:group:-1001:101"
        )


def test_named_profile_without_explicit_home_stays_isolated_under_profiles_root(tmp_path):
    from gateway.run import GatewayRunner

    gateway_home = tmp_path / "gateway"
    config = GatewayConfig()

    with hermes_home_context(gateway_home):
        runner = object.__new__(GatewayRunner)
        runner.config = config
        runner.session_store = SessionStore(gateway_home / "sessions", config)
        source = _source(agent_profile="cybrel-test")

        profile_store = runner._session_store_for_source(source)

        assert profile_store.sessions_dir == gateway_home / "profiles" / "cybrel-test" / "sessions"
        assert profile_store._db.db_path == gateway_home / "profiles" / "cybrel-test" / "state.db"
        assert profile_store is not runner.session_store


def test_relative_explicit_profile_home_falls_back_to_named_profile_root(tmp_path):
    from gateway.run import GatewayRunner

    gateway_home = tmp_path / "gateway"
    config = GatewayConfig()

    with hermes_home_context(gateway_home):
        runner = object.__new__(GatewayRunner)
        runner.config = config
        runner.session_store = SessionStore(gateway_home / "sessions", config)
        source = _source(agent_profile="cybrel-test", agent_hermes_home="relative-home")

        profile_store = runner._session_store_for_source(source)

        assert profile_store.sessions_dir == gateway_home / "profiles" / "cybrel-test" / "sessions"


@pytest.mark.asyncio
async def test_hermes_home_context_is_task_local(tmp_path):
    import asyncio

    gateway_home = tmp_path / "gateway"
    profile_home = tmp_path / "profile"

    async def read_home(path):
        with hermes_home_context(path):
            await asyncio.sleep(0)
            return get_hermes_home()

    first, second = await asyncio.gather(read_home(gateway_home), read_home(profile_home))

    assert first == gateway_home
    assert second == profile_home


def test_config_yaml_bridges_telegram_topic_profiles(monkeypatch, tmp_path):
    from gateway.config import load_gateway_config

    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    (tmp_path / "config.yaml").write_text(
        """
telegram:
  enabled: true
  token: test-token
  topic_profiles:
    - match:
        chat_id: "-1001"
        thread_id: 101
      profile: cybrel-test
""",
        encoding="utf-8",
    )

    config = load_gateway_config()

    assert config.platforms[Platform.TELEGRAM].extra["topic_profiles"][0]["profile"] == "cybrel-test"


def test_telegram_synthetic_message_event_sets_profile_on_event_and_source(tmp_path):
    pytest.importorskip("telegram")
    from telegram.constants import ChatType
    from gateway.platforms.telegram import TelegramAdapter

    profile_home = tmp_path / "cybrel-test"
    adapter = TelegramAdapter(
        PlatformConfig(
            enabled=True,
            token="test-token",
            extra={
                "topic_profiles": [
                    {
                        "match": {"chat_id": "-1001", "thread_id": "101"},
                        "profile": "cybrel-test",
                        "profile_home": str(profile_home),
                    }
                ]
            },
        )
    )
    message = SimpleNamespace(
        chat=SimpleNamespace(
            id=-1001,
            type=ChatType.SUPERGROUP,
            is_forum=True,
            title="Sandbox Forum",
        ),
        from_user=SimpleNamespace(id=42, full_name="Ayo Test"),
        message_thread_id=101,
        text="hello",
        message_id=7,
        reply_to_message=None,
        date=datetime(2026, 5, 1),
    )

    event = adapter._build_message_event(message, msg_type=MessageType.TEXT)

    assert event.agent_profile == "cybrel-test"
    assert event.agent_hermes_home == str(profile_home)
    assert event.source.agent_profile == "cybrel-test"
    assert event.source.agent_hermes_home == str(profile_home)
