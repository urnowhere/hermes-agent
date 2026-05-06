"""Tests for config.yaml structure validation (validate_config_structure)."""

import pytest

from hermes_cli.config import validate_config_structure, ConfigIssue


class TestCustomProvidersValidation:
    """custom_providers must be a YAML list, not a dict."""

    def test_dict_instead_of_list(self):
        """The exact Discord user scenario — custom_providers as flat dict."""
        issues = validate_config_structure({
            "custom_providers": {
                "name": "Generativelanguage.googleapis.com",
                "base_url": "https://generativelanguage.googleapis.com/v1beta",
                "api_key": "xxx",
                "model": "models/gemini-2.5-flash",
                "rate_limit_delay": 2.0,
                "fallback_model": {
                    "provider": "openrouter",
                    "model": "qwen/qwen3.6-plus:free",
                },
            },
            "fallback_providers": [],
        })
        errors = [i for i in issues if i.severity == "error"]
        assert any("dict" in i.message and "list" in i.message for i in errors), (
            "Should detect custom_providers as dict instead of list"
        )

    def test_dict_detects_misplaced_fields(self):
        """When custom_providers is a dict, detect fields that look misplaced."""
        issues = validate_config_structure({
            "custom_providers": {
                "name": "test",
                "base_url": "https://example.com",
                "api_key": "xxx",
            },
        })
        warnings = [i for i in issues if i.severity == "warning"]
        # Should flag base_url, api_key as looking like custom_providers entry fields
        misplaced = [i for i in warnings if "custom_providers entry fields" in i.message]
        assert len(misplaced) == 1

    def test_dict_detects_nested_fallback(self):
        """When fallback_model gets swallowed into custom_providers dict."""
        issues = validate_config_structure({
            "custom_providers": {
                "name": "test",
                "fallback_model": {"provider": "openrouter", "model": "test"},
            },
        })
        errors = [i for i in issues if i.severity == "error"]
        assert any("fallback_model" in i.message and "inside" in i.message for i in errors)

    def test_valid_list_no_issues(self):
        """Properly formatted custom_providers should produce no issues."""
        issues = validate_config_structure({
            "custom_providers": [
                {"name": "gemini", "base_url": "https://example.com/v1"},
            ],
            "model": {"provider": "custom", "default": "test"},
        })
        assert len(issues) == 0

    def test_list_entry_missing_name(self):
        """List entry without name should warn."""
        issues = validate_config_structure({
            "custom_providers": [{"base_url": "https://example.com/v1"}],
            "model": {"provider": "custom"},
        })
        assert any("missing 'name'" in i.message for i in issues)

    def test_list_entry_missing_base_url(self):
        """List entry without base_url should warn."""
        issues = validate_config_structure({
            "custom_providers": [{"name": "test"}],
            "model": {"provider": "custom"},
        })
        assert any("missing 'base_url'" in i.message for i in issues)

    def test_list_entry_not_dict(self):
        """Non-dict list entries should warn."""
        issues = validate_config_structure({
            "custom_providers": ["not-a-dict"],
            "model": {"provider": "custom"},
        })
        assert any("not a dict" in i.message for i in issues)

    def test_none_custom_providers_no_issues(self):
        """No custom_providers at all should be fine."""
        issues = validate_config_structure({
            "model": {"provider": "openrouter"},
        })
        assert len(issues) == 0


class TestFallbackModelValidation:
    """fallback_model should be a top-level dict with provider + model."""

    def test_missing_provider(self):
        issues = validate_config_structure({
            "fallback_model": {"model": "anthropic/claude-sonnet-4"},
        })
        assert any("missing 'provider'" in i.message for i in issues)

    def test_missing_model(self):
        issues = validate_config_structure({
            "fallback_model": {"provider": "openrouter"},
        })
        assert any("missing 'model'" in i.message for i in issues)

    def test_valid_fallback(self):
        issues = validate_config_structure({
            "fallback_model": {
                "provider": "openrouter",
                "model": "anthropic/claude-sonnet-4",
            },
        })
        # Only fallback-related issues should be absent
        fb_issues = [i for i in issues if "fallback" in i.message.lower()]
        assert len(fb_issues) == 0

    def test_non_dict_fallback(self):
        issues = validate_config_structure({
            "fallback_model": "openrouter:anthropic/claude-sonnet-4",
        })
        assert any("should be a dict" in i.message for i in issues)

    def test_empty_fallback_dict_no_issues(self):
        """Empty fallback_model dict means disabled — no warnings needed."""
        issues = validate_config_structure({
            "fallback_model": {},
        })
        fb_issues = [i for i in issues if "fallback" in i.message.lower()]
        assert len(fb_issues) == 0

    def test_valid_fallback_list(self):
        """List-form fallback_model (chain) should validate when every entry has provider+model."""
        issues = validate_config_structure({
            "fallback_model": [
                {"provider": "openrouter", "model": "anthropic/claude-sonnet-4"},
                {"provider": "anthropic", "model": "claude-sonnet-4-6"},
            ],
        })
        fb_issues = [i for i in issues if "fallback" in i.message.lower()]
        assert len(fb_issues) == 0

    def test_fallback_list_entry_missing_provider(self):
        issues = validate_config_structure({
            "fallback_model": [
                {"provider": "openrouter", "model": "anthropic/claude-sonnet-4"},
                {"model": "claude-sonnet-4-6"},
            ],
        })
        assert any("fallback_model[1]" in i.message and "provider" in i.message for i in issues)

    def test_fallback_list_entry_missing_model(self):
        issues = validate_config_structure({
            "fallback_model": [
                {"provider": "openrouter"},
            ],
        })
        assert any("fallback_model[0]" in i.message and "model" in i.message for i in issues)

    def test_fallback_list_entry_not_a_dict(self):
        issues = validate_config_structure({
            "fallback_model": ["openrouter:anthropic/claude-sonnet-4"],
        })
        assert any("fallback_model[0]" in i.message and "should be a dict" in i.message for i in issues)


