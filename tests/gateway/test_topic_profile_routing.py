import asyncio
import json
import os
import sys
import threading
import types
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from gateway.config import GatewayConfig, Platform, PlatformConfig
from gateway.platforms.base import (
    MessageEvent,
    MessageType,
    TopicProfileConfigError,
    resolve_topic_profile,
)
from gateway.session import SessionSource, SessionStore, build_session_key
from hermes_constants import get_hermes_home, hermes_home_context


class _CapturingAgent:
    last_init = None
    inits = []
    _lock = threading.Lock()

    def __init__(self, *args, **kwargs):
        from agent.prompt_builder import load_soul_md
        from tools.memory_tool import get_memory_dir

        record = dict(kwargs)
        record["hermes_home_at_init"] = str(get_hermes_home())
        record["soul"] = load_soul_md() or ""
        record["memory_dir"] = str(get_memory_dir())
        with self._lock:
            type(self).last_init = record
            type(self).inits.append(record)
        self.tools = []

    def run_conversation(self, user_message, conversation_history=None, task_id=None, persist_user_message=None):
        return {
            "final_response": "ok",
            "messages": [],
            "api_calls": 1,
            "completed": True,
        }


def _install_fake_agent(monkeypatch):
    fake_run_agent = types.ModuleType("run_agent")
    fake_run_agent.AIAgent = _CapturingAgent
    monkeypatch.setitem(sys.modules, "run_agent", fake_run_agent)
    _CapturingAgent.last_init = None
    _CapturingAgent.inits = []


def _make_runner(config=None, *, gateway_prompt="Gateway prompt"):
    from gateway import run as gateway_run
    from gateway.session import SessionStore

    runner = object.__new__(gateway_run.GatewayRunner)
    runner.adapters = {}
    runner._ephemeral_system_prompt = gateway_prompt
    runner._prefill_messages = [{"role": "system", "content": "gateway prefill"}]
    runner._reasoning_config = None
    runner._service_tier = None
    runner._provider_routing = {"order": ["gateway-provider"], "sort": "latency"}
    runner._fallback_model = None
    runner._running_agents = {}
    runner._pending_model_notes = {}
    runner._session_db = None
    runner._agent_cache = {}
    runner._agent_cache_lock = threading.Lock()
    runner._session_model_overrides = {}
    runner._session_reasoning_overrides = {}
    runner._draining = False
    runner.hooks = SimpleNamespace(loaded_hooks=False)
    runner.config = config or GatewayConfig()
    runner.session_store = SessionStore(get_hermes_home() / "sessions", runner.config)
    runner._get_or_create_gateway_honcho = lambda session_key: (None, None)
    runner._enrich_message_with_vision = AsyncMock(return_value="ENRICHED")
    return runner


def _provider_runtime(runtime_env=None):
    return {
        "provider": "openrouter",
        "api_mode": "chat_completions",
        "base_url": "https://openrouter.ai/api/v1",
        "api_key": (runtime_env or {}).get("OPENROUTER_API_KEY", "sk-test-gateway-key"),
    }


def _write_profile_config(profile_home, *, prompt, model, toolsets=None, disabled=None, provider_routing=None, prefill=None):
    prefill_line = f'prefill_messages_file: "{prefill}"\n' if prefill else ""
    provider_lines = ""
    if provider_routing:
        provider_lines = "provider_routing:\n" + "\n".join(
            f"  {key}: {json.dumps(value)}" for key, value in provider_routing.items()
        ) + "\n"
    disabled_lines = ""
    if disabled:
        disabled_lines = "  disabled_toolsets:\n" + "\n".join(f"    - {item}" for item in disabled) + "\n"
    toolset_lines = ""
    if toolsets:
        toolset_lines = "platform_toolsets:\n  telegram:\n" + "\n".join(f"    - {item}" for item in toolsets) + "\n"
    (profile_home / "config.yaml").write_text(
        (
            "model:\n"
            "  provider: openrouter\n"
            f"  default: {model}\n"
            "agent:\n"
            f"  system_prompt: {json.dumps(prompt)}\n"
            "  reasoning_effort: high\n"
            "  service_tier: priority\n"
            f"{disabled_lines}"
            f"{prefill_line}"
            f"{provider_lines}"
            f"{toolset_lines}"
        ),
        encoding="utf-8",
    )
    (profile_home / ".env").write_text("OPENROUTER_API_KEY=sk-test-profile-key\n", encoding="utf-8")


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


def test_parse_session_key_accepts_routed_profiles():
    from gateway.run import _parse_session_key

    parsed = _parse_session_key("agent:cybrel-test:telegram:group:-1001:101")

    assert parsed is not None
    assert parsed["agent_profile"] == "cybrel-test"
    assert parsed["platform"] == "telegram"
    assert parsed["chat_type"] == "group"
    assert parsed["chat_id"] == "-1001"


def test_session_key_treats_empty_profile_as_main_without_warning(caplog):
    source = _source(agent_profile="")

    assert build_session_key(source) == "agent:main:telegram:group:-1001:101"
    assert "Ignoring invalid agent profile" not in caplog.text


