"""DeepSeek provider profile.

DeepSeek uses top-level ``reasoning_effort`` (like Kimi), not
``extra_body.reasoning`` (like OpenRouter).  The native API accepts
``high`` and ``max``; Hermes ``xhigh`` and ``max`` both map to ``max``,
while ``low``/``medium``/``minimal`` map to ``high``.
"""

from typing import Any

from providers import register_provider
from providers.base import ProviderProfile


_DEEPSEEK_EFFORT_MAP = {
    "minimal": "high",
    "low": "high",
    "medium": "high",
    "high": "high",
    "xhigh": "max",
    "max": "max",
}


class DeepSeekProfile(ProviderProfile):
    """DeepSeek — top-level reasoning_effort with effort value mapping."""

    def build_api_kwargs_extras(
        self,
        *,
        reasoning_config: dict | None = None,
        supports_reasoning: bool = False,
        **context: Any,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Emit ``reasoning_effort`` as a top-level api_kwargs key.

        DeepSeek does not use ``extra_body.reasoning`` — the native API
        expects ``reasoning_effort`` directly.  Hermes effort values are
        mapped to DeepSeek's ``high`` / ``max`` because the API only
        supports those two levels.
        """
        top_level: dict[str, Any] = {}
        if reasoning_config and isinstance(reasoning_config, dict):
            if reasoning_config.get("enabled") is False:
                return {}, top_level  # thinking on by default, don't emit
            effort = str(reasoning_config.get("effort", "medium") or "medium").strip().lower()
            mapped = _DEEPSEEK_EFFORT_MAP.get(effort, "high")
            top_level["reasoning_effort"] = mapped
        return {}, top_level


deepseek = DeepSeekProfile(
    name="deepseek",
    aliases=("deepseek-chat",),
    env_vars=("DEEPSEEK_API_KEY",),
    display_name="DeepSeek",
    description="DeepSeek — native DeepSeek API",
    signup_url="https://platform.deepseek.com/",
    fallback_models=(
        "deepseek-chat",
        "deepseek-reasoner",
    ),
    base_url="https://api.deepseek.com/v1",
)

register_provider(deepseek)
