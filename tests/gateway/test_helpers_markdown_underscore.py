"""Tests for underscore markdown stripping in gateway helpers.

The blanket _(.+?)_ and __(.+?)__ patterns incorrectly consumed
snake_case identifiers like send_as_bot and user_id.  The fix adds
lookbehind/lookahead boundaries so underscores adjacent to
alphanumeric characters are not treated as markdown formatting.

Mirrors fixes already tested in hermes_cli/test_cli_markdown_rendering.py.
"""
import unittest

from gateway.platforms.helpers import strip_markdown


class TestGatewayUnderscoreRegex(unittest.TestCase):
    """Verify markdown stripping preserves snake_case identifiers."""

    def test_snake_case_preserved(self):
        text = "Set send_as_bot to true and check user_id"
        result = strip_markdown(text)
        self.assertIn("send_as_bot", result)
        self.assertIn("user_id", result)

    def test_bold_underscore_stripped(self):
        result = strip_markdown("Here is __bold text__ for you")
        self.assertIn("bold text", result)
        self.assertNotIn("__bold", result)

    def test_italic_underscore_stripped(self):
        result = strip_markdown("Here is _italic_ text")
        self.assertIn("italic", result)
        self.assertNotIn("_italic_", result)

    def test_double_underscore_in_identifier_preserved(self):
        """Double underscores embedded in alphanumeric context should survive."""
        # e.g. x__y where underscores are between alphanumeric chars
        result = strip_markdown("Check my_var__name for details")
        self.assertIn("my_var__name", result)

    def test_config_keys_preserved(self):
        result = strip_markdown("Set max_tokens to 4096 and api_base_url to localhost")
        self.assertIn("max_tokens", result)
        self.assertIn("api_base_url", result)

    def test_asterisk_bold_unaffected(self):
        result = strip_markdown("This is **bold** text")
        self.assertIn("bold", result)
        self.assertNotIn("**", result)

    def test_mixed_formatting_and_identifiers(self):
        result = strip_markdown("Use **bold** and set send_as_bot to _true_")
        self.assertIn("send_as_bot", result)
        self.assertNotIn("**", result)

    def test_multiple_snake_case_in_one_line(self):
        text = "Configure thread_id, session_key, and platform_name"
        result = strip_markdown(text)
        self.assertIn("thread_id", result)
        self.assertIn("session_key", result)
        self.assertIn("platform_name", result)
