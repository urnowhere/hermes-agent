"""Tests for Discord clarify prompts rendered as buttons."""

import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from gateway.config import PlatformConfig


class _FakeView:
    def __init__(self, timeout=None):
        self.timeout = timeout
        self.children = []

    def add_item(self, item):
        self.children.append(item)


class _FakeButton:
    def __init__(self, *, label=None, style=None, custom_id=None):
        self.label = label
        self.style = style
        self.custom_id = custom_id
        self.callback = None
        self.disabled = False


class _FakeEmbed:
    def __init__(self, *, title=None, description=None, color=None):
        self.title = title
        self.description = description
        self.color = color
        self.fields = []
        self.footer = None

    def add_field(self, *, name, value, inline=False):
        self.fields.append(SimpleNamespace(name=name, value=value, inline=inline))

    def set_footer(self, *, text):
        self.footer = text


def _ensure_discord_mock():
    if "discord" in sys.modules and hasattr(sys.modules["discord"], "__file__"):
        return

    discord_mod = MagicMock()
    discord_mod.Intents.default.return_value = MagicMock()
    discord_mod.Client = MagicMock
    discord_mod.File = MagicMock
    discord_mod.DMChannel = type("DMChannel", (), {})
    discord_mod.Thread = type("Thread", (), {})
    discord_mod.ForumChannel = type("ForumChannel", (), {})
    discord_mod.ui = SimpleNamespace(
        View=_FakeView,
        Button=_FakeButton,
        button=lambda *a, **k: (lambda fn: fn),
    )
    discord_mod.ButtonStyle = SimpleNamespace(
        primary=1,
        green=2,
        grey=3,
        blurple=4,
        red=5,
    )
    discord_mod.Color = SimpleNamespace(
        orange=lambda: "orange",
        green=lambda: "green",
        blue=lambda: "blue",
        purple=lambda: "purple",
        red=lambda: "red",
        greyple=lambda: "greyple",
    )
    discord_mod.Interaction = object
    discord_mod.Embed = _FakeEmbed
    discord_mod.app_commands = SimpleNamespace(
        describe=lambda **kwargs: (lambda fn: fn),
        choices=lambda **kwargs: (lambda fn: fn),
        Choice=lambda **kwargs: SimpleNamespace(**kwargs),
    )
    discord_mod.opus = SimpleNamespace(is_loaded=lambda: True)

    ext_mod = MagicMock()
    commands_mod = MagicMock()
    commands_mod.Bot = MagicMock
    ext_mod.commands = commands_mod

    sys.modules["discord"] = discord_mod
    sys.modules["discord.ext"] = ext_mod
    sys.modules["discord.ext.commands"] = commands_mod


_ensure_discord_mock()

from gateway.platforms.discord import ClarifyChoiceView, DiscordAdapter  # noqa: E402


@pytest.mark.asyncio
async def test_send_clarify_prompt_renders_choice_buttons():
    adapter = DiscordAdapter(PlatformConfig(enabled=True, token="test-token"))
    channel = AsyncMock()
    sent_msg = MagicMock()
    sent_msg.id = 42
    channel.send = AsyncMock(return_value=sent_msg)

    client = MagicMock()
    client.get_channel.return_value = channel
    adapter._client = client

    answers = []
    result = await adapter.send_clarify_prompt(
        chat_id="123",
        question="Pick a path",
        choices=["Fast", "Careful"],
        session_key="session",
        on_answer=answers.append,
    )

    assert result.success is True
    _, kwargs = channel.send.call_args
    assert kwargs["embed"].title == "Hermes needs your input"
    assert kwargs["embed"].description == "Pick a path"
    view = kwargs["view"]
    assert [button.label for button in view.children] == ["Fast", "Careful"]
    assert all(button.callback for button in view.children)


@pytest.mark.asyncio
async def test_clarify_choice_button_resolves_answer():
    answers = []
    view = ClarifyChoiceView(
        choices=["Fast", "Careful"],
        on_answer=answers.append,
        allowed_user_ids={"123"},
    )

    embed = _FakeEmbed(title="Hermes needs your input", description="Pick")
    interaction = SimpleNamespace(
        user=SimpleNamespace(id="123", display_name="Leon"),
        message=SimpleNamespace(embeds=[embed]),
        response=SimpleNamespace(
            edit_message=AsyncMock(),
            send_message=AsyncMock(),
        ),
    )

    await view.children[1].callback(interaction)

    assert answers == ["Careful"]
    assert all(button.disabled for button in view.children)
    interaction.response.edit_message.assert_awaited_once()
    interaction.response.send_message.assert_not_awaited()
    assert embed.footer == "Answered by Leon"