class TestMissingModelSection:
    """Warn when custom_providers exists but model section is missing."""

    def test_custom_providers_without_model(self):
        issues = validate_config_structure({
            "custom_providers": [
                {"name": "test", "base_url": "https://example.com/v1"},
            ],
        })
        assert any("no 'model' section" in i.message for i in issues)

    def test_custom_providers_with_model(self):
        issues = validate_config_structure({
            "custom_providers": [
                {"name": "test", "base_url": "https://example.com/v1"},
            ],
            "model": {"provider": "custom", "default": "test-model"},
        })
        # Should not warn about missing model section
        assert not any("no 'model' section" in i.message for i in issues)


class TestModelDefaultPseudoValue:
    """model.default must not be a pseudo-alias like 'latest', 'auto', etc.

    Regression for #14963: a support incident where 'model.default: latest'
    was incorrectly suggested as a valid Hermes config value.
    """

    def test_latest_warns(self):
        """'latest' is a known pseudo-alias and should produce a warning."""
        issues = validate_config_structure({
            "model": {"default": "latest", "provider": "openai"},
        })
        pseudo_issues = [
            i for i in issues
            if "model.default" in i.message and "latest" in i.message
        ]
        assert len(pseudo_issues) == 1
        assert pseudo_issues[0].severity == "warning"

    def test_auto_warns(self):
        issues = validate_config_structure({
            "model": {"default": "auto"},
        })
        assert any(
            "model.default" in i.message and "auto" in i.message
            for i in issues
        )

    def test_default_warns(self):
        issues = validate_config_structure({
            "model": {"default": "default"},
        })
        assert any("model.default" in i.message for i in issues)

    def test_case_insensitive(self):
        """Pseudo-values should be detected regardless of case."""
        for val in ("Latest", "LATEST", "Auto", "AUTO"):
            issues = validate_config_structure({
                "model": {"default": val},
            })
            assert any("model.default" in i.message for i in issues), (
                f"Expected warning for model.default: '{val}'"
            )

    def test_concrete_model_id_no_warning(self):
        """Real model IDs like 'gpt-5.5' must not trigger the warning."""
        for model_id in ("gpt-5.5", "anthropic/claude-sonnet-4", "deepseek-v4-flash", "hermes-3-llama-3.1-405b"):
            issues = validate_config_structure({
                "model": {"default": model_id},
            })
            pseudo_issues = [i for i in issues if "model.default" in i.message and "not a valid model ID" in i.message]
            assert len(pseudo_issues) == 0, (
                f"Concrete model ID '{model_id}' should not trigger pseudo-value warning, got: {pseudo_issues}"
            )

    def test_hint_mentions_concrete_example(self):
        """The warning hint should tell the user what to do instead."""
        issues = validate_config_structure({
            "model": {"default": "latest"},
        })
        pseudo_issues = [i for i in issues if "model.default" in i.message]
        assert len(pseudo_issues) == 1
        # Hint should reference 'hermes setup' or a concrete example
        assert "hermes setup" in pseudo_issues[0].hint or "gpt-" in pseudo_issues[0].hint

    def test_missing_model_section_no_crash(self):
        """Configs without any model section should not crash the validator."""
        issues = validate_config_structure({})
        # No pseudo-value warning when there is no model section
        assert not any("model.default" in i.message and "not a valid" in i.message for i in issues)

    def test_model_default_empty_string_no_warning(self):
        """Empty model.default should not trigger pseudo-value warning."""
        issues = validate_config_structure({
            "model": {"default": ""},
        })
        assert not any("not a valid model ID" in i.message for i in issues)

    def test_model_default_none_no_warning(self):
        """Null model.default should not trigger pseudo-value warning."""
        issues = validate_config_structure({
            "model": {"default": None},
        })
        assert not any("not a valid model ID" in i.message for i in issues)


class TestConfigIssueDataclass:
    """ConfigIssue should be a proper dataclass."""

    def test_fields(self):
        issue = ConfigIssue(severity="error", message="test msg", hint="test hint")
        assert issue.severity == "error"
        assert issue.message == "test msg"
        assert issue.hint == "test hint"

    def test_equality(self):
        a = ConfigIssue("error", "msg", "hint")
        b = ConfigIssue("error", "msg", "hint")
        assert a == b