def test_session_source_roundtrip_preserves_agent_profile_fields(tmp_path):
    source = _source(
        agent_profile="vault-test",
        agent_hermes_home=str(tmp_path / "vault-test"),
    )

    restored = SessionSource.from_dict(source.to_dict())

    assert restored.agent_profile == "vault-test"
    assert restored.agent_hermes_home == str(tmp_path / "vault-test")


def test_completion_event_rebuilds_source_with_routed_profile(tmp_path):
    from gateway.run import GatewayRunner

    gateway_home = tmp_path / "gateway"
    profile_home = tmp_path / "profiles" / "cybrel-test"
    gateway_home.mkdir()
    profile_home.mkdir(parents=True)

    with hermes_home_context(gateway_home):
        runner = object.__new__(GatewayRunner)
        runner.config = GatewayConfig()
        runner.session_store = SessionStore(gateway_home / "sessions", runner.config)

        source = runner._build_process_event_source(
            {
                "session_id": "proc_alpha",
                "session_key": "agent:cybrel-test:telegram:group:-1001:101",
                "platform": "telegram",
                "chat_type": "group",
                "chat_id": "-1001",
                "thread_id": "101",
                "user_id": "42",
                "agent_profile": "cybrel-test",
                "agent_hermes_home": str(profile_home),
            }
        )

    assert source is not None
    assert source.agent_profile == "cybrel-test"
    assert source.agent_hermes_home == str(profile_home)
    assert source.thread_id == "101"


@pytest.mark.asyncio
async def test_reload_mcp_reads_profile_cli_config_for_routed_topic(monkeypatch, tmp_path):
    from gateway.run import GatewayRunner
    from tools import mcp_tool

    gateway_home = tmp_path / "gateway"
    profile_home = tmp_path / "profiles" / "cybrel-test"
    gateway_home.mkdir()
    profile_home.mkdir(parents=True)
    captured_homes = []

    def fake_discover_mcp_tools():
        captured_homes.append(str(get_hermes_home()))
        mcp_tool._servers["profile-server"] = object()
        return [{"name": "profile_tool"}]

    monkeypatch.setattr(mcp_tool, "_servers", {})
    monkeypatch.setattr(mcp_tool, "_lock", threading.Lock())
    monkeypatch.setattr(mcp_tool, "shutdown_mcp_servers", lambda: None)
    monkeypatch.setattr(mcp_tool, "discover_mcp_tools", fake_discover_mcp_tools)

    with hermes_home_context(gateway_home):
        runner = object.__new__(GatewayRunner)
        runner.config = GatewayConfig()
        runner.session_store = SessionStore(gateway_home / "sessions", runner.config)
        source = _source(agent_profile="cybrel-test", agent_hermes_home=str(profile_home))
        event = MessageEvent(
            text="/reload-mcp",
            message_type=MessageType.COMMAND,
            source=source,
        )
        with hermes_home_context(profile_home):
            result = await runner._execute_mcp_reload(event)

    assert captured_homes == [str(profile_home)]
    assert "profile-server" in result


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


async def _run_captured_agent(monkeypatch, runner, source, hermes_home, *, context_prompt="", channel_prompt=None):
    from gateway import run as gateway_run

    _install_fake_agent(monkeypatch)
    monkeypatch.delenv("HERMES_EPHEMERAL_SYSTEM_PROMPT", raising=False)
    monkeypatch.delenv("HERMES_PREFILL_MESSAGES_FILE", raising=False)
    monkeypatch.setattr(gateway_run, "_resolve_runtime_agent_kwargs", _provider_runtime)
    monkeypatch.setattr(gateway_run, "load_dotenv", lambda *args, **kwargs: None)

    with hermes_home_context(hermes_home):
        result = await runner._run_agent(
            message="hi",
            context_prompt=context_prompt,
            history=[],
            source=source,
            session_id=f"session-{source.thread_id or 'main'}",
            session_key=build_session_key(source),
            channel_prompt=channel_prompt,
        )

    assert result["final_response"] == "ok"
    assert _CapturingAgent.last_init is not None
    return _CapturingAgent.last_init


class _CompressingAgent(_CapturingAgent):
    """Capturing agent that mutates ``session_id`` mid-turn to simulate the
    auto-compression code path: when ``run_conversation`` finishes, the
    agent's ``session_id`` differs from the one ``_run_agent`` was invoked
    with, which triggers the compression-split entry update at
    ``gateway/run.py:13041`` (bug E)."""

    new_session_id = "session-after-compression"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Constructor is invoked with ``session_id=...``; rewrite to a
        # different value so the post-result block sees a split.
        self.session_id = self.new_session_id


