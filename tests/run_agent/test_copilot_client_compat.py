"""Regression guard for Copilot Claude chat-completions client-build bugs (#12066).

Two separate bugs conspired to make Claude models on Copilot's chat-completions
endpoint fail with a misleading ``HTTP 400 model_not_supported`` even when the
exact same token + payload succeeded via raw ``requests.post``:

1. **Header-handoff bug** (``AIAgent.__init__``): when the routed-client path
   ran (no explicit creds), Hermes copied headers from ``_default_headers``
   only — the OpenAI SDK v1 attribute.  SDK v2 stores custom provider headers
   on ``_custom_headers`` (and exposes them via the public ``default_headers``
   property) instead, so Copilot's ``copilot-integration-id``,
   ``editor-version``, ``api-version`` headers silently vanished during the
   rebuild.

2. **Custom-transport incompatibility**
   (``_build_keepalive_http_client``): the
   ``HTTPTransport(socket_options=...)`` injection for TCP keepalive makes
   Copilot's Claude path return 400.  Same payload on a plain ``httpx.Client``
   succeeds.  Reporter's bisection narrowed it to the custom transport;
   a second user confirmed the patch fixes their session.

These tests pin both fixes at the **production-code entry points** so future
refactors (attribute-order changes, extra guards, etc.) can't let either bug
regress silently.  Key design choices driven by @Copilot's review on #15185:

* We mock ``httpx.Client`` construction and inspect the recorded ``kwargs``
  so every assertion is against the *actual* keyword arguments the code
  passes — a test that went green only because "a client came back" is
  worthless as a regression guard.
* ``TestRoutedHeaderHandoff`` exercises ``AIAgent.__init__`` through the
  routed-client branch rather than re-implementing the ``or``-chain in the
  test; a local reimplementation would stay green if the real code drifts.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import httpx
import pytest

from run_agent import AIAgent


# ---------------------------------------------------------------------------
# Fix 2 — per-endpoint transport compatibility (keepalive bypass)
# ---------------------------------------------------------------------------


class TestKeepaliveClientCopilotBypass:
    """``_build_keepalive_http_client`` must construct the ``httpx.Client``
    WITHOUT a custom ``HTTPTransport(socket_options=...)`` for
    ``api.githubcopilot.com``.  Every other host must keep the custom
    transport so the #10324 dead-peer-detection guarantee still holds.

    We mock ``httpx.Client`` at its module path inside ``run_agent`` and
    inspect the kwargs the call received — that way the assertions fail
    the moment the bypass regresses (even if a fake Client is still
    returned).
    """

    def _call_with_mocks(self, base_url: str):
        """Invoke ``_build_keepalive_http_client`` with ``httpx.Client``
        and ``httpx.HTTPTransport`` patched to capture construction args.
        Returns the recorded kwargs for both call-sites so the tests can
        assert shape.

        Note: we deliberately use plain ``MagicMock()`` for the fake
        return values rather than ``MagicMock(spec=httpx.Client)``.  A
        spec'd mock used as the ``transport=`` argument to a real
        ``httpx.Client`` (on some paths) triggers an internal TypeError
        that the outer try/except in ``_build_keepalive_http_client``
        swallows — the construction would never record and the test
        would spuriously fail.  A plain MagicMock is permissive enough
        to pass through httpx's internal wiring without actually being
        used as a transport (since Client is also mocked).
        """
        recorded = {"client_kwargs": None, "transport_kwargs": None}

        def _fake_client(*a, **kw):
            recorded["client_kwargs"] = dict(kw)
            return MagicMock()

        def _fake_transport(*a, **kw):
            recorded["transport_kwargs"] = dict(kw)
            return MagicMock()

        with patch("httpx.Client", side_effect=_fake_client), \
             patch("httpx.HTTPTransport", side_effect=_fake_transport):
            AIAgent._build_keepalive_http_client(base_url)
        return recorded

    # --- Copilot bypass: no custom transport -----------------------------

    def test_copilot_base_url_omits_custom_transport_kwarg(self):
        """Core fix: for ``api.githubcopilot.com`` the Client is built
        WITHOUT a ``transport=`` kwarg.  The previous-code path (which
        always passed ``transport=HTTPTransport(socket_options=[...])``)
        would FAIL this assertion — that's the whole point.
        """
        rec = self._call_with_mocks("https://api.githubcopilot.com/")
        assert rec["client_kwargs"] is not None, "Client was never constructed"
        assert "transport" not in rec["client_kwargs"], (
            f"Copilot host must NOT receive a custom transport — keepalive "
            f"transport breaks Claude chat-completions (#12066).  "
            f"Recorded kwargs: {rec['client_kwargs']!r}"
        )
        # And critically, HTTPTransport must not have been constructed at
        # all — that would mean the old keepalive path ran.
        assert rec["transport_kwargs"] is None, (
            "HTTPTransport(socket_options=...) was constructed for Copilot "
            "host — the bypass path must not touch the custom transport"
        )

    def test_copilot_bypass_still_forwards_proxy(self, monkeypatch):
        """Users behind Clash / corporate egress must not lose proxy
        routing just because we skipped the custom transport.  The proxy
        must still be forwarded to the Client ctor."""
        for key in ("HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY",
                    "https_proxy", "http_proxy", "all_proxy"):
            monkeypatch.delenv(key, raising=False)
        monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:7897")

        rec = self._call_with_mocks("https://api.githubcopilot.com/")
        assert rec["client_kwargs"] is not None
        assert rec["client_kwargs"].get("proxy") == "http://127.0.0.1:7897", (
            f"Copilot bypass dropped proxy forwarding: {rec['client_kwargs']!r}"
        )
        assert "transport" not in rec["client_kwargs"]

    @pytest.mark.parametrize("base_url", [
        "https://api.githubcopilot.com",
        "https://api.githubcopilot.com/",
        "https://api.githubcopilot.com/chat/completions",
        "https://API.GitHubCopilot.com/v1",
    ])
    def test_copilot_variant_hosts_all_bypass(self, base_url):
        """Trailing slash, path suffix, and mixed-case all trigger the
        bypass.  Users configure base_url in many shapes — all must
        route through the plain-client path."""
        rec = self._call_with_mocks(base_url)
        assert rec["client_kwargs"] is not None, f"no Client built for {base_url}"
        assert "transport" not in rec["client_kwargs"], (
            f"Copilot variant {base_url!r} fell through to keepalive "
            f"transport path — bypass must match all host variants"
        )

    # --- Non-Copilot hosts: keepalive transport stays installed ---------

    @pytest.mark.parametrize("base_url", [
        "https://api.openai.com/v1",
        "https://openrouter.ai/api/v1",
        "https://chatgpt.com/backend-api/codex",
        "https://api.anthropic.com/",
        "http://localhost:11434/v1",
    ])
    def test_non_copilot_hosts_get_custom_keepalive_transport(self, base_url):
        """Regression guard for the main keepalive use case (#10324).
        Every non-Copilot host MUST construct ``httpx.Client`` with a
        custom ``HTTPTransport(socket_options=[...])`` so the kernel
        detects dead provider sockets within ~60s.  Without this
        assertion, a future refactor that accidentally broadens the
        bypass (e.g. matching the wrong host substring) would silently
        remove keepalive from everyone.
        """
        rec = self._call_with_mocks(base_url)
        assert rec["client_kwargs"] is not None
        assert "transport" in rec["client_kwargs"], (
            f"Non-Copilot host {base_url!r} lost its custom keepalive "
            f"transport — #10324 dead-peer detection regresses.  "
            f"kwargs: {rec['client_kwargs']!r}"
        )
        assert rec["transport_kwargs"] is not None, (
            f"HTTPTransport was never constructed for {base_url!r}"
        )
        sock_opts = rec["transport_kwargs"].get("socket_options")
        assert sock_opts, (
            f"HTTPTransport for {base_url!r} was constructed WITHOUT "
            f"socket_options — keepalive tuning lost.  "
            f"transport kwargs: {rec['transport_kwargs']!r}"
        )
        # SO_KEEPALIVE=1 is the one option we require on every platform.
        import socket as _socket
        keepalive_opt = (_socket.SOL_SOCKET, _socket.SO_KEEPALIVE, 1)
        assert keepalive_opt in sock_opts, (
            f"SO_KEEPALIVE=1 missing from socket_options for {base_url!r}: "
            f"{sock_opts!r}"
        )

    def test_empty_base_url_does_not_bypass(self):
        """An empty base_url must not trigger the Copilot bypass — unknown
        hosts get the safe default (custom keepalive transport)."""
        rec = self._call_with_mocks("")
        assert rec["client_kwargs"] is not None
        assert "transport" in rec["client_kwargs"], (
            "Empty base_url fell through to Copilot bypass — the bypass "
            "must be opt-in on an explicit host match, not default-on"
        )


# ---------------------------------------------------------------------------
# Fix 1 — header handoff from routed client
# ---------------------------------------------------------------------------
#
# These tests exercise the REAL ``AIAgent.__init__`` routed-client branch
# by patching ``agent.auxiliary_client.resolve_provider_client`` to return
# fake clients with controlled attributes.  We then inspect
# ``agent._client_kwargs['default_headers']`` — that's the dict the agent
# will actually forward to the OpenAI SDK on the next API call, so a test
# that passes here proves the production code picks up the right headers
# regardless of how the ``or``-chain is spelt.


def _make_routed_agent(fake_routed_client, monkeypatch):
    """Build an AIAgent via the routed-client branch.

    The routed-client branch fires when ``api_key`` and ``base_url`` are
    not passed.  We intercept ``resolve_provider_client`` to return a
    pre-baked fake, so the agent ends up copying headers from our
    controlled object.  Other dependencies (OpenAI client construction,
    keepalive, etc.) are no-op'd so the test stays focused on the
    header-handoff slice.
    """
    monkeypatch.setattr(
        "agent.auxiliary_client.resolve_provider_client",
        lambda *a, **kw: (fake_routed_client, None),
    )
    # Prevent the agent from actually constructing an httpx keepalive
    # client or an OpenAI SDK client — we only care about what
    # client_kwargs looked like right before handoff.
    monkeypatch.setattr(
        AIAgent, "_build_keepalive_http_client",
        staticmethod(lambda base_url="": None),
    )
    monkeypatch.setattr(
        AIAgent, "_create_openai_client",
        lambda self, client_kwargs, *, reason, shared: MagicMock(),
    )

    agent = AIAgent(
        api_key=None,
        base_url=None,
        model="claude-opus-4.7",
        provider="copilot",
        quiet_mode=True,
    )
    return agent


class TestRoutedHeaderHandoff:
    """The routed-client branch must probe ``_custom_headers``,
    ``default_headers``, and ``_default_headers`` in that order and
    forward the winner onto ``client_kwargs['default_headers']``.

    These tests instantiate ``AIAgent`` for real and assert on
    ``agent._client_kwargs`` — not on a local re-implementation of the
    ``or``-chain — so they catch drift if the production code changes
    attribute order, adds extra guards, or renames the target kwarg.
    """

    def _fake_client_with_headers(
        self,
        *,
        custom=None,
        default_prop=None,
        default_underscore=None,
    ):
        """Return a fake routed client exposing whichever of the three
        header attributes we want populated.  ``SimpleNamespace`` is
        used so missing attributes raise ``AttributeError`` — which
        ``getattr(obj, name, None)`` handles, exactly matching the
        production code's shape."""
        fake = SimpleNamespace(
            api_key="routed-key",
            base_url="https://api.githubcopilot.com/",
        )
        if custom is not None:
            fake._custom_headers = custom
        if default_prop is not None:
            fake.default_headers = default_prop
        if default_underscore is not None:
            fake._default_headers = default_underscore
        return fake

    def test_custom_headers_wins_over_everything(self, monkeypatch):
        """SDK v2 path: ``_custom_headers`` populated → those flow onto
        ``client_kwargs['default_headers']`` verbatim."""
        routed = self._fake_client_with_headers(
            custom={"copilot-integration-id": "vscode-chat",
                    "api-version": "2025-04-01"},
            default_prop={"should-not": "see"},
            default_underscore={"also-ignored": "x"},
        )
        agent = _make_routed_agent(routed, monkeypatch)
        assert agent._client_kwargs.get("default_headers") == {
            "copilot-integration-id": "vscode-chat",
            "api-version": "2025-04-01",
        }, (
            f"_custom_headers must win on SDK v2.  Got: "
            f"{agent._client_kwargs.get('default_headers')!r}"
        )

    def test_default_headers_property_used_when_custom_missing(self, monkeypatch):
        """Hybrid SDK state: no ``_custom_headers`` but public
        ``default_headers`` is populated → falls to the public property."""
        routed = self._fake_client_with_headers(
            custom=None,
            default_prop={"editor-version": "vscode/1.99.0"},
            default_underscore={"legacy": "ignored"},
        )
        agent = _make_routed_agent(routed, monkeypatch)
        assert agent._client_kwargs.get("default_headers") == {
            "editor-version": "vscode/1.99.0"
        }

    def test_default_underscore_used_as_v1_fallback(self, monkeypatch):
        """SDK v1 legacy path: only ``_default_headers`` exists → used."""
        routed = self._fake_client_with_headers(
            custom=None,
            default_prop=None,
            default_underscore={"X-Legacy": "yes"},
        )
        agent = _make_routed_agent(routed, monkeypatch)
        assert agent._client_kwargs.get("default_headers") == {"X-Legacy": "yes"}

    def test_no_headers_leaves_kwargs_unset(self, monkeypatch):
        """Router returned a client with no headers at all → no
        ``default_headers`` entry on client_kwargs (unchanged upstream
        behaviour — the OpenAI SDK will use its defaults)."""
        routed = self._fake_client_with_headers(
            custom=None, default_prop=None, default_underscore=None,
        )
        agent = _make_routed_agent(routed, monkeypatch)
        assert "default_headers" not in agent._client_kwargs, (
            "No header source on routed client → client_kwargs must not "
            "carry an empty/None default_headers entry.  Got: "
            f"{agent._client_kwargs.get('default_headers')!r}"
        )

    def test_empty_custom_falls_through_to_next_slot(self, monkeypatch):
        """Critical: a real SDK v2 client may initialise
        ``_custom_headers = {}`` before the router installs overrides.
        An empty dict is falsy, so the ``or``-chain must fall through
        to the public ``default_headers`` property — otherwise the
        Copilot headers there would be swallowed."""
        routed = self._fake_client_with_headers(
            custom={},
            default_prop={"Copilot-Integration-Id": "chat"},
            default_underscore=None,
        )
        agent = _make_routed_agent(routed, monkeypatch)
        assert agent._client_kwargs.get("default_headers") == {
            "Copilot-Integration-Id": "chat"
        }, (
            "Empty _custom_headers must not swallow the next slot — "
            "Copilot's real headers would never make it to the client"
        )
