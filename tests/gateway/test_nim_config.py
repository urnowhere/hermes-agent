from unittest import mock

import pytest

from gateway.config import (
    GatewayConfig,
    Platform,
    PlatformConfig,
    _default_nim_bridge_command,
    _resolve_nim_bridge_command,
    load_nim_config,
    load_nim_instances,
    parse_nim_token,
)
from gateway.platforms.base import MessageEvent
from gateway.run import GatewayRunner
from gateway.session import SessionSource, build_session_context


class TestParseNimToken:
    def test_pipe_separator(self):
        creds = parse_nim_token("app|bot|secret")
        assert creds is not None
        assert creds.app_key == "app"
        assert creds.account == "bot"
        assert creds.token == "secret"

    def test_dash_separator(self):
        creds = parse_nim_token("app-bot-secret")
        assert creds is not None
        assert creds.app_key == "app"
        assert creds.account == "bot"
        assert creds.token == "secret"


class TestLoadNimConfig:
    def test_loads_from_platform_token(self):
        resolved = load_nim_config(
            PlatformConfig(
                enabled=True,
                extra={
                    "nimToken": "from-token|bot|secret",
                    "p2p": {"policy": "allowlist", "allowFrom": ["alice", "bob"]},
                    "team": {"policy": "allowlist", "allowFrom": ["team-a", "1|team-b|alice"]},
                    "qchat": {"policy": "allowlist", "allowFrom": ["server-1|channel-9|alice"]},
                    "advanced": {
                        "mediaMaxMb": 64,
                        "textChunkLimit": 2048,
                        "debug": True,
                    },
                },
            )
        )

        assert resolved.configured() is True
        assert resolved.credentials is not None
        assert resolved.credentials.app_key == "from-token"
        assert resolved.bridge_command == _default_nim_bridge_command()
        assert resolved.p2p_policy == "allowlist"
        assert resolved.p2p_allow_from == ["alice", "bob"]
        assert resolved.team_policy == "allowlist"
        assert resolved.team_allow_from == ["team-a", "1|team-b|alice"]
        assert resolved.qchat_policy == "allowlist"
        assert resolved.qchat_allow_from == ["server-1|channel-9|alice"]
        assert resolved.media_max_mb == 64
        assert resolved.text_chunk_limit == 2048
        assert resolved.debug is True

    def test_loads_from_explicit_fields(self):
        resolved = load_nim_config(
            PlatformConfig(
                enabled=True,
                extra={
                    "app_key": "app",
                    "account": "bot",
                    "token": "secret",
                    "allowed_users": ["alice", "bob"],
                    "group_allowlist": ["team-a", "team-b"],
                    "group_policy": "open",
                    "allow_all_users": True,
                },
            )
        )

        assert resolved.configured() is True
        assert resolved.allowed_users == ["alice", "bob"]
        assert resolved.group_allowlist == ["team-a", "team-b"]
        assert resolved.group_policy == "open"
        assert resolved.allow_all_users is True

    def test_compact_credentials_default_to_open_access(self):
        resolved = load_nim_config(
            PlatformConfig(enabled=True, extra={"nim_token": "app|bot|secret"})
        )

        assert resolved.configured() is True
        assert resolved.credentials is not None
        assert resolved.allow_all_users is True
        assert resolved.group_policy == "open"
        assert resolved.group_allowlist == []

    def test_default_bridge_command_uses_bundled_node_script(self):
        assert _resolve_nim_bridge_command(None) == _default_nim_bridge_command()
        assert _resolve_nim_bridge_command(None)[0] == "node"
        assert "nim_bot_py/bridge_js/index.mjs" in _resolve_nim_bridge_command(None)[1]

    def test_load_config_ignores_legacy_bridge_override(self):
        resolved = load_nim_config(
            PlatformConfig(
                enabled=True,
                extra={
                    "nim_token": "app|bot|secret",
                    "bridge_command": ["node", "/tmp/custom/index.mjs"],
                },
            ),
            {"NIM_BRIDGE_COMMAND": "node /tmp/ignored/index.mjs"},
        )

        assert resolved.bridge_command == _default_nim_bridge_command()

    def test_loads_multiple_instances_and_prefixes_chat_ids(self):
        instances = load_nim_instances(
            PlatformConfig(
                enabled=True,
                extra={
                    "nim_token": "app|default-bot|secret-default",
                    "instances": [
                        {
                            "nimToken": "app|work-bot|secret-work",
                            "home_channel": "user:42",
                        }
                    ],
                },
            )
        )

        assert [item.instance_name for item in instances] == ["default", "work-bot"]
        assert instances[0].route_prefix == "default/"
        assert instances[1].route_prefix == "work-bot/"
        assert instances[1].home_channel == "work-bot/user:42"

    def test_config_instances_ignore_env_credentials(self):
        instances = load_nim_instances(
            PlatformConfig(
                enabled=True,
                extra={
                    "instances": [
                        {"instance_name": "main", "nimToken": "app|yaml|secret-yaml"},
                    ],
                },
            ),
            {
                "NIM_CREDENTIALS": "app|legacy|secret-legacy",
            },
        )

        assert [item.instance_name for item in instances] == ["main"]
        assert [item.credentials.account for item in instances if item.credentials is not None] == ["yaml"]