@pytest.mark.asyncio
async def test_run_agent_compression_split_writes_to_profile_session_store(
    monkeypatch, tmp_path
):
    """Bug E regression: when auto-compression mid-turn rotates the
    agent's ``session_id``, ``_run_agent`` must update the entry on the
    routed profile's SessionStore — not the gateway-global store.

    Before the fix, ``self.session_store._entries.get(...)`` and
    ``self.session_store._save()`` (gateway/run.py:13041 + :13044) wrote
    to the gateway store, leaving the profile entry stuck on the old
    ``session_id`` and making the compressed transcript unreachable on
    the next routed turn.
    """
    from gateway import run as gateway_run

    gateway_home = tmp_path / "gateway"
    profiles_root = gateway_home / "profiles"
    profile_home = profiles_root / "cybrel-test"
    profile_home.mkdir(parents=True)
    config = GatewayConfig(
        platforms={
            Platform.TELEGRAM: PlatformConfig(
                extra={"topic_profiles_safe_root": str(profiles_root)}
            )
        }
    )

    fake_run_agent = types.ModuleType("run_agent")
    fake_run_agent.AIAgent = _CompressingAgent
    monkeypatch.setitem(sys.modules, "run_agent", fake_run_agent)
    _CompressingAgent.last_init = None
    _CompressingAgent.inits = []
    monkeypatch.delenv("HERMES_EPHEMERAL_SYSTEM_PROMPT", raising=False)
    monkeypatch.delenv("HERMES_PREFILL_MESSAGES_FILE", raising=False)
    monkeypatch.setattr(gateway_run, "_resolve_runtime_agent_kwargs", _provider_runtime)
    monkeypatch.setattr(gateway_run, "load_dotenv", lambda *args, **kwargs: None)

    with hermes_home_context(gateway_home):
        runner = _make_runner(config)
        source = _source(
            agent_profile="cybrel-test",
            agent_hermes_home=str(profile_home),
        )
        session_key = build_session_key(source)

        profile_store = runner._session_store_for_source(source)
        gateway_store = runner.session_store

        original_session_id = "session-before-compression"
        profile_entry = profile_store.get_or_create_session(source)
        profile_entry.session_id = original_session_id
        profile_store._save()
        gateway_entry = gateway_store.get_or_create_session(source)
        gateway_entry.session_id = "gateway-untouched"
        gateway_store._save()

        with hermes_home_context(profile_home):
            result = await runner._run_agent(
                message="hi",
                context_prompt="",
                history=[],
                source=source,
                session_id=original_session_id,
                session_key=session_key,
            )

    assert result["final_response"] == "ok"

    # Profile store entry must reflect the post-compression session id.
    profile_entry_after = profile_store._entries.get(session_key)
    assert profile_entry_after is not None
    assert profile_entry_after.session_id == _CompressingAgent.new_session_id

    # Gateway store must NOT have been written. Its entry, if present, keeps
    # the original placeholder id; either the entry is absent or untouched.
    gw_entry_after = gateway_store._entries.get(session_key)
    if gw_entry_after is not None:
        assert gw_entry_after.session_id == "gateway-untouched"


@pytest.mark.asyncio
async def test_run_agent_auto_title_writes_to_profile_session_db(
    monkeypatch, tmp_path
):
    """Bug E regression: auto-title generation at ``gateway/run.py:13056``
    must call ``maybe_auto_title`` with the routed profile's SessionDB,
    not ``self._session_db`` (which is the gateway-global one)."""
    from gateway import run as gateway_run

    gateway_home = tmp_path / "gateway"
    profiles_root = gateway_home / "profiles"
    profile_home = profiles_root / "cybrel-test"
    profile_home.mkdir(parents=True)
    config = GatewayConfig(
        platforms={
            Platform.TELEGRAM: PlatformConfig(
                extra={"topic_profiles_safe_root": str(profiles_root)}
            )
        }
    )

    _install_fake_agent(monkeypatch)
    monkeypatch.delenv("HERMES_EPHEMERAL_SYSTEM_PROMPT", raising=False)
    monkeypatch.delenv("HERMES_PREFILL_MESSAGES_FILE", raising=False)
    monkeypatch.setattr(gateway_run, "_resolve_runtime_agent_kwargs", _provider_runtime)
    monkeypatch.setattr(gateway_run, "load_dotenv", lambda *args, **kwargs: None)

    captured_dbs = []

    def _fake_maybe_auto_title(session_db, session_id, message, final_response, all_msgs, **kwargs):
        captured_dbs.append(session_db)

    import agent.title_generator as title_module
    monkeypatch.setattr(title_module, "maybe_auto_title", _fake_maybe_auto_title)

    with hermes_home_context(gateway_home):
        runner = _make_runner(config)
        # Force the gateway-global _session_db to a sentinel so any leak is
        # observable in the captured arguments.
        gateway_db_sentinel = MagicMock(name="gateway_session_db")
        runner._session_db = gateway_db_sentinel

        source = _source(
            agent_profile="cybrel-test",
            agent_hermes_home=str(profile_home),
        )
        profile_store = runner._session_store_for_source(source)
        profile_db = profile_store._db
        assert profile_db is not gateway_db_sentinel

        with hermes_home_context(profile_home):
            await runner._run_agent(
                message="hi",
                context_prompt="",
                history=[],
                source=source,
                session_id="session-init",
                session_key=build_session_key(source),
            )

    assert captured_dbs, "maybe_auto_title was never called"
    assert captured_dbs[0] is profile_db
    assert captured_dbs[0] is not gateway_db_sentinel


