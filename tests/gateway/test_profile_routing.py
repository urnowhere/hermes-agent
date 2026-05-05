"""Tests for gateway/profile_routing.py — ProfileRoute parsing and matching."""

import pytest
from unittest.mock import MagicMock
from dataclasses import dataclass

from gateway.profile_routing import (
    ProfileRoute,
    parse_profile_routes,
    match_profile_route,
)
from gateway.config import Platform


# ── Minimal SessionSource stand-in ──────────────────────────────────

def _make_source(platform="discord", chat_id=None, thread_id=None, user_id=None, chat_type="group"):
    """Create a fake SessionSource-like object with a real Platform enum."""
    src = MagicMock()
    src.platform = Platform(platform)
    src.chat_id = chat_id
    src.thread_id = thread_id
    src.user_id = user_id
    src.chat_type = chat_type
    return src


# ── ProfileRoute.from_dict ───────────────────────────────────────────

class TestProfileRouteFromDict:
    def test_basic_route(self):
        route = ProfileRoute.from_dict({
            "name": "trader",
            "platform": "discord",
            "profile": "trader",
            "chat_id": "123",
        })
        assert route.name == "trader"
        assert route.profile == "trader"
        assert route.chat_id == "123"
        assert route.enabled is True
        assert route.thread_id is None

    def test_disabled_route(self):
        route = ProfileRoute.from_dict({
            "name": "test",
            "platform": "discord",
            "profile": "test",
            "enabled": False,
        })
        assert route.enabled is False

    def test_unknown_platform_raises(self):
        with pytest.raises(ValueError):
            ProfileRoute.from_dict({
                "name": "bad",
                "platform": "nonexistent",
                "profile": "x",
            })


# ── Specificity ──────────────────────────────────────────────────────

class TestSpecificity:
    def test_thread_more_specific_than_chat(self):
        thread_route = ProfileRoute.from_dict({
            "name": "thread", "platform": "discord", "profile": "a",
            "chat_id": "1", "thread_id": "2",
        })
        chat_route = ProfileRoute.from_dict({
            "name": "chat", "platform": "discord", "profile": "b",
            "chat_id": "1",
        })
        assert thread_route.specificity > chat_route.specificity

    def test_chat_more_specific_than_platform_only(self):
        chat_route = ProfileRoute.from_dict({
            "name": "chat", "platform": "discord", "profile": "a",
            "chat_id": "1",
        })
        platform_route = ProfileRoute.from_dict({
            "name": "platform", "platform": "discord", "profile": "b",
        })
        assert chat_route.specificity > platform_route.specificity


# ── parse_profile_routes ─────────────────────────────────────────────

class TestParseProfileRoutes:
    def test_empty_list(self):
        assert parse_profile_routes([]) == []

    def test_valid_routes(self):
        raw = [
            {"name": "a", "platform": "discord", "profile": "a", "chat_id": "1"},
            {"name": "b", "platform": "telegram", "profile": "b"},
        ]
        routes = parse_profile_routes(raw)
        assert len(routes) == 2

    def test_skips_missing_profile(self):
        raw = [
            {"name": "bad", "platform": "discord"},  # no profile
            {"name": "ok", "platform": "discord", "profile": "ok"},
        ]
        routes = parse_profile_routes(raw)
        assert len(routes) == 1
        assert routes[0].name == "ok"

    def test_skips_disabled(self):
        raw = [
            {"name": "off", "platform": "discord", "profile": "off", "enabled": False},
            {"name": "on", "platform": "discord", "profile": "on"},
        ]
        routes = parse_profile_routes(raw)
        assert len(routes) == 1
        assert routes[0].name == "on"

    def test_sorted_by_specificity(self):
        raw = [
            {"name": "platform", "platform": "discord", "profile": "a"},
            {"name": "chat", "platform": "discord", "profile": "b", "chat_id": "1"},
            {"name": "thread", "platform": "discord", "profile": "c", "chat_id": "1", "thread_id": "2"},
        ]
        routes = parse_profile_routes(raw)
        names = [r.name for r in routes]
        assert names == ["thread", "chat", "platform"]


# ── match_profile_route ──────────────────────────────────────────────

class TestMatchProfileRoute:
    def test_exact_thread_match(self):
        routes = parse_profile_routes([
            {"name": "chat-only", "platform": "discord", "profile": "a", "chat_id": "1"},
            {"name": "thread", "platform": "discord", "profile": "b", "chat_id": "1", "thread_id": "2"},
        ])
        source = _make_source(chat_id="1", thread_id="2")
        match = match_profile_route(source, routes)
        assert match is not None
        assert match.profile == "b"

    def test_chat_match_falls_through_to_chat_route(self):
        routes = parse_profile_routes([
            {"name": "thread", "platform": "discord", "profile": "b", "chat_id": "1", "thread_id": "2"},
            {"name": "chat-only", "platform": "discord", "profile": "a", "chat_id": "1"},
        ])
        source = _make_source(chat_id="1", thread_id="99")
        match = match_profile_route(source, routes)
        assert match is not None
        assert match.profile == "a"

    def test_no_match(self):
        routes = parse_profile_routes([
            {"name": "discord", "platform": "discord", "profile": "a", "chat_id": "1"},
        ])
        source = _make_source(platform="telegram", chat_id="1")
        match = match_profile_route(source, routes)
        assert match is None

    def test_empty_routes(self):
        source = _make_source()
        assert match_profile_route(source, []) is None

    def test_disabled_routes_skipped(self):
        routes = parse_profile_routes([
            {"name": "off", "platform": "discord", "profile": "off", "enabled": False, "chat_id": "1"},
            {"name": "on", "platform": "discord", "profile": "on", "chat_id": "1"},
        ])
        source = _make_source(chat_id="1")
        match = match_profile_route(source, routes)
        assert match is not None
        assert match.profile == "on"

    def test_thread_route_requires_chat_id_match(self):
        """A thread route with chat_id=1 should NOT match chat_id=2 even if thread_id matches."""
        routes = parse_profile_routes([
            {"name": "thread", "platform": "discord", "profile": "x", "chat_id": "1", "thread_id": "2"},
        ])
        source = _make_source(chat_id="999", thread_id="2")
        match = match_profile_route(source, routes)
        assert match is None
