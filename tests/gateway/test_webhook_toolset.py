"""Regression tests for webhook platform/toolset registration."""

from hermes_cli.platforms import PLATFORMS as PLATFORM_REGISTRY
from hermes_cli.tools_config import PLATFORMS as TOOLS_CONFIG_PLATFORMS
from toolsets import _HERMES_CORE_TOOLS, get_toolset, resolve_toolset, validate_toolset


class TestHermesWebhookToolset:
    def test_toolset_exists(self):
        assert get_toolset("hermes-webhook") is not None

    def test_toolset_validates(self):
        assert validate_toolset("hermes-webhook")

    def test_toolset_resolves_to_shared_core_tools(self):
        assert set(resolve_toolset("hermes-webhook")) == set(_HERMES_CORE_TOOLS)


class TestWebhookPlatformRegistration:
    def test_platform_registry_maps_webhook_to_hermes_webhook(self):
        assert PLATFORM_REGISTRY["webhook"].default_toolset == "hermes-webhook"

    def test_tools_config_exports_webhook_platform_mapping(self):
        assert TOOLS_CONFIG_PLATFORMS["webhook"]["default_toolset"] == "hermes-webhook"

    def test_gateway_toolset_includes_hermes_webhook(self):
        gateway_tools = get_toolset("hermes-gateway")
        assert gateway_tools is not None
        assert "hermes-webhook" in gateway_tools["includes"]