@pytest.mark.asyncio
async def test_routed_profile_system_prompt_uses_profile_config_not_gateway_config(monkeypatch, tmp_path):
    gateway_home = tmp_path / "gateway"
    profiles_root = gateway_home / "profiles"
    profile_home = profiles_root / "cybrel-test"
    profile_home.mkdir(parents=True)
    (gateway_home / "config.yaml").write_text(
        'agent:\n  system_prompt: "Gateway config prompt"\n',
        encoding="utf-8",
    )
    _write_profile_config(
        profile_home,
        prompt="Profile config prompt",
        model="anthropic/claude-sonnet-4",
        toolsets=["web"],
    )
    config = GatewayConfig(
        platforms={
            Platform.TELEGRAM: PlatformConfig(
                extra={"topic_profiles_safe_root": str(profiles_root)}
            )
        }
    )

    with hermes_home_context(gateway_home):
        runner = _make_runner(config, gateway_prompt="Gateway runtime prompt")
    source = _source(agent_profile="cybrel-test", agent_hermes_home=str(profile_home))

    captured = await _run_captured_agent(
        monkeypatch,
        runner,
        source,
        profile_home,
        context_prompt="Context prompt",
        channel_prompt="Channel prompt",
    )

    assert captured["ephemeral_system_prompt"] == (
        "Context prompt\n\nChannel prompt\n\nProfile config prompt"
    )
    assert "Gateway" not in captured["ephemeral_system_prompt"]


@pytest.mark.asyncio
async def test_non_routed_system_prompt_keeps_existing_gateway_behavior(monkeypatch, tmp_path):
    gateway_home = tmp_path / "gateway"
    gateway_home.mkdir()
    (gateway_home / "config.yaml").write_text(
        'agent:\n  system_prompt: "Gateway config prompt"\n',
        encoding="utf-8",
    )

    with hermes_home_context(gateway_home):
        runner = _make_runner(gateway_prompt="Gateway runtime prompt")
    source = _source(agent_profile=None, agent_hermes_home=None)

    captured = await _run_captured_agent(
        monkeypatch,
        runner,
        source,
        gateway_home,
        context_prompt="Context prompt",
        channel_prompt="Channel prompt",
    )

    assert captured["ephemeral_system_prompt"] == (
        "Context prompt\n\nChannel prompt\n\nGateway runtime prompt"
    )


@pytest.mark.asyncio
async def test_routed_profile_soul_identity_is_loaded_from_profile_home(monkeypatch, tmp_path):
    gateway_home = tmp_path / "gateway"
    profiles_root = gateway_home / "profiles"
    profile_home = profiles_root / "cybrel-test"
    profile_home.mkdir(parents=True)
    (gateway_home / "SOUL.md").write_text("Gateway SOUL", encoding="utf-8")
    (profile_home / "SOUL.md").write_text("Profile SOUL", encoding="utf-8")
    _write_profile_config(profile_home, prompt="Profile prompt", model="profile-model", toolsets=["web"])
    config = GatewayConfig(
        platforms={
            Platform.TELEGRAM: PlatformConfig(
                extra={"topic_profiles_safe_root": str(profiles_root)}
            )
        }
    )

    with hermes_home_context(gateway_home):
        runner = _make_runner(config)
    source = _source(agent_profile="cybrel-test", agent_hermes_home=str(profile_home))

    captured = await _run_captured_agent(monkeypatch, runner, source, profile_home)

    assert captured["soul"] == "Profile SOUL"
    assert captured["hermes_home_at_init"] == str(profile_home)


@pytest.mark.asyncio
async def test_routed_profile_model_toolsets_and_disabled_toolsets_come_from_profile_config(monkeypatch, tmp_path):
    gateway_home = tmp_path / "gateway"
    profiles_root = gateway_home / "profiles"
    profile_home = profiles_root / "cybrel-test"
    profile_home.mkdir(parents=True)
    _write_profile_config(
        profile_home,
        prompt="Profile prompt",
        model="openrouter/profile-model",
        toolsets=["web"],
        disabled=["memory"],
    )
    config = GatewayConfig(
        platforms={
            Platform.TELEGRAM: PlatformConfig(
                extra={"topic_profiles_safe_root": str(profiles_root)}
            )
        }
    )

    with hermes_home_context(gateway_home):
        runner = _make_runner(config)
    source = _source(agent_profile="cybrel-test", agent_hermes_home=str(profile_home))

    captured = await _run_captured_agent(monkeypatch, runner, source, profile_home)

    assert captured["model"] == "openrouter/profile-model"
    assert "web" in set(captured["enabled_toolsets"])
    assert captured["disabled_toolsets"] == ["memory"]


@pytest.mark.asyncio
async def test_routed_profile_provider_routing_comes_from_profile_config(monkeypatch, tmp_path):
    gateway_home = tmp_path / "gateway"
    profiles_root = gateway_home / "profiles"
    profile_home = profiles_root / "cybrel-test"
    profile_home.mkdir(parents=True)
    _write_profile_config(
        profile_home,
        prompt="Profile prompt",
        model="openrouter/profile-model",
        toolsets=["web"],
        provider_routing={
            "only": ["anthropic"],
            "ignore": ["deepinfra"],
            "order": ["anthropic", "google"],
            "sort": "throughput",
            "require_parameters": True,
            "data_collection": "deny",
        },
    )
    config = GatewayConfig(
        platforms={
            Platform.TELEGRAM: PlatformConfig(
                extra={"topic_profiles_safe_root": str(profiles_root)}
            )
        }
    )

    with hermes_home_context(gateway_home):
        runner = _make_runner(config)
    source = _source(agent_profile="cybrel-test", agent_hermes_home=str(profile_home))

    captured = await _run_captured_agent(monkeypatch, runner, source, profile_home)

    assert captured["providers_allowed"] == ["anthropic"]
    assert captured["providers_ignored"] == ["deepinfra"]
    assert captured["providers_order"] == ["anthropic", "google"]
    assert captured["provider_sort"] == "throughput"
    assert captured["provider_require_parameters"] is True
    assert captured["provider_data_collection"] == "deny"


