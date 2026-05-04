"""Regression tests for vision provider routing with custom base_url (#16290).

When a user configures `auxiliary.vision.provider = zai` together with a
custom `base_url` (e.g. `https://open.bigmodel.cn/api/paas/v4`), the
resolver previously forced provider="custom" and dropped the named
provider's credential pool, producing 401s when the user expected
`ZAI_API_KEY` to be auto-resolved.

The fix keeps routing through the named provider so its credential pool
is consulted, while honouring the explicit base_url override.
"""

from unittest.mock import patch

from agent import auxiliary_client


def _fake_resolve_task_provider_model_factory(
    provider: str,
    *,
    base_url: str = "",
    api_key: str = "",
    model: str = "",
):
    def _fake(_task, *args, **kwargs):
        return (provider, model, base_url, api_key, "chat_completions")

    return _fake


def test_vision_with_named_provider_and_base_url_routes_through_named_provider():
    """provider=zai + base_url should route through "zai", not "custom".

    Direct repro for #16290: when ZAI_API_KEY is in the credential pool
    and the user pins base_url to open.bigmodel.cn, the named provider
    (and its credential pool) must be used.
    """
    with (
        patch(
            "agent.auxiliary_client._resolve_task_provider_model",
            side_effect=_fake_resolve_task_provider_model_factory(
                "zai",
                base_url="https://open.bigmodel.cn/api/paas/v4",
                api_key="",  # left empty: expecting credential-pool auto-resolve
                model="glm-4v",
            ),
        ),
        patch(
            "agent.auxiliary_client._named_provider_has_credentials",
            return_value=True,
        ),
        patch(
            "agent.auxiliary_client.resolve_provider_client",
            return_value=("zai-client-sentinel", "glm-4v"),
        ) as mock_resolve,
    ):
        provider, client, model = auxiliary_client.resolve_vision_provider_client(
            provider="zai",
            model="glm-4v",
            base_url="https://open.bigmodel.cn/api/paas/v4",
        )

    assert provider == "zai"
    assert client == "zai-client-sentinel"
    assert model == "glm-4v"

    # Most importantly: the router was called with provider="zai", not "custom",
    # and it forwarded the explicit base_url override.
    call_args = mock_resolve.call_args
    assert call_args.args[0] == "zai"
    assert call_args.kwargs["explicit_base_url"] == "https://open.bigmodel.cn/api/paas/v4"


def test_vision_with_base_url_falls_back_to_custom_when_named_provider_has_no_creds():
    """If the named provider has no credentials, fall back to "custom".

    Preserves prior behaviour for users who have base_url configured but
    no provider-specific key in their pool.
    """
    custom_calls = []

    def _record_resolve(provider, *args, **kwargs):
        custom_calls.append(provider)
        if provider == "custom":
            return ("custom-client-sentinel", "glm-4v")
        return (None, None)

    with (
        patch(
            "agent.auxiliary_client._resolve_task_provider_model",
            side_effect=_fake_resolve_task_provider_model_factory(
                "zai",
                base_url="https://open.bigmodel.cn/api/paas/v4",
                api_key="",
                model="glm-4v",
            ),
        ),
        patch(
            "agent.auxiliary_client._named_provider_has_credentials",
            return_value=False,
        ),
        patch(
            "agent.auxiliary_client.resolve_provider_client",
            side_effect=_record_resolve,
        ),
    ):
        provider, client, _ = auxiliary_client.resolve_vision_provider_client(
            provider="zai",
            model="glm-4v",
            base_url="https://open.bigmodel.cn/api/paas/v4",
        )

    assert custom_calls == ["custom"]
    assert provider == "custom"
    assert client == "custom-client-sentinel"


def test_vision_with_explicit_api_key_skips_named_provider_routing():
    """When the user already supplied api_key in config, don't try the
    credential pool — go straight to the custom path with the explicit
    key. Preserves prior behaviour for the documented workaround.
    """
    custom_calls = []

    def _record_resolve(provider, *args, **kwargs):
        custom_calls.append(provider)
        return ("custom-client-sentinel", "glm-4v")

    with (
        patch(
            "agent.auxiliary_client._resolve_task_provider_model",
            side_effect=_fake_resolve_task_provider_model_factory(
                "zai",
                base_url="https://open.bigmodel.cn/api/paas/v4",
                api_key="hardcoded-key",
                model="glm-4v",
            ),
        ),
        patch(
            "agent.auxiliary_client.resolve_provider_client",
            side_effect=_record_resolve,
        ),
    ):
        provider, _, _ = auxiliary_client.resolve_vision_provider_client(
            provider="zai",
            model="glm-4v",
            base_url="https://open.bigmodel.cn/api/paas/v4",
            api_key="hardcoded-key",
        )

    # Goes straight to custom — no detour through "zai".
    assert custom_calls == ["custom"]
    assert provider == "custom"