class TestConnectedPlatforms:
    def test_nim_recognized_via_config_extra(self):
        config = GatewayConfig(
            platforms={
                Platform.NIM: PlatformConfig(
                    enabled=True,
                    extra={"nim_token": "app|bot|secret"},
                ),
            }
        )

        assert Platform.NIM in config.get_connected_platforms()

    def test_nim_home_channel_falls_back_to_first_instance(self):
        config = GatewayConfig(
            platforms={
                Platform.NIM: PlatformConfig(
                    enabled=True,
                    extra={
                        "instances": [
                            {
                                "instance_name": "work",
                                "nim_token": "app|work|secret",
                                "home_channel": "user:100",
                            }
                        ],
                    },
                ),
            }
        )

        home = config.get_home_channel(Platform.NIM)
        assert home is not None
        assert home.chat_id == "user:100"

    def test_nim_home_channel_resolves_per_instance_route(self):
        config = GatewayConfig(
            platforms={
                Platform.NIM: PlatformConfig(
                    enabled=True,
                    extra={
                        "instances": [
                            {
                                "instance_name": "default",
                                "nim_token": "app|default|secret",
                                "home_channel": "user:100",
                            },
                            {
                                "instance_name": "work",
                                "nim_token": "app|work|secret",
                                "home_channel": "user:200",
                            },
                        ],
                    },
                ),
            }
        )

        home = config.get_home_channel(Platform.NIM, source_chat_id="work/user:42")
        assert home is not None
        assert home.chat_id == "work/user:200"


class TestNimSessionContext:
    def test_build_session_context_uses_current_nim_instance_home_channel(self):
        config = GatewayConfig(
            platforms={
                Platform.NIM: PlatformConfig(
                    enabled=True,
                    extra={
                        "instances": [
                            {
                                "instance_name": "default",
                                "nim_token": "app|default|secret",
                                "home_channel": "user:100",
                            },
                            {
                                "instance_name": "work",
                                "nim_token": "app|work|secret",
                                "home_channel": "user:200",
                            },
                        ],
                    },
                ),
            }
        )

        source = SessionSource(
            platform=Platform.NIM,
            chat_id="work/user:42",
            chat_type="dm",
        )
        context = build_session_context(source, config)

        assert Platform.NIM in context.home_channels
        assert context.home_channels[Platform.NIM].chat_id == "work/user:200"


class TestNimSetHome:
    @pytest.mark.asyncio
    async def test_sethome_updates_matching_nim_instance(self, monkeypatch, tmp_path):
        import gateway.run as gateway_run

        monkeypatch.setattr(gateway_run, "_hermes_home", tmp_path)
        (tmp_path / "config.yaml").write_text(
            "nim:\n"
            "  instances:\n"
            "    - instance_name: default\n"
            "      nimToken: app|default|secret\n"
            "    - instance_name: work\n"
            "      nimToken: app|work|secret\n",
            encoding="utf-8",
        )

        runner = object.__new__(GatewayRunner)
        runner.config = GatewayConfig(
            platforms={
                Platform.NIM: PlatformConfig(
                    enabled=True,
                    extra={
                        "instances": [
                            {"instance_name": "default", "nimToken": "app|default|secret"},
                            {"instance_name": "work", "nimToken": "app|work|secret"},
                        ],
                    },
                )
            }
        )

        event = MessageEvent(
            text="/sethome",
            source=SessionSource(
                platform=Platform.NIM,
                chat_id="work/user:42",
                chat_name="Work DM",
                chat_type="dm",
            ),
        )

        result = await runner._handle_set_home_command(event)

        assert "Home channel set" in result
        updated = (tmp_path / "config.yaml").read_text(encoding="utf-8")
        assert "instance_name: work" in updated
        assert "home_channel: user:42" in updated