@pytest.mark.asyncio
async def test_routed_profile_prefill_file_resolves_relative_to_profile_home(monkeypatch, tmp_path):
    gateway_home = tmp_path / "gateway"
    profiles_root = gateway_home / "profiles"
    profile_home = profiles_root / "cybrel-test"
    profile_home.mkdir(parents=True)
    profile_prefill = [{"role": "system", "content": "profile prefill"}]
    gateway_prefill = [{"role": "system", "content": "gateway prefill"}]
    (profile_home / "prefill.json").write_text(json.dumps(profile_prefill), encoding="utf-8")
    _write_profile_config(
        profile_home,
        prompt="Profile prompt",
        model="openrouter/profile-model",
        toolsets=["web"],
        prefill="prefill.json",
    )
    config = GatewayConfig(
        platforms={
            Platform.TELEGRAM: PlatformConfig(
                extra={"topic_profiles_safe_root": str(profiles_root)}
            )
        }
    )

    with hermes_home_context(gateway_home):
        runner = _make_runner(config)
        runner._prefill_messages = gateway_prefill
    source = _source(agent_profile="cybrel-test", agent_hermes_home=str(profile_home))

    captured = await _run_captured_agent(monkeypatch, runner, source, profile_home)

    assert captured["prefill_messages"] == profile_prefill
    assert captured["prefill_messages"] != gateway_prefill


def test_agent_cache_signature_changes_when_profile_prompt_provider_or_prefill_changes():
    from gateway.run import GatewayRunner

    runtime = {
        "provider": "openrouter",
        "base_url": "https://openrouter.ai/api/v1",
        "api_mode": "chat_completions",
        "api_key": "***",
    }
    base_keys = {
        "provider_routing.digest": GatewayRunner._stable_config_digest({"order": ["anthropic"]}),
        "prefill_messages.digest": GatewayRunner._stable_config_digest(
            [{"role": "system", "content": "prefill-a"}]
        ),
    }
    base = GatewayRunner._agent_config_signature(
        "model-a",
        runtime,
        ["web"],
        "prompt-a",
        cache_keys=base_keys,
    )
    changed_prompt = GatewayRunner._agent_config_signature(
        "model-a",
        runtime,
        ["web"],
        "prompt-b",
        cache_keys=base_keys,
    )
    changed_provider = GatewayRunner._agent_config_signature(
        "model-a",
        runtime,
        ["web"],
        "prompt-a",
        cache_keys={
            **base_keys,
            "provider_routing.digest": GatewayRunner._stable_config_digest({"order": ["google"]}),
        },
    )
    changed_prefill = GatewayRunner._agent_config_signature(
        "model-a",
        runtime,
        ["web"],
        "prompt-a",
        cache_keys={
            **base_keys,
            "prefill_messages.digest": GatewayRunner._stable_config_digest(
                [{"role": "system", "content": "prefill-b"}]
            ),
        },
    )

    assert changed_prompt != base
    assert changed_provider != base
    assert changed_prefill != base


def test_agent_cache_signature_changes_when_profile_home_or_soul_changes(tmp_path):
    from gateway.run import GatewayRunner

    profile_a = tmp_path / "profiles" / "cybrel-test"
    profile_b = tmp_path / "profiles" / "vault-test"
    profile_a.mkdir(parents=True)
    profile_b.mkdir(parents=True)
    (profile_a / "SOUL.md").write_text("SOUL A", encoding="utf-8")
    (profile_b / "SOUL.md").write_text("SOUL A", encoding="utf-8")
    runtime = {"provider": "openrouter", "base_url": "", "api_mode": "chat_completions", "api_key": "***"}

    base = GatewayRunner._agent_config_signature(
        "model-a",
        runtime,
        ["web"],
        "prompt-a",
        cache_keys={
            "hermes_home": str(profile_a),
            "soul_md.digest": GatewayRunner._file_content_digest(profile_a / "SOUL.md"),
        },
    )
    changed_home = GatewayRunner._agent_config_signature(
        "model-a",
        runtime,
        ["web"],
        "prompt-a",
        cache_keys={
            "hermes_home": str(profile_b),
            "soul_md.digest": GatewayRunner._file_content_digest(profile_b / "SOUL.md"),
        },
    )
    (profile_a / "SOUL.md").write_text("SOUL B", encoding="utf-8")
    changed_soul = GatewayRunner._agent_config_signature(
        "model-a",
        runtime,
        ["web"],
        "prompt-a",
        cache_keys={
            "hermes_home": str(profile_a),
            "soul_md.digest": GatewayRunner._file_content_digest(profile_a / "SOUL.md"),
        },
    )

    assert changed_home != base
    assert changed_soul != base


