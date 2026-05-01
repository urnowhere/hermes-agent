import os
from datetime import datetime
from types import SimpleNamespace

import pytest

from gateway.config import GatewayConfig, Platform, PlatformConfig
from gateway.platforms.base import (
    MessageType,
    TopicProfileConfigError,
    resolve_topic_profile,
)
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
    profile_home.mkdir(parents=True)
    route = resolve_topic_profile(
        {
            "topic_profiles_safe_root": str(tmp_path / "profiles"),
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


def test_topic_profile_resolver_handles_general_topic_and_absent_topic_falls_back():
    general = resolve_topic_profile(
        {
            "topic_profiles": [
                {"match": {"chat_id": "-1001", "thread_id": "1"}, "profile": "general-test"},
            ]
        },
        "-1001",
        "1",
    )
    no_topic = resolve_topic_profile(
        {
            "topic_profiles": [
                {"match": {"chat_id": "-1001", "thread_id": "1"}, "profile": "general-test"},
            ]
        },
        "-1001",
        None,
    )

    assert general == {"profile": "general-test"}
    assert no_topic is None


def test_topic_profile_resolver_rejects_invalid_profile_names():
    with pytest.raises(TopicProfileConfigError, match="Invalid .*profile"):
        resolve_topic_profile(
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


def test_topic_profile_resolver_rejects_missing_thread_id():
    with pytest.raises(TopicProfileConfigError, match="requires match.chat_id and match.thread_id"):
        resolve_topic_profile(
            {
                "topic_profiles": [
                    {"match": {"chat_id": "-1001"}, "profile": "no-topic-test"},
                ]
            },
            "-1001",
            None,
        )


def test_topic_profile_resolver_rejects_duplicate_routes():
    with pytest.raises(TopicProfileConfigError, match="Duplicate"):
        resolve_topic_profile(
            {
                "topic_profiles": [
                    {"match": {"chat_id": "-1001", "thread_id": 101}, "profile": "a"},
                    {"match": {"chat_id": "-1001", "thread_id": "101"}, "profile": "b"},
                ]
            },
            "-1001",
            "101",
        )


def test_topic_profile_resolver_rejects_profile_home_without_safe_root(tmp_path):
    with pytest.raises(TopicProfileConfigError, match="profile_home requires"):
        resolve_topic_profile(
            {
                "topic_profiles": [
                    {
                        "match": {"chat_id": "-1001", "thread_id": "101"},
                        "profile": "cybrel-test",
                        "profile_home": str(tmp_path / "cybrel-test"),
                    }
                ]
            },
            "-1001",
            "101",
        )


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
    profile_home.mkdir(parents=True)
    config = GatewayConfig(
        platforms={
            Platform.TELEGRAM: PlatformConfig(
                extra={"topic_profiles_safe_root": str(tmp_path / "profiles")}
            )
        }
    )

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
    (gateway_home / "profiles" / "cybrel-test").mkdir(parents=True)

    with hermes_home_context(gateway_home):
        runner = object.__new__(GatewayRunner)
        runner.config = config
        runner.session_store = SessionStore(gateway_home / "sessions", config)
        source = _source(agent_profile="cybrel-test")

        profile_store = runner._session_store_for_source(source)

        assert profile_store.sessions_dir == gateway_home / "profiles" / "cybrel-test" / "sessions"
        assert profile_store._db.db_path == gateway_home / "profiles" / "cybrel-test" / "state.db"
        assert profile_store is not runner.session_store


def test_relative_explicit_profile_home_is_resolved_inside_safe_root(tmp_path):
    from gateway.run import GatewayRunner

    gateway_home = tmp_path / "gateway"
    profiles_root = tmp_path / "profiles"
    profile_home = profiles_root / "relative-home"
    profile_home.mkdir(parents=True)
    config = GatewayConfig(
        platforms={
            Platform.TELEGRAM: PlatformConfig(
                extra={"topic_profiles_safe_root": str(profiles_root)}
            )
        }
    )

    with hermes_home_context(gateway_home):
        runner = object.__new__(GatewayRunner)
        runner.config = config
        runner.session_store = SessionStore(gateway_home / "sessions", config)
        source = _source(agent_profile="cybrel-test", agent_hermes_home="relative-home")

        profile_store = runner._session_store_for_source(source)

        assert profile_store.sessions_dir == profile_home / "sessions"


def test_missing_named_profile_fails_closed(tmp_path):
    from gateway.run import GatewayRunner, TopicProfileRoutingError

    gateway_home = tmp_path / "gateway"
    config = GatewayConfig()

    with hermes_home_context(gateway_home):
        runner = object.__new__(GatewayRunner)
        runner.config = config
        runner.session_store = SessionStore(gateway_home / "sessions", config)
        source = _source(agent_profile="missing-profile")

        with pytest.raises(TopicProfileRoutingError, match="does not exist"):
            runner._session_store_for_source(source)


def test_routed_profile_runtime_env_is_loaded_without_mutating_process_env(monkeypatch, tmp_path):
    from gateway import run as gateway_run
    from gateway.run import GatewayRunner

    gateway_home = tmp_path / "gateway"
    profile_home = gateway_home / "profiles" / "cybrel-test"
    profile_home.mkdir(parents=True)
    (profile_home / ".env").write_text("OPENROUTER_API_KEY=profile-key\n", encoding="utf-8")
    config = GatewayConfig()
    captured = {}

    def _capture_runtime(runtime_env=None):
        captured["runtime_env"] = dict(runtime_env or {})
        return {
            "api_key": runtime_env.get("OPENROUTER_API_KEY"),
            "base_url": "https://openrouter.ai/api/v1",
            "provider": "openrouter",
            "api_mode": "chat_completions",
        }

    monkeypatch.setenv("OPENROUTER_API_KEY", "main-key")
    monkeypatch.setattr(gateway_run, "_resolve_runtime_agent_kwargs", _capture_runtime)

    with hermes_home_context(gateway_home):
        runner = object.__new__(GatewayRunner)
        runner.config = config
        runner.session_store = SessionStore(gateway_home / "sessions", config)
        runner._session_model_overrides = {}
        runner._pending_model_notes = {}
        source = _source(agent_profile="cybrel-test")

        model, runtime = runner._resolve_session_agent_runtime(
            source=source,
            user_config={"model": {"default": "test-model"}},
        )

    assert model == "test-model"
    assert runtime["api_key"] == "profile-key"
    assert captured["runtime_env"] == {"OPENROUTER_API_KEY": "profile-key"}
    assert os.environ["OPENROUTER_API_KEY"] == "main-key"


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
    profile_home.mkdir()
    adapter = TelegramAdapter(
        PlatformConfig(
            enabled=True,
            token="test-token",
            extra={
                "topic_profiles_safe_root": str(tmp_path),
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