@pytest.mark.asyncio
async def test_routed_profile_soul_change_busts_cached_agent(monkeypatch, tmp_path):
    gateway_home = tmp_path / "gateway"
    profiles_root = gateway_home / "profiles"
    profile_home = profiles_root / "cybrel-test"
    profile_home.mkdir(parents=True)
    _write_profile_config(profile_home, prompt="Profile prompt", model="profile-model", toolsets=["web"])
    config = GatewayConfig(
        platforms={
            Platform.TELEGRAM: PlatformConfig(
                extra={"topic_profiles_safe_root": str(profiles_root)}
            )
        }
    )

    with hermes_home_context(gateway_home):
        runner = _make_runner(config)
    source = _source(agent_profile="cybrel-test", agent_hermes_home=str(profile_home))

    _install_fake_agent(monkeypatch)
    monkeypatch.delenv("HERMES_EPHEMERAL_SYSTEM_PROMPT", raising=False)
    monkeypatch.delenv("HERMES_PREFILL_MESSAGES_FILE", raising=False)
    from gateway import run as gateway_run
    monkeypatch.setattr(gateway_run, "_resolve_runtime_agent_kwargs", _provider_runtime)
    monkeypatch.setattr(gateway_run, "load_dotenv", lambda *args, **kwargs: None)

    with hermes_home_context(profile_home):
        await runner._run_agent(
            message="hi",
            context_prompt="",
            history=[],
            source=source,
            session_id=f"session-{source.thread_id or 'main'}",
            session_key=build_session_key(source),
        )
    (profile_home / "SOUL.md").write_text("Profile SOUL after first turn", encoding="utf-8")
    with hermes_home_context(profile_home):
        await runner._run_agent(
            message="hi",
            context_prompt="",
            history=[],
            source=source,
            session_id=f"session-{source.thread_id or 'main'}",
            session_key=build_session_key(source),
        )

    assert len(_CapturingAgent.inits) == 2
    assert _CapturingAgent.inits[-1]["soul"] == "Profile SOUL after first turn"


@pytest.mark.asyncio
async def test_reset_command_uses_profile_session_store_for_routed_topic(tmp_path):
    from gateway import run as gateway_run

    gateway_home = tmp_path / "gateway"
    profile_home = gateway_home / "profiles" / "cybrel-test"
    profile_home.mkdir(parents=True)
    config = GatewayConfig(
        platforms={
            Platform.TELEGRAM: PlatformConfig(
                extra={"topic_profiles_safe_root": str(gateway_home / "profiles")}
            )
        }
    )

    with hermes_home_context(gateway_home):
        runner = _make_runner(config)
        runner.hooks = SimpleNamespace(loaded_hooks=False, emit=AsyncMock())
        source = _source(agent_profile="cybrel-test", agent_hermes_home=str(profile_home))
        profile_store = runner._session_store_for_source(source)
        old_entry = profile_store.get_or_create_session(source)
        gateway_store = runner.session_store
        gateway_run._hermes_home = gateway_home

        event = MessageEvent(text="/new", message_type=MessageType.TEXT, source=source)
        await runner._handle_reset_command(event)

    profile_entry = profile_store.get_or_create_session(source)
    assert profile_entry.session_id != old_entry.session_id
    assert runner._session_key_for_source(source) not in gateway_store._entries


@pytest.mark.asyncio
async def test_undo_command_rewrites_profile_transcript_only_for_routed_topic(tmp_path):
    gateway_home = tmp_path / "gateway"
    profiles_root = gateway_home / "profiles"
    profile_home = profiles_root / "cybrel-test"
    profile_home.mkdir(parents=True)
    config = GatewayConfig(
        platforms={
            Platform.TELEGRAM: PlatformConfig(
                extra={"topic_profiles_safe_root": str(profiles_root)}
            )
        }
    )

    with hermes_home_context(gateway_home):
        runner = _make_runner(config)
        source = _source(agent_profile="cybrel-test", agent_hermes_home=str(profile_home))
        profile_store = runner._session_store_for_source(source)
        profile_entry = profile_store.get_or_create_session(source)
        gateway_entry = runner.session_store.get_or_create_session(source)
        profile_store.rewrite_transcript(
            profile_entry.session_id,
            [
                {"role": "user", "content": "profile keep"},
                {"role": "assistant", "content": "profile kept"},
                {"role": "user", "content": "profile remove"},
                {"role": "assistant", "content": "profile removed"},
            ],
        )
        runner.session_store.rewrite_transcript(
            gateway_entry.session_id,
            [
                {"role": "user", "content": "gateway keep"},
                {"role": "assistant", "content": "gateway kept"},
                {"role": "user", "content": "gateway remove"},
                {"role": "assistant", "content": "gateway removed"},
            ],
        )

        event = MessageEvent(text="/undo", message_type=MessageType.TEXT, source=source)
        response = await runner._handle_undo_command(event)

    assert "profile remove" in response
    assert [m["content"] for m in profile_store.load_transcript(profile_entry.session_id)] == [
        "profile keep",
        "profile kept",
    ]
    assert [m["content"] for m in runner.session_store.load_transcript(gateway_entry.session_id)] == [
        "gateway keep",
        "gateway kept",
        "gateway remove",
        "gateway removed",
    ]


@pytest.mark.asyncio
async def test_retry_command_rewrites_profile_transcript_only_for_routed_topic(tmp_path):
    gateway_home = tmp_path / "gateway"
    profiles_root = gateway_home / "profiles"
    profile_home = profiles_root / "cybrel-test"
    profile_home.mkdir(parents=True)
    config = GatewayConfig(
        platforms={
            Platform.TELEGRAM: PlatformConfig(
                extra={"topic_profiles_safe_root": str(profiles_root)}
            )
        }
    )

    with hermes_home_context(gateway_home):
        runner = _make_runner(config)
        runner._handle_message = AsyncMock(return_value="retried")
        source = _source(agent_profile="cybrel-test", agent_hermes_home=str(profile_home))
        profile_store = runner._session_store_for_source(source)
        profile_entry = profile_store.get_or_create_session(source)
        gateway_entry = runner.session_store.get_or_create_session(source)
        profile_store.rewrite_transcript(
            profile_entry.session_id,
            [
                {"role": "user", "content": "profile keep"},
                {"role": "assistant", "content": "profile kept"},
                {"role": "user", "content": "profile retry"},
                {"role": "assistant", "content": "profile answer"},
            ],
        )
        runner.session_store.rewrite_transcript(
            gateway_entry.session_id,
            [
                {"role": "user", "content": "gateway keep"},
                {"role": "assistant", "content": "gateway kept"},
                {"role": "user", "content": "gateway retry"},
                {"role": "assistant", "content": "gateway answer"},
            ],
        )

        event = MessageEvent(text="/retry", message_type=MessageType.TEXT, source=source)
        response = await runner._handle_retry_command(event)

    assert response == "retried"
    retry_event = runner._handle_message.await_args.args[0]
    assert retry_event.text == "profile retry"
    assert [m["content"] for m in profile_store.load_transcript(profile_entry.session_id)] == [
        "profile keep",
        "profile kept",
    ]
    assert [m["content"] for m in runner.session_store.load_transcript(gateway_entry.session_id)] == [
        "gateway keep",
        "gateway kept",
        "gateway retry",
        "gateway answer",
    ]


@pytest.mark.asyncio
async def test_title_command_uses_profile_session_db_for_routed_topic(tmp_path):
    gateway_home = tmp_path / "gateway"
    profiles_root = gateway_home / "profiles"
    profile_home = profiles_root / "cybrel-test"
    profile_home.mkdir(parents=True)
    config = GatewayConfig(
        platforms={
            Platform.TELEGRAM: PlatformConfig(
                extra={"topic_profiles_safe_root": str(profiles_root)}
            )
        }
    )

    with hermes_home_context(gateway_home):
        runner = _make_runner(config)
        source = _source(agent_profile="cybrel-test", agent_hermes_home=str(profile_home))
        profile_store = runner._session_store_for_source(source)
        profile_entry = profile_store.get_or_create_session(source)
        gateway_entry = runner.session_store.get_or_create_session(source)
        runner._session_db = runner.session_store._db

        event = MessageEvent(text="/title Profile Title", message_type=MessageType.TEXT, source=source)
        response = await runner._handle_title_command(event)

    assert "Profile Title" in response
    assert profile_store._db.get_session_title(profile_entry.session_id) == "Profile Title"
    assert runner.session_store._db.get_session_title(gateway_entry.session_id) is None


@pytest.mark.asyncio
async def test_status_command_reads_profile_session_db_for_routed_topic(tmp_path):
    gateway_home = tmp_path / "gateway"
    profiles_root = gateway_home / "profiles"
    profile_home = profiles_root / "cybrel-test"
    profile_home.mkdir(parents=True)
    config = GatewayConfig(
        platforms={
            Platform.TELEGRAM: PlatformConfig(
                extra={"topic_profiles_safe_root": str(profiles_root)}
            )
        }
    )

    with hermes_home_context(gateway_home):
        runner = _make_runner(config)
        source = _source(agent_profile="cybrel-test", agent_hermes_home=str(profile_home))
        profile_store = runner._session_store_for_source(source)
        profile_entry = profile_store.get_or_create_session(source)
        profile_store._db.set_session_title(profile_entry.session_id, "Profile DB Title")
        runner._session_db = runner.session_store._db

        event = MessageEvent(text="/status", message_type=MessageType.TEXT, source=source)
        response = await runner._handle_status_command(event)

    assert "Profile DB Title" in response


@pytest.mark.asyncio
async def test_personality_command_is_disabled_for_routed_profile_topics(tmp_path):
    from gateway import run as gateway_run

    gateway_home = tmp_path / "gateway"
    profile_home = gateway_home / "profiles" / "cybrel-test"
    profile_home.mkdir(parents=True)
    gateway_home.mkdir(exist_ok=True)
    gateway_config = gateway_home / "config.yaml"
    gateway_config.write_text('agent:\n  system_prompt: "Gateway prompt"\n', encoding="utf-8")
    (profile_home / "config.yaml").write_text(
        "agent:\n"
        "  personalities:\n"
        "    analyst: Routed analyst prompt\n",
        encoding="utf-8",
    )
    config = GatewayConfig()

    with hermes_home_context(gateway_home):
        runner = _make_runner(config)
        gateway_run._hermes_home = gateway_home
    source = _source(agent_profile="cybrel-test", agent_hermes_home=str(profile_home))
    event = MessageEvent(text="/personality analyst", message_type=MessageType.TEXT, source=source)

    with hermes_home_context(profile_home):
        response = await runner._handle_personality_command(event)

    assert "disabled" in response.lower()
    assert "Gateway prompt" in gateway_config.read_text(encoding="utf-8")
    assert "Routed analyst prompt" not in gateway_config.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_personality_command_still_works_for_non_routed_gateway_sessions(tmp_path):
    from gateway import run as gateway_run

    gateway_home = tmp_path / "gateway"
    gateway_home.mkdir()
    gateway_config = gateway_home / "config.yaml"
    gateway_config.write_text(
        "agent:\n"
        "  personalities:\n"
        "    analyst: Gateway analyst prompt\n",
        encoding="utf-8",
    )
    config = GatewayConfig()

    with hermes_home_context(gateway_home):
        runner = _make_runner(config)
        gateway_run._hermes_home = gateway_home
        source = _source(agent_profile=None, agent_hermes_home=None)
        event = MessageEvent(text="/personality analyst", message_type=MessageType.TEXT, source=source)
        response = await runner._handle_personality_command(event)

    assert "set to" in response.lower()
    assert "Gateway analyst prompt" in gateway_config.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_routed_profile_memory_store_uses_profile_memories_dir(monkeypatch, tmp_path):
    gateway_home = tmp_path / "gateway"
    profiles_root = gateway_home / "profiles"
    profile_home = profiles_root / "cybrel-test"
    profile_home.mkdir(parents=True)
    _write_profile_config(profile_home, prompt="Profile prompt", model="profile-model", toolsets=["web"])
    config = GatewayConfig(
        platforms={
            Platform.TELEGRAM: PlatformConfig(
                extra={"topic_profiles_safe_root": str(profiles_root)}
            )
        }
    )

    with hermes_home_context(gateway_home):
        runner = _make_runner(config)
    source = _source(agent_profile="cybrel-test", agent_hermes_home=str(profile_home))

    captured = await _run_captured_agent(monkeypatch, runner, source, profile_home)

    assert captured["memory_dir"] == str(profile_home / "memories")


@pytest.mark.asyncio
async def test_concurrent_routed_profiles_do_not_cross_contaminate_prompt_env_or_toolsets(monkeypatch, tmp_path):
    from gateway import run as gateway_run

    gateway_home = tmp_path / "gateway"
    profiles_root = gateway_home / "profiles"
    profile_a = profiles_root / "cybrel-test"
    profile_b = profiles_root / "vault-test"
    profile_a.mkdir(parents=True)
    profile_b.mkdir(parents=True)
    _write_profile_config(profile_a, prompt="Prompt A", model="model-a", toolsets=["web"])
    _write_profile_config(profile_b, prompt="Prompt B", model="model-b", toolsets=["todo"])
    (profile_a / ".env").write_text("OPENROUTER_API_KEY=sk-test-profile-a\n", encoding="utf-8")
    (profile_b / ".env").write_text("OPENROUTER_API_KEY=sk-test-profile-b\n", encoding="utf-8")
    config = GatewayConfig(
        platforms={
            Platform.TELEGRAM: PlatformConfig(
                extra={"topic_profiles_safe_root": str(profiles_root)}
            )
        }
    )

    _install_fake_agent(monkeypatch)
    monkeypatch.delenv("HERMES_EPHEMERAL_SYSTEM_PROMPT", raising=False)
    monkeypatch.delenv("HERMES_PREFILL_MESSAGES_FILE", raising=False)
    monkeypatch.setattr(gateway_run, "_resolve_runtime_agent_kwargs", _provider_runtime)
    monkeypatch.setattr(gateway_run, "load_dotenv", lambda *args, **kwargs: None)
    with hermes_home_context(gateway_home):
        runner = _make_runner(config)

    source_a = _source(
        thread_id="101",
        agent_profile="cybrel-test",
        agent_hermes_home=str(profile_a),
    )
    source_b = _source(
        thread_id="202",
        agent_profile="vault-test",
        agent_hermes_home=str(profile_b),
    )

    async def run_one(source, home):
        with hermes_home_context(home):
            return await runner._run_agent(
                message="hi",
                context_prompt="",
                history=[],
                source=source,
                session_id=f"session-{source.agent_profile}",
                session_key=build_session_key(source),
            )

    result_a, result_b = await asyncio.gather(
        run_one(source_a, profile_a),
        run_one(source_b, profile_b),
    )

    assert result_a["final_response"] == "ok"
    assert result_b["final_response"] == "ok"
    by_key = {item["gateway_session_key"]: item for item in _CapturingAgent.inits}
    captured_a = by_key["agent:cybrel-test:telegram:group:-1001:101"]
    captured_b = by_key["agent:vault-test:telegram:group:-1001:202"]
    assert captured_a["model"] == "model-a"
    assert captured_b["model"] == "model-b"
    assert captured_a["ephemeral_system_prompt"] == "Prompt A"
    assert captured_b["ephemeral_system_prompt"] == "Prompt B"
    assert captured_a["api_key"] == "sk-test-profile-a"
    assert captured_b["api_key"] == "sk-test-profile-b"
    assert "web" in set(captured_a["enabled_toolsets"])
    assert "todo" in set(captured_b["enabled_toolsets"])
